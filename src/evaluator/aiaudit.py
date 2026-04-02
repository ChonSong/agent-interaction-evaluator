"""aiaudit CLI — Audit trail generation, export, diff, and pruning.

Commands:
    aiaudit trail <session_id>        Generate audit trail for a session
    aiaudit trail --event-id <id>      Trail leading to a specific event
    aiaudit export --format md|json|html --session <id>  Export trail
    aiaudit diff <session_a> <session_b>   Compare two sessions
    aiaudit prune --before YYYY-MM-DD  Delete events before date
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audit as _audit_mod
from . import txtai_client as _tx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prune helpers — operate on SQLite sidecar + txtai metadata
# ---------------------------------------------------------------------------

def _get_aie_db_path() -> str:
    """Resolve path to the AIE metadata SQLite database."""
    return os.environ.get(
        "AIE_DB_PATH",
        str(
            Path(__file__).resolve().parent.parent.parent
            / "evaluator" / "data" / "aie_meta.db"
        ),
    )


def _prune_events_before(before_iso: str) -> dict[str, Any]:
    """
    Delete events and their associated records from the SQLite sidecar
    where event timestamp < before_iso.

    Also removes metadata from the txtai SQLite metadata store.

    Returns a summary dict.
    """
    db_path = _get_aie_db_path()
    txtai_index_path = os.environ.get(
        "TXTAI_INDEX_PATH",
        os.path.expanduser("~/workspace/zoul/repo-transmute/data/txtai/"),
    )
    txtai_meta_db = Path(txtai_index_path) / "agent_events_meta.db"

    deleted_meta = 0
    deleted_oracle = 0
    deleted_drift = 0
    deleted_events_meta = 0

    # --- SQLite sidecar: aie_meta.db ---
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Find event_ids to delete
        rows = conn.execute(
            """
            SELECT event_id FROM agent_events_meta
            WHERE json_extract(data, '$.timestamp') < ?
            """,
            (before_iso,),
        ).fetchall()
        event_ids = [r["event_id"] for r in rows]

        if event_ids:
            placeholders = ",".join("?" * len(event_ids))

            # Delete oracle_results referencing these events
            cur = conn.execute(
                f"DELETE FROM oracle_results WHERE event_id IN ({placeholders})",
                event_ids,
            )
            deleted_oracle = cur.rowcount

            # Delete drift_log entries referencing these events
            cur = conn.execute(
                f"DELETE FROM drift_log WHERE current_event_id IN ({placeholders})",
                event_ids,
            )
            deleted_drift = cur.rowcount

            # Delete agent_events_meta records
            cur = conn.execute(
                f"DELETE FROM agent_events_meta WHERE event_id IN ({placeholders})",
                event_ids,
            )
            deleted_events_meta = cur.rowcount

        conn.commit()
        conn.close()
    else:
        event_ids = []

    # --- txtai metadata: agent_events_meta.db ---
    if txtai_meta_db.exists():
        conn2 = sqlite3.connect(txtai_meta_db)
        conn2.row_factory = sqlite3.Row

        rows2 = conn2.execute(
            """
            SELECT event_id FROM agent_events_meta
            WHERE json_extract(data, '$.timestamp') < ?
            """,
            (before_iso,),
        ).fetchall()
        txtai_event_ids = [r["event_id"] for r in rows2]

        if txtai_event_ids:
            placeholders2 = ",".join("?" * len(txtai_event_ids))
            cur2 = conn2.execute(
                f"DELETE FROM agent_events_meta WHERE event_id IN ({placeholders2})",
                txtai_event_ids,
            )
            deleted_meta = cur2.rowcount

        conn2.close()

    return {
        "before": before_iso,
        "event_ids_found": len(event_ids),
        "events_deleted_from_meta": deleted_events_meta,
        "oracle_results_deleted": deleted_oracle,
        "drift_records_deleted": deleted_drift,
        "txtai_meta_deleted": deleted_meta,
    }


# ---------------------------------------------------------------------------
# CLI argument parsers
# ---------------------------------------------------------------------------

def _build_trail_parser(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to generate audit trail for",
    )
    sub.add_argument(
        "--event-id",
        dest="event_id",
        metavar="ID",
        help="Generate trail leading to a specific event ID",
    )


def _build_export_parser(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--format",
        "-f",
        dest="fmt",
        choices=["md", "json", "html"],
        default="md",
        help="Export format (default: md)",
    )
    sub.add_argument(
        "--session",
        "-s",
        dest="session_id",
        required=True,
        help="Session ID to export",
    )
    sub.add_argument(
        "--output",
        "-o",
        dest="output",
        help="Output file path (default: stdout)",
    )


def _build_diff_parser(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "session_a",
        help="First session ID (before)",
    )
    sub.add_argument(
        "session_b",
        help="Second session ID (after)",
    )


def _build_prune_parser(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--before",
        "-b",
        dest="before",
        required=True,
        metavar="YYYY-MM-DD",
        help="Delete all events before this date (ISO format: YYYY-MM-DD)",
    )


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------

def _cmd_trail(args: argparse.Namespace) -> None:
    """Handle 'aiaudit trail <session_id>' or 'aiaudit trail --event-id <id>'."""
    client = _tx.AIETxtaiClient()
    gen = _audit_mod.AuditGenerator(client, db_path=_get_aie_db_path())

    if args.event_id:
        # Trail leading to a specific event: find its session and build from there
        # Query txtai for the event's metadata to recover its session_id
        events = client.query_events(
            filters={},
            top_k=1000,
        )
        target_event = None
        for e in events:
            if e.get("event_id") == args.event_id:
                target_event = e
                break

        if not target_event:
            # Fallback: try querying by event_id directly via SQLite
            db_path = _get_aie_db_path()
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT data FROM agent_events_meta WHERE event_id = ?",
                    (args.event_id,),
                ).fetchone()
                conn.close()
                if row:
                    import json as _json

                    data = _json.loads(row["data"])
                    session_id = data.get("session_id")
                    if session_id:
                        print(
                            f"# Event {args.event_id} belongs to session {session_id}",
                            file=sys.stderr,
                        )
                        target_event = data
                        target_event["event_id"] = args.event_id

        if not target_event:
            print(f"Event {args.event_id} not found.", file=sys.stderr)
            sys.exit(1)

        session_id = target_event.get("session_id")
        if not session_id:
            print(
                f"Event {args.event_id} has no session_id, cannot build trail.",
                file=sys.stderr,
            )
            sys.exit(1)

        trail = gen.build_trail(session_id)
    elif args.session_id:
        trail = gen.build_trail(args.session_id)
    else:
        print(
            "aiaudit trail: either <session_id> or --event-id is required.\n"
            "Usage: aiaudit trail <session_id>\n"
            "       aiaudit trail --event-id <event_id>",
            file=sys.stderr,
        )
        sys.exit(1)

    if not trail:
        print(f"No events found for session '{args.session_id or session_id}'.", file=sys.stderr)
        sys.exit(1)

    output = _audit_mod.to_markdown(trail)
    print(output)


def _cmd_export(args: argparse.Namespace) -> None:
    """Handle 'aiaudit export --format <fmt> --session <id>'."""
    client = _tx.AIETxtaiClient()
    gen = _audit_mod.AuditGenerator(client, db_path=_get_aie_db_path())

    trail = gen.build_trail(args.session_id)
    if not trail:
        print(f"No events found for session '{args.session_id}'.", file=sys.stderr)
        sys.exit(1)

    if args.fmt == "json":
        content = _audit_mod.to_json(trail)
    elif args.fmt == "html":
        content = _audit_mod.to_html(trail)
    else:
        content = _audit_mod.to_markdown(trail)

    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"Exported to {args.output}")
    else:
        print(content)


def _cmd_diff(args: argparse.Namespace) -> None:
    """Handle 'aiaudit diff <session_a> <session_b>'."""
    client = _tx.AIETxtaiClient()
    gen = _audit_mod.AuditGenerator(client, db_path=_get_aie_db_path())

    trail_a = gen.build_trail(args.session_a)
    trail_b = gen.build_trail(args.session_b)

    if not trail_a:
        print(f"Session '{args.session_a}' not found.", file=sys.stderr)
        sys.exit(1)
    if not trail_b:
        print(f"Session '{args.session_b}' not found.", file=sys.stderr)
        sys.exit(1)

    diff = gen.diff(trail_a, trail_b)
    print(json.dumps(diff.to_dict(), indent=2, default=str))


def _cmd_prune(args: argparse.Namespace) -> None:
    """Handle 'aiaudit prune --before YYYY-MM-DD'."""
    before_date = args.before

    # Validate date format
    try:
        datetime.strptime(before_date, "%Y-%m-%d")
    except ValueError:
        print(
            f"Invalid date: '{before_date}'. Expected format: YYYY-MM-DD",
            file=sys.stderr,
        )
        sys.exit(1)

    before_iso = f"{before_date}T00:00:00+00:00"

    result = _prune_events_before(before_iso)
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="aiaudit",
        description="Audit trail CLI — generate, export, diff, and prune audit trails.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_trail = sub.add_parser("trail", help="Generate audit trail for a session")
    _build_trail_parser(p_trail)

    p_export = sub.add_parser("export", help="Export audit trail in various formats")
    _build_export_parser(p_export)

    p_diff = sub.add_parser("diff", help="Compare two session audit trails")
    _build_diff_parser(p_diff)

    p_prune = sub.add_parser("prune", help="Delete events before a given date")
    _build_prune_parser(p_prune)

    args = parser.parse_args()

    if args.command == "trail":
        _cmd_trail(args)
    elif args.command == "export":
        _cmd_export(args)
    elif args.command == "diff":
        _cmd_diff(args)
    elif args.command == "prune":
        _cmd_prune(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
