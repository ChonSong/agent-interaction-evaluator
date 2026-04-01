"""Tests for SQLite sidecar — REQUIREMENTS.md P1.4."""

import sys
import os
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest
import aiosqlite

from evaluator import db as db_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db_path():
    """Provide a temporary database path that is cleaned up after each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@pytest.fixture
async def init_db(tmp_db_path):
    """Initialise a test database."""
    conn = await db_mod.init_db(tmp_db_path)
    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# Test init_db
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db_path):
    conn = await db_mod.init_db(tmp_db_path)
    await conn.close()

    # Verify tables exist
    async with aiosqlite.connect(tmp_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            rows = await cur.fetchall()
        table_names = {r["name"] for r in rows}

    assert "sessions" in table_names
    assert "oracle_results" in table_names
    assert "drift_log" in table_names


# ---------------------------------------------------------------------------
# Test insert_session + get_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_and_get_session_roundtrip(tmp_db_path):
    await db_mod.init_db(tmp_db_path)

    session = {
        "session_id": "session-123",
        "agent_id": "agent-codi",
        "channel": "slack",
        "started_at": "2026-04-01T10:00:00Z",
        "ended_at": None,
        "event_count": 5,
        "drift_count": 1,
        "circuit_breaker_halts": 0,
    }

    await db_mod.insert_session(session, tmp_db_path)
    result = await db_mod.get_session("session-123", tmp_db_path)

    assert result is not None
    assert result["session_id"] == "session-123"
    assert result["agent_id"] == "agent-codi"
    assert result["channel"] == "slack"
    assert result["event_count"] == 5
    assert result["drift_count"] == 1


@pytest.mark.asyncio
async def test_get_session_not_found(tmp_db_path):
    await db_mod.init_db(tmp_db_path)
    result = await db_mod.get_session("nonexistent-session", tmp_db_path)
    assert result is None


@pytest.mark.asyncio
async def test_insert_session_upserts(tmp_db_path):
    await db_mod.init_db(tmp_db_path)

    session1 = {
        "session_id": "session-upsert",
        "agent_id": "agent-1",
        "channel": "test",
        "started_at": "2026-04-01T10:00:00Z",
        "event_count": 10,
        "drift_count": 2,
        "circuit_breaker_halts": 0,
    }
    await db_mod.insert_session(session1, tmp_db_path)

    # Insert same session with updated counts
    session2 = {
        "session_id": "session-upsert",
        "agent_id": "agent-1",
        "channel": "test",
        "event_count": 15,
        "drift_count": 3,
        "circuit_breaker_halts": 1,
    }
    await db_mod.insert_session(session2, tmp_db_path)

    result = await db_mod.get_session("session-upsert", tmp_db_path)
    assert result["event_count"] == 15
    assert result["drift_count"] == 3
    assert result["circuit_breaker_halts"] == 1


# ---------------------------------------------------------------------------
# Test insert_oracle_result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_and_get_oracle_result(tmp_db_path):
    await db_mod.init_db(tmp_db_path)

    result = {
        "result_id": "result-001",
        "event_id": "event-abc",
        "oracle_id": "no_empty_context",
        "passed": True,
        "deviation": None,
        "evaluated_at": "2026-04-01T10:30:00Z",
    }

    await db_mod.insert_oracle_result(result, tmp_db_path)

    async with aiosqlite.connect(tmp_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM oracle_results WHERE result_id = ?", ("result-001",)
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row["event_id"] == "event-abc"
    assert row["oracle_id"] == "no_empty_context"
    assert row["passed"] == 1  # stored as integer
    assert row["deviation"] is None


@pytest.mark.asyncio
async def test_insert_oracle_result_failed(tmp_db_path):
    await db_mod.init_db(tmp_db_path)

    result = {
        "result_id": "result-002",
        "event_id": "event-def",
        "oracle_id": "no_confidence_zero",
        "passed": False,
        "deviation": "confidence was 0.0",
        "evaluated_at": "2026-04-01T11:00:00Z",
    }

    await db_mod.insert_oracle_result(result, tmp_db_path)

    async with aiosqlite.connect(tmp_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM oracle_results WHERE result_id = ?", ("result-002",)
        ) as cur:
            row = await cur.fetchone()

    assert row["passed"] == 0
    assert row["deviation"] == "confidence was 0.0"


# ---------------------------------------------------------------------------
# Test drift log — insert, get_open_drifts, resolve_drift
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_and_get_open_drifts(tmp_db_path):
    await db_mod.init_db(tmp_db_path)

    drift1 = {
        "drift_id": "drift-001",
        "current_event_id": "event-10",
        "contradicted_event_id": "event-5",
        "contradiction_type": "semantic",
        "drift_score": 0.87,
        "action_taken": "flagged",
        "resolved_at": None,
    }
    drift2 = {
        "drift_id": "drift-002",
        "current_event_id": "event-20",
        "contradicted_event_id": "event-15",
        "contradiction_type": "direct",
        "drift_score": 0.95,
        "action_taken": "halted",
        "resolved_at": None,
    }
    # Resolved drift — should NOT appear in get_open_drifts
    drift3 = {
        "drift_id": "drift-003",
        "current_event_id": "event-30",
        "contradicted_event_id": "event-25",
        "contradiction_type": "implicit",
        "drift_score": 0.5,
        "action_taken": "flagged",
        "resolved_at": "2026-04-01T12:00:00Z",
    }

    await db_mod.insert_drift_log(drift1, tmp_db_path)
    await db_mod.insert_drift_log(drift2, tmp_db_path)
    await db_mod.insert_drift_log(drift3, tmp_db_path)

    open_drifts = await db_mod.get_open_drifts(tmp_db_path)
    assert len(open_drifts) == 2
    drift_ids = {d["drift_id"] for d in open_drifts}
    assert "drift-001" in drift_ids
    assert "drift-002" in drift_ids
    assert "drift-003" not in drift_ids  # resolved


@pytest.mark.asyncio
async def test_resolve_drift(tmp_db_path):
    await db_mod.init_db(tmp_db_path)

    drift = {
        "drift_id": "drift-resolve-me",
        "current_event_id": "event-100",
        "contradicted_event_id": "event-99",
        "contradiction_type": "semantic",
        "drift_score": 0.78,
        "action_taken": "flagged",
        "resolved_at": None,
    }
    await db_mod.insert_drift_log(drift, tmp_db_path)

    # Verify it's open
    open_before = await db_mod.get_open_drifts(tmp_db_path)
    assert any(d["drift_id"] == "drift-resolve-me" for d in open_before)

    # Resolve it
    await db_mod.resolve_drift("drift-resolve-me", tmp_db_path)

    # Verify it's now resolved
    open_after = await db_mod.get_open_drifts(tmp_db_path)
    assert not any(d["drift_id"] == "drift-resolve-me" for d in open_after)

    # Verify resolved_at is set
    async with aiosqlite.connect(tmp_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT resolved_at FROM drift_log WHERE drift_id = ?",
            ("drift-resolve-me",)
        ) as cur:
            row = await cur.fetchone()
    assert row["resolved_at"] is not None
    assert row["resolved_at"] != ""


# ---------------------------------------------------------------------------
# Test db_path configurable via env var
# ---------------------------------------------------------------------------

def test_default_db_path_uses_env():
    # Reset the cached default
    # The default should be from AIE_DB_PATH env var if set
    original = os.environ.get("AIE_DB_PATH")

    os.environ["AIE_DB_PATH"] = "/tmp/test_aie.db"
    # Re-import to pick up the env var
    import importlib
    importlib.reload(db_mod)

    # Note: The default path is computed at module load time via os.environ.get
    # so after reloading it should use the env var
    assert db_mod.DEFAULT_DB_PATH == "/tmp/test_aie.db"

    if original is not None:
        os.environ["AIE_DB_PATH"] = original
    else:
        del os.environ["AIE_DB_PATH"]
    importlib.reload(db_mod)
