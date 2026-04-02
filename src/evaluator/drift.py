"""DriftDetector and aidrift CLI — SPEC.md §5.2, REQUIREMENTS.md P2.2.

Detects assumption drift by comparing current assumption statements against
prior indexed assumptions using semantic similarity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from . import db as db_mod
from . import schema as schema_mod
from . import txtai_client as tx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DriftResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class DriftResult:
    """Result of a drift detection check."""

    event_id: str
    contradicted_event_id: str
    contradiction_type: str  # "direct" | "semantic" | "implicit"
    drift_score: float  # 0.0 to 1.0
    current_statement: str
    prior_statement: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "contradicted_event_id": self.contradicted_event_id,
            "contradiction_type": self.contradiction_type,
            "drift_score": self.drift_score,
            "current_statement": self.current_statement,
            "prior_statement": self.prior_statement,
        }


# ---------------------------------------------------------------------------
# DriftDetector
# ---------------------------------------------------------------------------

class DriftDetector:
    """
    Detects assumption drift by comparing current assumption statements
    against prior indexed assumptions using semantic similarity.

    Drift thresholds (SPEC.md §5.2):
        - similarity >= 0.95: direct contradiction
        - 0.85 <= similarity < 0.95: semantic contradiction
        - similarity < 0.85: no drift
    """

    SIMILARITY_THRESHOLD = 0.85
    DIRECT_THRESHOLD = 0.95

    def __init__(self, txtai_client: tx.TxtaiClient | None = None) -> None:
        """
        Args:
            txtai_client: TxtaiClient instance. If None, uses the global client.
        """
        self._client = txtai_client

    @property
    def client(self) -> tx.TxtaiClient:
        """Get the txtai client."""
        if self._client is None:
            self._client = tx.get_client()
        return self._client

    def check(self, event_id: str, event: dict) -> DriftResult | None:
        """
        Check a single assumption event for drift.

        Args:
            event_id: The event ID to check.
            event: The full event dict.

        Returns:
            DriftResult if drift was detected, None otherwise.
        """
        # Only check assumption events
        if event.get("event_type") != "assumption":
            return None

        assumption = event.get("assumption", {})
        current_statement = assumption.get("statement", "")
        if not current_statement:
            return None

        session_id = event.get("session_id")

        # Query for prior assumptions with similarity > threshold
        prior_assumptions = self.client.query_assumptions(
            text=current_statement,
            session_id=session_id,
            top_k=20,
        )

        for prior in prior_assumptions:
            prior_event_id = prior.get("event_id")
            prior_statement = prior.get("assumption_statement", "")

            # Don't compare with self
            if prior_event_id == event_id:
                continue

            if not prior_statement:
                continue

            # Compute similarity (query_assumptions already returned scored results)
            similarity = prior.get("score", 0.0)

            if similarity >= self.SIMILARITY_THRESHOLD:
                # Determine contradiction type
                if similarity >= self.DIRECT_THRESHOLD:
                    contradiction_type = "direct"
                else:
                    contradiction_type = "semantic"

                return DriftResult(
                    event_id=event_id,
                    contradicted_event_id=prior_event_id,
                    contradiction_type=contradiction_type,
                    drift_score=float(similarity),
                    current_statement=current_statement,
                    prior_statement=prior_statement,
                )

        return None

    def scan_session(self, session_id: str) -> list[DriftResult]:
        """
        Scan all assumption events in a session for drift.

        Args:
            session_id: The session to scan.

        Returns:
            List of DriftResult for all detected drifts in the session.
        """
        events = self.client.query_events(
            filters={"event_type": "assumption", "session_id": session_id},
            top_k=100,
        )

        results = []
        for event_meta in events:
            event_id = event_meta.get("event_id")
            if not event_id:
                continue

            # Reconstruct a minimal event dict for the check
            event = {
                "event_id": event_id,
                "event_type": "assumption",
                "session_id": session_id,
                "assumption": {
                    "statement": event_meta.get("assumption_statement", ""),
                },
            }

            drift_result = self.check(event_id, event)
            if drift_result:
                results.append(drift_result)

        return results

    def scan_all_active(self, days: int = 7) -> list[DriftResult]:
        """
        Scan all sessions with events in the last N days for drift.

        Args:
            days: Number of days to look back. Default 7.

        Returns:
            List of all DriftResults found across active sessions.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")

        events = self.client.query_events(
            filters={"event_type": "assumption", "since": cutoff_iso},
            top_k=200,
        )

        results = []
        for event_meta in events:
            event_id = event_meta.get("event_id")
            session_id = event_meta.get("session_id")
            if not event_id:
                continue

            event = {
                "event_id": event_id,
                "event_type": "assumption",
                "session_id": session_id,
                "assumption": {
                    "statement": event_meta.get("assumption_statement", ""),
                },
            }

            drift_result = self.check(event_id, event)
            if drift_result:
                results.append(drift_result)

        return results


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "evaluator" / "data"
LOG_DIR = DATA_DIR / "logs"


def _load_event_from_logs(event_id: str) -> dict | None:
    """Load an event from the JSONL log files by event_id."""
    if not LOG_DIR.exists():
        return None

    # Search through recent log files
    log_files = sorted(LOG_DIR.glob("*.jsonl"), reverse=True)[:7]  # Last 7 days

    for log_file in log_files:
        try:
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("event_id") == event_id:
                            return event
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

    return None


