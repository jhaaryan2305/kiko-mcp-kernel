"""AWS serverless relay for async and time-based triggers (EventBridge -> Lambda).

Privacy rule: a :class:`RelayJob` carries **redacted intent only** — no tool
arguments, no token map. When a job fires, the callback re-enters the kernel and
goes through the firewall like any other call (Tier 2 still needs the HUD).

Credentials come from the standard AWS credential chain (env, profile, SSO).
Never pass keys in code or config files.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .contracts import JsonObject, to_iso

try:
    from typing import Protocol
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol  # type: ignore


@dataclass(frozen=True)
class RelayJob:
    job_id: str
    fire_at: datetime
    intent_redacted: str
    trace_id: Optional[str] = None

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "job_id": self.job_id,
            "fire_at": to_iso(self.fire_at),
            "intent_redacted": self.intent_redacted,
        }
        if self.trace_id:
            payload["trace_id"] = self.trace_id
        return payload


class AsyncRelay(Protocol):
    async def schedule(self, job: RelayJob) -> None: ...

    async def cancel(self, job_id: str) -> None: ...


class EventBridgeRelay:
    """EventBridge Scheduler-backed relay.

    TODO(sprint):
      - create a one-time schedule per job targeting the relay Lambda
        (``boto3.client("scheduler").create_schedule``)
      - Lambda posts the payload back to the gateway callback endpoint
      - authenticate callbacks (signed payload) before re-entering the firewall
    """

    def __init__(self, region: Optional[str], target_arn: Optional[str], role_arn: Optional[str]) -> None:
        self.region = region
        self.target_arn = target_arn
        self.role_arn = role_arn

    async def schedule(self, job: RelayJob) -> None:
        if not (self.target_arn and self.role_arn):
            raise RuntimeError("relay not configured: set KIKO_RELAY_TARGET_ARN and KIKO_RELAY_ROLE_ARN")
        raise NotImplementedError("TODO(sprint): EventBridge Scheduler create_schedule")

    async def cancel(self, job_id: str) -> None:
        raise NotImplementedError("TODO(sprint): EventBridge Scheduler delete_schedule")
