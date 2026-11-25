# CLAUDE.md — K.I.K.O.

**K.I.K.O. (Knowledge Injected Kernel Orchestrator)** by Team **KERNOPS**, built for **wHACKiest 2026, Track T-1: Make MCP Matter**.

K.I.K.O. is a zero-config, OS-native voice and hotkey (`Ctrl + Option + K`) kernel that takes MCP out of developer IDEs and puts it in front of everyday users. It orchestrates files, messaging, calendars, commute logistics and web tasks, and every action passes through deterministic safety controls.

## Core Principle

> **Your data stays local. Only intent hits the cloud.**

Every design decision follows from this:

- Raw user data (files, messages, screen context, memory) never leaves the device.
- PII is redacted locally **before** anything is sent to a remote model or relay.
- Only the minimal, redacted intent crosses the network boundary.
- Local state (memory, Undo Ledger) lives on-device in SQLite.

If a change would send raw user content off-device, it is wrong. Redesign it.

## Architecture

```
Voice / Hotkey / Screen Context      (SwiftUI HUD + ElevenLabs)
        ↓
Local Privacy & Intent Router        (PII redaction + local memory)
        ↓
3-Tier Risk & Rollback Firewall      (deterministic policy engine)
        ↓
FastMCP Gateway + AWS Serverless Relay
        ↓
MCP Servers: kiko-os · kiko-comms · kiko-life · kiko-mobility · kiko-web
```

## 3-Tier Risk & Rollback Firewall

The firewall is **deterministic**: a tool's tier comes from `schemas/policy_tiers.json`, never from a model's judgment at runtime.

| Tier | Policy key | Scope | Behavior |
|------|-----------|-------|----------|
| **0: Silent Read** | `tier_0_silent` | Read-only queries | Runs autonomously and silently. No side effects. |
| **1: Reversible Write** | `tier_1_reversible` | Reversible local writes | Runs immediately. Each action is recorded **before it runs** in the local SQLite Undo Ledger (WAL mode) so it can be rolled back with one click (Agentic Ctrl+Z). |
| **2: Circuit Breaker** | `tier_2_circuit_breaker` | Irreversible or external actions | Execution stops. The user must confirm explicitly through an MCP Elicitation card in the HUD before anything runs. The decision is recorded as an `audit` ledger entry. |

Firewall rules:

- **Fail closed.** A tool missing from the policy file is treated as Tier 2.
- A Tier 1 action that cannot write its ledger entry must not run.
- A Tier 2 action never runs without confirmation, and no flag, config or prompt can bypass it.
- Moving a tool to a lower tier is a security-sensitive change. Say so explicitly in the PR.

## Repository Layout

```
kernel/          policy.py · firewall.py · ledger.py · elicitation.py · redactor.py · contracts.py
                 gateway.py (FastMCP) · relay.py (AWS EventBridge) · config.py (env only)
tests/           stdlib unittest suite, including policy ↔ server contract tests
mcp_servers/     One directory per domain MCP server
  kiko-os-mcp/         files, active window, semantic search
  kiko-comms-mcp/      drafts and messaging
  kiko-life-mcp/       calendar and personal scheduling
  kiko-mobility-mcp/   ETA and ride staging
  kiko-web-mcp/        headless web tasks
client_hud/      Native macOS SwiftUI floating HUD, ElevenLabs voice feedback, Tier 2 approval cards
schemas/         policy_tiers.json (source of truth) + JSON Schema contracts for the policy,
                 ledger entries and Tier 2 elicitation; examples/ and check_consistency.py
```

## Engineering Conventions

**Tools and servers**
- Name tools `<domain>.<verb_object>` (for example `os.semantic_file_search` or `comms.send_message`). The domain must match its server (`kiko-<domain>-mcp`).
- Every new tool needs a tier entry in `schemas/policy_tiers.json` in the same change.
- Each MCP server is self-contained and talks to the others only through the kernel. Servers never call each other directly.
- Each `mcp_servers/<server>/server.py` exports `SERVER_NAME`, `TOOLS` (exactly its policy tools) and `INVERSES` (exactly its Tier 1 tools). `tests/test_servers.py` enforces this.
- Tier 1 tools return data that is only known after they run (such as a created id) under an `"undo"` key. The firewall moves it into the ledger.
- Every Tier 1 tool declares an `undo` strategy in the policy file and implements its inverse next to its forward operation.

**Privacy**
- Never log raw user content. Logs and OpenTelemetry spans carry redacted intent, tool names, tiers and outcomes only.
- No secrets in the repo. Load credentials (AWS, ElevenLabs, etc.) from the environment.

**Policy and schemas**
- `schemas/` is the single source of truth. Code reads tiers and contracts from it and never hardcodes them.
- Bump `kernel_policy_version` whenever tier assignments change, and update the docs and `schemas/examples/` to match.
- Ledger rows and Tier 2 approval payloads must conform to `ledger_entry.schema.json` and `elicitation.schema.json`.
- Run `python3 schemas/check_consistency.py` before every commit that touches `schemas/`, `README.md` or `ARCHITECTURE.md`. It must pass.

**Code**
- Kernel and servers: Python 3.11+ with type hints. HUD: Swift/SwiftUI.
- The kernel core (`policy`, `ledger`, `firewall`, `elicitation`, `redactor`) uses only the standard library. FastMCP and boto3 are imported lazily, only in `gateway.py`, `relay.py` and `mcp_servers/`.
- Tests: `python -m unittest`. Install `.[dev]` so the schema-conformance tests run instead of being skipped.
- Mark unfinished work `TODO(sprint): …`. Stubs raise `NotImplementedError` and never return fake success.
- Failure reasons report the exception type only. Exception messages can contain user data.
- Keep changes small and focused. Prefer clear, deterministic logic over clever heuristics on the safety path.
- Any change to the policy engine, redactor or Undo Ledger needs a test for its failure path (denied, rolled back, redacted).

**Git**
- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
- `main` should always be in a demo-ready state.
