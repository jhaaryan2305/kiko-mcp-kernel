"""kiko-life-mcp: calendar and personal scheduling.

Tiers (from schemas/policy_tiers.json):
    life.get_calendar_events     Tier 0  silent read
    life.create_calendar_block   Tier 1  reversible (undo: delete_created)

This server only executes. Tiering, approval and the ledger live in the kernel.
Never call another kiko-*-mcp server from here.

Run standalone:  python mcp_servers/kiko-life-mcp/server.py   (requires fastmcp)
"""
from typing import Any, Callable, Dict

SERVER_NAME = "kiko-life-mcp"


def get_calendar_events(start: str, end: str) -> Dict[str, Any]:
    """List events between two RFC 3339 timestamps.

    Returns: ``{"events": [{"event_id": str, "title": str, "start": str, "end": str}]}``
    """
    # TODO(sprint): EventKit (pyobjc) read access; local calendars only.
    raise NotImplementedError("life.get_calendar_events")


def create_calendar_block(title: str, start: str, end: str) -> Dict[str, Any]:
    """Create a block on the user's own calendar (no invitees). Tier 1: reversible.

    Returns: ``{"event_id": str, "undo": {"event_id": str}}``
    """
    # TODO(sprint): EventKit write; reject invitees here (inviting others would be external -> Tier 2).
    raise NotImplementedError("life.create_calendar_block")


# ---------------------------------------------------------------- Tier 1 inverses (not exposed as tools)


def delete_calendar_event(event_id: str, **_: Any) -> None:
    """Inverse of ``life.create_calendar_block`` (strategy ``delete_created``)."""
    # TODO(sprint): EventKit remove; no-op if already deleted.
    raise NotImplementedError("undo life.create_calendar_block")


TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "life.get_calendar_events": get_calendar_events,
    "life.create_calendar_block": create_calendar_block,
}

INVERSES: Dict[str, Callable[..., None]] = {
    "life.create_calendar_block": delete_calendar_event,
}


def build_server() -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME)
    for name, fn in TOOLS.items():
        mcp.tool(name=name)(fn)
    return mcp


if __name__ == "__main__":
    build_server().run()
