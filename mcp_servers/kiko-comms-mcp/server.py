"""kiko-comms-mcp: drafts and messaging.

Tiers (from schemas/policy_tiers.json):
    comms.create_draft    Tier 1  reversible (undo: delete_created)
    comms.send_message    Tier 2  circuit breaker

This server only executes. Tiering, approval and the ledger live in the kernel.
Never call another kiko-*-mcp server from here.

Run standalone:  python mcp_servers/kiko-comms-mcp/server.py   (requires fastmcp)
"""
from typing import Any, Callable, Dict

SERVER_NAME = "kiko-comms-mcp"


def create_draft(to: str, body: str, channel: str = "default") -> Dict[str, Any]:
    """Create an unsent draft. Tier 1: reversible.

    Args:
        to: recipient (rehydrated locally from a ``[CONTACT_n]`` placeholder).
        body: message text.
        channel: ``"default"``, ``"imessage"``, ``"email"`` or ``"whatsapp"``.

    Returns: ``{"draft_id": str, "undo": {"draft_id": str}}``
    """
    # TODO(sprint): Mail.app / Messages draft via AppleScript; return a stable draft id.
    raise NotImplementedError("comms.create_draft")


def send_message(to: str, body: str, channel: str = "default") -> Dict[str, Any]:
    """Send a message. Tier 2: runs only after HUD approval; cannot be undone.

    Returns: ``{"message_id": str, "sent_at": str}``
    """
    # TODO(sprint): send via the channel's local client; never via a third-party relay.
    raise NotImplementedError("comms.send_message")


# ---------------------------------------------------------------- Tier 1 inverses (not exposed as tools)


def delete_draft(draft_id: str, **_: Any) -> None:
    """Inverse of ``comms.create_draft`` (strategy ``delete_created``)."""
    # TODO(sprint): delete the draft; no-op if the user already sent or deleted it.
    raise NotImplementedError("undo comms.create_draft")


TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "comms.create_draft": create_draft,
    "comms.send_message": send_message,
}

INVERSES: Dict[str, Callable[..., None]] = {
    "comms.create_draft": delete_draft,
}


def build_server() -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME)
    for name, fn in TOOLS.items():
        mcp.tool(name=name)(fn)
    return mcp


if __name__ == "__main__":
    build_server().run()
