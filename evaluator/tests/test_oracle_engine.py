# test_oracle_engine.py — Tests for Phase 3 Oracle Engine

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evaluator.oracle_engine import (
    OracleRegistry,
    Oracle,
    ConditionResult,
    evaluate_conditions,
    apply_actions,
    evaluate_event,
)


class TestConditionEvaluators:
    """Test each condition type."""

    def test_field_required_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="warning",
            conditions=[{"type": "field_required", "field": "task.description"}],
            actions=[],
            metadata={},
        )
        event = {"task": {"description": "do something"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_required_fail(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="warning",
            conditions=[{"type": "field_required", "field": "task.description"}],
            actions=[],
            metadata={},
        )
        event = {"task": {}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is False
        assert "field_required" in result.deviation

    def test_field_not_empty_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_not_empty", "field": "task.context_summary"}],
            actions=[],
            metadata={},
        )
        event = {"task": {"context_summary": "some context here"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_not_empty_fail_empty_string(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_not_empty", "field": "task.context_summary"}],
            actions=[],
            metadata={},
        )
        event = {"task": {"context_summary": ""}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is False

    def test_field_min_length_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="critical",
            conditions=[{"type": "field_min_length", "field": "task.context_summary", "min_length": 20}],
            actions=[],
            metadata={},
        )
        event = {"task": {"context_summary": "this is a valid context summary string"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_min_length_fail(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="critical",
            conditions=[{"type": "field_min_length", "field": "task.context_summary", "min_length": 50}],
            actions=[],
            metadata={},
        )
        event = {"task": {"context_summary": "short"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is False

    def test_field_regex_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_regex", "field": "task.task_id", "pattern": r"^task-\d+$"}],
            actions=[],
            metadata={},
        )
        event = {"task": {"task_id": "task-123"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_regex_fail(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_regex", "field": "task.task_id", "pattern": r"^task-\d+$"}],
            actions=[],
            metadata={},
        )
        event = {"task": {"task_id": "invalid"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is False

    def test_field_eq_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="assumption",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_eq", "field": "assumption.category", "value": "context"}],
            actions=[],
            metadata={},
        )
        event = {"assumption": {"category": "context"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_eq_fail(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="assumption",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_eq", "field": "assumption.category", "value": "context"}],
            actions=[],
            metadata={},
        )
        event = {"assumption": {"category": "capability"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is False

    def test_field_gt_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="warning",
            conditions=[{"type": "field_gt", "field": "task.context_fidelity", "value": 0.5}],
            actions=[],
            metadata={},
        )
        event = {"task": {"context_fidelity": 0.85}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_gt_fail(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="delegation",
            trigger="on_event",
            severity="warning",
            conditions=[{"type": "field_gt", "field": "task.context_fidelity", "value": 0.5}],
            actions=[],
            metadata={},
        )
        event = {"task": {"context_fidelity": 0.3}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is False

    def test_field_lt_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="drift_detected",
            trigger="on_event",
            severity="warning",
            conditions=[{"type": "field_lt", "field": "drift_score", "value": 0.9}],
            actions=[],
            metadata={},
        )
        event = {"drift_score": 0.5}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_in_pass(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="tool_call",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_in", "field": "outcome.status", "values": ["success", "partial"]}],
            actions=[],
            metadata={},
        )
        event = {"outcome": {"status": "success"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is True

    def test_field_in_fail(self):
        oracle = Oracle(
            oracle_id="test",
            name="test",
            description="",
            event_type="tool_call",
            trigger="on_event",
            severity="info",
            conditions=[{"type": "field_in", "field": "outcome.status", "values": ["success", "partial"]}],
            actions=[],
            metadata={},
        )
        event = {"outcome": {"status": "error"}}
        result = evaluate_conditions(oracle, event)
        assert result.passed is False


class TestOracleRegistry:
    """Test OracleRegistry loading and querying."""

    def test_load_oracles(self):
        registry = OracleRegistry()
        # Use the actual oracles directory
        oracles_dir = Path(__file__).parent.parent.parent / "oracles"
        if oracles_dir.exists():
            registry.load(str(oracles_dir))
            assert len(registry.list()) >= 3  # At least the Phase 1 oracles

    def test_get_for_event_type(self):
        registry = OracleRegistry()
        oracles_dir = Path(__file__).parent.parent.parent / "oracles"
        if oracles_dir.exists():
            registry.load(str(oracles_dir))
            delegation_oracles = registry.get_for_event_type("delegation")
            assert len(delegation_oracles) >= 1
            assert all(o.event_type == "delegation" for o in delegation_oracles)

    def test_get_oracle(self):
        registry = OracleRegistry()
        oracles_dir = Path(__file__).parent.parent.parent / "oracles"
        if oracles_dir.exists():
            registry.load(str(oracles_dir))
            oracle = registry.get("no_empty_context")
            if oracle:
                assert oracle.oracle_id == "no_empty_context"
                assert oracle.event_type == "delegation"

    def test_unknown_oracle_returns_none(self):
        registry = OracleRegistry()
        assert registry.get("nonexistent_oracle") is None


class TestEvaluateEvent:
    """Test evaluate_event with real oracles."""

    def test_evaluate_delegation_event_no_context(self):
        """no_empty_context oracle should fail on delegation with empty context."""
        registry = OracleRegistry()
        oracles_dir = Path(__file__).parent.parent.parent / "oracles"
        if oracles_dir.exists():
            registry.load(str(oracles_dir))
            event = {
                "event_type": "delegation",
                "task": {
                    "description": "test",
                    "context_summary": "",  # Empty — should fail
                },
            }
            results = evaluate_event(event, registry)
            failed = [r for r in results if r.passed is False]
            assert len(failed) >= 1

    def test_evaluate_delegation_event_with_context(self):
        """no_empty_context oracle should pass on delegation with proper context."""
        registry = OracleRegistry()
        oracles_dir = Path(__file__).parent.parent.parent / "oracles"
        if oracles_dir.exists():
            registry.load(str(oracles_dir))
            event = {
                "event_type": "delegation",
                "task": {
                    "description": "test",
                    "context_summary": "some valid context that is long enough",
                },
            }
            results = evaluate_event(event, registry)
            no_empty = [r for r in results if r.passed is False]
            # Should have no failures for no_empty_context
            no_empty_ids = [r.oracle_id for r in no_empty]
            if "no_empty_context" in [o.oracle_id for o in registry.list()]:
                assert "no_empty_context" not in no_empty_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
