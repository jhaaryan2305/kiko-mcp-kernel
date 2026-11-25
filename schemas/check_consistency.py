#!/usr/bin/env python3
"""Check that policy, schemas, examples and docs agree.

Stdlib-only structural checks always run. If `jsonschema` (>=4.18) is
installed, the policy file and examples are also validated against the
JSON Schemas.

Usage: python3 schemas/check_consistency.py
Exit status is non-zero on any inconsistency.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
TIER_BY_LEVEL = {0: "tier_0_silent", 1: "tier_1_reversible", 2: "tier_2_circuit_breaker"}
TOOL_RE = re.compile(r"`((?:os|comms|life|mobility|web)\.[a-z_]+)`")

errors: List[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def check_policy(policy: dict) -> Dict[str, dict]:
    tools: Dict[str, dict] = policy["tools"]
    if policy.get("default_tier") != "tier_2_circuit_breaker":
        fail("policy: default_tier must fail closed to tier_2_circuit_breaker")
    for name, spec in tools.items():
        domain = name.split(".", 1)[0]
        if spec["server"] != "kiko-{}-mcp".format(domain):
            fail("policy: {} is owned by {}, expected kiko-{}-mcp".format(name, spec["server"], domain))
        if spec["tier"] not in policy["tiers"]:
            fail("policy: {} has unknown tier {}".format(name, spec["tier"]))
        if (spec["tier"] == "tier_1_reversible") != ("undo" in spec):
            fail("policy: {} must define undo if and only if it is Tier 1".format(name))
    return tools


def check_readme(tools: Dict[str, dict]) -> None:
    text = (ROOT / "README.md").read_text()
    for line in text.splitlines():
        m = re.match(r"\|\s*\*\*(\d):", line)
        if not m:
            continue
        tier = TIER_BY_LEVEL[int(m.group(1))]
        for name in TOOL_RE.findall(line):
            if name in tools and tools[name]["tier"] != tier:
                fail("README.md: {} listed as {} but policy says {}".format(name, tier, tools[name]["tier"]))


def check_architecture(tools: Dict[str, dict]) -> None:
    text = (ROOT / "ARCHITECTURE.md").read_text()
    seen = set()
    for line in text.splitlines():
        m = re.match(r"\|\s*`kiko-(\w+)-mcp`", line)
        if not m:
            continue
        domain = m.group(1)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for level, cell in enumerate(cells[2:5]):
            for short in re.findall(r"`([a-z_]+)`", cell):
                name = "{}.{}".format(domain, short)
                seen.add(name)
                expected = TIER_BY_LEVEL[level]
                if name not in tools:
                    fail("ARCHITECTURE.md: server table lists unknown tool {}".format(name))
                elif tools[name]["tier"] != expected:
                    fail("ARCHITECTURE.md: {} listed as {} but policy says {}".format(name, expected, tools[name]["tier"]))
    for name in sorted(set(tools) - seen):
        fail("ARCHITECTURE.md: server table is missing {}".format(name))


def check_doc_mentions(tools: Dict[str, dict]) -> None:
    for doc in ("README.md", "ARCHITECTURE.md", "CLAUDE.md"):
        for name in set(TOOL_RE.findall((ROOT / doc).read_text())):
            if name not in tools:
                fail("{}: mentions {} which is not in policy_tiers.json".format(doc, name))


def check_examples(policy: dict, tools: Dict[str, dict]) -> None:
    version = policy["kernel_policy_version"]
    ex = SCHEMAS / "examples"
    for path in sorted(ex.glob("*.json")):
        doc = load(path)
        if "tool" not in doc:
            continue  # approval responses reference a request, not a tool
        name = doc["tool"]
        if name not in tools:
            fail("{}: unknown tool {}".format(path.name, name))
            continue
        if doc.get("tier") != tools[name]["tier"]:
            fail("{}: tier {} disagrees with policy {}".format(path.name, doc.get("tier"), tools[name]["tier"]))
        if doc.get("server") != tools[name]["server"]:
            fail("{}: server disagrees with policy".format(path.name))
        if doc.get("policy_version") != version:
            fail("{}: policy_version {} != {}".format(path.name, doc.get("policy_version"), version))
        strategy = doc.get("inverse", {}).get("strategy")
        if strategy and strategy != tools[name]["undo"]["strategy"]:
            fail("{}: undo strategy disagrees with policy".format(path.name))
    req = load(ex / "elicitation.request.json")
    resp = load(ex / "elicitation.response.json")
    if (req["request_id"], req["arguments_sha256"]) != (resp["request_id"], resp["arguments_sha256"]):
        fail("examples: elicitation response is not bound to its request")
    if req["arguments_sha256"] != canonical_sha256(req["arguments_preview"]):
        fail("examples: elicitation request hash is not canonical_sha256(arguments_preview)")
    audit = load(ex / "ledger_entry.audit.json")
    if audit.get("approval", {}).get("request_id") == req["request_id"] and audit["arguments_sha256"] != req["arguments_sha256"]:
        fail("examples: audit entry hash differs from the approval request it references")
    undo = load(ex / "ledger_entry.undo.json")
    forward = undo.get("inverse", {}).get("arguments", {}).get("forward_arguments")
    if forward is None or undo["arguments_sha256"] != canonical_sha256(forward):
        fail("examples: undo entry hash is not canonical_sha256(inverse.arguments.forward_arguments)")


def canonical_sha256(arguments: dict) -> str:
    """Same canonical form as kernel.contracts.canonical_sha256 (kept stdlib-local on purpose)."""
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_with_jsonschema(policy: dict) -> bool:
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError:
        return False
    schemas = {p.name: load(p) for p in SCHEMAS.glob("*.schema.json")}
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )

    def validate(instance: dict, schema_file: str, label: str) -> None:
        cls = jsonschema.validators.validator_for(schemas[schema_file])
        validator = cls(schemas[schema_file], registry=registry, format_checker=cls.FORMAT_CHECKER)
        for err in validator.iter_errors(instance):
            fail("{}: {} at {}".format(label, err.message, "/".join(map(str, err.absolute_path)) or "<root>"))

    validate(policy, "policy_tiers.schema.json", "policy_tiers.json")
    for path in sorted((SCHEMAS / "examples").glob("*.json")):
        target = "ledger_entry.schema.json" if path.name.startswith("ledger") else "elicitation.schema.json"
        validate(load(path), target, "examples/" + path.name)
    return True


def main() -> int:
    policy = load(SCHEMAS / "policy_tiers.json")
    tools = check_policy(policy)
    check_readme(tools)
    check_architecture(tools)
    check_doc_mentions(tools)
    check_examples(policy, tools)
    validated = validate_with_jsonschema(policy)

    if errors:
        print("\n".join("✗ " + e for e in errors))
        return 1
    print("✓ {} tools consistent across policy, schemas, examples and docs".format(len(tools)))
    if not validated:
        print("  (install jsonschema>=4.18 to also run JSON Schema validation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
