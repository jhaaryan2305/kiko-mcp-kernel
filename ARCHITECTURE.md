# K.I.K.O. Architecture

> **Your data stays local. Only intent hits the cloud.**

K.I.K.O. is designed as a kernel. It sits between the user and every MCP capability on the machine. The kernel owns privacy, policy and rollback, and the MCP servers only carry out actions. This document describes the components, the trust boundaries and how a request moves through the system.

## System Diagram

```mermaid
flowchart TB
    subgraph DEVICE["🔒 Local Device: trusted zone"]
        direction TB
        subgraph HUD["client_hud · SwiftUI"]
            IN["Voice · Ctrl+Option+K · Screen Context"]
            VOICE["ElevenLabs Voice Feedback"]
            CARD["Tier 2 Elicitation Card"]
        end

        subgraph ROUTER["Local Privacy & Intent Router"]
            PII["PII Redactor"]
            MEM[("Local Memory<br/>SQLite")]
            INTENT["Intent Builder"]
        end

        subgraph FW["3-Tier Risk & Rollback Firewall"]
            POLICY["Policy Engine<br/>schemas/policy_tiers.json"]
            T0["Tier 0 · Silent Read"]
            T1["Tier 1 · Reversible Write"]
            T2["Tier 2 · Circuit Breaker"]
            LEDGER[("Undo / Audit Ledger<br/>SQLite WAL")]
        end

        GW["FastMCP Gateway"]
        OTEL["OpenTelemetry<br/>redacted spans"]

        subgraph SERVERS["Modular MCP Servers"]
            OS["kiko-os-mcp"]
            COMMS["kiko-comms-mcp"]
            LIFE["kiko-life-mcp"]
            MOB["kiko-mobility-mcp"]
            WEB["kiko-web-mcp"]
        end
    end

    subgraph CLOUD["☁️ Cloud: untrusted zone, intent only"]
        LLM["Reasoning Model"]
        subgraph AWS["AWS Serverless Relay"]
            EB["EventBridge<br/>schedules and triggers"]
            LAMBDA["Lambda<br/>async workers"]
        end
    end

    IN --> PII
    PII --> INTENT
    MEM <--> INTENT
    INTENT -- "redacted intent" --> LLM
    LLM -- "tool plan" --> POLICY

    POLICY --> T0
    POLICY --> T1
    POLICY --> T2
    T1 -- "record inverse first" --> LEDGER
    T2 -- "pause" --> CARD
    T2 -- "audit decision" --> LEDGER
    CARD -- "explicit approve" --> GW
    T0 --> GW
    T1 --> GW

    GW --> OS & COMMS & LIFE & MOB & WEB
    GW <-->|"redacted async jobs"| EB
    EB --> LAMBDA
    LAMBDA -- "callback" --> GW

    GW -. spans .-> OTEL
    GW -- "result" --> VOICE
    LEDGER -. "Agentic Ctrl+Z" .-> GW
```

## Trust Boundary

There is exactly **one** path out of the device: the redacted intent that leaves the Privacy Router, plus the redacted async jobs sent to the AWS relay.

| Stays on-device | Crosses the boundary |
|---|---|
| Files, messages, contacts, screen content | Redacted intent (for example *"draft a reply to [CONTACT_1] about [EVENT_1]"*) |
| Local memory, the PII token map | Tool names and plans |
| Undo Ledger, tool results | Redacted async job payloads |

PII placeholders are re-hydrated **locally** in the gateway just before a tool runs. Cloud components never see the real values.

## Components

### 1. Client HUD (`client_hud/`)
A native macOS SwiftUI floating panel. It captures voice, the global hotkey and screen context, speaks results through ElevenLabs, and shows **MCP Elicitation cards** for Tier 2 approvals. It is the only place a Tier 2 approval can come from.

### 2. Local Privacy & Intent Router (`kernel/`)
- **PII Redactor:** swaps names, numbers, addresses and identifiers for stable placeholders and keeps the mapping on-device.
- **Local Memory:** SQLite-backed preferences and context, used to fill in intent without a cloud round-trip.
- **Intent Builder:** produces the smallest redacted request needed for reasoning.

### 3. 3-Tier Risk & Rollback Firewall (`kernel/`)
A deterministic policy engine that reads its rules from `schemas/policy_tiers.json`. The tier is looked up from that file and is never inferred by a model.

