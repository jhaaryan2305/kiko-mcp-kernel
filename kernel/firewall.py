"""The 3-Tier Risk & Rollback Firewall: the only path from a tool plan to execution.

    Tier 0  silent read         -> execute
    Tier 1  reversible write    -> ledger (pending, with inverse) -> execute -> ledger (executed/failed)
    Tier 2  circuit breaker     -> ledger (pending audit) -> HUD elicitation -> verify -> execute -> ledger

Transport is injected (``ToolExecutor``, ``InverseExecutor``, ``Approver``), so the
firewall is pure, deterministic and testable without FastMCP, the HUD or AWS.

Tool result convention for Tier 1: a tool may return an ``"undo"`` object holding
data only known after execution (e.g. a created draft id). It is merged into the
ledger entry's inverse arguments and stripped from the result passed upstream.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Mapping, Optional

from .contracts import (
    ApprovalRequest,
    ApprovalResponse,
    InverseAction,
    JsonObject,
    LedgerEntry,
    LedgerKind,
    Tier,
    UndoStrategy,
    canonical_sha256,
    utc_now,
)
from .elicitation import ApprovalDenied, build_approval_request, verify_approval
from .ledger import UndoLedger
from .policy import Action, Decision, PolicyEngine

try:  # Python 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol  # type: ignore

UNDO_RESULT_KEY = "undo"


# --------------------------------------------------------------------------- ports


class ToolExecutor(Protocol):
    """Calls a forward tool on its MCP server (implemented by the gateway)."""

    async def call_tool(self, server: str, tool: str, arguments: JsonObject) -> JsonObject: ...


class InverseExecutor(Protocol):
    """Runs a Tier 1 inverse on the owning server (implemented by the gateway)."""

    async def call_inverse(self, server: str, tool: str, inverse: InverseAction) -> None: ...


class Approver(Protocol):
    """Shows the Elicitation card on the HUD and returns the user's decision."""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse: ...


# --------------------------------------------------------------------------- values


@dataclass(frozen=True)
class ToolCall:
    """One step of a tool plan, after local PII rehydration."""

    tool: str
    arguments: JsonObject
    intent_redacted: str
    """Redacted intent that produced this call. Goes to the ledger; never raw content."""
    summary: Optional[str] = None
    """Human-readable line for the Tier 2 card (rendered locally)."""
    trace_id: Optional[str] = None


class Outcome(str, Enum):
    EXECUTED = "executed"
    FAILED = "failed"
    DECLINED = "declined"
    EXPIRED = "expired"
    REJECTED = "rejected"  # malformed tool name; never routed


@dataclass(frozen=True)
class DispatchResult:
    tool: str
    tier: Tier
    outcome: Outcome
    result: Optional[JsonObject] = None
    ledger_entry_id: Optional[str] = None
    reason: Optional[str] = None


# --------------------------------------------------------------------------- firewall


