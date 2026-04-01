# Agent Interaction Evaluator — SPEC.md

> **Purpose:** Structured observability for multi-agent ecosystems. Reduce errors, surface assumption drift, produce auditable decision trails. Built on the principles in `agentic-workflow-philosophy.md`.
>
> **Status:** Draft — implementation in progress (Phase 1).

---

## 1. Overview

### What It Is

The Agent Interaction Evaluator (AIE) is a passive observability and evaluation framework that:

1. **Logs** structured interaction events emitted by agents and human-agent interactions
2. **Indexes** those events semantically using txtai/FAISS (shared with RepoTransmute)
3. **Evaluates** events against user-defined oracles — codified definitions of "correct" behaviour
4. **Detects** assumption drift by cross-referencing current statements against indexed history
5. **Produces** audit trails — decision-level provenance for consequential actions
6. **Alerts** via Circuit Breaker gates when drift or failure conditions are met

### What It Is Not

- Not an agent runtime modification — AIE observes, it does not execute
- Not a replacement for promptfoo — AIE tests *interaction quality*, not output quality
- Not a process enforcement tool — AIE reports and alerts, humans decide and act

### Design Principles

From `agentic-workflow-philosophy.md`:
- **Context fidelity is the moat** — AIE measures what survives across delegation chains
- **Evaluation precedes deployment** — oracles are defined before agents operate
- **Reliability is about honesty** — AIE surfaces what it doesn't know, not just what it finds
- **Human-agent symmetry** — humans and agents both emit events and read audit trails

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Agent Ecosystem                           │
│  codi · reviewer · g3 · journal · humans (via bot)          │
└─────────────────────────┬────────────────────────────────────┘
                          │ Structured events (JSONL over IPC)
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                 Agent Interaction Logger (AIL)               │
│  - Event validator (JSON schema)                             │
│  - Event router (persist + forward)                           │
│  - Backpressure handling                                      │
└─────────────────────────┬────────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
┌─────────────────────────┐   ┌──────────────────────────────┐
│   Local JSONL logfile   │   │   txtai/FAISS Index          │
│   (raw archive)         │   │   collection: agent_events   │
│                         │   │   (semantic search + filter)  │
└─────────────────────────┘   └──────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────┐
│                    Oracle Engine                              │
│  - YAML oracle registry (loaded at startup)                   │
│  - Per-event-type evaluation rules                           │
│  - Scoring + deviation flags                                 │
└─────────────────────────┬────────────────────────────────────┘
                          │
              ┌───────────┼───────────┬──────────────┐
              ▼           ▼           ▼              ▼
         Audit Trail   Drift Flag  Alert/Halt    ClawFlow
         (JSONL/HTML)  (flagged    (Circuit       Orchestration
                        event)       Breaker gate)  (aie_heartbeat)
