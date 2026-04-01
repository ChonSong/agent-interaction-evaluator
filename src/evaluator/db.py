"""SQLite sidecar for Agent Interaction Evaluator — SPEC.md §6.2.

Provides async persistence for sessions, oracle results, and drift logs.
"""

from __future__ import annotations

import os
from typing import Any

import aiosqlite

#: Default database path; override via AIE_DB_PATH env var
DEFAULT_DB_PATH = os.environ.get(
    "AIE_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "evaluator", "data", "aie_meta.db"),
)


async def init_db(db_path: str | None = None) -> aiosqlite.Connection:
    """
    Initialise the SQLite database, creating tables if they don't exist.

    Tables:
        - sessions
        - oracle_results
        - drift_log
    """
    path = db_path or DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)

    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row

    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id         TEXT PRIMARY KEY,
            agent_id           TEXT,
            channel            TEXT,
            started_at         TEXT,
            ended_at           TEXT,
            event_count        INTEGER DEFAULT 0,
            drift_count        INTEGER DEFAULT 0,
            circuit_breaker_halts INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS oracle_results (
            result_id          TEXT PRIMARY KEY,
            event_id           TEXT,
            oracle_id          TEXT,
            passed             INTEGER,
            deviation          TEXT,
            evaluated_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS drift_log (
            drift_id                    TEXT PRIMARY KEY,
            current_event_id            TEXT,
            contradicted_event_id       TEXT,
            contradiction_type          TEXT,
            drift_score                  REAL,
            action_taken                 TEXT,
            resolved_at                  TEXT
        );
    """)
    await conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Session operations
# ---------------------------------------------------------------------------


async def insert_session(session: dict, db_path: str | None = None) -> None:
    """Upsert a session record."""
    path = db_path or DEFAULT_DB_PATH
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            """
            INSERT INTO sessions
                (session_id, agent_id, channel, started_at, ended_at,
                 event_count, drift_count, circuit_breaker_halts)
            VALUES
                (:session_id, :agent_id, :channel, :started_at, :ended_at,
                 :event_count, :drift_count, :circuit_breaker_halts)
            ON CONFLICT(session_id) DO UPDATE SET
                agent_id          = excluded.agent_id,
                channel           = excluded.channel,
                ended_at          = excluded.ended_at,
                event_count       = excluded.event_count,
                drift_count       = excluded.drift_count,
                circuit_breaker_halts = excluded.circuit_breaker_halts
            """,
            {
                "session_id": session["session_id"],
                "agent_id": session.get("agent_id"),
                "channel": session.get("channel"),
                "started_at": session.get("started_at"),
                "ended_at": session.get("ended_at"),
                "event_count": session.get("event_count", 0),
                "drift_count": session.get("drift_count", 0),
                "circuit_breaker_halts": session.get("circuit_breaker_halts", 0),
            },
        )
        await conn.commit()


async def get_session(session_id: str, db_path: str | None = None) -> dict | None:
    """Fetch a session by session_id. Returns None if not found."""
    path = db_path or DEFAULT_DB_PATH
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Oracle result operations
# ---------------------------------------------------------------------------


async def insert_oracle_result(
    result: dict, db_path: str | None = None
) -> None:
    """Insert an oracle evaluation result."""
    path = db_path or DEFAULT_DB_PATH
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            """
            INSERT INTO oracle_results
                (result_id, event_id, oracle_id, passed, deviation, evaluated_at)
            VALUES
                (:result_id, :event_id, :oracle_id, :passed, :deviation, :evaluated_at)
            """,
            {
                "result_id": result["result_id"],
                "event_id": result["event_id"],
                "oracle_id": result["oracle_id"],
                "passed": 1 if result.get("passed") else 0,
                "deviation": result.get("deviation"),
                "evaluated_at": result["evaluated_at"],
            },
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Drift log operations
# ---------------------------------------------------------------------------


async def insert_drift_log(drift: dict, db_path: str | None = None) -> None:
    """Insert a drift log entry."""
    path = db_path or DEFAULT_DB_PATH
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            """
            INSERT INTO drift_log
                (drift_id, current_event_id, contradicted_event_id,
                 contradiction_type, drift_score, action_taken, resolved_at)
            VALUES
                (:drift_id, :current_event_id, :contradicted_event_id,
                 :contradiction_type, :drift_score, :action_taken, :resolved_at)
            """,
            {
                "drift_id": drift["drift_id"],
                "current_event_id": drift["current_event_id"],
                "contradicted_event_id": drift["contradicted_event_id"],
                "contradiction_type": drift["contradiction_type"],
                "drift_score": drift["drift_score"],
                "action_taken": drift["action_taken"],
                "resolved_at": drift.get("resolved_at"),
            },
        )
        await conn.commit()


async def get_open_drifts(db_path: str | None = None) -> list[dict]:
    """Return all unresolved drifts (resolved_at IS NULL)."""
    path = db_path or DEFAULT_DB_PATH
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM drift_log WHERE resolved_at IS NULL ORDER BY drift_id"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def resolve_drift(drift_id: str, db_path: str | None = None) -> None:
    """Mark a drift as resolved by setting resolved_at to current UTC time."""
    from datetime import datetime, timezone

    path = db_path or DEFAULT_DB_PATH
    resolved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "UPDATE drift_log SET resolved_at = ? WHERE drift_id = ?",
            (resolved_at, drift_id),
        )
        await conn.commit()
