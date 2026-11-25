"""kiko-web-mcp: headless web tasks.

Tiers (from schemas/policy_tiers.json):
    web.submit_external_form   Tier 2  circuit breaker

This server only executes. Tiering, approval and the ledger live in the kernel.
Never call another kiko-*-mcp server from here.

Run standalone:  python mcp_servers/kiko-web-mcp/server.py   (requires fastmcp)
"""
from typing import Any, Callable, Dict

SERVER_NAME = "kiko-web-mcp"


def submit_external_form(url: str, fields: Dict[str, str]) -> Dict[str, Any]:
    """Fill and submit a form on an external site. Tier 2: needs HUD approval.

    Args:
        url: https URL of the page containing the form.
        fields: form field name -> value (rehydrated locally).

    Returns: ``{"submitted": bool, "final_url": str, "status": int}``
    """
    # TODO(sprint): headless Playwright; https only; isolated browser profile; no stored cookies.
    raise NotImplementedError("web.submit_external_form")


TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "web.submit_external_form": submit_external_form,
}

INVERSES: Dict[str, Callable[..., None]] = {}


def build_server() -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME)
    for name, fn in TOOLS.items():
        mcp.tool(name=name)(fn)
    return mcp


if __name__ == "__main__":
    build_server().run()
