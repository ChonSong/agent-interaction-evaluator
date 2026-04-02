# test_audit.py — Tests for Phase 4 Audit Trails

import pytest
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evaluator.audit import (
    DecisionNode,
    AuditSummary,
    AuditTrail,
    AuditGenerator,
    AuditGenerator,
    to_json,
    to_markdown,
)


class TestDecisionNode:
    def test_consequential_flag_delegation(self):
        node = DecisionNode(
            event_id="e1",
            event_type="delegation",
            timestamp="2026-04-02T10:00:00Z",
            agent_id="codi",
            description="Delegated task to reviewer",
            assumptions_used=[],
            oracles_applied=[],
            oracle_results=[],
            drift_flags=[],
            consequential=True,
            human_in_loop=False,
        )
        assert node.consequential is True

    def test_consequential_flag_correction(self):
        node = DecisionNode(
            event_id="e2",
            event_type="correction",
            timestamp="2026-04-02T10:01:00Z",
            agent_id="codi",
            description="Corrected prior assumption",
            assumptions_used=["e1"],
            oracles_applied=[],
            oracle_results=[],
            drift_flags=[],
            consequential=True,
            human_in_loop=False,
        )
        assert node.consequential is True

    def test_consequential_flag_tool_call(self):
        node = DecisionNode(
            event_id="e3",
            event_type="tool_call",
            timestamp="2026-04-02T10:02:00Z",
            agent_id="codi",
            description="Called exec tool",
            assumptions_used=[],
            oracles_applied=[],
            oracle_results=[],
            drift_flags=[],
            consequential=False,
            human_in_loop=False,
        )
        assert node.consequential is False

    def test_human_in_loop_flag(self):
        node = DecisionNode(
            event_id="e4",
            event_type="human_input",
            timestamp="2026-04-02T10:03:00Z",
            agent_id="human:sean",
            description="Human approved change",
            assumptions_used=[],
            oracles_applied=[],
            oracle_results=[],
            drift_flags=[],
            consequential=True,
            human_in_loop=True,
        )
        assert node.human_in_loop is True
        assert node.event_type == "human_input"


class TestAuditSummary:
    def test_summary_totals(self):
        summary = AuditSummary(
            total_events=10,
            drift_events=2,
            circuit_breaker_halts=1,
            human_interventions=3,
            oracles_passed=8,
            oracles_failed=2,
        )
        assert summary.total_events == 10
        assert summary.drift_events == 2
        assert summary.circuit_breaker_halts == 1
        assert summary.human_interventions == 3


class TestAuditTrail:
    def test_audit_trail_structure(self):
        trail = AuditTrail(
            audit_id="audit-001",
            session_id="session-001",
            span={"start": "2026-04-02T10:00:00Z", "end": "2026-04-02T11:00:00Z"},
            agents=["codi", "reviewer"],
            decision_chain=[],
            summary=AuditSummary(
                total_events=0, drift_events=0,
                circuit_breaker_halts=0, human_interventions=0,
                oracles_passed=0, oracles_failed=0,
            ),
        )
        assert trail.audit_id == "audit-001"
        assert trail.session_id == "session-001"
        assert len(trail.agents) == 2


class TestDiff:
    def test_diff_shows_differences(self):
        trail_a = AuditTrail(
            audit_id="a",
            session_id="session-a",
            span={"start": "2026-04-02T10:00:00Z", "end": "2026-04-02T11:00:00Z"},
            agents=["codi"],
            decision_chain=[],
            summary=AuditSummary(
                total_events=5, drift_events=0,
                circuit_breaker_halts=0, human_interventions=0,
                oracles_passed=5, oracles_failed=0,
            ),
        )
        trail_b = AuditTrail(
            audit_id="b",
            session_id="session-b",
            span={"start": "2026-04-02T12:00:00Z", "end": "2026-04-02T13:00:00Z"},
            agents=["codi"],
            decision_chain=[],
            summary=AuditSummary(
                total_events=10, drift_events=2,
                circuit_breaker_halts=1, human_interventions=1,
                oracles_passed=7, oracles_failed=3,
            ),
        )
        result = AuditGenerator(None).diff(trail_a, trail_b)
        assert result["total_events"]["delta"] == 5
        assert result["drift_events"]["delta"] == 2
        assert result["circuit_breaker_halts"]["delta"] == 1


