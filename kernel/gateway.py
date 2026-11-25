"""FastMCP gateway: the only component that talks to the MCP servers.

Responsibilities:
  - route approved calls to ``kiko-<domain>-mcp`` (implements ``ToolExecutor``)
  - run Tier 1 inverses for Agentic Ctrl+Z (implements ``InverseExecutor``)
  - rehydrate PII locally, immediately before execution
  - emit redacted OpenTelemetry spans (tool, tier, outcome — never arguments)
  - hand future-dated work to the AWS relay

FastMCP is imported lazily so the policy engine, ledger and firewall stay
importable and testable without it.
"""
from __future__ import annotations

from typing import Dict

from .contracts import InverseAction, JsonObject


class McpGateway:
    """Routes tool calls to MCP servers.

    TODO(sprint):
      - spawn/attach each server (``fastmcp.Client`` over stdio) on startup
      - ``call_tool``: ``await client.call_tool(tool, arguments)`` and return structured content
      - ``call_inverse``: invoke the server's inverse entry point for ``tool``
        (see ``INVERSES`` in each ``mcp_servers/*/server.py``)
      - wrap each call in an OTel span with attributes: tool, server, tier, outcome
    """

    def __init__(self, servers: Dict[str, str]) -> None:
        """``servers`` maps server name (``kiko-os-mcp``) to its launch target."""
        self._servers = dict(servers)

    async def call_tool(self, server: str, tool: str, arguments: JsonObject) -> JsonObject:
        self._require(server)
        raise NotImplementedError("TODO(sprint): route {} via fastmcp.Client".format(tool))

    async def call_inverse(self, server: str, tool: str, inverse: InverseAction) -> None:
        self._require(server)
        raise NotImplementedError("TODO(sprint): run inverse for {}".format(tool))

    def _require(self, server: str) -> None:
        if server not in self._servers:
            raise LookupError("unknown MCP server: {}".format(server))