class Firewall:
    def __init__(
        self,
        policy: PolicyEngine,
        ledger: UndoLedger,
        executor: ToolExecutor,
        approver: Approver,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._policy = policy
        self._ledger = ledger
        self._executor = executor
        self._approver = approver
        self._clock = clock

    async def dispatch(self, call: ToolCall) -> DispatchResult:
        decision = self._policy.evaluate(call.tool)
        if decision.server is None:
            return DispatchResult(call.tool, decision.tier, Outcome.REJECTED, reason="malformed tool name")
        # Freeze arguments: what is hashed, approved and executed must be identical.
        arguments: JsonObject = copy.deepcopy(dict(call.arguments))

        if decision.action is Action.EXECUTE:
            return await self._silent(decision, call, arguments)
        if decision.action is Action.EXECUTE_WITH_UNDO:
            return await self._reversible(decision, call, arguments)
        return await self._circuit_breaker(decision, call, arguments)

    async def undo_last(self, inverse_executor: InverseExecutor) -> Optional[LedgerEntry]:
        """Agentic Ctrl+Z: reverse the newest executed Tier 1 action.

        Returns the undone entry, or ``None`` if there is nothing to undo. If the
        inverse raises, the entry stays ``executed`` so the user can retry.
        """
        entry = self._ledger.latest_undoable()
        if entry is None:
            return None
        assert entry.inverse is not None
        await inverse_executor.call_inverse(entry.server, entry.tool, entry.inverse)
        return self._ledger.mark_undone(entry.entry_id)

    # ------------------------------------------------------------------ tiers

    async def _silent(self, decision: Decision, call: ToolCall, arguments: JsonObject) -> DispatchResult:
        assert decision.server is not None
        try:
            result = await self._executor.call_tool(decision.server, call.tool, arguments)
        except Exception as exc:  # noqa: BLE001 - report type only; messages may hold user data
            return DispatchResult(call.tool, decision.tier, Outcome.FAILED, reason=type(exc).__name__)
        return DispatchResult(call.tool, decision.tier, Outcome.EXECUTED, result=result)

    async def _reversible(self, decision: Decision, call: ToolCall, arguments: JsonObject) -> DispatchResult:
        assert decision.server is not None and decision.policy is not None and decision.policy.undo is not None
        strategy: UndoStrategy = decision.policy.undo.strategy
        # Ledger first. A LedgerError propagates and the tool never runs.
        entry = self._ledger.record_pending(
            kind=LedgerKind.UNDO,
            tool=call.tool,
            server=decision.server,
            tier=decision.tier,
            policy_version=decision.policy_version,
            intent_redacted=call.intent_redacted,
            arguments_sha256=canonical_sha256(arguments),
            inverse=InverseAction(strategy, {"forward_arguments": arguments}),
            trace_id=call.trace_id,
        )
        try:
            result = await self._executor.call_tool(decision.server, call.tool, arguments)
        except Exception as exc:  # noqa: BLE001
            self._ledger.mark_failed(entry.entry_id)
            return DispatchResult(call.tool, decision.tier, Outcome.FAILED, ledger_entry_id=entry.entry_id,
                                  reason=type(exc).__name__)
        result = dict(result)
        undo_data = result.pop(UNDO_RESULT_KEY, None)
        if undo_data is not None and not isinstance(undo_data, Mapping):
            undo_data = None  # TODO(sprint): treat malformed undo data as a server contract violation
        self._ledger.mark_executed(entry.entry_id, inverse_arguments=undo_data)
        return DispatchResult(call.tool, decision.tier, Outcome.EXECUTED, result=result,
                              ledger_entry_id=entry.entry_id)

    async def _circuit_breaker(self, decision: Decision, call: ToolCall, arguments: JsonObject) -> DispatchResult:
        assert decision.server is not None
        entry = self._ledger.record_pending(
            kind=LedgerKind.AUDIT,
            tool=call.tool,
            server=decision.server,
            tier=Tier.CIRCUIT_BREAKER,
            policy_version=decision.policy_version,
            intent_redacted=call.intent_redacted,
            arguments_sha256=canonical_sha256(arguments),
            trace_id=call.trace_id,
        )
        request = build_approval_request(
            decision, arguments,
            summary=call.summary or "Run {}".format(call.tool),
            now=self._clock(),
            trace_id=call.trace_id,
        )
        response = await self._approver.request_approval(request)
        record = response.to_record()
        try:
            verify_approval(request, response, arguments, now=self._clock())
        except ApprovalDenied as denied:
            if denied.reason == "expired":
                self._ledger.mark_expired(entry.entry_id, approval=record)
                outcome = Outcome.EXPIRED
            else:
                self._ledger.mark_declined(entry.entry_id, approval=record)
                outcome = Outcome.DECLINED
            return DispatchResult(call.tool, decision.tier, outcome, ledger_entry_id=entry.entry_id,
                                  reason=denied.reason)
        try:
            result = await self._executor.call_tool(decision.server, call.tool, arguments)
        except Exception as exc:  # noqa: BLE001
            self._ledger.mark_failed(entry.entry_id, approval=record)
            return DispatchResult(call.tool, decision.tier, Outcome.FAILED, ledger_entry_id=entry.entry_id,
                                  reason=type(exc).__name__)
        self._ledger.mark_executed(entry.entry_id, approval=record)
        return DispatchResult(call.tool, decision.tier, Outcome.EXECUTED, result=dict(result),
                              ledger_entry_id=entry.entry_id)
