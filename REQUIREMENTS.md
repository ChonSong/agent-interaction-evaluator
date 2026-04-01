# Agent Interaction Evaluator — REQUIREMENTS.md

> **Purpose:** Development checklist derived from SPEC.md. Each item is concrete, testable, and independently actionable so development can proceed without back-and-forth.
>
> **How to use:** Developer reads a phase, completes all items, runs the corresponding tests, and moves to the next phase. Open questions must be resolved before beginning a phase.

---

## Conventions

- All Python code: `src/evaluator/`
- All oracles: `oracles/[event_type]/[oracle_id].yaml`
- All tests: `tests/test_[component].py`
- All CLI commands: `ailogger`, `aidrift`, `aieval`, `aiaudit`
- All event JSON uses the schema in `SPEC.md §3`
- No external services. Runs on existing workspace infra.
- Autonomous work uses **ClawFlow** (not raw crontab) — see SPEC.md §7
- Breaking changes to schema must update `SPEC.md` simultaneously.

---

## Phase 1 — Foundation

### P1.1 Project Scaffold

- [ ] `pyproject.toml` created with:
  - Name: `agent-interaction-evaluator`
  - Python `>=3.11`
  - Dependencies: `txtai>=6.0.0`, `faiss-cpu`, `jsonschema`, `pyyaml`, `aiosqlite`
  - Dev dependencies: `pytest>=8.0.0`, `pytest-asyncio`
  - Entry points: `ailogger`, `aidrift`, `aieval`, `aiaudit` console scripts
  - `src/evaluator/` as package
- [ ] `src/evaluator/__init__.py` exists (package marker, `__version__ = "0.1.0"`)
- [ ] `evaluator/data/`, `evaluator/data/logs/`, `evaluator/data/audit_trails/`, `evaluator/data/inbox/` created
- [ ] `evaluator/tests/` created with `__init__.py`
- [ ] `.gitignore` created (ignores `data/`, `*.pyc`, `__pycache__/`, `.pytest_cache/`)

### P1.2 Event Schema

- [ ] `src/evaluator/schema.py` implements:
  - `BASE_EVENT_FIELDS` — all fields from SPEC.md §3.1
  - `EVENT_SCHEMAS` — dict of event_type → dict schema (delegation, tool_call, assumption, correction, drift_detected, circuit_breaker, human_input)
  - `validate_event(event: dict) -> tuple[bool, str | None]` — returns (valid, error_message)
  - `get_schema(event_type: str) -> dict` — returns the schema for an event type
  - `generate_event_id() -> str` — UUID v4
  - `get_current_timestamp() -> str` — ISO-8601
- [ ] `tests/test_schema.py`:
  - Valid event for each type passes validation
  - Invalid event (missing required field, wrong type) fails with specific error
  - Unknown event_type fails gracefully
  - `generate_event_id()` returns valid UUID v4
  - `get_current_timestamp()` returns valid ISO-8601

### P1.3 Secret Sanitiser

- [ ] `src/evaluator/sanitiser.py` implements:
  - `SANITISE_FIELDS = ["PASSWORD", "SECRET", "TOKEN", "KEY", "API_KEY", "AUTHORIZATION", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_TOKEN"]`
  - `sanitise_event(event: dict) -> dict` — replaces matching top-level and nested keys with `"[REDACTED]"`, does NOT remove keys
  - Recursive — handles nested dicts and lists
  - Case-insensitive field matching (e.g., `api_key` matches `API_KEY`)
- [ ] `tests/test_sanitiser.py`:
  - Event with secrets in top-level field → value replaced, key preserved
  - Event with secrets in nested dict → all levels sanitised
  - Event with secrets in list of dicts → all items sanitised
  - Event with no secrets → unchanged
  - Case-insensitive matching: `api_key` and `Api_Key` both sanitised

### P1.4 SQLite Sidecar

