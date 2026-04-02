# AIE Data — What Gets Stored Where

This document describes how AIE manages its data lifecycle — what is stored where, what is git-ignored, and how to work with example/anonymised data.

## Data Directory Structure

```
agent-interaction-evaluator/
├── data/                         # ⚠️ GIT-IGNORED — never committed
│   ├── logs/                     # Raw event JSONL logs
│   │   ├── YYYY-MM-DD.jsonl
│   │   └── drift_reports/
│   │       └── YYYY-MM-DD.json
│   ├── audit_trails/             # Generated audit trails
│   │   └── {session_id}/
│   │       └── {date}.json
│   ├── inbox/                    # Drop directory for agents
│   ├── aie_meta.db              # SQLite sidecar
│   ├── agent_events_meta.db      # txtai metadata (separate from RepoTransmute)
│   └── health.log
│
├── examples/                      # ✅ TRACKED — anonymised example data
│   ├── events/
│   │   └── sample_delegation_event.json
│   ├── oracles/
│   │   └── sample_oracle.yaml
│   ├── audit_trails/
│   │   └── sample_trail.json
│   └── sessions/
│       └── anonymised_session_events.jsonl
│
├── fixtures/                      # ✅ TRACKED — test fixtures
│   ├── events/
│   │   └── valid_delegation_event.json
│   └── sessions/
│       └── golden_test_session.jsonl
```

## Why data/ is git-ignored

- Event logs may contain workspace paths, file names, agent outputs
- SQLite DBs contain full event history
- Audit trails may expose session internals
- These are sensitive by default — git-tracking would be a data leak

## Working with Example Data

### Generate an example event

```python
from evaluator.schema import generate_event_id, get_current_timestamp
import json

event = {
    "schema_version": "1.0",
    "event_id": generate_event_id(),
    "event_type": "delegation",
    "timestamp": get_current_timestamp(),
    "agent_id": "codi",
    "session_id": "example-session-001",
    "interaction_context": {
        "channel": "terminal",
        "workspace_path": "/workspace/project",
        "parent_event_id": None
    },
    "delegator": {"agent_id": "codi", "role": "orchestrator"},
    "delegate": {"agent_id": "reviewer", "role": "specialist"},
    "task": {
        "task_id": "task-001",
        "description": "Review Python module for security issues",
        "intent": "Ensure code quality before merge",
        "constraints": ["Do not modify files", "Report findings"],
        "context_summary": "repo/path/to/module.py, 200 lines, no external deps",
        "context_fidelity": 0.85,
        "max_turns": None,
        "deadline": None
    },
    "oracle_ref": None
}

print(json.dumps(event, indent=2))
```

### Save anonymised session for fixtures

```python
from evaluator.sanitiser import sanitise_event
import json

# Load real events, sanitise, save to examples/
with open("data/logs/2026-04-01.jsonl") as f:
    for line in f:
        event = json.loads(line)
        sanitised = sanitise_event(event)
        # Replace any remaining identifiers
        sanitised["agent_id"] = "agent-x"
        sanitised["session_id"] = "anonymised-session-001"
        with open("examples/sessions/anonymised_session.jsonl", "a") as out:
            out.write(json.dumps(sanitised) + "\n")
```

### Index example events into txtai (without affecting production)

```python
# Use a separate test index path
from evaluator.txtai_client import AIETxtaiClient

test_client = AIETxtaiClient(
    index_path="/tmp/aie_test_index"
)

# Load and index example events
with open("examples/sessions/anonymised_session.jsonl") as f:
    for line in f:
        event = json.loads(line)
        test_client.index_event(event)

print(test_client.get_stats())
```

## Exporting Audit Trails for Documentation

To create an example audit trail for documentation:

```python
from evaluator.audit import AuditGenerator
from evaluator.txtai_client import AIETxtaiClient

txtai = AIETxtaiClient()
audit = AuditGenerator(txtai_client=txtai)

trail = audit.build_trail("anonymised-session-001")
import json
with open("examples/audit_trails/sample_trail.json", "w") as f:
    json.dump(trail.__dict__, f, indent=2)
```

## Restoring from Backup

If you need to restore event logs from a backup:

```bash
# Copy backup to inbox (will be ingested by logger)
cp /backup/events/2026-03-28.jsonl evaluator/data/inbox/

# Or directly replay into txtai
python -c "
from evaluator.txtai_client import AIETxtaiClient
import json
client = AIETxtaiClient()
with open('/backup/events/2026-03-28.jsonl') as f:
    for line in f:
        client.index_event(json.loads(line))
"
```

## Data Retention

| Data | Retention | Policy |
|---|---|---|
| Raw JSONL logs | 30 days | `aiaudit prune --before 30days` weekly |
| txtai index | 90 days | Rebuild from logs if needed |
| SQLite drift_log | Until resolved | `resolve_drift()` or manual |
| Audit trails | 30 days | Weekly prune via ClawFlow |
| Health logs | 7 days | Rotated daily |
