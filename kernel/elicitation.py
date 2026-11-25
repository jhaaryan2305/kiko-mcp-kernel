"""Tier 2 approval: build MCP Elicitation requests and verify responses.

An approval is accepted only if **all** of these hold:

1. it answers this exact request (``request_id``),
2. it came from the local HUD (``source == "client_hud"``),
3. it is bound to the exact arguments shown (``arguments_sha256``), and those
   arguments still hash the same at execution time,
4. it has not expired (``approval_ttl_seconds`` from the policy),
5. the user accepted *and* confirmed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Mapping, Optional

from .contracts import (
    APPROVAL_SOURCE,
    ApprovalAction,
    ApprovalRequest,
    ApprovalResponse,
    JsonObject,
    SideEffects,
    canonical_sha256,
)
from .policy import Decision

SUMMARY_MAX_CHARS = 280


class ApprovalDenied(Exception):
    """The Tier 2 action must not run. ``reason`` is a stable machine-readable code."""

    REASONS = ("declined", "cancelled", "expired", "mismatch", "untrusted_source", "unconfirmed")

    def __init__(self, reason: str) -> None:
        assert reason in self.REASONS, reason
        super().__init__(reason)
        self.reason = reason


def build_approval_request(
    decision: Decision,
    arguments: Mapping[str, object],
    *,
    summary: str,
    now: datetime,
    trace_id: Optional[str] = None,
) -> ApprovalRequest:
    """Create the payload for the HUD's Elicitation card.

    ``arguments`` are the rehydrated (real) values: this payload never leaves the device.
    """
    if decision.server is None:
        raise ValueError("cannot request approval for a malformed tool name: {}".format(decision.tool))
    ttl = decision.rules.approval_ttl_seconds
    assert ttl is not None, "Tier 2 rules always carry approval_ttl_seconds"
    # Unlisted tools fail closed: treat their side effects as external.
    effects = decision.policy.side_effects if decision.policy else SideEffects.EXTERNAL
    preview: JsonObject = dict(arguments)
    return ApprovalRequest(
        request_id=str(uuid.uuid4()),
        tool=decision.tool,
        server=decision.server,
        policy_version=decision.policy_version,
        summary=summary[:SUMMARY_MAX_CHARS],
        arguments_preview=preview,
        arguments_sha256=canonical_sha256(arguments),
        side_effects=effects,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
        trace_id=trace_id,
    )


def verify_approval(
    request: ApprovalRequest,
    response: ApprovalResponse,
    arguments: Mapping[str, object],
    *,
    now: datetime,
) -> None:
    """Raise :class:`ApprovalDenied` unless the response authorizes exactly these arguments."""
    if response.request_id != request.request_id:
        raise ApprovalDenied("mismatch")
    if response.source != APPROVAL_SOURCE:
        raise ApprovalDenied("untrusted_source")
    if now > request.expires_at or response.responded_at > request.expires_at:
        raise ApprovalDenied("expired")
    if response.action is ApprovalAction.DECLINE:
        raise ApprovalDenied("declined")
    if response.action is ApprovalAction.CANCEL:
        raise ApprovalDenied("cancelled")
    if response.confirm is not True:
        raise ApprovalDenied("unconfirmed")
    current = canonical_sha256(arguments)
    if not (response.arguments_sha256 == request.arguments_sha256 == current):
        raise ApprovalDenied("mismatch")
