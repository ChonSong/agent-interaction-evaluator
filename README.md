# Agent Interaction Evaluator

> Structured observability for multi-agent ecosystems.

AIE reduces errors, surfaces assumption drift, and produces auditable decision trails for agent systems — built on the principles in [The Architecture of Agentic Workflows](https://github.com/ChonSong/agent-interaction-evaluator/blob/main/SPEC.md).

## Status

Alpha. See [SPEC.md](./SPEC.md) for full architecture and [REQUIREMENTS.md](./REQUIREMENTS.md) for the development roadmap.

## Quick Start

```bash
# Install
pip install -e .

# Start the logger server
ailogger serve

# In another terminal — emit a test event
echo '{"event_type": "delegation", ...}' | ailogger emit --stdin

# Check drift
aidrift scan --session <session_id>

# List oracles
aieval oracle list
```

## What It Does

| Capability | Description |
|---|---|
| **Event logging** | Structured JSON events for delegation, tool calls, assumptions, corrections, drift, circuit breakers, human input |
| **Semantic indexing** | txtai/FAISS powered — query events by content, agent, session, time |
| **Oracle evaluation** | YAML-defined rules evaluate whether events meet defined standards |
| **Drift detection** | Cross-references current assumptions against indexed history |
| **Audit trails** | Human-readable + machine-parseable decision provenance |
| **Cron alerts** | Automated scans with Discord alerts on critical drift |

## Architecture

```
Agent ecosystem → AILogger (IPC) → txtai/FAISS index
                      ↓
               SQLite sidecar
                      ↓
              Oracle Engine → Drift Detector → Alert
                      ↓
               Audit Trail Exporter
```

See [SPEC.md](./SPEC.md) §2 for full architecture diagram.

## Requirements

- Python 3.11+
- txtai 6.0+ (shared with RepoTransmute)
- No external services — runs on existing workspace infrastructure

## License

MIT
