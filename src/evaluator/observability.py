"""Structured observability for the AIE — metric snapshots and queries.

Provides a query layer on top of the existing AIE event stream so you can
answer questions like:
  - Is drift frequency increasing?
  - Which assumption categories keep getting corrected?
  - How many circuit breakers have fired today?

Stores metric snapshots in SQLite (evaluator/data/aie_meta.db) so you don't
need to re-parse JSONL files on every query.

Usage:
  # Snapshots are captured automatically by the logger every 5 minutes.
  # Query the current dashboard:

  from evaluator.observability import get_summary
  summary = await get_summary(buckets=["6h", "24h", "7d"])
  print(summary["drift_rate_6h"], summary["total_events_24h"])

  # Manually trigger a snapshot:
  from evaluator.observability import capture_snapshot
  await capture_snapshot()

  # Discord summary (returns formatted string):
  from evaluator.observability import get_discord_summary
  msg = await get_discord_summary()
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from . import db as _db

# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL,          -- ISO-8601
    window_label    TEXT NOT NULL,          -- e.g. "6h", "24h", "7d"
    total_events    INTEGER DEFAULT 0,
    events_by_type  TEXT,                   -- JSON: {event_type: count}
    total_sessions  INTEGER DEFAULT 0,
    total_drifts    INTEGER DEFAULT 0,
    total_oracle_evals  INTEGER DEFAULT 0,
    oracle_pass_rate    REAL DEFAULT 0.0,   -- 0.0-1.0
    total_circuit_breakers INTEGER DEFAULT 0,
    halt_count      INTEGER DEFAULT 0,
    top_assumption_categories TEXT,           -- JSON: [category]
    avg_drift_score REAL DEFAULT 0.0,
    UNIQUE(captured_at, window_label)
);
"""


async def init_observability_db(db_path: str | None = None) -> None:
    """Ensure the metric_snapshots table exists."""
    path = db_path or _db.DEFAULT_DB_PATH
    async with aiosqlite.connect(path) as conn:
        await conn.execute(CREATE_TABLE)
        await conn.commit()


# ---------------------------------------------------------------------------
# Snapshot capture
# ---------------------------------------------------------------------------

async def capture_snapshot(db_path: str | None = None) -> dict[str, Any]:
    """
    Capture a metric snapshot for the current moment.

    Counts events from three windows:
      - 6 hours
      - 24 hours
      - 7 days

    Returns the aggregated snapshot dict (all three windows).
    """
    path = db_path or _db.DEFAULT_DB_PATH
    await init_observability_db(path)

    now = datetime.now(timezone.utc)
    windows = {
        "6h": _window_start(now, hours=6),
        "24h": _window_start(now, hours=24),
        "7d": _window_start(now, days=7),
    }

    jsonl_base = str(Path(str(_db.DEFAULT_DB_PATH).replace("/aie_meta.db", "/logs")))
    import json

    snapshots = {}
    for label, start in windows.items():
        start_iso = start.isoformat().replace("+00:00", "Z")

        # Count events from JSONL files in the window
        total_events, events_by_type = _count_jsonl_events(jsonl_base, start_iso, now.isoformat().replace("+00:00", "Z"))

        # Query SQLite for session / drift / oracle / CB stats
        total_sessions, total_drifts, total_oracle, oracle_pass, total_cb, halt_count, avg_drift = \
            await _query_sqlite_stats(path, start_iso, now.isoformat().replace("+00:00", "Z"))

        # Assumption categories (from JSONL)
        categories = _get_assumption_categories(jsonl_base, start_iso, now.isoformat().replace("+00:00", "Z"))

        captured_at = now.isoformat().replace("+00:00", "Z")

        snapshot = {
            "captured_at": captured_at,
            "window_label": label,
            "total_events": total_events,
            "events_by_type": json.dumps(events_by_type),
            "total_sessions": total_sessions,
            "total_drifts": total_drifts,
            "total_oracle_evals": total_oracle,
            "oracle_pass_rate": round(oracle_pass, 3),
            "total_circuit_breakers": total_cb,
            "halt_count": halt_count,
            "top_assumption_categories": json.dumps(categories),
            "avg_drift_score": round(avg_drift, 3),
        }

        # Upsert
        async with aiosqlite.connect(path) as conn:
            await conn.execute(
                """
                INSERT INTO metric_snapshots
                    (captured_at, window_label, total_events, events_by_type,
                     total_sessions, total_drifts, total_oracle_evals,
                     oracle_pass_rate, total_circuit_breakers, halt_count,
                     top_assumption_categories, avg_drift_score)
                VALUES
                    (:captured_at, :window_label, :total_events, :events_by_type,
                     :total_sessions, :total_drifts, :total_oracle_evals,
                     :oracle_pass_rate, :total_circuit_breakers, :halt_count,
                     :top_assumption_categories, :avg_drift_score)
                ON CONFLICT(captured_at, window_label) DO UPDATE SET
                    total_events = excluded.total_events,
                    events_by_type = excluded.events_by_type,
                    total_sessions = excluded.total_sessions,
                    total_drifts = excluded.total_drifts,
                    total_oracle_evals = excluded.total_oracle_evals,
                    oracle_pass_rate = excluded.oracle_pass_rate,
                    total_circuit_breakers = excluded.total_circuit_breakers,
                    halt_count = excluded.halt_count,
                    top_assumption_categories = excluded.top_assumption_categories,
                    avg_drift_score = excluded.avg_drift_score
                """,
                snapshot,
            )
            await conn.commit()

        snapshots[label] = snapshot

    return snapshots