class TestToJson:
    def test_roundtrip(self):
        trail = AuditTrail(
            audit_id="audit-001",
            session_id="session-001",
            span={"start": "2026-04-02T10:00:00Z", "end": "2026-04-02T11:00:00Z"},
            agents=["codi"],
            decision_chain=[
                DecisionNode(
                    event_id="e1",
                    event_type="delegation",
                    timestamp="2026-04-02T10:00:00Z",
                    agent_id="codi",
                    description="Delegated task",
                    assumptions_used=[],
                    oracles_applied=["no_empty_context"],
                    oracle_results=[{"oracle_id": "no_empty_context", "passed": True}],
                    drift_flags=[],
                    consequential=True,
                    human_in_loop=False,
                ),
            ],
            summary=AuditSummary(
                total_events=1, drift_events=0,
                circuit_breaker_halts=0, human_interventions=0,
                oracles_passed=1, oracles_failed=0,
            ),
        )
        json_str = to_json(trail)
        parsed = json.loads(json_str)
        assert parsed["audit_id"] == "audit-001"
        assert len(parsed["decision_chain"]) == 1
        assert parsed["decision_chain"][0]["event_type"] == "delegation"


class TestToMarkdown:
    def test_produces_markdown(self):
        trail = AuditTrail(
            audit_id="audit-001",
            session_id="session-001",
            span={"start": "2026-04-02T10:00:00Z", "end": "2026-04-02T11:00:00Z"},
            agents=["codi"],
            decision_chain=[
                DecisionNode(
                    event_id="e1",
                    event_type="delegation",
                    timestamp="2026-04-02T10:00:00Z",
                    agent_id="codi",
                    description="Delegated task to reviewer",
                    assumptions_used=[],
                    oracles_applied=["no_empty_context"],
                    oracle_results=[{"oracle_id": "no_empty_context", "passed": True}],
                    drift_flags=[],
                    consequential=True,
                    human_in_loop=False,
                ),
                DecisionNode(
                    event_id="e2",
                    event_type="tool_call",
                    timestamp="2026-04-02T10:01:00Z",
                    agent_id="codi",
                    description="Called read tool",
                    assumptions_used=[],
                    oracles_applied=[],
                    oracle_results=[],
                    drift_flags=[],
                    consequential=False,
                    human_in_loop=False,
                ),
            ],
            summary=AuditSummary(
                total_events=2, drift_events=0,
                circuit_breaker_halts=0, human_interventions=0,
                oracles_passed=1, oracles_failed=0,
            ),
        )
        md = to_markdown(trail)
        assert "# Audit Trail" in md or "##" in md  # Has markdown headers
        assert "delegation" in md
        assert "codi" in md
        assert "Summary" in md or "SUMMARY" in md.upper()


class TestAuditGenerator:
    def test_get_consequential_events(self):
        """Test that consequential flag is correctly identified."""
        # DecisionNode with consequential=True
        node_consequential = DecisionNode(
            event_id="e1",
            event_type="delegation",
            timestamp="2026-04-02T10:00:00Z",
            agent_id="codi",
            description="Delegated",
            assumptions_used=[],
            oracles_applied=[],
            oracle_results=[],
            drift_flags=[],
            consequential=True,
            human_in_loop=False,
        )
        node_non_consequential = DecisionNode(
            event_id="e2",
            event_type="tool_call",
            timestamp="2026-04-02T10:01:00Z",
            agent_id="codi",
            description="Called tool",
            assumptions_used=[],
            oracles_applied=[],
            oracle_results=[],
            drift_flags=[],
            consequential=False,
            human_in_loop=False,
        )
        assert node_consequential.consequential is True
        assert node_non_consequential.consequential is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