```

### Shared Infrastructure with RepoTransmute

AIE uses the **same txtai/FAISS instance** as RepoTransmute:
- **RepoTransmute** indexes code blueprints — collection: `blueprints`
- **AIE** indexes agent interactions — collection: `agent_events`
- Both share `~/workspace/zoul/repo-transmute/data/txtai/` (FAISS index files)
- AIE has its own SQLite sidecar for interaction metadata (see §6)

This is intentional: it enables cross-search — e.g., "find interactions where an agent referenced code chunk X" or "show me all assumption corrections near Blueprint Y".

---

## 3. Event Schema

All events are JSON. Every event has a **base schema**; each **event type** extends it.

### 3.1 Base Schema

```json
{
  "schema_version": "1.0",
  "event_id": "uuid-v4",
  "event_type": "string",
  "timestamp": "ISO-8601",
  "agent_id": "string",
  "session_id": "string",
  "interaction_context": {
    "channel": "string",
    "workspace_path": "string|null",
    "parent_event_id": "string|null"
  }
}
```

### 3.2 Event Types

#### `delegation`
Emitted when one agent (or human) delegates a task to another.

```json
{
  "event_type": "delegation",
  "delegator": {
    "agent_id": "string",
    "role": "string"
  },
  "delegate": {
    "agent_id": "string",
    "role": "string"
  },
  "task": {
    "task_id": "uuid",
    "description": "string",
    "intent": "string",
    "constraints": ["string"],
    "context_summary": "string",
    "context_fidelity": 0.0,
    "max_turns": "integer|null",
    "deadline": "ISO-8601|null"
  },
  "oracle_ref": "string|null"
}
```

#### `tool_call`
Emitted when an agent invokes a tool.

```json
{
  "event_type": "tool_call",
  "tool": {
    "name": "string",
    "namespace": "string",
    "arguments": "object",
    "argument_schema": "string|null"
  },
  "trigger": {
    "type": "string",
    "triggered_by_event_id": "string|null"
  },
  "outcome": {
    "status": "success|error|partial|unknown",
    "duration_ms": "integer",
    "error_message": "string|null",
    "output_summary": "string|null"
  }
}
```

#### `assumption`
Emitted when an agent states an assumption (explicit belief about state).

```json
{
  "event_type": "assumption",
  "assumption": {
    "statement": "string",
    "category": "string",
    "confidence": 0.0,
    "grounded_in": "string|null"
  },
  "derived_from": ["event_id"],
  "oracle_ref": "string|null"
}
```

#### `correction`
Emitted when an agent revises a prior assumption or action.

```json
{
  "event_type": "correction",
  "prior_event_id": "event_id",
  "correction": {
    "reason": "string",
    "prior_statement": "string",
    "revised_statement": "string",
    "severity": "minor|moderate|critical"
  },
  "downstream_impact": {
    "events_affected": ["event_id"],
    "reversible": "boolean"
  }
}
```

#### `drift_detected`
Emitted internally when the drift detector flags a contradiction.

```json
{
  "event_type": "drift_detected",
  "current_assumption": {
    "event_id": "event_id",
    "statement": "string"
  },
  "contradicted_by": {
    "event_id": "event_id",
    "statement": "string"
  },
  "contradiction_type": "direct|semantic|implicit",
  "drift_score": 0.0,
  "action_taken": "flagged|halted|alerted"
}
```

#### `circuit_breaker`
Emitted when a Circuit Breaker gate halts an action.

```json
{
  "event_type": "circuit_breaker",
  "gate": {
    "name": "string",
    "threshold": "string",
    "assumptions_violated": ["event_id"]
  },
  "action_blocked": "string|null",
  "halt_session": "boolean",
  "alert_sent": "boolean",
  "audit_ref": "string"
}
```

#### `human_input`
Emitted when a human provides input that affects agent behaviour.

```json
{
  "event_type": "human_input",
  "human": {
    "id": "string",
    "role": "string"
  },
  "input": {
    "type": "string",
    "content": "string",
    "context_summary": "string"
  },
  "impact": {
    "events_affected": ["event_id"],
    "session_modified": "boolean"
  }
}
```

---

## 4. Oracle Definition Format

Oracles are YAML files. Each oracle defines what "correct" looks like for a given scenario.

### 4.1 Directory Structure

```
evaluator/oracles/
  ├── _registry.yaml
  ├── delegation/
  │   ├── no_empty_context.yaml
  │   └── context_fidelity_threshold.yaml
  ├── assumption/
  │   ├── no_confidence_zero.yaml
  │   └── groundedness_required.yaml
  ├── tool_call/
  │   ├── no_secret_exposure.yaml
  │   ├── schema_compliance.yaml
  │   └── error_recovery_rate.yaml
  └── circuit_breaker/
      └── halt_on_critical_drift.yaml
```

### 4.2 Oracle Schema

```yaml
oracle_id: "no_empty_context"
name: "Delegation must include context summary"
description: |
  When an agent delegates a task, the context_summary field
  must not be empty. Empty context is a leading indicator of
  context bankruptcy (Philosophy §V).
event_type: "delegation"
trigger: "on_event"
severity: "critical"
conditions:
  - type: "field_required"
    field: "task.context_summary"
    value: { "not_empty": true }
  - type: "field_min_length"
    field: "task.context_summary"
    value: { "min_length": 20 }
