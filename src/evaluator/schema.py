"""Event schema for Agent Interaction Evaluator — SPEC.md §3."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema(
    base: dict[str, Any], properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    """Merge base event fields with type-specific properties."""
    merged = {
        **base,
        "properties": {
            **base["properties"],
            **properties,
        },
        "required": list(base["required"]) + [
            r for r in required if r not in base["required"]
        ],
    }
    return merged


# ---------------------------------------------------------------------------
# Base event fields — SPEC.md §3.1
# ---------------------------------------------------------------------------

BASE_EVENT_FIELDS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "event_id": {"type": "string"},
        "event_type": {"type": "string"},
        "timestamp": {"type": "string"},
        "agent_id": {"type": "string"},
        "session_id": {"type": "string"},
        "interaction_context": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "workspace_path": {"type": ["string", "null"]},
                "parent_event_id": {"type": ["string", "null"]},
            },
            "required": ["channel"],
            "additionalProperties": True,
        },
    },
    "required": [
        "schema_version",
        "event_id",
        "event_type",
        "timestamp",
        "agent_id",
        "session_id",
        "interaction_context",
    ],
    "additionalProperties": True,
}

# ---------------------------------------------------------------------------
# Event-type-specific schemas — extend BASE_EVENT_FIELDS per SPEC.md §3.2
# ---------------------------------------------------------------------------

EVENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "delegation": _make_schema(
        base=BASE_EVENT_FIELDS,
        properties={
            "delegator": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["agent_id", "role"],
                "additionalProperties": True,
            },
            "delegate": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["agent_id", "role"],
                "additionalProperties": True,
            },
            "task": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "description": {"type": "string"},
                    "intent": {"type": "string"},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "context_summary": {"type": "string"},
                    "context_fidelity": {"type": "number"},
                    "max_turns": {"type": ["integer", "null"]},
                    "deadline": {"type": ["string", "null"]},
                },
                "required": ["task_id", "description", "intent", "context_summary"],
                "additionalProperties": True,
            },
            "oracle_ref": {"type": ["string", "null"]},
        },
        required=["delegator", "delegate", "task"],
    ),
    "tool_call": _make_schema(
        base=BASE_EVENT_FIELDS,
        properties={
            "tool": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                    "arguments": {"type": "object"},
                    "argument_schema": {"type": ["string", "null"]},
                },
                "required": ["name", "namespace", "arguments"],
                "additionalProperties": True,
            },
            "trigger": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "triggered_by_event_id": {"type": ["string", "null"]},
                },
                "required": ["type"],
                "additionalProperties": True,
            },
            "outcome": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["success", "error", "partial", "unknown"],
                    },
                    "duration_ms": {"type": "integer"},
                    "error_message": {"type": ["string", "null"]},
                    "output_summary": {"type": ["string", "null"]},
                },
                "required": ["status", "duration_ms"],
                "additionalProperties": True,
            },
        },
        required=["tool", "trigger", "outcome"],
    ),
    "assumption": _make_schema(
        base=BASE_EVENT_FIELDS,
        properties={
            "assumption": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "category": {"type": "string"},
                    "confidence": {"type": "number"},
                    "grounded_in": {"type": ["string", "null"]},
                },
                "required": ["statement", "confidence"],
                "additionalProperties": True,
            },
            "derived_from": {
                "type": "array",
                "items": {"type": "string"},
            },
            "oracle_ref": {"type": ["string", "null"]},
        },
        required=["assumption", "derived_from"],
    ),
    "correction": _make_schema(
        base=BASE_EVENT_FIELDS,
        properties={
            "prior_event_id": {"type": "string"},
            "correction": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "prior_statement": {"type": "string"},
                    "revised_statement": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["minor", "moderate", "critical"],
                    },
                },
                "required": ["reason", "prior_statement", "revised_statement", "severity"],
                "additionalProperties": True,
            },
            "downstream_impact": {
                "type": "object",
                "properties": {
                    "events_affected": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "reversible": {"type": "boolean"},
                },
                "additionalProperties": True,
            },
        },
        required=["prior_event_id", "correction", "downstream_impact"],
    ),
    "drift_detected": _make_schema(
        base=BASE_EVENT_FIELDS,
        properties={
            "current_assumption": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "statement": {"type": "string"},
                },
                "required": ["event_id", "statement"],
                "additionalProperties": True,
            },
            "contradicted_by": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "statement": {"type": "string"},
                },
                "required": ["event_id", "statement"],
                "additionalProperties": True,
            },
            "contradiction_type": {
                "type": "string",
                "enum": ["direct", "semantic", "implicit"],
            },
            "drift_score": {"type": "number"},
            "action_taken": {
                "type": "string",
                "enum": ["flagged", "halted", "alerted"],
            },
        },
        required=[
            "current_assumption",
            "contradicted_by",
            "contradiction_type",
            "drift_score",
            "action_taken",
        ],
    ),
    "circuit_breaker": _make_schema(
        base=BASE_EVENT_FIELDS,
        properties={
            "gate": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "threshold": {"type": "string"},
                    "assumptions_violated": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "threshold"],
                "additionalProperties": True,
            },
            "action_blocked": {"type": ["string", "null"]},
            "halt_session": {"type": "boolean"},
            "alert_sent": {"type": "boolean"},
            "audit_ref": {"type": "string"},
        },
        required=["gate", "halt_session", "alert_sent", "audit_ref"],
    ),
    "human_input": _make_schema(
        base=BASE_EVENT_FIELDS,
        properties={
            "human": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["id", "role"],
                "additionalProperties": True,
            },
            "input": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "content": {"type": "string"},
                    "context_summary": {"type": "string"},
                },
                "required": ["type", "content", "context_summary"],
                "additionalProperties": True,
            },
            "impact": {
                "type": "object",
                "properties": {
                    "events_affected": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "session_modified": {"type": "boolean"},
                },
                "required": ["events_affected", "session_modified"],
                "additionalProperties": True,
            },
        },
        required=["human", "input", "impact"],
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_event(event: dict) -> tuple[bool, str | None]:
    """
    Validate an event against its schema.

    Returns:
        (True, None) if valid.
        (False, error_message) if invalid.
    """
    try:
        from jsonschema import Draft7Validator
    except ImportError as exc:
        raise ImportError(
            "jsonschema is required for event validation. "
            "Install with: pip install jsonschema"
        ) from exc

    event_type = event.get("event_type")
    if not event_type:
        return False, "Missing required field: event_type"

    schema = EVENT_SCHEMAS.get(event_type)
    if not schema:
        return False, f"Unknown event_type: {event_type!r}"

    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(event))
    if not errors:
        return True, None

    # Return the first error message
    first = errors[0]
    path = ".".join(str(p) for p in first.path) if first.path else "root"
    return False, f"[{path}] {first.message}"


def get_schema(event_type: str) -> dict[str, Any]:
    """Return the JSON schema for a given event_type."""
    schema = EVENT_SCHEMAS.get(event_type)
    if not schema:
        raise ValueError(f"Unknown event_type: {event_type!r}")
    return schema


def generate_event_id() -> str:
    """Generate a UUID v4 string."""
    return str(uuid.uuid4())


def get_current_timestamp() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