| Tier | Side effects | Gate | Ledger | Guarantee |
|---|---|---|---|---|
| **0: Silent Read** | none | none | not recorded | No side effects, so it can run autonomously |
| **1: Reversible Write** | local | the `undo` ledger entry must be written first | `undo` entry | Every action has a recorded inverse (`restore_snapshot` or `delete_created`) that **Agentic Ctrl+Z** replays, newest first |
| **2: Circuit Breaker** | local or external | explicit approval via MCP Elicitation | `audit` entry | Nothing irreversible runs without a human decision |

**Invariants** (pinned as `const` in `schemas/policy_tiers.schema.json`)
- **Fail closed:** tools that aren't listed are treated as Tier 2.
- **Ledger first:** a Tier 1 action that cannot record its inverse does not run.
- **External means Tier 2:** any tool with external side effects is Tier 2.
- **No bypass:** no flag, prompt or config can skip the Tier 2 confirmation. Only the local HUD (`client_hud`) can approve, never a cloud component or an AWS trigger.
- **Bound approvals:** an approval covers the `arguments_sha256` it was shown. The gateway re-hashes the arguments at execution time and refuses if they differ. Requests expire after `approval_ttl_seconds` (120 s).

### 4. FastMCP Gateway (`kernel/`)
The only component that talks to the MCP servers. It routes approved calls to the right server, re-hydrates PII locally, emits redacted OpenTelemetry spans, and dispatches jobs that should run later to the AWS relay.

### 5. AWS Serverless Relay (`kernel/`)
Handles async and time-based triggers, such as *"remind me when it's time to leave"* or *"follow up if no reply by 5 pm"*. **EventBridge** schedules the job, **Lambda** runs it, and the result comes back to the gateway as a callback. Payloads are redacted intent only. Any action a trigger leads to goes back through the firewall when it returns.

### 6. Modular MCP Servers (`mcp_servers/`)
Each server is self-contained and owns one domain. Tools follow the `<domain>.<verb_object>` naming scheme. Servers never call each other directly; everything goes through the gateway.

| Server | Domain | Tier 0 | Tier 1 | Tier 2 |
|---|---|---|---|---|
| `kiko-os-mcp` | Files, windows | `read_active_window`, `semantic_file_search` | `organize_files` | `delete_file` |
| `kiko-comms-mcp` | Messaging | — | `create_draft` | `send_message` |
| `kiko-life-mcp` | Calendar | `get_calendar_events` | `create_calendar_block` | — |
| `kiko-mobility-mcp` | Commute | `estimate_eta` | — | `stage_ride_deeplink` |
| `kiko-web-mcp` | Headless web | — | — | `submit_external_form` |

## Request Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant HUD as Client HUD
    participant R as Privacy Router
    participant LLM as Cloud Model
    participant FW as Firewall
    participant L as Undo Ledger
    participant GW as FastMCP Gateway
    participant S as MCP Server

    User->>HUD: "Tell Priya I'm running 10 min late"
    HUD->>R: utterance + context
    R->>R: redact → "message [CONTACT_1]: running late"
    R->>LLM: redacted intent
    LLM-->>FW: plan: comms.create_draft → comms.send_message
    FW->>L: Tier 1: record inverse of create_draft
    FW->>GW: create_draft
    GW->>S: execute (PII re-hydrated locally)
    FW->>HUD: Tier 2: Elicitation card for send_message
    User->>HUD: Approve
    HUD->>GW: approved send_message
    GW->>S: execute
    GW-->>HUD: result → voice confirmation
```

## Source of Truth

| Concern | Location |
|---|---|
| Tool → tier, undo strategy, privacy rules, policy version | `schemas/policy_tiers.json` |
| Risk classification contract and tier invariants | `schemas/policy_tiers.schema.json` |
| Undo / audit ledger row contract | `schemas/ledger_entry.schema.json` |
| Tier 2 approval request / response contract | `schemas/elicitation.schema.json` |
| Reference payloads | `schemas/examples/` |
| Consistency check (policy ↔ schemas ↔ docs) | `python3 schemas/check_consistency.py` |
| Engineering conventions | `CLAUDE.md` |
