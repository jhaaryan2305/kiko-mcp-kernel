"""Typed contracts shared across the kernel.

These mirror the JSON Schemas in ``schemas/`` one-to-one:

- :class:`LedgerEntry`      -> ``schemas/ledger_entry.schema.json``
- :class:`ApprovalRequest`  -> ``schemas/elicitation.schema.json#/$defs/approval_request``
- :class:`ApprovalResponse` -> ``schemas/elicitation.schema.json#/$defs/approval_response``

If you change a field here, change the schema (and ``schemas/examples/``) in the same commit.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional

JsonObject = Dict[str, Any]


class Tier(str, Enum):
    """Risk tier keys, exactly as they appear in ``policy_tiers.json``."""

    SILENT = "tier_0_silent"
    REVERSIBLE = "tier_1_reversible"
    CIRCUIT_BREAKER = "tier_2_circuit_breaker"

    @property
    def level(self) -> int:
        return {"tier_0_silent": 0, "tier_1_reversible": 1, "tier_2_circuit_breaker": 2}[self.value]


class SideEffects(str, Enum):
    NONE = "none"
    LOCAL = "local"
    EXTERNAL = "external"


class UndoStrategy(str, Enum):
    """How a Tier 1 action is reversed.

    ``restore_snapshot``: revert to prior state captured in the ledger.
    ``delete_created``:   remove the object whose id was captured in the ledger.
    """

    RESTORE_SNAPSHOT = "restore_snapshot"
    DELETE_CREATED = "delete_created"


class LedgerKind(str, Enum):
    UNDO = "undo"  # Tier 1
    AUDIT = "audit"  # Tier 2


class LedgerStatus(str, Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"
    UNDONE = "undone"
    DECLINED = "declined"
    EXPIRED = "expired"


class ApprovalAction(str, Enum):
    """Mirrors MCP elicitation response actions."""

    ACCEPT = "accept"
    DECLINE = "decline"
    CANCEL = "cancel"


APPROVAL_SOURCE = "client_hud"
"""The only component allowed to approve Tier 2 actions."""


# --------------------------------------------------------------------------- helpers


def canonical_sha256(arguments: Mapping[str, Any]) -> str:
    """Hash tool arguments canonically (sorted keys, compact separators, UTF-8).

    Tier 2 approvals are bound to this hash, so the HUD, firewall and gateway
    must all use this exact function.
    """
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """RFC 3339 UTC timestamp with a ``Z`` suffix, second precision."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- ledger


@dataclass(frozen=True)
class InverseAction:
    """Everything needed to reverse a Tier 1 action. Device-only."""

    strategy: UndoStrategy
    arguments: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        return {"strategy": self.strategy.value, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class ApprovalRecord:
    """Outcome of a Tier 2 elicitation, as stored on an audit ledger entry."""

    request_id: str
    action: ApprovalAction
    responded_at: datetime

    def to_dict(self) -> JsonObject:
        return {
            "request_id": self.request_id,
            "action": self.action.value,
            "responded_at": to_iso(self.responded_at),
        }


@dataclass(frozen=True)
class LedgerEntry:
    """One row of the local Undo / Audit Ledger."""

    entry_id: str
    sequence: int
    kind: LedgerKind
    tool: str
    server: str
    tier: Tier
    policy_version: str
    intent_redacted: str
    arguments_sha256: str
    status: LedgerStatus
    recorded_at: datetime
    executed_at: Optional[datetime] = None
    undone_at: Optional[datetime] = None
    trace_id: Optional[str] = None
    inverse: Optional[InverseAction] = None
    approval: Optional[ApprovalRecord] = None

    def to_dict(self) -> JsonObject:
        """Serialize to the ``ledger_entry.schema.json`` shape."""
        out: JsonObject = {
            "entry_id": self.entry_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "tool": self.tool,
            "server": self.server,
            "tier": self.tier.value,
            "policy_version": self.policy_version,
            "intent_redacted": self.intent_redacted,
            "arguments_sha256": self.arguments_sha256,
            "status": self.status.value,
            "recorded_at": to_iso(self.recorded_at),
        }
        if self.executed_at:
            out["executed_at"] = to_iso(self.executed_at)
        if self.undone_at:
            out["undone_at"] = to_iso(self.undone_at)
        if self.trace_id:
            out["trace_id"] = self.trace_id
        if self.inverse:
            out["inverse"] = self.inverse.to_dict()
        if self.approval:
            out["approval"] = self.approval.to_dict()
        return out


# --------------------------------------------------------------------------- elicitation


@dataclass(frozen=True)
class ApprovalRequest:
    """Payload rendered on the HUD's Tier 2 MCP Elicitation card. Device-only."""

    request_id: str
    tool: str
    server: str
    policy_version: str
    summary: str
    arguments_preview: JsonObject
    arguments_sha256: str
    side_effects: SideEffects
    created_at: datetime
    expires_at: datetime
    trace_id: Optional[str] = None

    def to_dict(self) -> JsonObject:
        out: JsonObject = {
            "type": "approval_request",
            "request_id": self.request_id,
            "tool": self.tool,
            "server": self.server,
            "tier": Tier.CIRCUIT_BREAKER.value,
            "policy_version": self.policy_version,
            "summary": self.summary,
            "arguments_preview": dict(self.arguments_preview),
            "arguments_sha256": self.arguments_sha256,
            "irreversible": True,
            "side_effects": self.side_effects.value,
            "created_at": to_iso(self.created_at),
            "expires_at": to_iso(self.expires_at),
        }
        if self.trace_id:
            out["trace_id"] = self.trace_id
        return out


@dataclass(frozen=True)
class ApprovalResponse:
    """The user's decision, sent back from the HUD."""

    request_id: str
    arguments_sha256: str
    action: ApprovalAction
    responded_at: datetime
    confirm: Optional[bool] = None
    source: str = APPROVAL_SOURCE

    def to_dict(self) -> JsonObject:
        out: JsonObject = {
            "type": "approval_response",
            "request_id": self.request_id,
            "arguments_sha256": self.arguments_sha256,
            "action": self.action.value,
            "source": self.source,
            "responded_at": to_iso(self.responded_at),
        }
        if self.confirm is not None:
            out["content"] = {"confirm": self.confirm}
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApprovalResponse":
        content = data.get("content") or {}
        return cls(
            request_id=data["request_id"],
            arguments_sha256=data["arguments_sha256"],
            action=ApprovalAction(data["action"]),
            responded_at=from_iso(data["responded_at"]),
            confirm=content.get("confirm"),
            source=data["source"],
        )

    def to_record(self) -> ApprovalRecord:
        return ApprovalRecord(self.request_id, self.action, self.responded_at)