actions:
  - type: "flag"
    output: "drift_event"
  - type: "alert"
    output: "discord"
    channel: "evaluator-alerts"
  - type: "halt"
    condition: "context_fidelity < 0.3"
metadata:
  author: "sean"
  created: "2026-04-01"
  tags: ["context", "delegation", "reliability"]
  philosophy_ref: "Pillar-3"
```

### 4.3 Oracle Evaluation Engine

The engine:
1. Loads all oracles from `evaluator/oracles/` at startup
2. On each incoming event, finds matching oracles (by `event_type`)
3. Evaluates all `conditions` against the event
4. If any condition fails and `severity` is `critical` → emit `circuit_breaker` event
5. If any condition fails → emit `drift_detected` or `flag` event
6. Runs `actions` — flag, alert, halt, audit
7. Supports **semantic contradiction checks** using txtai similarity on `assumption.statement` vs all prior assumptions in the index

---

## 5. Core Components

### 5.1 `ailogger` — Agent Interaction Logger

```
ailogger [command]

Commands:
  serve        Run the logger as a local IPC server (Unix socket)
  emit         Emit a single event (for testing / human input)
  replay       Replay events from a JSONL log file
  status       Show logger health and event counts
```

**IPC Protocol:** JSON-RPC 2.0 over Unix socket `/tmp/ailogger.sock`

**Backpressure:** If txtai indexing lags, events are queued to local JSONL buffer and replayed on recovery. No events are dropped.

**Security:** No secrets in event arguments. The logger sanitises fields matching `PASSWORD`, `SECRET`, `TOKEN`, `KEY`, `API_KEY` before persisting.

### 5.2 `aidrift` — Drift Detector

```
aidrift [command]

Commands:
  check <event_id>    Check a specific assumption event for drift
  scan [--session S]  Scan all sessions for drift since last check
  report              Generate drift summary report
  stats               Show drift statistics
```

**Drift Algorithm:**
1. On each `assumption` event, embed `assumption.statement` using the txtai model
2. Query the `agent_events` collection for prior assumptions with cosine similarity > 0.85
3. If a prior assumption contradicts the current one, flag as `drift_detected`
4. `contradiction_type`:
   - `direct` — similar text, different conclusion (similarity ≥ 0.95)
   - `semantic` — different text, same meaning (similarity 0.85–0.95)
   - `implicit` — logically incompatible (NLI model, v2)

### 5.3 `aieval` — Oracle Evaluator

```
aieval [command]

Commands:
  evaluate <event_file>   Evaluate a single event against all applicable oracles
  evaluate --stdin         Evaluate events from JSON lines on stdin
  oracle list              List all loaded oracles
  oracle validate          Validate oracle YAML syntax
  oracle run [--oracle ID] Run specific oracle against recent events
  report [--since YYYY-MM-DD]  Generate evaluation report
```

**Evaluation modes:**
- `on_event` — synchronous, evaluated as events arrive (for critical oracles)
- `on_demand` — triggered via CLI or API
- `on_cron` — scheduled batch evaluation (see §7)

### 5.4 `aiaudit` — Audit Trail Exporter

```
aiaudit [command]

Commands:
  trail <session_id>       Generate full audit trail for a session
  trail --event-id <id>    Show trail leading up to a specific event
  export [--format html|json|md]   Export trail(s)
  diff <session_a> <session_b>  Compare two session trails
  prune --before <date>    Archive and delete old trails
```

---

## 6. Data Storage

### 6.1 txtai Collections

| Collection | Purpose |
|---|---|
| `blueprints` | RepoTransmute code blueprints |
| `agent_events` | All AIE interaction events |

**`agent_events` index fields:**
```
event_id (primary key), event_type, agent_id, session_id, timestamp,
assumption_statement (full-text search for drift), task_description,
oracles_triggered, severity, drift_score
```

### 6.2 SQLite Sidecar

AIE maintains a SQLite database at `evaluator/data/aie_meta.db`:

```sql
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  agent_id TEXT,
  channel TEXT,
  started_at TEXT,
  ended_at TEXT,
  event_count INTEGER,
  drift_count INTEGER,
  circuit_breaker_halts INTEGER
);

