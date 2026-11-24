# K.I.K.O. — Knowledge Injected Kernel Orchestrator

> Keeping everything strictly to you, in a world where everything lives on a server. Your data stays local. Only intent hits the cloud.

**Team:** KERNOPS (ef59bf21) | **College:** Dayananda Sagar University  
**Event:** wHACKiest 2026 (MSRIT) | **Track:** T-1 — Make MCP Matter

## Overview
Model Context Protocol (MCP) is currently trapped inside developer IDEs and manual JSON configs. K.I.K.O. brings MCP to everyday users as a zero-config, OS-native voice and hotkey autonomy kernel (Ctrl + Option + K) that orchestrates files, messaging, calendars, commute logistics, and web tasks under deterministic safety controls.

## Architecture Flow
Voice / Hotkey / Screen Context (SwiftUI HUD + ElevenLabs) -> Local Privacy & Intent Router (PII Redaction + Local Memory) -> Deterministic 3-Tier Policy Firewall -> FastMCP Gateway + AWS Serverless Relay -> Modular Everyday MCP Servers (kiko-os-mcp, kiko-comms-mcp, kiko-life-mcp, kiko-mobility-mcp, kiko-web-mcp).

## 3-Tier Risk & Rollback Firewall
- Tier 0 (Read-only queries): Silent autonomous execution with zero side effects.
- Tier 1 (Reversible local writes): Immediate execution logged to a local SQLite Undo Ledger for one-click Agentic Ctrl+Z rollback.
- Tier 2 (Irreversible external actions): Hard circuit-breaker pausing execution with an interactive MCP Elicitation card for explicit confirmation.

## Repository Roadmap (24-Hour Hack Day Build)
- /kernel: FastMCP gateway, 3-Tier Policy Engine, PII redactor, SQLite Undo Ledger, OpenTelemetry tracing, and AWS Lambda/EventBridge async relay.
- /mcp_servers: Modular everyday MCP tool servers (OS, Comms, Life, Mobility, Headless Web).
- /client_hud: Lightweight native macOS SwiftUI floating HUD with ElevenLabs voice feedback and Tier-2 approval cards.
- /schemas: Declarative policy tier rules and MCP tool contracts.

## Team KERNOPS
- Aryan Jha (Lead) — ENG26AM0739
- Deepti Sunil — ENG26CS0565
- Aayush Srivastava — ENG26AM0344
- Shubham Kumar Sah — ENG26AM0711
