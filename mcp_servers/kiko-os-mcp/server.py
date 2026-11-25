"""kiko-os-mcp: local files and windows.

Tiers (from schemas/policy_tiers.json):
    os.read_active_window     Tier 0  silent read
    os.semantic_file_search   Tier 0  silent read
    os.organize_files         Tier 1  reversible (undo: restore_snapshot)
    os.delete_file            Tier 2  circuit breaker

This server only executes. Tiering, approval and the ledger live in the kernel.
Never call another kiko-*-mcp server from here.

Run standalone:  python mcp_servers/kiko-os-mcp/server.py   (requires fastmcp)
"""
from typing import Any, Callable, Dict, List

SERVER_NAME = "kiko-os-mcp"


def read_active_window() -> Dict[str, Any]:
    """Return the frontmost app and window title.

    Returns: ``{"app": str, "title": str}``
    """
    # TODO(sprint): NSWorkspace.frontmostApplication + Accessibility API (AXTitle) via pyobjc.
    raise NotImplementedError("os.read_active_window")


def semantic_file_search(query: str, limit: int = 10) -> Dict[str, Any]:
    """Search local files by meaning using an on-device embedding index.

    Args:
        query: natural-language query (already rehydrated locally).
        limit: maximum results, 1-50.

    Returns: ``{"results": [{"path": str, "score": float, "snippet": str}]}``
    """
    # TODO(sprint): Spotlight (mdfind) candidate set -> local embedding rerank. No cloud calls.
    raise NotImplementedError("os.semantic_file_search")


def organize_files(moves: List[Dict[str, str]]) -> Dict[str, Any]:
    """Move or rename files. Tier 1: must be fully reversible.

    Args:
        moves: ``[{"from": "<abs path>", "to": "<abs path>"}]``. Refuse to overwrite existing targets.

    Returns: ``{"moved": int, "undo": {"moves": [{"from": <to>, "to": <from>}]}}``
        The ``undo`` object is stored in the ledger and passed to ``undo_organize_files``.
    """
    # TODO(sprint): validate all paths first, apply atomically (os.replace), roll back partial moves on error.
    raise NotImplementedError("os.organize_files")


def delete_file(path: str) -> Dict[str, Any]:
    """Permanently delete a file. Tier 2: runs only after HUD approval.

    Returns: ``{"deleted": str}``
    """
    # TODO(sprint): refuse directories and paths outside the user's home; no recursive deletes.
    raise NotImplementedError("os.delete_file")


# ---------------------------------------------------------------- Tier 1 inverses (not exposed as tools)


def undo_organize_files(moves: List[Dict[str, str]], **_: Any) -> None:
    """Inverse of ``os.organize_files`` (strategy ``restore_snapshot``): apply the recorded reverse moves."""
    # TODO(sprint): apply reverse moves in reverse order; fail loudly if a target was modified since.
    raise NotImplementedError("undo os.organize_files")


TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "os.read_active_window": read_active_window,
    "os.semantic_file_search": semantic_file_search,
    "os.organize_files": organize_files,
    "os.delete_file": delete_file,
}

INVERSES: Dict[str, Callable[..., None]] = {
    "os.organize_files": undo_organize_files,
}


def build_server() -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME)
    for name, fn in TOOLS.items():
        mcp.tool(name=name)(fn)
    return mcp


if __name__ == "__main__":
    build_server().run()