- [ ] `src/evaluator/db.py` implements:
  - `init_db(db_path: str)` — creates tables if not exist
  - `insert_session(session: dict)` — upsert into sessions table
  - `insert_oracle_result(result: dict)` — insert into oracle_results
  - `insert_drift_log(drift: dict)` — insert into drift_log
  - `get_session(session_id: str) -> dict | None`
  - `get_open_drifts() -> list[dict]` — unresolved drifts only
  - `resolve_drift(drift_id: str)` — set resolved_at
  - All methods use aiosqlite, are async
  - `db_path` configurable via env `AIE_DB_PATH`
- [ ] `tests/test_db.py`:
  - `init_db` creates tables
  - insert + retrieve session roundtrips correctly
  - insert + retrieve oracle result roundtrips correctly
  - `get_open_drifts` returns only unresolved drifts
  - `resolve_drift` sets resolved_at

### P1.5 Logger IPC Server

- [ ] `src/evaluator/logger.py` implements `AILogger` class:
  - Binds to Unix socket `/tmp/ailogger.sock` (configurable via env `AILOGGER_SOCKET`)
  - JSON-RPC 2.0 protocol
  - Methods: `emit(event: dict)`, `emit_batch(events: list[dict])`, `status() -> dict`
  - On `emit`: validate → sanitise → write to JSONL log file (`data/logs/YYYY-MM-DD.jsonl`) → write to SQLite session → forward to txtai index (async, non-blocking)
    → if txtai unavailable, buffer in memory; do NOT drop event
  - On `emit_batch`: iterate and call emit
  - On `status`: return `{"events_received": N, "buffered": M, "logger_uptime_seconds": S}`
  - On invalid JSON-RPC → return error response per JSON-RPC spec
- [ ] `ailogger serve` CLI — starts the server, logs PID to `data/ailogger.pid`
- [ ] `ailogger status` CLI — connects to socket and prints status as JSON
- [ ] `ailogger emit --stdin` CLI — reads one JSON event from stdin and emits it
- [ ] `tests/test_logger.py`:
  - Mock socket, send valid event → received and persisted
  - Send invalid event → error returned, not persisted
  - Backpressure: txtai down → event buffered, recovered on txtai up
  - Status returns correct counts

### P1.6 Logger Client

- [ ] `src/evaluator/logger_client.py` implements `AILoggerClient`:
  - Connects to `/tmp/ailogger.sock` (configurable)
  - `emit(event: dict) -> bool` — returns True on success, raises on error
  - `emit_batch(events: list[dict]) -> bool`
  - `status() -> dict`
  - Context manager (`async with AILoggerClient() as client:`)
  - `close()`
- [ ] `tests/test_logger_client.py` — integration test against real logger socket (skip if socket unavailable)

### P1.7 Basic Oracles (3 minimum)

Create one oracle per event type:

- [ ] `oracles/delegation/no_empty_context.yaml`
  - Trigger: `on_event`
  - Condition: `task.context_summary` not empty, min 20 chars
  - Severity: `critical`
  - Action: `flag`
- [ ] `oracles/assumption/no_confidence_zero.yaml`
  - Trigger: `on_event`
  - Condition: `assumption.confidence > 0.0`
  - Severity: `warning`
  - Action: `flag`
- [ ] `oracles/tool_call/no_secret_exposure.yaml`
  - Trigger: `on_event`
  - Condition: `tool.arguments` sanitises clean (re-run sanitiser on emitted event, compare)
  - Severity: `critical`
  - Action: `halt`
  - Note: Tests that the logger's own sanitiser works correctly

### P1.8 Phase 1 Tests Pass

```bash
pytest tests/test_schema.py tests/test_sanitiser.py tests/test_db.py -x -q
```

All must pass. No skips. No xfails.

---

## Phase 2 — Indexing + Drift

### P2.1 txtai Client