CREATE TABLE oracle_results (
  result_id TEXT PRIMARY KEY,
  event_id TEXT,
  oracle_id TEXT,
  passed BOOLEAN,
  deviation TEXT,
  evaluated_at TEXT
);

CREATE TABLE drift_log (
  drift_id TEXT PRIMARY KEY,
  current_event_id TEXT,
  contradicted_event_id TEXT,
  contradiction_type TEXT,
  drift_score REAL,
  action_taken TEXT,
  resolved_at TEXT
);
```

---

## 7. Autonomous Orchestration — ClawFlow

AIE uses **ClawFlow** for autonomous operation — not raw crontab. This follows the same methodology as RepoTransmute's autonomous task cascade.

### 7.1 Why ClawFlow

| | Raw cron | ClawFlow |
|---|---|---|
| State between runs | None (stateless) | Persists — flow tracks what was done |
| Failure handling | Exit code only | Flow state → resume from failure point |
| Inspection | Logs only | `openclaw flow status aie_heartbeat` |
| Multi-step coordination | Chain in shell | Native step→step with wait states |
| Human intervention | Kill cron, fix, restart | `openclaw flow steer aie_heartbeat` |

### 7.2 The `aie_heartbeat` Flow

```
Flow: "aie_heartbeat"
Owner: zoul main session

Step 1: aidrift scan --all-sessions
  → If critical drift found → emit alert → halt → notify
  → If minor drift → flag → continue

Step 2: aieval oracle batch --since 24h
  → If critical oracle failure → circuit_breaker → alert
  → If warnings → log

Step 3: aiaudit trail --sessions-with-drift
  → Write audit trail to data/audit_trails/

Step 4: health_check
  → ailogger alive? txtai reachable? backpressure clear?

Set flow waiting (30 min)
Resume → repeat
```

### 7.3 Cron Trigger (minimal)

A lightweight cron entry wakes the ClawFlow if it is idle:

```bash
# Entry point — lightweight, just wakes the flow
0 * * * * openclaw flow trigger aie_heartbeat --if-idle
```

The actual work happens inside the Flow — it persists across cron invocations, tracks state, and can be inspected/resumed.

### 7.4 Alert Routing

| Severity | Action |
|---|---|
| `critical` | Discord alert to `#evaluator-alerts`, halt session |
| `warning` | Discord alert, logged to drift_log |
| `info` | Logged only |

### 7.5 Scheduled Jobs

| Job | Schedule (AEST) | What it does |
|---|---|---|
| `drift_scan` | Every 6 hours (via ClawFlow) | Scan all active sessions for unreported drift |
| `oracle_batch` | Every 24h 02:00 (via ClawFlow) | Run all `on_cron` oracles against recent events |
| `audit_prune` | Weekly Sunday 03:00 (via ClawFlow) | Archive trails > 30 days |
| `health_check` | Every 30 min (via ClawFlow) | Verify logger alive, txtai reachable |

---

## 8. Agent Integration

### 8.1 How Agents Emit Events

Agents do **not** need to be modified directly. AIE provides two integration paths:

**Option A — IPC (preferred):**
```python
from evaluator.logger_client import AILoggerClient
client = AILoggerClient(socket_path="/tmp/ailogger.sock")
client.emit(event_dict)
```

**Option B — File drop:**
Agents write JSONL to `evaluator/data/inbox/`. The logger watches the directory and ingests.

### 8.2 OpenClaw Agent Integration

OpenClaw agents emit events via a thin wrapper. A logger subprocess runs alongside the agent ecosystem.

**Mapping OpenClaw session events → AIE events:**
- `delegation` = spawning a subagent or handing off to another agent
- `tool_call` = any tool invocation (read, write, exec, sessions_spawn)
- `assumption` = explicit "Assuming..." statements in agent reasoning
- `correction` = "Wait, actually...", "Correction:..." in agent output
- `human_input` = heartbeat responses, approvals, corrections from humans

### 8.3 Priority Integration Order