def _window_start(now: datetime, hours: int = 0, days: int = 0) -> datetime:
    from datetime import timedelta
    delta = timedelta(hours=hours, days=days)
    return now - delta


def _count_jsonl_events(
    jsonl_dir: str, start_iso: str, end_iso: str
) -> tuple[int, dict[str, int]]:
    """Count events and event types from JSONL files within a time window."""
    import json
    from pathlib import Path

    total = 0
    by_type: dict[str, int] = {}
    start_dt = _parse_iso(start_iso)
    end_dt = _parse_iso(end_iso)

    log_dir = Path(jsonl_dir)
    if not log_dir.exists():
        return 0, {}

    for log_file in sorted(log_dir.glob("*.jsonl")):
        with open(log_file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                ts = event.get("timestamp", "")
                if not ts:
                    continue
                try:
                    event_dt = _parse_iso(ts)
                except Exception:
                    continue
                if start_dt <= event_dt <= end_dt:
                    total += 1
                    et = event.get("event_type", "unknown")
                    by_type[et] = by_type.get(et, 0) + 1

    return total, by_type


def _get_assumption_categories(
    jsonl_dir: str, start_iso: str, end_iso: str
) -> list[str]:
    """Return unique assumption categories seen in the window."""
    import json
    from pathlib import Path
    from collections import Counter

    cats: Counter[str] = Counter()
    start_dt = _parse_iso(start_iso)
    end_dt = _parse_iso(end_iso)

    log_dir = Path(jsonl_dir)
    if not log_dir.exists():
        return []

    for log_file in sorted(log_dir.glob("*.jsonl")):
        with open(log_file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("event_type") != "assumption":
                    continue
                ts = event.get("timestamp", "")
                if not ts:
                    continue
                try:
                    event_dt = _parse_iso(ts)
                except Exception:
                    continue
                if start_dt <= event_dt <= end_dt:
                    cat = event.get("assumption", {}).get("category", "unknown")
                    if cat:
                        cats[cat] += 1

    return [c for c, _ in sorted(cats.items(), key=lambda x: -x[1])[:5]]


async def _query_sqlite_stats(
    db_path: str, start_iso: str, end_iso: str
) -> tuple[int, int, int, float, int, int, float]:
    """Query SQLite for session/drift/oracle/CB stats in a time window."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row

        # Total sessions
        async with conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE started_at >= ? AND started_at <= ?",
            (start_iso, end_iso),
        ) as cur:
            row = await cur.fetchone()
        total_sessions = row[0] if row else 0

        # Drift stats — count from drift_log directly
        async with conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(drift_score), 0) FROM drift_log WHERE 1=1",
        ) as cur:
            row = await cur.fetchone()
        total_drifts = row[0] if row else 0
        avg_drift = float(row[1]) if row and row[1] else 0.0

        # Oracle evaluations in window
        async with conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(CASE WHEN passed = 1 THEN 1.0 ELSE 0.0 END), 0) "
            "FROM oracle_results WHERE evaluated_at >= ? AND evaluated_at <= ?",
            (start_iso, end_iso),
        ) as cur:
            row3 = await cur.fetchone()
        total_oracle = row3[0] if row3 else 0
        oracle_pass_rate = float(row3[1]) if row3 and row3[1] else 0.0

        # Circuit breakers
        async with conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(halt_session), 0) FROM circuit_breaker_events",
        ) as cur:
            row4 = await cur.fetchone()
        total_cb = row4[0] if row4 else 0
        halt_count = int(row4[1]) if row4 and row4[1] else 0

        return total_sessions, total_drifts, total_oracle, oracle_pass_rate, total_cb, halt_count, avg_drift


def _parse_iso(ts: str) -> datetime:
    """Parse ISO-8601 timestamp, handling Z suffix."""
    ts = ts.replace("Z", "+00:00")
    if "." in ts and "+" not in ts and "-" in ts.split(".")[1][:3]:
        # Handle '2026-04-06T06:32:00.123456+00:00' vs '2026-04-06T06:32:00+00:00'
        pass
    return datetime.fromisoformat(ts.replace("+00:00", ""))


# ---------------------------------------------------------------------------
# Summary queries
# ---------------------------------------------------------------------------

async def get_summary(
    windows: list[str] | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """
    Return the most recent metric snapshot for each requested window.

    Windows: list of "6h", "24h", "7d". Defaults to all three.
    """
    import json

    if windows is None:
        windows = ["6h", "24h", "7d"]

    path = db_path or _db.DEFAULT_DB_PATH
    await init_observability_db(path)

    result: dict[str, Any] = {}
    for label in windows:
        async with aiosqlite.connect(path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT * FROM metric_snapshots
                WHERE window_label = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (label,),
            ) as cur:
                row = await cur.fetchone()
        if row:
            d = dict(row)
            if d.get("events_by_type"):
                d["events_by_type"] = json.loads(d["events_by_type"])
            if d.get("top_assumption_categories"):
                d["top_assumption_categories"] = json.loads(d["top_assumption_categories"])
            result[label] = d
        else:
            result[label] = None

    return result


async def get_latest_snapshot(
    window: str = "24h", db_path: str | None = None
) -> dict[str, Any] | None:
    """Return the most recent snapshot for a window, or None."""
    summaries = await get_summary(windows=[window], db_path=db_path)
    return summaries.get(window)


# ---------------------------------------------------------------------------
# Discord summary formatter
# ---------------------------------------------------------------------------

async def get_discord_summary(db_path: str | None = None) -> str:
    """
    Return a Discord-formatted observability summary string.

    Suitable for posting to #opik on a schedule.
    """
    import json

    summary = await get_summary(windows=["24h", "7d"], db_path=db_path)

    lines = ["**📊 AIE Observability Report**"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"_{now}_\n")

    for label, label_name in [("24h", "Last 24 hours"), ("7d", "Last 7 days")]:
        data = summary.get(label)
        if not data:
            lines.append(f"**{label_name}:** no data")
            continue

        events = data.get("total_events", 0)
        drifts = data.get("total_drifts", 0)
        oracles = data.get("total_oracle_evals", 0)
        pass_rate = data.get("oracle_pass_rate", 0.0)
        cbs = data.get("total_circuit_breakers", 0)
        halts = data.get("halt_count", 0)
        sessions = data.get("total_sessions", 0)
        drift_rate = round(drifts / max(events, 1), 3)
        cats = data.get("top_assumption_categories", [])
        et_map = data.get("events_by_type", {})

        lines.append(f"**{label_name}:**")
        lines.append(f"  • Events: {events} ({sessions} sessions)")
        lines.append(f"  • Drift rate: {drift_rate:.1%} ({drifts} corrections)")
        lines.append(f"  • Oracle pass rate: {pass_rate:.0%} ({oracles} evals)")
        if cbs > 0:
            lines.append(f"  • Circuit breakers: {cbs} total, {halts} halts")
        if cats:
            lines.append(f"  • Top assumption categories: {', '.join(cats[:4])}")
        if et_map:
            top_types = sorted(et_map.items(), key=lambda x: -x[1])[:4]
            type_str = " · ".join(f"{t}:{c}" for t, c in top_types)
            lines.append(f"  • Event types: {type_str}")
        lines.append("")

    # Drift trend — compare 24h vs 7d
    d24 = summary.get("24h", {})
    d7 = summary.get("7d", {})
    if d24 and d7:
        events_24 = d24.get("total_events", 0)
        events_7 = d7.get("total_events", 0)
        if events_7 > 0:
            daily_avg_7 = events_7 / 7
            daily_24 = events_24
            if daily_24 > daily_avg_7 * 1.5:
                lines.append("⚠️ Event volume trending up significantly")
            elif daily_24 < daily_avg_7 * 0.5 and events_24 > 0:
                lines.append("📉 Event volume trending down")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Background snapshot job (called by logger or a separate cron)
# ---------------------------------------------------------------------------

_snapshot_lock = asyncio.Lock()


async def periodic_snapshot(interval_seconds: float = 300) -> None:
    """
    Capture a metric snapshot every `interval_seconds`.

    Run as a background task alongside the logger server.
    """
    while True:
        try:
            async with _snapshot_lock:
                await capture_snapshot()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Periodic snapshot failed: %s", exc)
        await asyncio.sleep(interval_seconds)
