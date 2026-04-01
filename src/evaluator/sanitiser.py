"""Secret sanitiser for Agent Interaction Evaluator events.

Replaces matching field values with [REDACTED] without removing keys.
Recursive, case-insensitive field matching.
"""

from __future__ import annotations

import re

# Fields that should be sanitised — matched case-insensitively
SANITISE_FIELDS: list[str] = [
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "KEY",
    "API_KEY",
    "AUTHORIZATION",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "ACCESS_TOKEN",
]

_REDACTED = "[REDACTED]"


def sanitise_event(event: dict) -> dict:
    """
    Recursively sanitise an event dict in-place, replacing values of
    matching secret fields with [REDACTED].

    Matching is:
    - Case-insensitive (PASSWORD matches password, Password, etc.)
    - Field-name includes check (api_key matches fields containing "api_key")
    - Recursive into nested dicts and lists of dicts

    Keys are preserved; only values are replaced.
    """
    return _sanitise_value(event)


def _sanitise_value(value):
    """Recursively sanitise a value, handling dicts, lists, and primitives."""
    if isinstance(value, dict):
        return _sanitise_dict(value)
    elif isinstance(value, list):
        return [_sanitise_value(item) for item in value]
    else:
        return value


def _sanitise_dict(d: dict) -> dict:
    """Sanitise a dict in-place, returning the same dict object."""
    for key, value in d.items():
        if _matches_sanitise_field(key):
            # Replace value but keep the key
            d[key] = _REDACTED
        elif isinstance(value, dict):
            _sanitise_dict(value)
        elif isinstance(value, list):
            d[key] = [_sanitise_value(item) for item in value]
        # else: primitive, leave unchanged
    return d


def _matches_sanitise_field(field_name: str) -> bool:
    """
    Return True if field_name matches any SANITISE_FIELDS entry.
    Matching is case-insensitive and checks if the field name (uppercased)
    contains any of the sanitise field names.
    """
    upper = field_name.upper()
    for sf in SANITISE_FIELDS:
        if sf in upper:
            return True
    return False