| Agent | Priority | Reason |
|---|---|---|
| `codi` | 1 | Most active, regular delegation events |
| `reviewer` | 2 | Assumption-heavy, clear oracle opportunities |
| `g3` | 3 | Rust agent with uncommitted changes, high complexity |
| `journal` | 4 | Regular structured output, good test case |

---

## 9. API

### 9.1 Logger API (IPC)

```json
// JSON-RPC 2.0 over Unix socket

// emit
{"jsonrpc": "2.0", "method": "emit", "params": {"event": {...}}, "id": 1}

// emit_batch
{"jsonrpc": "2.0", "method": "emit_batch", "params": {"events": [...]}, "id": 2}

// status
{"jsonrpc": "2.0", "method": "status", "params": {}, "id": 3}
→ {"jsonrpc": "2.0", "result": {"events_received": N, "buffered": M, "logger_uptime": S}, "id": 3}
```

### 9.2 CLI API

All CLI commands listed in §5.

### 9.3 REST API (future)

```
GET  /events?session_id=&agent_id=&event_type=&since=&limit=
GET  /events/<event_id>
GET  /sessions
GET  /sessions/<session_id>/audit
GET  /drift?since=&severity=
GET  /oracles
POST /oracles/validate
GET  /report?since=&format=html
```

---

## 10. Directory Structure

```
agent-interaction-evaluator/
├── SPEC.md
├── README.md
├── REQUIREMENTS.md
├── pyproject.toml
├── src/
│   └── evaluator/
│       ├── __init__.py
│       ├── logger.py             # ailogger implementation
│       ├── logger_client.py      # thin client for agents
│       ├── drift.py              # aidrift implementation
│       ├── evaluator.py          # aieval implementation
│       ├── audit.py              # aiaudit implementation
│       ├── oracle_engine.py      # oracle loading + evaluation
│       ├── schema.py              # event schema + validation
│       ├── txtai_client.py        # shared txtai/FAISS client
│       ├── db.py                  # SQLite sidecar
│       ├── sanitiser.py           # removes secrets from events
│       └── alerts.py              # Discord alert integration
├── oracles/                      # oracle definitions (YAML)
│   ├── _registry.yaml
│   ├── delegation/
│   ├── assumption/
│   ├── tool_call/
│   └── circuit_breaker/
├── data/
│   ├── aie_meta.db
│   ├── logs/                    # raw JSONL event logs
│   ├── audit_trails/            # generated audit trails
│   └── inbox/                   # drop directory for agents
├── tests/
│   ├── test_schema.py
│   ├── test_oracle_engine.py
│   ├── test_drift.py
│   ├── test_logger.py
│   ├── test_txtai_client.py
│   ├── test_audit.py
│   └── fixtures/
└── scripts/
    └── cron_setup.sh           # installs crontab (minimal trigger only)
```

---

## 11. Dependencies

```
# Core
python>=3.11
txtai>=6.0.0
faiss-cpu
jsonschema
pyyaml
aiosqlite

# Optional (implicit drift detection, v2)
# cross-encoder/nli-deberta-v3-small

# Testing
pytest>=8.0.0
pytest-asyncio

# Infrastructure
openclaw (for ClawFlow)
```

---

## 12. Testing Strategy

### 12.1 Unit Tests

| Test file | What it covers |
|---|---|
| `test_schema.py` | All event types validate correctly; invalid events rejected |
| `test_sanitiser.py` | Secrets stripped, allowed fields preserved |
| `test_oracle_engine.py` | Each condition type evaluates correctly |
| `test_drift.py` | Direct and semantic drift detection |

### 12.2 Integration Tests

| Test file | What it covers |
|---|---|
| `test_logger.py` | Logger IPC, buffering, backpressure, replay |
| `test_txtai_client.py` | Round-trip: event → index → query |
| `test_audit.py` | Session trail generation, diff |

### 12.3 Oracle Validation Tests

Each oracle has a corresponding YAML test fixture:
```json
// oracles/delegation/no_empty_context.fixture.json
{
  "oracle_id": "no_empty_context",
  "fixtures": [
    { "event": { "task": { "context_summary": "" } }, "expect": "fail" },
    { "event": { "task": { "context_summary": "Because the file was missing" } }, "expect": "pass" }
  ]
}
```

