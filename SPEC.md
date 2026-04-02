# Agent Interaction Evaluator — SPEC.md

> **Purpose:** Structured observability for multi-agent ecosystems. Reduce errors, surface assumption drift, produce auditable decision trails. Built on the principles in `agentic-workflow-philosophy.md`.
>
> **Status:** Implementation in progress — Phases 1-5 complete, 6-7 planned
> **Last updated:** 2026-04-01

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
│  ClawTeam swarms (delegation + task events)                  │
└─────────────────────────┬────────────────────────────────────┘
                          │ Structured events (JSONL over IPC)
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                 Agent Interaction Logger (AIL)               │
│  Phase 1: JSON-RPC IPC server + client                      │
│  Phase 2: txtai indexing on every event emit                 │
└─────────────────────────┬────────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
┌─────────────────────────┐   ┌──────────────────────────────┐
│   Local JSONL logfile   │   │   txtai/FAISS Index         │
│   (raw archive)         │   │   collection: agent_events  │
│   evaluator/data/logs/  │   │   shared w/ RepoTransmute   │
└─────────────────────────┘   └──────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────┐
│                    Oracle Engine (Phase 3)                  │
│  - YAML oracle registry (loaded at startup)                  │
│  - Per-event-type evaluation rules                           │
│  - Condition types: field_required, field_min_length,       │
│    field_regex, similarity_threshold, drift_score_threshold   │
└─────────────────────────┬────────────────────────────────────┘
                          │
              ┌───────────┼───────────┬──────────────┐
              ▼           ▼           ▼              ▼
         Audit Trail  Drift Flag  Alert/Halt    ClawFlow
         (Phase 4)   (Phase 2)  (Phase 5)    Orchestration
                                              (aie_heartbeat)
