"""Deterministic policy engine for the 3-Tier Risk & Rollback Firewall.

The tier of a tool comes from ``schemas/policy_tiers.json`` and nothing else.
A model's plan can name a tool; it can never choose that tool's tier.

The loader re-checks the invariants from ``policy_tiers.schema.json`` at
runtime, so a hand-edited policy file cannot weaken the firewall.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from .contracts import SideEffects, Tier, UndoStrategy

TOOL_NAME_RE = re.compile(r"^(os|comms|life|mobility|web)\.[a-z]+(_[a-z]+)*$")


class PolicyError(ValueError):
    """The policy document violates a firewall invariant. The kernel must not start."""


class Action(str, Enum):
    """What the firewall must do with a tool call."""

    EXECUTE = "execute"  # Tier 0
    EXECUTE_WITH_UNDO = "execute_with_undo"  # Tier 1
    REQUIRE_APPROVAL = "require_approval"  # Tier 2


_ACTION_BY_TIER = {
    Tier.SILENT: Action.EXECUTE,
    Tier.REVERSIBLE: Action.EXECUTE_WITH_UNDO,
    Tier.CIRCUIT_BREAKER: Action.REQUIRE_APPROVAL,
}


@dataclass(frozen=True)
class UndoSpec:
    strategy: UndoStrategy
    description: str


@dataclass(frozen=True)
class ToolPolicy:
    """One entry of ``policy_tiers.json#/tools``."""

    name: str
    server: str
    tier: Tier
    side_effects: SideEffects
    description: str
    undo: Optional[UndoSpec] = None


@dataclass(frozen=True)
class TierRules:
    """One entry of ``policy_tiers.json#/tiers``."""

    tier: Tier
    requires_confirmation: bool
    rollback_required: bool
    ledger: str  # "none" | "undo" | "audit"
    approval_ttl_seconds: Optional[int] = None


@dataclass(frozen=True)
class Decision:
    """The firewall's verdict for a single tool name."""

    tool: str
    tier: Tier
    action: Action
    rules: TierRules
    policy_version: str
    server: Optional[str]
    """Owning server. ``None`` only when the tool name itself is malformed."""
    policy: Optional[ToolPolicy]
    """``None`` when the tool is not listed (fail-closed decision)."""
    reason: str

    @property
    def listed(self) -> bool:
        return self.policy is not None


def default_policy_path() -> Path:
    """``<repo>/schemas/policy_tiers.json``, resolved relative to this package."""
    return Path(__file__).resolve().parent.parent / "schemas" / "policy_tiers.json"