### 12.4 Golden Session Tests

Real agent session logs (anononymised) stored as fixtures. Run full pipeline on them, assert:
- No unexpected drift flags
- Correct oracle pass/fail counts
- Audit trail completeness

---

## 13. Development Phases

### Phase 1 — Foundation
- [ ] Project scaffold (`pyproject.toml`, dir structure)
- [ ] Event schema (`schema.py`) with full JSON schema
- [ ] Logger IPC server + client (`logger.py`, `logger_client.py`)
- [ ] Secret sanitiser (`sanitiser.py`)
- [ ] SQLite sidecar (`db.py`)
- [ ] 3 basic oracles (one per event type)
- [ ] Unit tests (schema, sanitiser, oracles)

**Deliverable:** `ailogger serve` + `ailogger emit` functional

### Phase 2 — Indexing + Drift
- [ ] txtai client (`txtai_client.py`) — shared with RepoTransmute
- [ ] Index events into `agent_events` collection
- [ ] `aidrift check` — direct drift detection
- [ ] `aidrift scan` — session-wide drift scan
- [ ] txtai integration tests

**Deliverable:** `aidrift scan` functional against real events

### Phase 3 — Oracle Engine + Evaluation
- [ ] Oracle registry loader (`oracle_engine.py`)
- [ ] All condition types implemented
- [ ] `aieval` CLI with list/validate/evaluate
- [ ] 5 oracles across 3 event types
- [ ] Oracle validation tests

**Deliverable:** `aieval oracle run` functional

### Phase 4 — Audit Trails
- [ ] `audit.py` — trail generation
- [ ] `aiaudit trail` CLI
- [ ] Export formats (JSON, Markdown)
- [ ] Session diff
- [ ] Audit trail tests

**Deliverable:** `aiaudit trail <session_id>` produces complete trail

### Phase 5 — ClawFlow Orchestration + Alerts
- [ ] ClawFlow `aie_heartbeat` flow definition
- [ ] `cron_setup.sh` — minimal cron trigger (`openclaw flow trigger aie_heartbeat --if-idle`)
- [ ] Discord alert integration (`alerts.py`)
- [ ] Health check step in flow
- [ ] Alert routing by severity

**Deliverable:** `openclaw flow trigger aie_heartbeat` runs full evaluation cycle

### Phase 6 — Agent Integration
- [ ] codi integration (priority 1)
- [ ] reviewer integration (priority 2)
- [ ] g3 integration (priority 3)
- [ ] OpenClaw session → AIE event mapping
- [ ] Real session golden tests

**Deliverable:** Live events from real agents flowing into AIE

### Phase 7 — Advanced Drift (v2)
- [ ] Semantic drift detection (cross-encoder NLI)
- [ ] `implicit` contradiction type
- [ ] Context fidelity scoring
- [ ] Cascade impact analysis

---

## 14. Open Questions

| # | Question | Decision needed from |
|---|---|---|
| 1 | Which channel for alerts — `#evaluator-alerts` or DMs? | Sean |
| 2 | Do we instrument `g3` despite uncommitted changes? | Sean |
| 3 | Semantic drift (NLI model) — v1 or v2? | Alto/Sean |
| 4 | Should AIE logs be git-ignored or tracked? | Alto |

**Resolved:**
- Own repo ✅ (repo created: `ChonSong/agent-interaction-evaluator`)
- Autonomous methodology ✅ (ClawFlow, not raw cron)

---

## 15. References

- `agentic-workflow-philosophy.md` — founding document
- `repo-transmute/` — existing txtai/FAISS infrastructure
- `TOOLS.md` §txtai — current txtai architecture
- `clawflow/SKILL.md` — ClawFlow runtime substrate
- `repo-transmute/adr-001-automatic-task-cascade.md` — autonomous methodology reference
- Philosophy §V — the five failure modes AIE addresses
- Philosophy §VI — the Circuit Breaker critique of paperclip
