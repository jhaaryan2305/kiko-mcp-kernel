"""kiko-mobility-mcp: commute and ride logistics.

Tiers (from schemas/policy_tiers.json):
    mobility.estimate_eta          Tier 0  silent read
    mobility.stage_ride_deeplink   Tier 2  circuit breaker

This server only executes. Tiering, approval and the ledger live in the kernel.
Never call another kiko-*-mcp server from here.

Run standalone:  python mcp_servers/kiko-mobility-mcp/server.py   (requires fastmcp)
"""
from typing import Any, Callable, Dict

SERVER_NAME = "kiko-mobility-mcp"


def estimate_eta(destination: str, mode: str = "driving") -> Dict[str, Any]:
    """Estimate travel time from the current location.

    Args:
        destination: address or place name (rehydrated locally).
        mode: ``"driving"``, ``"transit"``, ``"walking"`` or ``"cycling"``.

    Returns: ``{"minutes": int, "mode": str, "departure_suggestion": str}``
    """
    # TODO(sprint): MapKit ETA (MKDirections) on-device; API keys, if any, come from the environment.
    raise NotImplementedError("mobility.estimate_eta")


def stage_ride_deeplink(pickup: str, destination: str, provider: str) -> Dict[str, Any]:
    """Open a ride-hailing app with pickup and destination prefilled. Tier 2: needs HUD approval.

    Returns: ``{"deeplink": str, "opened": bool}``
    """
    # TODO(sprint): build provider deeplink and open it; never book or pay on the user's behalf.
    raise NotImplementedError("mobility.stage_ride_deeplink")


TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "mobility.estimate_eta": estimate_eta,
    "mobility.stage_ride_deeplink": stage_ride_deeplink,
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