class PolicyEngine:
    """Loads, validates and evaluates the tier policy."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        self._doc = document
        self.version: str = _require(document, "kernel_policy_version", str)
        self._validate_privacy(document)
        if document.get("default_tier") != Tier.CIRCUIT_BREAKER.value:
            raise PolicyError("default_tier must fail closed to tier_2_circuit_breaker")
        self._rules: Dict[Tier, TierRules] = self._load_tiers(document)
        self.tools: Dict[str, ToolPolicy] = self._load_tools(document)

    @classmethod
    def from_file(cls, path: Optional[Union[str, Path]] = None) -> "PolicyEngine":
        target = Path(path) if path else default_policy_path()
        with target.open(encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------------------------ public API

    def rules(self, tier: Tier) -> TierRules:
        return self._rules[tier]

    def evaluate(self, tool_name: str) -> Decision:
        """Classify a tool call. Unlisted tools fail closed to Tier 2."""
        policy = self.tools.get(tool_name)
        if policy is not None:
            return Decision(
                tool=tool_name,
                tier=policy.tier,
                action=_ACTION_BY_TIER[policy.tier],
                rules=self._rules[policy.tier],
                policy_version=self.version,
                server=policy.server,
                policy=policy,
                reason="listed in policy",
            )
        server = "kiko-{}-mcp".format(tool_name.split(".", 1)[0]) if TOOL_NAME_RE.match(tool_name) else None
        return Decision(
            tool=tool_name,
            tier=Tier.CIRCUIT_BREAKER,
            action=Action.REQUIRE_APPROVAL,
            rules=self._rules[Tier.CIRCUIT_BREAKER],
            policy_version=self.version,
            server=server,
            policy=None,
            reason="unlisted tool: fail closed",
        )

    # ------------------------------------------------------------------ validation

    @staticmethod
    def _validate_privacy(doc: Mapping[str, Any]) -> None:
        expected = {
            "mode": "local_first_pii_redacted",
            "egress": "redacted_intent_only",
            "pii_redaction": "on_device_before_egress",
            "pii_rehydration": "local_gateway_only",
            "ledger_locality": "device_only",
            "log_raw_content": False,
        }
        privacy = _require(doc, "privacy", dict)
        for key, value in expected.items():
            if privacy.get(key) != value:
                raise PolicyError("privacy.{} must be {!r}".format(key, value))

    @staticmethod
    def _load_tiers(doc: Mapping[str, Any]) -> Dict[Tier, TierRules]:
        tiers = _require(doc, "tiers", dict)
        pinned = {
            Tier.SILENT: {"level": 0, "requires_confirmation": False, "rollback_required": False, "ledger": "none"},
            Tier.REVERSIBLE: {
                "level": 1, "requires_confirmation": False, "rollback_required": True, "ledger": "undo",
                "ledger_backend": "sqlite_local_wal", "ledger_write_before_execute": True,
            },
            Tier.CIRCUIT_BREAKER: {
                "level": 2, "requires_confirmation": True, "rollback_required": False, "ledger": "audit",
                "ledger_backend": "sqlite_local_wal", "elicitation_protocol": "mcp_elicitation",
                "approval_source": "client_hud", "approval_binding": "arguments_sha256", "bypassable": False,
            },
        }
        out: Dict[Tier, TierRules] = {}
        for tier, constants in pinned.items():
            spec = tiers.get(tier.value)
            if not isinstance(spec, dict):
                raise PolicyError("missing tier {}".format(tier.value))
            for key, value in constants.items():
                if spec.get(key) != value:
                    raise PolicyError("{}.{} must be {!r}".format(tier.value, key, value))
            ttl = spec.get("approval_ttl_seconds")
            if tier is Tier.CIRCUIT_BREAKER and not (isinstance(ttl, int) and 10 <= ttl <= 600):
                raise PolicyError("tier_2_circuit_breaker.approval_ttl_seconds must be an int in [10, 600]")
            out[tier] = TierRules(
                tier=tier,
                requires_confirmation=spec["requires_confirmation"],
                rollback_required=spec["rollback_required"],
                ledger=spec["ledger"],
                approval_ttl_seconds=ttl,
            )
        return out

    @staticmethod
    def _load_tools(doc: Mapping[str, Any]) -> Dict[str, ToolPolicy]:
        tools = _require(doc, "tools", dict)
        out: Dict[str, ToolPolicy] = {}
        for name, spec in tools.items():
            if not TOOL_NAME_RE.match(name):
                raise PolicyError("{}: tool names must be <domain>.<verb_object>".format(name))
            domain = name.split(".", 1)[0]
            server = spec.get("server")
            if server != "kiko-{}-mcp".format(domain):
                raise PolicyError("{}: must be owned by kiko-{}-mcp, not {}".format(name, domain, server))
            try:
                tier = Tier(spec["tier"])
                effects = SideEffects(spec["side_effects"])
            except (KeyError, ValueError) as exc:
                raise PolicyError("{}: invalid tier or side_effects ({})".format(name, exc)) from None
            undo_raw = spec.get("undo")
            if (tier is Tier.REVERSIBLE) != (undo_raw is not None):
                raise PolicyError("{}: undo must be declared if and only if the tool is Tier 1".format(name))
            if tier is Tier.SILENT and effects is not SideEffects.NONE:
                raise PolicyError("{}: Tier 0 tools must have no side effects".format(name))
            if tier is Tier.REVERSIBLE and effects is not SideEffects.LOCAL:
                raise PolicyError("{}: Tier 1 tools must have local side effects only".format(name))
            if effects is SideEffects.EXTERNAL and tier is not Tier.CIRCUIT_BREAKER:
                raise PolicyError("{}: external side effects require Tier 2".format(name))
            undo = UndoSpec(UndoStrategy(undo_raw["strategy"]), undo_raw["description"]) if undo_raw else None
            out[name] = ToolPolicy(name, server, tier, effects, spec.get("description", ""), undo)
        return out


def _require(doc: Mapping[str, Any], key: str, kind: type) -> Any:
    value = doc.get(key)
    if not isinstance(value, kind):
        raise PolicyError("{} must be a {}".format(key, kind.__name__))
    return value
