"""Shared fakes for kernel tests. No network, no FastMCP, no HUD."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

from kernel.contracts import ApprovalAction, ApprovalRequest, ApprovalResponse, InverseAction, JsonObject

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
T0 = datetime(2026, 11, 26, 9, 0, 0, tzinfo=timezone.utc)


def load_policy_doc() -> Dict[str, Any]:
    with (SCHEMAS / "policy_tiers.json").open() as f:
        return json.load(f)


def load_server(dirname: str) -> ModuleType:
    path = ROOT / "mcp_servers" / dirname / "server.py"
    spec = importlib.util.spec_from_file_location(dirname.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class FakeExecutor:
    def __init__(self, results: Optional[Dict[str, JsonObject]] = None, on_call: Optional[Callable[[str], None]] = None):
        self.results = results or {}
        self.calls: List[Tuple[str, str, JsonObject]] = []
        self.on_call = on_call
        self.fail: Optional[Exception] = None

    async def call_tool(self, server: str, tool: str, arguments: JsonObject) -> JsonObject:
        if self.on_call:
            self.on_call(tool)
        self.calls.append((server, tool, arguments))
        if self.fail:
            raise self.fail
        return dict(self.results.get(tool, {"ok": True}))


class FakeInverseExecutor:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, InverseAction]] = []

    async def call_inverse(self, server: str, tool: str, inverse: InverseAction) -> None:
        self.calls.append((server, tool, inverse))


class FakeApprover:
    """Responds to Elicitation requests. ``respond`` may customise the response."""

    def __init__(
        self,
        action: ApprovalAction = ApprovalAction.ACCEPT,
        clock: Optional[Clock] = None,
        respond: Optional[Callable[[ApprovalRequest], ApprovalResponse]] = None,
        delay_seconds: int = 0,
    ) -> None:
        self.action = action
        self.clock = clock
        self.respond = respond
        self.delay_seconds = delay_seconds
        self.requests: List[ApprovalRequest] = []

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(request)
        if self.clock:
            self.clock.advance(self.delay_seconds)
        if self.respond:
            return self.respond(request)
        return ApprovalResponse(
            request_id=request.request_id,
            arguments_sha256=request.arguments_sha256,
            action=self.action,
            responded_at=self.clock() if self.clock else request.created_at,
            confirm=True if self.action is ApprovalAction.ACCEPT else None,
        )


def schema_validator(schema_file: str):
    """Return a jsonschema validator, or None if jsonschema>=4.18 is not installed."""
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError:
        return None
    schemas = {}
    for p in SCHEMAS.glob("*.schema.json"):
        with p.open() as f:
            schemas[p.name] = json.load(f)
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())
    cls = jsonschema.validators.validator_for(schemas[schema_file])
    return cls(schemas[schema_file], registry=registry, format_checker=cls.FORMAT_CHECKER)
