"""AuditTrail generation — SPEC.md §7, REQUIREMENTS.md P4.1–P4.2.

Produces auditable decision chains from txtai events + SQLite metadata,
with export to JSON / Markdown / HTML.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from . import db as db_mod
from . import txtai_client as tx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses — SPEC.md §7.1
# ---------------------------------------------------------------------------

@dataclass
class DecisionNode:
    event_id: str
    event_type: str
    timestamp: str
    agent_id: str
    description: str
    assumptions_used: list[str]
    oracles_applied: list[str]
    oracle_results: list[dict]   # [{oracle_id, passed, deviation}]
    drift_flags: list[str]       # drift event IDs
    consequential: bool          # True: delegation/correction/circuit_breaker
    human_in_loop: bool          # True: human_input

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditSummary:
    total_events: int
    drift_events: int
    circuit_breaker_halts: int
    human_interventions: int
    oracles_passed: int
    oracles_failed: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditTrail:
    audit_id: str
    session_id: str
    span: dict          # {"start": str, "end": str}
    agents: list[str]
    decision_chain: list[DecisionNode]
    summary: AuditSummary

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "session_id": self.session_id,
            "span": self.span,
            "agents": self.agents,
            "decision_chain": [n.to_dict() for n in self.decision_chain],
            "summary": self.summary.to_dict(),
        }


@dataclass
class AuditDiff:
    before: AuditSummary
    after: AuditSummary
    delta: dict         # {field: delta_value}

    def to_dict(self) -> dict:
        return {
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "delta": self.delta,
        }


# ---------------------------------------------------------------------------
# AuditGenerator
# ---------------------------------------------------------------------------

class AuditGenerator:
    """
    Builds audit trails from events stored in txtai (agent_events collection)
    and SQLite sidecar (oracle_results, drift_log, sessions).
    """

    # Event types considered "consequential"
    CONSEQUENTIAL_TYPES = {"delegation", "correction", "circuit_breaker"}

    # Event types that indicate human-in-the-loop
    HUMAN_IN_LOOP_TYPES = {"human_input"}

    def __init__(self, txtai_client: tx.TxtaiClient, db_path: str | None = None) -> None:
        """
        Args:
            txtai_client: AIETxtaiClient instance for querying events.
            db_path: Path to SQLite sidecar. Defaults to aie_meta.db.
        """
        self.txtai = txtai_client
        self.db_path = db_path or os.environ.get(
            "AIE_DB_PATH",
            os.path.join(
                os.path.dirname(__file__), "..", "..", "evaluator", "data", "aie_meta.db"
            ),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_trail(self, session_id: str) -> AuditTrail | None:
        """
        Build a full audit trail for a session.

        1. Query all events for session from txtai (ordered by timestamp)
        2. Query oracle results and drift logs from SQLite
        3. Build decision_chain with linked oracle results and drift flags
        4. Mark consequential and human_in_loop flags
        5. Compute AuditSummary
        """
        # Fetch events from txtai
        events = self._query_session_events(session_id)
        if not events:
            return None

        # Fetch oracle results and drift logs
        oracle_results_map = self._query_oracle_results(session_id)
        drift_log_map = self._query_drift_logs(session_id)

        # Build decision chain
        decision_chain: list[DecisionNode] = []
        agents_set: set[str] = set()
        timestamps: list[str] = []

        for event in events:
            event_id = event.get("event_id", "")
            event_type = event.get("event_type", "")
            agent_id = event.get("agent_id", "")
            timestamp = event.get("timestamp", "")

            agents_set.add(agent_id)
            timestamps.append(timestamp)

            # Build description
            description = self._build_description(event)

            # Collect assumption IDs used
            assumptions_used = self._collect_assumptions(event)

            # Oracle results for this event
            event_oracle_results = oracle_results_map.get(event_id, [])
            oracles_applied = [r["oracle_id"] for r in event_oracle_results]

            # Drift flags involving this event
            drift_flags = self._collect_drift_flags(event_id, drift_log_map)

            # Flags
            consequential = event_type in self.CONSEQUENTIAL_TYPES
            human_in_loop = event_type in self.HUMAN_IN_LOOP_TYPES

            node = DecisionNode(
                event_id=event_id,
                event_type=event_type,
                timestamp=timestamp,
                agent_id=agent_id,
                description=description,
                assumptions_used=assumptions_used,
                oracles_applied=oracles_applied,
                oracle_results=event_oracle_results,
                drift_flags=drift_flags,
                consequential=consequential,
                human_in_loop=human_in_loop,
            )
            decision_chain.append(node)

        # Compute summary
        summary = self._compute_summary(
            decision_chain, oracle_results_map, drift_log_map
        )

        # Build span
        span_start = min(timestamps) if timestamps else ""
        span_end = max(timestamps) if timestamps else ""

        # Generate audit_id
        import uuid
        audit_id = str(uuid.uuid4())

        return AuditTrail(
            audit_id=audit_id,
            session_id=session_id,
            span={"start": span_start, "end": span_end},
            agents=sorted(agents_set),
            decision_chain=decision_chain,
            summary=summary,
        )

    def get_consequential_events(self, session_id: str) -> list[dict]:
        """Return only consequential events for a session."""
        events = self._query_session_events(session_id)
        return [
            {k: v for k, v in e.items() if k in ("event_id", "event_type", "timestamp", "agent_id")}
            for e in events
            if e.get("event_type") in self.CONSEQUENTIAL_TYPES
        ]

    def diff(self, trail_a: AuditTrail, trail_b: AuditTrail) -> AuditDiff:
        """Compare two audit trails, returning before/after/delta per summary field."""
        ba = trail_a.summary
        bb = trail_b.summary

        before = AuditSummary(
            total_events=ba.total_events,
            drift_events=ba.drift_events,
            circuit_breaker_halts=ba.circuit_breaker_halts,
            human_interventions=ba.human_interventions,
            oracles_passed=ba.oracles_passed,
            oracles_failed=ba.oracles_failed,
        )
        after = AuditSummary(
            total_events=bb.total_events,
            drift_events=bb.drift_events,
            circuit_breaker_halts=bb.circuit_breaker_halts,
            human_interventions=bb.human_interventions,
            oracles_passed=bb.oracles_passed,
            oracles_failed=bb.oracles_failed,
        )
        delta = {
            "total_events": bb.total_events - ba.total_events,
            "drift_events": bb.drift_events - ba.drift_events,
            "circuit_breaker_halts": bb.circuit_breaker_halts - ba.circuit_breaker_halts,
            "human_interventions": bb.human_interventions - ba.human_interventions,
            "oracles_passed": bb.oracles_passed - ba.oracles_passed,
            "oracles_failed": bb.oracles_failed - ba.oracles_failed,
        }
        return AuditDiff(before=before, after=after, delta=delta)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _query_session_events(self, session_id: str) -> list[dict]:
        """Query all events for a session from txtai, ordered by timestamp."""
        results = self.txtai.query_events(
            filters={"session_id": session_id},
            top_k=1000,
        )
        # Sort by timestamp
        results.sort(key=lambda e: e.get("timestamp", ""))
        return results

    def _query_oracle_results(self, session_id: str) -> dict[str, list[dict]]:
        """Query all oracle results for a session from SQLite. Returns {event_id: [results]}."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT o.result_id, o.event_id, o.oracle_id, o.passed, o.deviation, o.evaluated_at
            FROM oracle_results o
            JOIN agent_events_meta m ON o.event_id = m.event_id
            WHERE json_extract(m.data, '$.session_id') = ?
            ORDER BY o.evaluated_at
            """,
            (session_id,),
        ).fetchall()
        conn.close()

        result_map: dict[str, list[dict]] = {}
        for row in rows:
            event_id = row["event_id"]
            result = {
                "oracle_id": row["oracle_id"],
                "passed": bool(row["passed"]),
                "deviation": row["deviation"],
            }
            result_map.setdefault(event_id, []).append(result)
        return result_map

    def _query_drift_logs(self, session_id: str) -> dict[str, list[dict]]:
        """Query all drift logs for a session. Returns {event_id: [drift_records]}."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT d.drift_id, d.current_event_id, d.contradicted_event_id,
                   d.contradiction_type, d.drift_score, d.action_taken
            FROM drift_log d
            JOIN agent_events_meta m ON d.current_event_id = m.event_id
            WHERE json_extract(m.data, '$.session_id') = ?
            """,
            (session_id,),
        ).fetchall()
        conn.close()

        drift_map: dict[str, list[dict]] = {}
        for row in rows:
            event_id = row["current_event_id"]
            drift = {
                "drift_id": row["drift_id"],
                "contradicted_event_id": row["contradicted_event_id"],
                "contradiction_type": row["contradiction_type"],
                "drift_score": row["drift_score"],
                "action_taken": row["action_taken"],
            }
            drift_map.setdefault(event_id, []).append(drift)
        return drift_map

    def _build_description(self, event: dict) -> str:
        """Build a human-readable description for an event."""
        event_type = event.get("event_type", "")

        if event_type == "delegation":
            task = event.get("task", {})
            delegator = event.get("delegator", {})
            delegate = event.get("delegate", {})
            desc = task.get("description", "delegation")
            return f"[delegation] {delegator.get('agent_id','?')} → {delegate.get('agent_id','?')}: {desc}"

        elif event_type == "tool_call":
            tool = event.get("tool", {})
            name = tool.get("name", "?")
            status = event.get("outcome", {}).get("status", "?")
            return f"[tool_call] {name} → {status}"

        elif event_type == "assumption":
            statement = event.get("assumption", {}).get("statement", "")
            cat = event.get("assumption", {}).get("category", "")
            return f"[assumption/{cat}] {statement[:80]}"

        elif event_type == "correction":
            reason = event.get("correction", {}).get("reason", "")
            return f"[correction] {reason[:80]}"

        elif event_type == "drift_detected":
            ds = event.get("drift_score", 0.0)
            ctype = event.get("contradiction_type", "?")
            return f"[drift_detected] {ctype} drift score={ds:.2f}"

        elif event_type == "circuit_breaker":
            gate = event.get("gate", {})
            name = gate.get("name", "?")
            halted = event.get("halt_session", False)
            return f"[circuit_breaker] gate={name} halt={halted}"

        elif event_type == "human_input":
            human = event.get("human", {})
            inp_type = event.get("input", {}).get("type", "?")
            return f"[human_input] {human.get('id','?')} ({inp_type})"

        else:
            return f"[{event_type}]"

    def _collect_assumptions(self, event: dict) -> list[str]:
        """Return list of assumption event IDs referenced in an event."""
        derived = event.get("derived_from", [])
        if isinstance(derived, list):
            return derived
        return []

    def _collect_drift_flags(self, event_id: str, drift_log_map: dict[str, list[dict]]) -> list[str]:
        """Return list of drift IDs for events that match this event_id."""
        return [d["drift_id"] for d in drift_log_map.get(event_id, [])]

    def _compute_summary(
        self,
        decision_chain: list[DecisionNode],
        oracle_results_map: dict[str, list[dict]],
        drift_log_map: dict[str, list[dict]],
    ) -> AuditSummary:
        """Compute aggregate summary stats from the decision chain."""
        total_events = len(decision_chain)
        drift_events = sum(1 for e in decision_chain if e.event_type == "drift_detected")
        circuit_breaker_halts = sum(
            1 for e in decision_chain
            if e.event_type == "circuit_breaker" and e.drift_flags
        )
        human_interventions = sum(1 for e in decision_chain if e.human_in_loop)

        # Oracle pass/fail across all events
        oracles_passed = 0
        oracles_failed = 0
        for results in oracle_results_map.values():
            for r in results:
                if r["passed"]:
                    oracles_passed += 1
                else:
                    oracles_failed += 1

        return AuditSummary(
            total_events=total_events,
            drift_events=drift_events,
            circuit_breaker_halts=circuit_breaker_halts,
            human_interventions=human_interventions,
            oracles_passed=oracles_passed,
            oracles_failed=oracles_failed,
        )


# ---------------------------------------------------------------------------
# Export formats — REQUIREMENTS.md P4.2
# ---------------------------------------------------------------------------

def to_json(trail: AuditTrail) -> str:
    """Serialize an AuditTrail to JSON string."""
    return json.dumps(trail.to_dict(), indent=2, default=str)


def to_markdown(trail: AuditTrail) -> str:
    """Render an AuditTrail as formatted Markdown."""
    lines: list[str] = []

    # Header
    lines.append(f"# Audit Trail — `{trail.session_id}`")
    lines.append("")
    lines.append(f"**Audit ID:** `{trail.audit_id}`")
    lines.append(f"**Span:** {trail.span['start']} → {trail.span['end']}")
    lines.append(f"**Agents:** {', '.join(trail.agents)}")
    lines.append("")

    # Summary box
    s = trail.summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---------|-------|")
    lines.append(f"| Total events | {s.total_events} |")
    lines.append(f"| Drift events | {s.drift_events} |")
    lines.append(f"| Circuit breaker halts | {s.circuit_breaker_halts} |")
    lines.append(f"| Human interventions | {s.human_interventions} |")
    lines.append(f"| Oracles passed | {s.oracles_passed} |")
    lines.append(f"| Oracles failed | {s.oracles_failed} |")
    lines.append("")

    # Decision chain table
    lines.append("## Decision Chain")
    lines.append("")
    lines.append("| # | Event ID | Type | Agent | Consequential | Human | Description |")
    lines.append("|---|----------|------|-------|---------------|-------|-------------|")
    for i, node in enumerate(trail.decision_chain):
        flags = []
        if node.consequential:
            flags.append("✅")
        if node.human_in_loop:
            flags.append("👤")
        flag_str = " ".join(flags)
        desc = node.description.replace("|", "\\|")
        lines.append(
            f"| {i+1} | `{node.event_id}` | {node.event_type} | {node.agent_id} | "
            f"{flag_str} | {'Yes' if node.human_in_loop else '-'} | {desc} |"
        )
    lines.append("")

    # Drift events section
    drift_nodes = [n for n in trail.decision_chain if n.event_type == "drift_detected"]
    if drift_nodes:
        lines.append("## Drift Events")
        lines.append("")
        for node in drift_nodes:
            lines.append(f"- **{node.event_id}** ({node.timestamp}): {node.description}")
            if node.drift_flags:
                lines.append(f"  - Drift IDs: {', '.join(node.drift_flags)}")
        lines.append("")

    return "\n".join(lines)


def to_html(trail: AuditTrail) -> str:
    """Render an AuditTrail as a self-contained HTML document."""
    s = trail.summary

    # Build rows for decision chain
    chain_rows = ""
    for i, node in enumerate(trail.decision_chain):
        cls_consequential = " consequential" if node.consequential else ""
        cls_human = " human-in-loop" if node.human_in_loop else ""
        chain_rows += f"""
        <tr class="{'consequential' if node.consequential else ''}{' human-in-loop' if node.human_in_loop else ''}">
          <td>{i+1}</td>
          <td><code>{node.event_id}</code></td>
          <td>{node.event_type}</td>
          <td>{node.agent_id}</td>
          <td>{'Yes' if node.consequential else '-'}</td>
          <td>{'Yes' if node.human_in_loop else '-'}</td>
          <td>{node.description}</td>
        </tr>"""

    drift_rows = ""
    for node in trail.decision_chain:
        if node.event_type == "drift_detected":
            drift_rows += f"""
        <li><code>{node.event_id}</code>: {node.description}</li>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Audit Trail — {trail.session_id}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 2rem; background: #f9f9f9; }}
    .container {{ max-width: 1100px; margin: 0 auto; background: white; border-radius: 8px; padding: 2rem; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    h1 {{ color: #1a1a2e; border-bottom: 2px solid #e2e8f0; padding-bottom: 0.5rem; }}
    h2 {{ color: #2d3748; margin-top: 2rem; }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
    .meta-card {{ background: #f7fafc; border-radius: 6px; padding: 1rem; }}
    .meta-card .label {{ font-size: 0.75rem; color: #718096; text-transform: uppercase; letter-spacing: 0.05em; }}
    .meta-card .value {{ font-size: 1.25rem; font-weight: 600; color: #2d3748; margin-top: 0.25rem; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
    th {{ text-align: left; background: #edf2f7; padding: 0.6rem 0.75rem; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; color: #4a5568; }}
    td {{ padding: 0.6rem 0.75rem; border-bottom: 1px solid #e2e8f0; font-size: 0.875rem; }}
    tr.consequential td {{ background: #fffbeb; }}
    tr.human-in-loop td {{ background: #eff6ff; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ background: #fff5f5; border-left: 3px solid #fc8181; padding: 0.5rem 1rem; margin-bottom: 0.5rem; border-radius: 4px; font-size: 0.875rem; }}
    .badge {{ display: inline-block; background: #e2e8f0; border-radius: 9999px; padding: 0.1rem 0.5rem; font-size: 0.7rem; margin-left: 0.25rem; }}
  </style>
</head>
<body>
<div class="container">
  <h1>Audit Trail</h1>
  <div class="meta">
    <div class="meta-card"><div class="label">Session</div><div class="value"><code>{trail.session_id}</code></div></div>
    <div class="meta-card"><div class="label">Audit ID</div><div class="value"><code>{trail.audit_id[:8]}…</code></div></div>
    <div class="meta-card"><div class="label">Span</div><div class="value">{trail.span['start'][:19]} → {trail.span['end'][:19]}</div></div>
    <div class="meta-card"><div class="label">Agents</div><div class="value">{', '.join(trail.agents)}</div></div>
  </div>

  <h2>Summary</h2>
  <table>
    <thead><tr><th>Metric</th><th>Value</th></tr></thead>
    <tbody>
      <tr><td>Total events</td><td>{s.total_events}</td></tr>
      <tr><td>Drift events</td><td>{s.drift_events}</td></tr>
      <tr><td>Circuit breaker halts</td><td>{s.circuit_breaker_halts}</td></tr>
      <tr><td>Human interventions</td><td>{s.human_interventions}</td></tr>
      <tr><td>Oracles passed</td><td>{s.oracles_passed}</td></tr>
      <tr><td>Oracles failed</td><td>{s.oracles_failed}</td></tr>
    </tbody>
  </table>

  <h2>Decision Chain</h2>
  <table>
    <thead><tr><th>#</th><th>Event ID</th><th>Type</th><th>Agent</th><th>Consequential</th><th>Human</th><th>Description</th></tr></thead>
    <tbody>{chain_rows}
    </tbody>
  </table>

  {"<h2>Drift Events</h2><ul>" + drift_rows + "</ul>" if drift_rows else ""}
</div>
</body>
</html>"""
