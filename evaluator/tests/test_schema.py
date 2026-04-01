"""Tests for event schema — REQUIREMENTS.md P1.2."""

import sys
from pathlib import Path

# Ensure src/evaluator is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from evaluator.schema import (
    BASE_EVENT_FIELDS,
    EVENT_SCHEMAS,
    generate_event_id,
    get_current_timestamp,
    get_schema,
    validate_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_base_event(**overrides):
    """Return a minimal valid base event."""
    return {
        "schema_version": "1.0",
        "event_id": generate_event_id(),
        "event_type": "delegation",
        "timestamp": get_current_timestamp(),
        "agent_id": "agent-1",
        "session_id": "session-1",
        "interaction_context": {
            "channel": "test",
            "workspace_path": None,
            "parent_event_id": None,
        },
        **overrides,
    }


# ---------------------------------------------------------------------------
# Test BASE_EVENT_FIELDS
# ---------------------------------------------------------------------------

def test_base_event_fields_has_required_keys():
    assert "properties" in BASE_EVENT_FIELDS
    assert "required" in BASE_EVENT_FIELDS
    assert "schema_version" in BASE_EVENT_FIELDS["required"]
    assert "event_id" in BASE_EVENT_FIELDS["required"]
    assert "event_type" in BASE_EVENT_FIELDS["required"]
    assert "timestamp" in BASE_EVENT_FIELDS["required"]
    assert "agent_id" in BASE_EVENT_FIELDS["required"]
    assert "session_id" in BASE_EVENT_FIELDS["required"]


# ---------------------------------------------------------------------------
# Test generate_event_id
# ---------------------------------------------------------------------------

def test_generate_event_id_is_uuid_v4():
    import uuid

    eid = generate_event_id()
    # Should be a valid UUID v4 string
    parsed = uuid.UUID(eid)
    assert parsed.version == 4


def test_generate_event_id_unique():
    ids = [generate_event_id() for _ in range(100)]
    assert len(set(ids)) == 100


# ---------------------------------------------------------------------------
# Test get_current_timestamp
# ---------------------------------------------------------------------------

def test_get_current_timestamp_is_iso8601():
    from datetime import datetime

    ts = get_current_timestamp()
    # Should end with Z (UTC) or contain +00:00
    assert ts.endswith("Z") or "+00:00" in ts
    # Should be parseable as datetime
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert dt.year == 2026


# ---------------------------------------------------------------------------
# Test get_schema
# ---------------------------------------------------------------------------

def test_get_schema_delegation():
    schema = get_schema("delegation")
    assert "properties" in schema
    assert "delegator" in schema["properties"]
    assert "delegate" in schema["properties"]
    assert "task" in schema["properties"]


def test_get_schema_all_event_types():
    for event_type in ["delegation", "tool_call", "assumption", "correction",
                       "drift_detected", "circuit_breaker", "human_input"]:
        schema = get_schema(event_type)
        assert "properties" in schema
        assert event_type in EVENT_SCHEMAS


def test_get_schema_unknown_event_type_raises():
    with pytest.raises(ValueError, match="Unknown event_type"):
        get_schema("nonexistent")


# ---------------------------------------------------------------------------
# Test validate_event — valid events
# ---------------------------------------------------------------------------

def test_validate_delegation_valid():
    event = make_base_event(
        event_type="delegation",
        delegator={"agent_id": "agent-1", "role": "orchestrator"},
        delegate={"agent_id": "agent-2", "role": "worker"},
        task={
            "task_id": "task-1",
            "description": "Fix the login bug",
            "intent": "Resolve user reported issue",
            "constraints": [],
            "context_summary": "User reported login failure with Google OAuth",
            "context_fidelity": 0.85,
            "max_turns": 10,
            "deadline": None,
        },
        oracle_ref=None,
    )
    valid, err = validate_event(event)
    assert valid is True
    assert err is None