- [ ] `src/evaluator/txtai_client.py` implements:
  - `TXTaiClient` — connects to shared RepoTransmute txtai instance at `~/workspace/zoul/repo-transmute/data/txtai/`
  - `index_event(event: dict)` — embeds relevant text fields, indexes into `agent_events` collection
    - Embeds: `event_id`, `event_type`, `agent_id`, `session_id`, `timestamp`, `assumption_statement`, `task_description`
    - Uses `sentence-transformers/all-MiniLM-L6-v2` embedding (same as RepoTransmute)
    - Configurable via env `TXTai_INDEX_PATH`
  - `query_assumptions(text: str, session_id: str | None = None, top_k: int = 10) -> list[dict]`
    - Returns prior assumptions with similarity scores
  - `query_events(filters: dict, top_k: int = 50) -> list[dict]`
  - `get_index_stats() -> dict` — collection size, last updated
  - `ensure_collection()` — creates `agent_events` collection if not exists
  - Graceful fallback: if txtai unavailable, log warning and return empty results (do NOT crash)

### P2.2 Drift Detection — aidrift CLI

- [ ] `src/evaluator/drift.py` implements `DriftDetector`:
  - `check(event_id: str, event: dict) -> DriftResult | None`
    - Get `assumption.statement` from event
    - Query txtai for prior assumptions (session-scope or global)
    - For each prior assumption with similarity > 0.85:
      - Compare statements
      - If `similarity >= 0.95` → `direct` contradiction
      - If `0.85 <= similarity < 0.95` → `semantic` contradiction
    - Return `DriftResult` with `drift_score`, `contradiction_type`, `contradicted_by_event_id`
    - Return `None` if no drift found
  - `scan_session(session_id: str) -> list[DriftResult]`
  - `scan_all_active() -> list[DriftResult]`
  - `DriftResult` dataclass: `event_id, contradicted_event_id, contradiction_type, drift_score, current_statement, prior_statement`
- [ ] `aidrift check <event_id>` CLI
- [ ] `aidrift scan --session <session_id>` CLI
- [ ] `aidrift scan --all` CLI — scan all active sessions
- [ ] `aidrift report` CLI — prints summary of all open drifts as JSON
- [ ] `aidrift stats` CLI — prints drift statistics

### P2.3 txtai Integration Tests

- [ ] `tests/test_txtai_client.py`:
  - `test_index_and_query_roundtrip` — index a synthetic event, query it back
  - `test_query_by_session` — query returns only events from specified session
  - `test_get_index_stats` — returns non-zero collection size after indexing
  - `test_graceful_fallback` — when txtai is unavailable, returns empty without crashing

---

## Phase 3 — Oracle Engine + Evaluation

### P3.1 Oracle Registry

- [ ] `oracles/_registry.yaml` created listing all oracles
- [ ] `src/evaluator/oracle_engine.py` implements:
  - `OracleRegistry` class:
    - `load(path: str = "oracles/")` — walks directory, loads all YAML files, validates schema
    - `get_oracles_for_event_type(event_type: str) -> list[Oracle]`
    - `get_oracle(oracle_id: str) -> Oracle | None`
    - `list_oracles() -> list[Oracle]`
    - `validate_oracle(oracle_id: str) -> tuple[bool, str | None]`
  - `Oracle` dataclass: all fields from SPEC.md §4.2
  - `Condition` evaluation classes: one per condition type
    - `FieldRequired` — field exists and not None
    - `FieldMinLength` — field value has min length
    - `FieldRegex` — field matches regex pattern
    - `SimilarityThreshold` — txtai similarity against a reference statement
    - `DriftScoreThreshold` — drift score comparison
  - `evaluate_conditions(oracle: Oracle, event: dict) -> ConditionResult`
  - `ConditionResult` dataclass: `passed: bool, failed_conditions: list[str], deviation: str | None`

### P3.2 Oracle CLI — aieval

- [ ] `aieval evaluate <event_file>` — load event, evaluate against all applicable oracles, print results as JSON
- [ ] `aieval evaluate --stdin` — read JSON lines from stdin, evaluate each
- [ ] `aieval oracle list` — list all loaded oracles with IDs, event_type, severity, trigger
- [ ] `aieval oracle validate` — validate all oracle YAML files, print errors or "All valid"
- [ ] `aieval oracle run --oracle <id>` — run specific oracle against recent events (last 24h)
- [ ] `aieval report --since YYYY-MM-DD` — generate evaluation summary report as JSON

### P3.3 Full Oracle Set (5 oracles)

