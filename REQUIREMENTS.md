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
- Autonomous work uses **ClawFlow** (not raw crontab) — see SPEC.md §8
- Breaking changes to schema must update `SPEC.md` simultaneously.
- txtai reuse: share the same FAISS index as RepoTransmute, create separate `agent_events` collection

---

## Phase 1 — Foundation ✅ COMPLETE

All items completed in previous sprint. See commit `746a4a7`.

---

## Phase 2 — txtai Indexing + Drift Detection

### P2.1 AIETxtaiClient (src/evaluator/txtai_client.py)

**Important:** Extend RepoTransmute's `TxtaiClient` pattern, do NOT rewrite. Import from RepoTransmute where possible.

- [ ] Import `TxtaiClient` from `repo_transmute.txtai.client` (path: `~/workspace/zoul/repo-transmute/src/`)
- [ ] `AIETxtaiClient` class extends/uses `TxtaiClient`:
  - `index_dir`: `~/workspace/zoul/repo-transmute/data/txtai/` (shared with RepoTransmute)
  - `COLLECTION_NAME = "agent_events"` (separate from RepoTransmute's `blueprints`)
  - `DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"`
- [ ] `ensure_collection()` — create `agent_events` collection if not exists
- [ ] `index_event(event: dict)` — build doc per schema in SPEC.md §5.3, embed the text field, store metadata in SQLite sidecar
- [ ] `query_assumptions(text: str, session_id: str | None, top_k: int = 10) -> list[dict]` — semantic search for prior assumptions
- [ ] `query_events(filters: dict, top_k: int = 50) -> list[dict]` — query by event_type, agent_id, session_id, since
- [ ] `get_stats() -> dict` — collection size, last updated
- [ ] Graceful fallback: if txtai unavailable, log warning and return empty (do NOT crash)
- [ ] **Note:** Do NOT import the entire RepoTransmute repo (circular dependency risk). Only import `TxtaiClient` directly.

### P2.2 Drift Detection — aidrift CLI

- [ ] `src/evaluator/drift.py` — `DriftDetector` class:
  - `check(event_id: str, event: dict) -> DriftResult | None`
    - Get `assumption.statement` from event
    - Query txtai for prior assumptions in same session (session_id filter)
    - For each prior assumption with similarity > 0.85:
      - similarity ≥ 0.95 → `direct`
      - 0.85 ≤ similarity < 0.95 → `semantic`
    - Return `DriftResult` with: event_id, contradicted_event_id, contradiction_type, drift_score (=similarity), current_statement, prior_statement
    - Return `None` if no drift found
  - `scan_session(session_id: str) -> list[DriftResult]`
  - `scan_all_active() -> list[DriftResult]` — scan sessions with events in last 7 days
- [ ] `DriftResult` dataclass: `event_id, contradicted_event_id, contradiction_type, drift_score, current_statement, prior_statement`
- [ ] `aidrift check <event_id>` CLI — reads event from `data/logs/YYYY-MM-DD/` or as JSON arg
- [ ] `aidrift scan --session <session_id>` CLI
- [ ] `aidrift scan --all` CLI — scan all active sessions (last 7 days)
- [ ] `aidrift report` CLI — print open drifts as JSON
- [ ] `aidrift stats` CLI — print drift statistics

### P2.3 Logger txtai Integration

- [ ] Update `AILogger.emit()` in `logger.py` to call `AIETxtaiClient.index_event()` after persisting to JSONL
- [ ] This should be async and non-blocking — do NOT block the emit if txtai is slow
- [ ] If txtai is unavailable, buffer the event in memory and retry later

### P2.4 txtai Integration Tests (tests/test_txtai_client.py)

- [ ] `test_index_and_query_roundtrip` — index a synthetic event, query it back
- [ ] `test_query_by_session` — query returns only events from specified session
- [ ] `test_get_stats` — returns non-zero collection size after indexing
- [ ] `test_graceful_fallback` — when txtai unavailable, returns empty without crashing
- [ ] `test_drift_detection_direct` — two nearly identical assumption statements → similarity ≥ 0.95 → `direct`
- [ ] `test_drift_detection_semantic` — two different but similar statements → similarity 0.85–0.95 → `semantic`
- [ ] `test_drift_detection_none` — unrelated statements → no drift flagged

### P2.5 Phase 2 Tests Pass

```bash
pytest tests/test_txtai_client.py tests/test_drift.py -x -q
```

All must pass. No skips. No xfails.

---

## Phase 3 — Oracle Engine + Evaluation

### P3.1 Oracle Registry

- [ ] `src/evaluator/oracle_engine.py`:
  - `Oracle` dataclass — all fields from SPEC.md §4.2
  - `ConditionResult` dataclass — `passed: bool, failed_conditions: list[str], deviation: str | None`
  - `OracleRegistry` class:
    - `load(path: str = "oracles/")` — walk directory, load all YAML, validate schema
    - `get_for_event_type(event_type: str) -> list[Oracle]`
    - `get(oracle_id: str) -> Oracle | None`
    - `list() -> list[Oracle]`
    - `validate(oracle_id: str) -> tuple[bool, str | None]`

### P3.2 Condition Evaluators

Implement one class per condition type:

- [ ] `FieldRequiredEvaluator` — field exists and not None
- [ ] `FieldNotEmptyEvaluator` — field is not empty string or empty list
- [ ] `FieldMinLengthEvaluator` — string field minimum length
- [ ] `FieldMaxLengthEvaluator` — string field maximum length
- [ ] `FieldRegexEvaluator` — field matches regex pattern
- [ ] `FieldEqEvaluator` — field equals value
- [ ] `FieldGtEvaluator` — numeric field greater than
- [ ] `FieldLtEvaluator` — numeric field less than
- [ ] `FieldInEvaluator` — field value in list
- [ ] `SimilarityThresholdEvaluator` — uses AIETxtaiClient.similarity() to compare field text against reference
- [ ] `DriftScoreThresholdEvaluator` — drift score comparison (threshold, op)
- [ ] `RatioThresholdEvaluator` — ratio of N events meeting condition (for session-level aggregates)

### P3.3 Oracle Evaluation

- [ ] `evaluate_conditions(oracle: Oracle, event: dict) -> ConditionResult`
  - Evaluate all conditions in sequence (AND — all must pass for oracle to pass)
  - Return ConditionResult with passed status and list of failed conditions
- [ ] `apply_actions(oracle: Oracle, event: dict, result: ConditionResult) -> None`
  - If passed: no action (or log if oracle severity is info)
  - If failed + critical: emit circuit_breaker event + send alert
  - If failed + warning: emit drift_detected event + send alert
  - If failed + info: log only
- [ ] `evaluate_event(event: dict) -> list[ConditionResult]` — evaluate all applicable oracles for event type

### P3.4 aieval CLI

- [ ] `aieval evaluate <event_file>` — load event, evaluate against applicable oracles, print results as JSON
- [ ] `aieval evaluate --stdin` — read JSON lines from stdin, evaluate each
- [ ] `aieval oracle list` — list all loaded oracles with ID, event_type, severity, trigger
- [ ] `aieval oracle validate` — validate all oracle YAML files, print "All valid" or errors
- [ ] `aieval oracle run --oracle <id>` — run specific oracle against recent events (last 24h)
- [ ] `aieval batch --since YYYY-MM-DD --trigger on_cron` — batch evaluate all `on_cron` oracles
- [ ] `aieval report --since YYYY-MM-DD` — generate evaluation summary report as JSON

### P3.5 Full Oracle Set (8 oracles total)

Existing 3 from Phase 1, add 5 more:

- [ ] `oracles/delegation/context_fidelity_threshold.yaml`
  - Trigger: `on_event`, Severity: `warning`
  - Condition: `task.context_fidelity >= 0.5`
- [ ] `oracles/assumption/groundedness_required.yaml`
  - Trigger: `on_event`, Severity: `info`
  - Condition: `assumption.grounded_in` is not null
- [ ] `oracles/tool_call/schema_compliance.yaml`
  - Trigger: `on_event`, Severity: `warning`
  - Condition: `tool.argument_schema` exists → arguments conform
- [ ] `oracles/tool_call/error_recovery_rate.yaml`
  - Trigger: `on_cron`, Severity: `warning`
  - Condition: session error rate > 20% in last 24h → ratio_threshold on tool_call events with outcome.status=error
- [ ] `oracles/circuit_breaker/halt_on_critical_drift.yaml`
  - Trigger: `on_event`, Severity: `critical`
  - Condition: `drift_score >= 0.9`
  - Action: `halt`

### P3.6 Oracle Engine Tests (tests/test_oracle_engine.py)

- [ ] All condition types evaluate correctly (12 tests — one per condition type)
- [ ] Oracle with failing condition → ConditionResult.passed = False
- [ ] Oracle with all passing → ConditionResult.passed = True
- [ ] Unknown oracle_id → returns None gracefully
- [ ] `oracle validate` passes all current oracles
- [ ] `apply_actions` calls correct action type for each severity level

### P3.7 Phase 3 Tests Pass

```bash
pytest tests/test_oracle_engine.py -x -q
```

---

## Phase 4 — Audit Trails

### P4.1 AuditGenerator

- [ ] `src/evaluator/audit.py`:
  - `DecisionNode` dataclass — all fields from SPEC.md §7.1
  - `AuditSummary` dataclass — `total_events, drift_events, circuit_breaker_halts, human_interventions, oracles_passed, oracles_failed`
  - `AuditTrail` dataclass — `audit_id, session_id, span, agents, decision_chain, summary`
  - `AuditGenerator` class:
    - `__init__(self, txtai_client: AIETxtaiClient, db_path: str)`
    - `build_trail(session_id: str) -> AuditTrail | None`
      - Query txtai for all events in session (ordered by timestamp)
      - Query SQLite for oracle results and drift logs
      - Build decision_chain with consequential flag (delegation, correction, circuit_breaker = True)
      - Compute summary stats
      - Return AuditTrail
    - `get_consequential_events(session_id: str) -> list[dict]`

### P4.2 Session Diff

- [ ] `diff(trail_a: AuditTrail, trail_b: AuditTrail) -> AuditDiff`
  - Compare: total_events, drift_events, circuit_breaker_halts, oracle pass/fail rates
  - Return struct with before/after values and delta

### P4.3 aiaudit CLI

- [ ] `aiaudit trail <session_id>` — generate and print audit trail as JSON
- [ ] `aiaudit trail --event-id <id>` — show trail leading up to event
- [ ] `aiaudit export --format json --session <id>` — export trail as JSON
- [ ] `aiaudit export --format md --session <id>` — export as Markdown
- [ ] `aiaudit export --format html --session <id>` — export as HTML
- [ ] `aiaudit diff <session_a> <session_b>` — diff two session trails
- [ ] `aiaudit prune --before YYYY-MM-DD` — archive trails older than date

### P4.4 Audit Tests (tests/test_audit.py)

- [ ] `test_build_trail` — build trail for session with 5 events → 5 nodes
- [ ] `test_consequential_flag` — delegation/correction/circuit_breaker = True, others = False
- [ ] `test_human_in_loop_flag` — only human_input = True
- [ ] `test_export_json_roundtrip` — export + parse = identical
- [ ] `test_export_markdown` — produces valid markdown with sections
- [ ] `test_prune` — removes events before date, keeps events after

### P4.5 Phase 4 Tests Pass

```bash
pytest tests/test_audit.py -x -q
```

---

## Phase 5 — ClawFlow Orchestration + Alerts

### P5.1 ClawFlow Definition

- [ ] Create `evaluator/flows/aie_heartbeat.lobster` or equivalent (see SPEC.md §8.1 for flow definition)
- [ ] Flow steps:
  1. `aidrift scan --all --since 6h`
  2. For each critical drift → circuit_breaker + alert
  3. `aieval batch --since 24h --trigger on_cron`
  4. For each critical failure → circuit_breaker + alert
  5. `aiaudit trail` for sessions with drift or failures
  6. `aidrift stats` + log summary
  7. `set_flow_waiting(seconds=1800)` — 30 min

### P5.2 Cron Trigger

- [ ] `evaluator/scripts/cron_setup.sh`:
  - Installs crontab: `0 * * * * openclaw flow trigger aie_heartbeat --if-idle >> evaluator/data/logs/cron_trigger.log 2>&1`
  - `TZ=Australia/Sydney` at top
  - `--uninstall` flag removes the crontab
  - Dry-run mode: `--dry-run` prints crontab without installing

### P5.3 Alert Integration

- [ ] `src/evaluator/alerts.py`:
  - `send_alert(message: str, severity: str, channel: str = "evaluator-alerts")`
  - Uses `openclaw message --channel <channel> <message>`
  - Configurable via env: `ALERT_CHANNEL`
  - Severity: critical → exit 1 after alert; warning → alert only; info → logged
- [ ] Alert templates:
  - Critical drift: "🚨 CRITICAL DRIFT in session {session_id}: {statement} contradicts {prior_statement}"
  - Critical oracle failure: "🚨 ORACLE FAILURE: {oracle_id} failed on {event_type} event"
  - Warning: "⚠️ {type}: {summary}"

### P5.4 Phase 5 — Verification

- [ ] `openclaw flow trigger aie_heartbeat --if-idle` runs without error
- [ ] Alert fires correctly on simulated critical drift
- [ ] Crontab installs and cron fires correctly

---

## Phase 6 — Agent Integration

### P6.1 codi Integration

- [ ] Locate codi's tool wrapper (find sessions_spawn, read, write, exec calls)
- [ ] Add `AILoggerClient` import and context to each tool call
- [ ] Emit `delegation` event on every `sessions_spawn`
- [ ] Emit `tool_call` event on every tool invocation
- [ ] Parse agent output for `assumption` events ("Assuming...")
- [ ] Parse agent output for `correction` events ("Wait, actually...", "Correction:...")
- [ ] End-to-end: run codi on known task, verify events appear in `aidrift scan`

### P6.2 reviewer Integration

- [ ] Same pattern as codi, prioritising `assumption` and `correction`
- [ ] Verify audit trail shows clear decision chain

### P6.3 Golden Session Tests

- [ ] `tests/fixtures/sessions/` — store anonymised real session logs
- [ ] `tests/test_golden_sessions.py`:
  - Load fixture
  - Run full pipeline: index → oracle evaluate → drift scan → audit trail
  - Assert: expected drift count, expected oracle pass/fail, trail completeness

### P6.4 Phase 6 — Verification

- [ ] `aidrift scan --all` returns events from live codi session
- [ ] Events appear in `aiaudit trail <session_id>`
- [ ] No events dropped, no crashes

---

## Phase 7 — Advanced Drift (v2)

### P7.1 NLI Second-Pass

- [ ] Install `cross-encoder/nli-deberta-v3-small`
- [ ] `ImplicitDriftDetector.check(statement1, statement2) -> str`
  - Returns: "contradiction" | "entailment" | "neutral"
- [ ] Update `DriftDetector` to run NLI on `semantic` contradictions (0.85–0.95 similarity)
- [ ] If NLI says "contradiction" → upgrade to `implicit` drift with higher severity

### P7.2 Context Fidelity Scoring

- [ ] `compute_context_fidelity(delegation_event, downstream_events) -> float`
  - Track how many times delegation's context_summary is referenced in downstream events
  - Return 0.0–1.0 score
- [ ] Add as oracle condition: `context_fidelity < 0.3` → critical

### P7.3 Cascade Impact Analysis

- [ ] `trace_cascade(drift_event, all_events) -> list[dict]`
  - Find all events that used the invalidated assumption
  - Return affected events with severity assessment

### P7.4 Phase 7 — Verification

- [ ] NLI model loads and runs on CPU in <100ms per pair
- [ ] `implicit` drift type correctly assigned when NLI confirms contradiction
- [ ] Context fidelity score computed and stored on delegation events

---

## Open Questions (must resolve before starting relevant phase)

| # | Question | Blocking |
|---|---|---|
| 1 | Alert channel — `#evaluator-alerts` (new Discord channel) or DM? | Phase 5 |
| 2 | Do we instrument g3 despite uncommitted changes? | Phase 6 |
| 3 | AIE logs in git-ignored data/ dir? | All phases |

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
8. Real agent events from codi flow into AIE and appear in `aidrift scan`