def test_validate_tool_call_valid():
    event = make_base_event(
        event_type="tool_call",
        tool={
            "name": "read_file",
            "namespace": "filesystem",
            "arguments": {"path": "/tmp/test.txt"},
            "argument_schema": None,
        },
        trigger={"type": "user_request", "triggered_by_event_id": None},
        outcome={
            "status": "success",
            "duration_ms": 42,
            "error_message": None,
            "output_summary": "Read 128 bytes",
        },
    )
    valid, err = validate_event(event)
    assert valid is True
    assert err is None


def test_validate_assumption_valid():
    event = make_base_event(
        event_type="assumption",
        assumption={
            "statement": "The file is located in /tmp",
            "category": "filesystem",
            "confidence": 0.7,
            "grounded_in": "prior_read_event",
        },
        derived_from=["event-1", "event-2"],
        oracle_ref=None,
    )
    valid, err = validate_event(event)
    assert valid is True
    assert err is None


def test_validate_correction_valid():
    event = make_base_event(
        event_type="correction",
        prior_event_id="event-5",
        correction={
            "reason": "File location was wrong",
            "prior_statement": "The file is in /tmp",
            "revised_statement": "The file is in /home",
            "severity": "moderate",
        },
        downstream_impact={
            "events_affected": ["event-6", "event-7"],
            "reversible": False,
        },
    )
    valid, err = validate_event(event)
    assert valid is True
    assert err is None


def test_validate_drift_detected_valid():
    event = make_base_event(
        event_type="drift_detected",
        current_assumption={
            "event_id": "event-10",
            "statement": "The API returns JSON",
        },
        contradicted_by={
            "event_id": "event-5",
            "statement": "The API returns XML",
        },
        contradiction_type="semantic",
        drift_score=0.87,
        action_taken="flagged",
    )
    valid, err = validate_event(event)
    assert valid is True
    assert err is None


def test_validate_circuit_breaker_valid():
    event = make_base_event(
        event_type="circuit_breaker",
        gate={
            "name": "critical_drift_gate",
            "threshold": "drift_score >= 0.9",
            "assumptions_violated": ["event-3"],
        },
        action_blocked="send_alert",
        halt_session=True,
        alert_sent=True,
        audit_ref="audit-001",
    )
    valid, err = validate_event(event)
    assert valid is True
    assert err is None


def test_validate_human_input_valid():
    event = make_base_event(
        event_type="human_input",
        human={"id": "human-1", "role": "reviewer"},
        input={
            "type": "correction",
            "content": "The assumption is incorrect",
            "context_summary": "Human reviewed the delegation",
        },
        impact={
            "events_affected": ["event-8"],
            "session_modified": True,
        },
    )
    valid, err = validate_event(event)
    assert valid is True
    assert err is None


# ---------------------------------------------------------------------------
# Test validate_event — invalid events
# ---------------------------------------------------------------------------

def test_validate_missing_event_type():
    event = make_base_event()
    del event["event_type"]
    valid, err = validate_event(event)
    assert valid is False
    assert err is not None
    assert "event_type" in err.lower() or "Missing" in err


def test_validate_missing_required_field():
    event = make_base_event(event_type="delegation")
    # delegation requires delegator, delegate, task
    valid, err = validate_event(event)
    assert valid is False
    assert err is not None


def test_validate_wrong_type():
    event = make_base_event(event_type="delegation")
    event["timestamp"] = 12345  # should be string
    valid, err = validate_event(event)
    assert valid is False
    assert err is not None


def test_validate_unknown_event_type():
    event = make_base_event(event_type="unknown_type")
    valid, err = validate_event(event)
    assert valid is False
    assert "Unknown event_type" in err


def test_validate_nested_missing_field():
    event = make_base_event(
        event_type="delegation",
        delegator={"agent_id": "agent-1", "role": "orchestrator"},
        delegate={"agent_id": "agent-2"},  # missing role
        task={
            "task_id": "task-1",
            "description": "Fix bug",
            "intent": "Resolve",
            # missing context_summary
        },
    )
    valid, err = validate_event(event)
    assert valid is False
    assert err is not None