- [ ] `oracles/delegation/context_fidelity_threshold.yaml`
  - Condition: `task.context_fidelity >= 0.5`
  - Severity: `warning`
- [ ] `oracles/assumption/groundedness_required.yaml`
  - Condition: `assumption.grounded_in` is not null
  - Severity: `info`
- [ ] `oracles/tool_call/schema_compliance.yaml`
  - Condition: `tool.argument_schema` exists and arguments conform
  - Severity: `warning`
- [ ] `oracles/tool_call/error_recovery_rate.yaml` (session-level aggregate)
  - Trigger: `on_cron`
  - Aggregates last 24h of `tool_call` events per session
  - Flags if error rate > 20%
  - Severity: `warning`
- [ ] `oracles/circuit_breaker/halt_on_critical_drift.yaml`
  - Condition: `drift_score >= 0.9`
  - Severity: `critical`
  - Action: `halt`

### P3.4 Oracle Engine Tests

- [ ] `tests/test_oracle_engine.py`:
  - All condition types evaluate correctly
  - Oracle with failing condition → ConditionResult.passed = False
  - Oracle with all passing → ConditionResult.passed = True
  - Unknown oracle_id → graceful error
  - Duplicate oracle_id → raises ValueError
  - `oracle validate` passes all current oracles

---

## Phase 4 — Audit Trails

### P4.1 Audit Trail Generator

- [ ] `src/evaluator/audit.py` implements `AuditGenerator`:
  - `build_trail(session_id: str) -> AuditTrail | None`
    - Query all events for session from txtai (ordered by timestamp)
    - Query all oracle results for session from SQLite
    - Query all drift logs for session from SQLite
    - Build `DecisionChain` — list of `DecisionNode` with:
      - `event_id, event_type, timestamp, agent_id`
      - `description` — human-readable summary of the event
      - `assumptions_used` — event IDs of assumptions referenced
      - `oracles_applied` — oracle IDs evaluated against this event
      - `oracle_results` — pass/fail/deviation per oracle
      - `drift_flags` — any drift event IDs linked to this event
      - `consequential` — True if event_type is in `["delegation", "circuit_breaker", "correction"]`
      - `human_in_loop` — True if event_type == "human_input"
    - Compute `AuditSummary`: total_events, drift_events, circuit_breaker_halts, human_interventions
    - Return `AuditTrail` dataclass
  - `DecisionNode` dataclass: all fields above
  - `AuditTrail` dataclass: `audit_id, session_id, span, agents, decision_chain, summary`
  - `get_consequential_events(session_id: str) -> list[dict]` — filtered view

### P4.2 aiaudit CLI

- [ ] `aiaudit trail <session_id>` — generate and print audit trail for session
- [ ] `aiaudit trail --event-id <id>` — show trail leading up to event
- [ ] `aiaudit export --format html --session <id>` — export trail as HTML
- [ ] `aiaudit export --format json --session <id>` — export trail as JSON
- [ ] `aiaudit export --format md --session <id>` — export trail as Markdown
- [ ] `aiaudit diff <session_a> <session_b>` — diff two session trails
- [ ] `aiaudit prune --before YYYY-MM-DD` — archive trails older than date, delete raw events

### P4.3 Audit Tests

- [ ] `tests/test_audit.py`:
  - Build trail for session with 5 events → AuditTrail.decision_chain has 5 nodes
  - `consequential` flag set correctly for each event type
  - `human_in_loop` True only for human_input events
  - Export to JSON roundtrips correctly
  - `prune` removes events before date but not after

---

## Phase 5 — ClawFlow Orchestration + Alerts

### P5.1 ClawFlow Definition

- [ ] Write ClawFlow definition for `aie_heartbeat` flow (see SPEC.md §7.2)
- [ ] Flow file: `evaluator/flows/aie_heartbeat.lobster` or equivalent
- [ ] Steps:
  1. `aidrift scan --all` → collect drift results
  2. For each critical drift → `circuit_breaker` event → alert
  3. `aieval oracle batch --since 24h` → collect oracle results
  4. For each critical failure → `circuit_breaker` event → alert
  5. `aiaudit trail --sessions-with-drift` → write audit trails
  6. `aidrift stats` → log summary
  7. Set waiting state (30 min)
  8. Resume → repeat from step 1

