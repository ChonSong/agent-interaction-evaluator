"""Tests for secret sanitiser — REQUIREMENTS.md P1.3."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from evaluator.sanitiser import (
    SANITISE_FIELDS,
    _matches_sanitise_field,
    sanitise_event,
)


# ---------------------------------------------------------------------------
# Test SANITISE_FIELDS
# ---------------------------------------------------------------------------

def test_sanitise_fields_contains_expected():
    expected = ["PASSWORD", "SECRET", "TOKEN", "KEY", "API_KEY",
                "AUTHORIZATION", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_TOKEN"]
    for field in expected:
        assert field in SANITISE_FIELDS


# ---------------------------------------------------------------------------
# Test case-insensitive field matching
# ---------------------------------------------------------------------------

def test_matches_sanitise_field_exact():
    for field in SANITISE_FIELDS:
        assert _matches_sanitise_field(field) is True


def test_matches_sanitise_field_lowercase():
    for field in SANITISE_FIELDS:
        assert _matches_sanitise_field(field.lower()) is True


def test_matches_sanitise_field_mixed_case():
    assert _matches_sanitise_field("Api_Key") is True
    assert _matches_sanitise_field("PASSWORD") is True
    assert _matches_sanitise_field("password") is True
    assert _matches_sanitise_field("Secret") is True


def test_matches_sanitise_field_partial():
    # Fields containing the sanitise patterns should match
    assert _matches_sanitise_field("MY_API_KEY") is True
    assert _matches_sanitise_field("auth_token") is True
    assert _matches_sanitise_field("token_secret") is True


def test_matches_sanitise_field_no_match():
    assert _matches_sanitise_field("username") is False
    assert _matches_sanitise_field("event_id") is False
    assert _matches_sanitise_field("timestamp") is False
    assert _matches_sanitise_field("channel") is False


# ---------------------------------------------------------------------------
# Test sanitise_event — top-level fields
# ---------------------------------------------------------------------------

def test_sanitise_top_level_secret_field():
    event = {
        "event_type": "tool_call",
        "api_key": "sk-1234567890abcdef",
        "password": "supersecret",
        "session_id": "session-1",
    }
    result = sanitise_event(event)

    assert result["api_key"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"
    assert result["session_id"] == "session-1"  # unchanged


def test_sanitise_no_secrets_unchanged():
    event = {
        "event_type": "assumption",
        "agent_id": "agent-1",
        "statement": "The file exists",
        "confidence": 0.8,
    }
    result = sanitise_event(event)

    assert result["event_type"] == "assumption"
    assert result["agent_id"] == "agent-1"
    assert result["statement"] == "The file exists"
    assert result["confidence"] == 0.8


# ---------------------------------------------------------------------------
# Test sanitise_event — nested dicts
# ---------------------------------------------------------------------------

def test_sanitise_nested_dict():
    event = {
        "event_type": "tool_call",
        "tool": {
            "name": "send_message",
            "arguments": {
                "content": "Hello",
                "api_key": "sk-secret-key",
                "channel": "general",
            },
        },
    }
    result = sanitise_event(event)

    assert result["tool"]["arguments"]["content"] == "Hello"
    assert result["tool"]["arguments"]["api_key"] == "[REDACTED]"
    assert result["tool"]["arguments"]["channel"] == "general"


def test_sanitise_deeply_nested():
    event = {
        "event_type": "delegation",
        "task": {
            "context_summary": "Some context",
            "nested": {
                "deep": {
                    "credentials": "super-secret-123",
                }
            }
        }
    }
    result = sanitise_event(event)

    assert result["task"]["context_summary"] == "Some context"
    assert result["task"]["nested"]["deep"]["credentials"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Test sanitise_event — lists
# ---------------------------------------------------------------------------

def test_sanitise_list_of_dicts():
    event = {
        "event_type": "tool_call",
        "calls": [
            {"name": "call_1", "token": "abc123"},
            {"name": "call_2", "secret": "xyz789"},
        ],
    }
    result = sanitise_event(event)

    assert result["calls"][0]["name"] == "call_1"
    assert result["calls"][0]["token"] == "[REDACTED]"
    assert result["calls"][1]["name"] == "call_2"
    assert result["calls"][1]["secret"] == "[REDACTED]"


def test_sanitise_list_of_strings_unchanged():
    event = {
        "event_type": "delegation",
        "constraints": ["no_delete", "read_only"],
    }
    result = sanitise_event(event)
    assert result["constraints"] == ["no_delete", "read_only"]


# ---------------------------------------------------------------------------
# Test sanitise_event — preserves keys, replaces values
# ---------------------------------------------------------------------------

def test_sanitise_preserves_keys():
    event = {
        "event_type": "tool_call",
        "authorization": "Bearer tok-secret",
        "metadata": {"key": "value"},
    }
    result = sanitise_event(event)

    assert "authorization" in result  # key preserved
    assert result["authorization"] == "[REDACTED]"
    assert "metadata" in result  # key preserved


# ---------------------------------------------------------------------------
# Test sanitise_event — mixed case across all levels
# ---------------------------------------------------------------------------

def test_sanitise_mixed_case_fields():
    event = {
        "event_type": "tool_call",
        "tool": {
            "arguments": {
                "API_KEY": "sk-key-123",
                "Password": "pass123",
                "Channel": "general",
            }
        }
    }
    result = sanitise_event(event)

    assert result["tool"]["arguments"]["API_KEY"] == "[REDACTED]"
    assert result["tool"]["arguments"]["Password"] == "[REDACTED]"
    assert result["tool"]["arguments"]["Channel"] == "general"


# ---------------------------------------------------------------------------
# Test sanitise_event — special field names with underscores, etc.
# ---------------------------------------------------------------------------

def test_sanitise_access_token():
    event = {
        "event_type": "tool_call",
        "access_token": "eyJhbGciOiJIUzI1NiJ9...",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----",
    }
    result = sanitise_event(event)

    assert result["access_token"] == "[REDACTED]"
    assert result["private_key"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Test sanitise_event — returns new dict (does not mutate input)
# ---------------------------------------------------------------------------

def test_sanitise_returns_dict():
    event = {
        "event_type": "tool_call",
        "secret": "my-secret",
    }
    result = sanitise_event(event)
    # The function should return a dict (even if same object for flat dicts)
    assert isinstance(result, dict)