```

### Shared Infrastructure with RepoTransmute

AIE uses the **same txtai/FAISS instance** as RepoTransmute:
- **RepoTransmute** indexes code blueprints — collection: `blueprints`
- **AIE** indexes agent interactions — collection: `agent_events`
- Both share `~/workspace/zoul/repo-transmute/data/txtai/` (FAISS index files)
- AIE has its own SQLite sidecar at `evaluator/data/aie_meta.db`

This enables cross-search — e.g., "find interactions where an agent referenced code chunk X".

---

## 3. Event Schema

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
```json
{
  "event_type": "delegation",
  "delegator": { "agent_id": "string", "role": "string" },
  "delegate": { "agent_id": "string", "role": "string" },
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
```json
{
  "event_type": "drift_detected",
  "current_assumption": { "event_id": "event_id", "statement": "string" },
  "contradicted_by": { "event_id": "event_id", "statement": "string" },
  "contradiction_type": "direct|semantic|implicit",
  "drift_score": 0.0,
  "action_taken": "flagged|halted|alerted"
}
```

#### `circuit_breaker`
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
```json
{
  "event_type": "human_input",
  "human": { "id": "string", "role": "string" },
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

### 4.1 Directory Structure

```
agent-interaction-evaluator/oracles/
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
oracle_id: "unique_oracle_id"
name: "Human-readable name"
description: |
  Detailed description of what this oracle evaluates.
event_type: "delegation|tool_call|assumption|..."
trigger: "on_event|on_demand|on_cron"
severity: "critical|warning|info"
conditions:
  - type: "field_required"           # condition type (see §4.3)
    field: "path.to.field"
    value: { ... }
actions:
  - type: "flag|alert|halt"
    output: "drift_event|discord|..."
metadata:
  author: "sean"
  created: "2026-04-01"
  tags: ["context", "delegation"]
  philosophy_ref: "Pillar-3"
```

### 4.3 Condition Types

| Type | Description | Fields |
|---|---|---|
| `field_required` | Field exists and is not None | `field: str` |
| `field_not_empty` | Field is not empty string or empty list | `field: str` |
| `field_min_length` | String field minimum length | `field: str`, `min_length: int` |
| `field_max_length` | String field maximum length | `field: str`, `max_length: int` |
| `field_regex` | Field matches regex pattern | `field: str`, `pattern: str` |
| `field_eq` | Field equals value | `field: str`, `value: Any` |
| `field_gt` | Numeric field greater than | `field: str`, `value: float` |
| `field_lt` | Numeric field less than | `field: str`, `value: float` |
| `field_in` | Field value in list | `field: str`, `values: list` |
| `similarity_threshold` | txtai similarity vs reference text | `field: str`, `reference: str`, `threshold: float` |
| `drift_score_threshold` | Drift score comparison | `threshold: float`, `op: gt\|lt\|gte\|lte` |
| `ratio_threshold` | Ratio of N events meeting condition | `events: list`, `condition: dict`, `threshold: float`, `op: gt\|lt` |

### 4.4 Action Types

| Type | Behaviour |
|---|---|
| `flag` | Write drift event to SQLite drift_log, mark event in index |
| `alert` | Send Discord alert via alerts.py |
| `halt` | Emit circuit_breaker event, halt session if halt_session=true |
| `log` | Write to JSONL log only |
| `audit` | Include in next audit trail generation |

---

## 5. Phase 2 — Drift Detection (txtai + Semantic)

### 5.1 txtai Client

**Inherits from RepoTransmute's `TxtaiClient` pattern** — same FAISS index, separate `agent_events` collection.

```python
class AIETxtaiClient(TxtaiClient):
    """Extends RepoTransmute TxtaiClient with AIE-specific collection."""
    COLLECTION = "agent_events"
    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def index_event(self, event: dict) -> None:
        """Index a single event. Embeds text field, stores metadata in SQLite."""
        doc = self._build_doc(event)
        self.index([doc])

    def query_assumptions(
        self,
        text: str,
        session_id: str | None = None,
        top_k: int = 10
    ) -> list[dict]:
        """Find prior assumptions similar to text."""

    def query_events(
        self,
        filters: dict,
        top_k: int = 50
    ) -> list[dict]:
        """Query events by event_type, agent_id, session_id, since."""

    def get_stats(self) -> dict:
        """Return collection size, last updated."""
```

### 5.2 Drift Detection Algorithm

```
For each new assumption event:
  1. Embed assumption.statement → vector
  2. Query agent_events collection for prior assumption events
     (same session_id OR global, top_k=10)
  3. For each prior assumption with similarity > 0.85:
     - similarity ≥ 0.95 → contradiction_type = "direct"
     - 0.85 ≤ similarity < 0.95 → contradiction_type = "semantic"
       (Phase 7: NLI second-pass to distinguish contradiction vs neutral)
  4. If contradiction found:
     - Create DriftResult
     - Index drift_detected event
     - Insert into SQLite drift_log
     - If drift_score ≥ 0.9 → critical → trigger circuit_breaker
```

### 5.3 txtai Document Schema

```python
{
    "id": "event_id",  # used as UID
    "text": "assumption: {statement} | category: {category} | agent: {agent_id}",
    "event_type": "assumption",
    "event_id": "uuid",
    "agent_id": "string",
    "session_id": "string",
    "timestamp": "ISO-8601",
    "statement": "string",  # the assumption text for drift comparison
    "category": "string",
    "drift_score": 0.0,
    "oracles_triggered": []
}
```

---

## 6. Phase 3 — Oracle Engine

### 6.1 Registry

```python
class OracleRegistry:
    """Loads and manages all oracle definitions."""

    def load(self, path: str = "oracles/") -> None:
        """Walk oracles/ directory, load all .yaml files."""

    def get_for_event_type(self, event_type: str) -> list[Oracle]:
        """Return all oracles that apply to an event type."""

    def get(self, oracle_id: str) -> Oracle | None

    def list(self) -> list[Oracle]:
        """Return all loaded oracles."""

    def validate(self, oracle_id: str) -> tuple[bool, str | None]:
        """Validate oracle YAML syntax and condition types."""
```

### 6.2 Condition Evaluators

```python
class FieldRequiredEvaluator:
    def evaluate(self, event: dict, field: str) -> bool

class FieldMinLengthEvaluator:
    def evaluate(self, event: dict, field: str, min_length: int) -> bool

class FieldRegexEvaluator:
    def evaluate(self, event: dict, field: str, pattern: str) -> bool

class SimilarityThresholdEvaluator:
    def evaluate(self, event: dict, field: str, reference: str, threshold: float) -> bool
    # Uses AIETxtaiClient.similarity()

class RatioThresholdEvaluator:
    def evaluate(self, events: list[dict], condition: dict, threshold: float) -> bool
    # For session-level aggregates (e.g., error rate > 20%)
```

### 6.3 Evaluation Flow

```
On event emit (or on_demand / on_cron batch):
  1. Load all oracles for event_type
  2. For each oracle:
     a. Evaluate all conditions in sequence (AND logic)
     b. If any condition fails:
        - Record ConditionResult(passed=False, failed_conditions=[...], deviation=str)
        - Execute actions based on severity:
          critical → circuit_breaker event + alert
          warning → drift_flag event + alert
          info → log only
     c. If all pass:
        - Record ConditionResult(passed=True)
  3. Insert all ConditionResults into SQLite oracle_results table
```

### 6.4 Session-Level Oracle Evaluation (on_cron)

For `on_cron` oracles (e.g., error_recovery_rate):
- Batch evaluate all events in last 24h per session
- Run ratio conditions across the event set
- Emit results as aggregate oracle results

---

## 7. Phase 4 — Audit Trails

### 7.1 Audit Trail Schema

```python
@dataclass
class AuditTrail:
    audit_id: str
    session_id: str
    span: {"start": str, "end": str}
    agents: list[str]
    decision_chain: list[DecisionNode]
    summary: AuditSummary

@dataclass
class DecisionNode:
    event_id: str
    event_type: str
    timestamp: str
    agent_id: str
    description: str          # human-readable summary
    assumptions_used: list[str]  # assumption event IDs
    oracles_applied: list[str]   # oracle IDs
    oracle_results: list[OracleResult]
    drift_flags: list[str]
    consequential: bool       # True for delegation/correction/circuit_breaker
    human_in_loop: bool      # True for human_input events

@dataclass
class AuditSummary:
    total_events: int
    drift_events: int
    circuit_breaker_halts: int
    human_interventions: int
    oracles_passed: int
    oracles_failed: int
```

### 7.2 Trail Generation

```python
class AuditGenerator:
    def build_trail(self, session_id: str) -> AuditTrail | None:
        """Build full audit trail from txtai events + SQLite metadata."""
        # 1. Query all events for session (ordered by timestamp)
        # 2. Query all oracle results for session
        # 3. Query all drift events for session
        # 4. Build decision_chain:
        #    - Mark consequential events (delegation, correction, circuit_breaker)
        #    - Link assumptions_used to assumption event IDs
        #    - Link oracle_results to applied oracles
        #    - Link drift_flags to drift_detected events
        # 5. Compute AuditSummary

    def get_consequential_events(self, session_id: str) -> list[dict]:
        """Return only consequential events for a session."""
```

### 7.3 Export Formats

| Format | Use case |
|---|---|
| JSON | Machine parsing, diff |
| Markdown | Human review, documentation |
| HTML | Web dashboard (future) |

### 7.4 Session Diff

```python
def diff(trail_a: AuditTrail, trail_b: AuditTrail) -> AuditDiff:
    """Compare two audit trails."""
    # Differences in:
    # - total_events, drift_events, circuit_breaker_halts
    # - decision_chain length and content
    # - oracle pass/fail rates
    # - drift patterns
```

---

## 8. Phase 5 — ClawFlow Orchestration

### 8.1 The `aie_heartbeat` Flow

```
Flow: "aie_heartbeat"
Owner: zoul main session (agent:zoul)
Purpose: Autonomous monitoring and evaluation

Steps:

Step 1: DRIFT_SCAN
  script: |
    aidrift scan --all --since 6h
  on_result (drifts):
    if any drift.drift_score >= 0.9:
      → circuit_breaker event
      → send_alert(critical, channel="#evaluator-alerts")
    if any drift:
      → log to drift_report/YYYY-MM-DD.json
      → continue

Step 2: ORACLE_BATCH
  script: |
    aieval batch --since 24h --trigger on_cron
  on_result (results):
    if any result.oracle.severity == critical and result.passed == False:
      → circuit_breaker event
      → send_alert(critical)
    if any failures:
      → log to oracle_report/YYYY-MM-DD.json

Step 3: AUDIT_TRAILS
  script: |
    sessions = get_active_sessions_since 24h
    for session in sessions:
      if session has drift or failures:
        aiaudit trail {session} --export json
  output: audit_trails/{session_id}/{date}.json

Step 4: HEALTH_CHECK
  script: |
    ailogger status
    # check: buffered_events < 100, txtai reachable, logger_uptime > 0
  on_result (health):
    if not healthy:
      send_alert(warning, "AIE health check failed")

Step 5: SET_WAITING
  set_flow_waiting(seconds=1800)  # 30 minutes

Resume from Step 1.
```

### 8.2 Cron Trigger

Minimal crontab entry — just wakes the flow:

```bash
# Every hour: if flow is idle, trigger it
0 * * * * openclaw flow trigger aie_heartbeat --if-idle >> evaluator/data/logs/cron_trigger.log 2>&1
```

The actual work lives in the flow, not in crontab.

### 8.3 Alert Routing

| Severity | Action |
|---|---|
| `critical` | Discord `#evaluator-alerts` + halt session |
| `warning` | Discord `#evaluator-alerts` |
| `info` | Logged to `evaluator/data/logs/info.log` |

---

## 9. Phase 6 — Agent Integration

### 9.1 How Agents Emit Events

**Option A — IPC (preferred):** Agent calls `AILoggerClient.emit(event)` over Unix socket.

**Option B — File drop:** Agent writes JSONL to `evaluator/data/inbox/`. Logger watches and ingests.

### 9.2 codi Integration

codi is the priority — most active, regular delegation patterns.

**Events to emit:**
- `delegation` — on every `sessions_spawn` call
- `tool_call` — on every `read`, `write`, `exec` call
- `assumption` — on explicit "Assuming..." in agent output
- `correction` — on "Wait, actually...", "Correction:..."

**Implementation:** Wrap codi's tool calls in a thin event-emitting layer:
```python
# In codi's tool wrapper
from evaluator.logger_client import AILoggerClient

async def wrapped_sessions_spawn(args):
    event = build_delegation_event(args)
    client = AILoggerClient()
    await client.emit(event)
    result = await original_sessions_spawn(args)
    # emit outcome event
    await client.close()
    return result
```

### 9.3 reviewer Integration

Same as codi, but prioritising `assumption` and `correction` events.

### 9.4 g3 Integration

Via sidecar Python wrapper that reads g3's output logs and emits events.

### 9.5 ClawTeam Swarm Integration

When ClawTeam swarms emit delegation events, those can feed directly into AIE:
- ClawTeam's `delegation` events → AIE `delegation` events
- ClawTeam's task board events → AIE `tool_call` events

### 9.6 Priority Order

| Agent | Priority | Reason |
|---|---|---|
| codi | 1 | Most active, clean delegation patterns |
| reviewer | 2 | Assumption-heavy |
| ClawTeam swarms | 3 | Natural delegation events |
| g3 | 4 | Complex, uncommitted changes |
| journal | 5 | Structured but low-stakes |

---

## 10. Phase 7 — Advanced Drift (v2)

### 10.1 NLI Second-Pass

For `semantic` contradictions (similarity 0.85–0.95), run NLI to distinguish `contradiction` from `neutral`:

```python
from sentence_transformers import CrossEncoder

model = CrossEncoder("cross-encoder/nli-deberta-v3-small")

def check_implicit_contradiction(statement1: str, statement2: str) -> str:
    scores = model.predict([(statement1, statement2)])
    # scores: [contradiction, entailment, neutral]
    label = ["contradiction", "entailment", "neutral"][scores.argmax()]
    return label  # contradiction | entailment | neutral
```

Only flag as `implicit` drift if NLI returns `contradiction` on semantically similar pairs.

### 10.2 Context Fidelity Scoring

Track how much context survives each delegation:

```python
def compute_context_fidelity(delegation_event, downstream_events):
    original_context = delegation_event["task"]["context_summary"]
    referenced_in_subsequent = count_context_references(original_context, downstream_events)
    return min(referenced_in_subsequent / 3, 1.0)  # 0.0 to 1.0
```

Flag delegations with fidelity < 0.3 as critical.

### 10.3 Cascade Impact Analysis

When a critical drift occurs, trace downstream events that may have been affected:

```python
def trace_cascade(drift_event, all_events):
    """Find all events that used the invalidated assumption."""
    assumption_id = drift_event["invalidated_assumption_ids"][0]
    affected = []
    for event in all_events:
        if assumption_id in event.get("derived_from", []):
            affected.append(event)
    return affected
```

---

## 11. Data Storage

### 11.1 txtai Collections

| Collection | Purpose |
|---|---|
| `blueprints` | RepoTransmute code blueprints |
| `agent_events` | AIE interaction events |

### 11.2 SQLite Sidecar (`evaluator/data/aie_meta.db`)

```sql
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  agent_id TEXT,
  channel TEXT,
  started_at TEXT,
  ended_at TEXT,
  event_count INTEGER DEFAULT 0,
  drift_count INTEGER DEFAULT 0,
  circuit_breaker_halts INTEGER DEFAULT 0
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

## 12. CLI Reference

### ailogger
```
ailogger serve          Start IPC server
ailogger emit --stdin   Emit event from stdin
ailogger status         Show logger health
```

### aidrift
```
aidrift check <event_id>        Check one event for drift
aidrift scan --session <id>     Scan one session
aidrift scan --all              Scan all active sessions
aidrift report                  Print open drifts as JSON
aidrift stats                  Print drift statistics
```

### aieval
```
aieval evaluate <file>         Evaluate event file
aieval evaluate --stdin         Evaluate from stdin
aieval oracle list              List all oracles
aieval oracle validate          Validate oracle YAML
aieval oracle run --oracle <id> Run specific oracle
aieval batch --since <date>     Batch evaluate (on_cron)
aieval report --since <date>    Evaluation report
```

### aiaudit
```
aiaudit trail <session_id>      Generate audit trail
aiaudit trail --event-id <id>   Trail leading to event
aiaudit export --format md|json|html --session <id>
aiaudit diff <session_a> <session_b>
aiaudit prune --before <date>
```

---

## 13. Development Phases

| Phase | Status | Deliverable |
|---|---|---|
| 1 — Foundation | ✅ Complete | ailogger serve + emit, schema, sanitiser, SQLite, 3 oracles |
| 2 — txtai + Drift | ✅ Complete | AIETxtaiClient, aidrift scan, txtai indexing |
| 3 — Oracle Engine | ✅ Complete | Full oracle registry, condition evaluators, aieval CLI |
| 4 — Audit Trails | ✅ Complete | AuditGenerator, aiaudit trail/export/diff |
| 5 — ClawFlow | ✅ Complete (installed, not yet running on cron) | aie_heartbeat flow, cron_setup.sh, alerts |
| 6 — Agent Integration | 📋 Planned | codi → AIE event emission |
| 7 — Advanced Drift | 📋 Planned | NLI second-pass, context fidelity, cascade tracing |

---


## 14. Open Questions

| # | Question | Decision |
|---|---|---|
| 3 | Semantic drift NLI confirmation in v1 or v2? | v2 (Phase 7) |
| (1) | Alert channel | ✅ `#evaluator-alerts` |
| (2) | g3 instrumentation | ✅ Include (revisit later) |
| (4) | AIE logs git-ignored | ✅ data/ gitignored + docs/DATA-TRACKING.md |

**Resolved:**
- Own repo ✅ (github.com/ChonSong/agent-interaction-evaluator)
- Autonomous methodology ✅ (ClawFlow, not raw cron)
- txtai reuse ✅ (extend RepoTransmute pattern, share index)
- Drift thresholds ✅ (≥0.95 direct, 0.85–0.95 semantic, NLI in v2)
- Alert channel ✅ (`#evaluator-alerts`)
- g3 integration ✅ (Phase 6, revisit later)
- Data docs ✅ (docs/DATA-TRACKING.md)

## 15. References

- `agentic-workflow-philosophy.md` — founding document
- `repo-transmute/src/repo_transmute/txtai/client.py` — TxtaiClient pattern
- `repo-transmute/src/repo_transmute/evaluator/drift_detector.py` — keyword-based drift detection
- `docs/PHASE2-RESEARCH.md` — research sprint findings
- `docs/DATA-TRACKING.md` — data lifecycle documentation
- `clawflow/SKILL.md` — ClawFlow runtime substrate
- `HKUDS/ClawTeam` — swarm orchestration (complementary)
- `cross-encoder/nli-deberta-v3-small` — NLI model for Phase 7

---

## 16. Simplification Sprint Results (2026-04-02)

Post-Phase 5 simplification sprint completed these changes:

### txtai Deduplication
AIE `txtai_client.py` now extends RepoTransmute's `TxtaiClient`. Shared FAISS index at `~/workspace/zoul/repo-transmute/data/txtai/`, separate `agent_events` collection.

### Tool Execution
codi's `nanobot_tools.py` delegates to `claw-aie ToolExecutor` for bash, file_read, file_write, glob, grep. `openhands_events.py` in codi deleted (confirmed unused).

### claw-aie Integration
claw-aie is the canonical instrumented harness. codi delegates tool execution to it. All claw-aie tool calls emit AIE-compatible events via PreToolUse/PostToolUse hooks.

### ClawTeam Sidecar
Integration point identified: `FileTaskStore.update()` detects `owner` changes → emits AIE `delegation` events. Sidecar approach (no fork required).

### Pending
- `repo-transmute/evaluator/` pending deletion — verify AIE covers all use cases before removing
- ClawTeam sidecar implementation