### P5.2 Cron Trigger

- [ ] `evaluator/scripts/cron_setup.sh` created:
  - Installs minimal crontab: `0 * * * * openclaw flow trigger aie_heartbeat --if-idle`
  - Adds `TZ=Australia/Sydney` at top of crontab
  - Only installs the cron trigger, not the full job logic (that's in ClawFlow)
- [ ] `evaluator/scripts/cron_setup.sh --uninstall` removes the crontab

### P5.3 Alert Integration

- [ ] `src/evaluator/alerts.py` implements:
  - `send_alert(message: str, severity: str, channel: str = "evaluator-alerts")`
  - Uses OpenClaw Discord message tool: `openclaw message --channel <channel> <message>`
  - Configurable via env: `ALERT_CHANNEL`
  - Severity mapping:
    - `critical` → immediate Discord alert + exit code 1
    - `warning` → Discord alert
    - `info` → logged only
- [ ] Critical drift: any `drift_score >= 0.9` → send Discord alert
- [ ] Circuit breaker halt: immediately send alert
- [ ] Oracle failure (critical): send alert
- [ ] Health check failure: send alert

### P5.4 Health Check

- [ ] `evaluator/scripts/health_check.sh`:
  - Checks: logger socket alive, txtai reachable, no backpressure queue > 100, disk space > 10%
  - Exits 0 if healthy, exits 1 if not
  - Run as step in ClawFlow, not as standalone cron

---

## Phase 6 — Agent Integration

### P6.1 codi Integration

- [ ] Instrument `codi` agent to emit events via `AILoggerClient`
- [ ] Emit `delegation` events on every subagent spawn
- [ ] Emit `tool_call` events on every tool invocation (read, write, exec, sessions_spawn)
- [ ] Emit `assumption` events on explicit "Assuming..." statements in agent output
- [ ] Emit `correction` events on "Wait, actually..." / "Correction:..." patterns
- [ ] Record `session_id` — use OpenClaw session ID as canonical
- [ ] End-to-end test: run codi on a simple task, verify events in txtai

### P6.2 reviewer Integration

- [ ] Same as P6.1 but for reviewer agent
- [ ] reviewer is assumption-heavy — prioritise `assumption` and `correction` events
- [ ] Verify reviewer audit trail shows clear decision chain

### P6.3 g3 Integration

- [ ] Instrument g3 Rust agent to emit events (via sidecar Python wrapper or direct JSON-RPC)
- [ ] Align `g3` agent IDs with AIE `agent_id` field
- [ ] Test with a real g3 task

### P6.4 Golden Session Tests

- [ ] Store anonymised real session logs as fixtures in `tests/fixtures/sessions/`
- [ ] `tests/test_golden_sessions.py`:
  - Load fixture
  - Run full pipeline: ingest → index → oracle evaluate → drift scan → audit trail
  - Assert: no unexpected drift flags, oracle pass/fail counts match expected, audit trail complete

---

## Open Questions (must resolve before starting relevant phase)

| # | Question | Blocking |
|---|---|---|
| 1 | Alert channel — `#evaluator-alerts` (new Discord channel) or DM to Sean? | Phase 5 |
| 2 | g3 instrument now or after uncommitted changes resolved? | Phase 6 |
| 3 | `implicit` contradiction (NLI) in v1 or v2? | Phase 2 |
| 4 | AIE logs in git-ignored `data/` dir or tracked? | Phase 1 |

---

## Definition of Done

All phases complete when:

1. Every checkbox above is ticked
2. `pytest tests/ -x -q` passes with 0 failures
3. `ailogger serve` starts without error
4. `aidrift scan` returns results against a synthetic session
5. `aieval oracle list` lists all loaded oracles
6. `aiaudit trail <session_id>` produces a valid audit trail
7. `openclaw flow trigger aie_heartbeat` runs without error
8. Real agent events from codi flow into AIE and appear in txtai query results