async def _get_open_drifts() -> list[dict]:
    """Get all open drifts from the SQLite database."""
    try:
        return await db_mod.get_open_drifts()
    except Exception:
        return []


async def _get_drift_stats() -> dict[str, Any]:
    """Get drift statistics from the database."""
    try:
        import aiosqlite

        db_path = os.environ.get("AIE_DB_PATH")
        conn = await db_mod.init_db(db_path)

        async with conn.execute(
            "SELECT COUNT(*) FROM drift_log WHERE resolved_at IS NULL"
        ) as cur:
            open_row = await cur.fetchone()
        open_count = open_row[0] if open_row else 0

        async with conn.execute(
            "SELECT COUNT(*) FROM drift_log"
        ) as cur:
            total_row = await cur.fetchone()
        total_count = total_row[0] if total_row else 0

        async with conn.execute(
            "SELECT COUNT(*) FROM drift_log WHERE resolved_at IS NOT NULL"
        ) as cur:
            resolved_row = await cur.fetchone()
        resolved_count = resolved_row[0] if resolved_row else 0

        await conn.close()


        # Get contradiction type breakdown for open drifts
        conn2 = await db_mod.init_db(db_path)
        conn2.row_factory = aiosqlite.Row
        type_breakdown = {}
        async with conn2.execute(
            """
            SELECT contradiction_type, COUNT(*) as cnt
            FROM drift_log WHERE resolved_at IS NULL
            GROUP BY contradiction_type
            """
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            type_breakdown[row["contradiction_type"]] = row["cnt"]
        await conn2.close()

        return {
            "total_drifts": total_count,
            "open_drifts": open_count,
            "resolved_drifts": resolved_count,
            "open_by_type": type_breakdown,
        }
    except Exception as exc:
        logger.warning("Failed to get drift stats: %s", exc)
        return {
            "total_drifts": 0,
            "open_drifts": 0,
            "resolved_drifts": 0,
            "open_by_type": {},
        }


# ---------------------------------------------------------------------------
# aidrift CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for aidrift."""
    import aiosqlite

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        _print_help()
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "--help" or cmd == "-h":
        _print_help()
        sys.exit(0)

    if cmd == "check":
        _cmd_check(sys.argv[2:])
    elif cmd == "scan":
        _cmd_scan(sys.argv[2:])
    elif cmd == "report":
        _cmd_report(sys.argv[2:])
    elif cmd == "stats":
        _cmd_stats(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        _print_help()
        sys.exit(1)


def _print_help() -> None:
    """Print aidrift usage."""
    print("""aidrift — Drift Detection CLI

Usage:
  aidrift check <event_id>         Check a specific assumption event for drift
  aidrift scan --session <id>      Scan all events in a session for drift
  aidrift scan --all               Scan all active sessions for drift
  aidrift report                   Print summary of all open drifts as JSON
  aidrift stats                    Print drift statistics as JSON

Examples:
  aidrift check evt-123            Check event evt-123 for drift
  aidrift scan --session sess-1   Scan session sess-1
  aidrift scan --all              Scan all sessions from last 7 days
  aidrift report                  Show all open drifts
  aidrift stats                   Show drift statistics
""")


def _cmd_check(args: list[str]) -> None:
    """Handle 'aidrift check <event_id>'."""
    if len(args) < 1:
        print("Usage: aidrift check <event_id>", file=sys.stderr)
        sys.exit(1)

    event_id = args[0]

    # Load event from logs
    event = _load_event_from_logs(event_id)
    if not event:
        print(f"Event {event_id} not found in logs", file=sys.stderr)
        sys.exit(1)

    # Run drift check
    detector = DriftDetector()
    result = detector.check(event_id, event)

    if result:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(json.dumps({"event_id": event_id, "drift_detected": False}, indent=2))


def _cmd_scan(args: list[str]) -> None:
    """Handle 'aidrift scan --session <id>' or 'aidrift scan --all'."""
    session_id: str | None = None
    scan_all = False

    i = 0
    while i < len(args):
        if args[i] == "--session" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        elif args[i] == "--all":
            scan_all = True
            i += 1
        else:
            print(f"Unknown flag: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if not session_id and not scan_all:
        print("Usage: aidrift scan --session <id>  OR  aidrift scan --all", file=sys.stderr)
        sys.exit(1)

    detector = DriftDetector()

    if scan_all:
        results = detector.scan_all_active()
    else:
        assert session_id is not None
        results = detector.scan_session(session_id)

    output = {
        "scan_type": "all" if scan_all else "session",
        "session_id": session_id,
        "drifts_found": len(results),
        "drifts": [r.to_dict() for r in results],
    }
    print(json.dumps(output, indent=2))


def _cmd_report(args: list[str]) -> None:
    """Handle 'aidrift report'."""
    import aiosqlite

    async def run():
        open_drifts = await db_mod.get_open_drifts()
        print(json.dumps({
            "open_drifts": open_drifts,
            "count": len(open_drifts),
        }, indent=2))

    asyncio.run(run())


def _cmd_stats(args: list[str]) -> None:
    """Handle 'aidrift stats'."""
    async def run():
        stats = await _get_drift_stats()
        print(json.dumps(stats, indent=2))

    asyncio.run(run())


if __name__ == "__main__":
    main()
