"""aieval CLI — Oracle evaluation for agent interaction events.

Commands:
    aieval evaluate <event_file>       — Load event, evaluate against applicable oracles
    aieval evaluate --stdin            — Read JSON events from stdin, evaluate each
    aieval oracle list                 — List all loaded oracles
    aieval oracle validate            — Validate all oracle YAML files
    aieval oracle run --oracle <id>   — Run specific oracle against recent events
    aieval batch --since YYYY-MM-DD  — Batch evaluate all on_cron oracles
    aieval report --since YYYY-MM-DD  — Generate evaluation summary as JSON
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import oracle_engine as oe
from . import txtai_client as tx

logger = logging.getLogger(__name__)


def _print_help() -> None:
    print("""aieval — Oracle Evaluation CLI

Usage:
  aieval evaluate <event_file>           Evaluate event file against applicable oracles
  aieval evaluate --stdin               Read JSON from stdin, evaluate each
  aieval oracle list                    List all loaded oracles
  aieval oracle validate               Validate all oracle YAML files
  aieval oracle run --oracle <id>      Run specific oracle against recent events (last 24h)
  aieval batch --since YYYY-MM-DD      Batch evaluate all on_cron oracles
  aieval report --since YYYY-MM-DD      Generate evaluation summary as JSON

Examples:
  aieval evaluate events/session-001.jsonl
  aieval evaluate --stdin < events.jsonl
  aieval oracle list
  aieval oracle validate
  aieval oracle run --oracle no_empty_context
  aieval batch --since 2026-04-01
  aieval report --since 2026-04-01
""")


def _cmd_evaluate(args: list[str]) -> None:
    registry = oe.OracleRegistry()
    registry.load(args.get("oracles_dir", "oracles"))

    if args.get("stdin"):
        # Read events from stdin
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON line")
                continue
            results = oe.evaluate_event(event, registry)
            for r in results:
                print(json.dumps(r.__dict__ if hasattr(r, "__dict__") else r, default=str))
    else:
        # Load from file
        filepath = Path(args.get("event_file", ""))
        if not filepath.exists():
            print(f"File not found: {filepath}", file=sys.stderr)
            sys.exit(1)
        with open(filepath) as f:
            event = json.load(f)
        results = oe.evaluate_event(event, registry)
        for r in results:
            output = r.__dict__ if hasattr(r, "__dict__") else r
            print(json.dumps(output, default=str, indent=2))


def _cmd_oracle_list(args: list[str]) -> None:
    registry = oe.OracleRegistry()
    registry.load(args.get("oracles_dir", "oracles"))
    for o in registry.list():
        print(f"{o.oracle_id}: [{o.severity}] {o.event_type} ({o.trigger}) — {o.description[:60] if o.description else '(no description)'}")


def _cmd_oracle_validate(args: list[str]) -> None:
    registry = oe.OracleRegistry()
    count = registry.load(args.get("oracles_dir", "oracles"))
    print(f"Loaded {count} oracles")
    all_valid = True
    for o in registry.list():
        ok, err = registry.validate(o.oracle_id)
        status = "OK" if ok else f"FAIL: {err}"
        print(f"  {o.oracle_id}: {status}")
        if not ok:
            all_valid = False
    sys.exit(0 if all_valid else 1)


def _cmd_oracle_run(args: list[str]) -> None:
    oracle_id = args.get("oracle_id")
    if not oracle_id:
        print("Usage: aieval oracle run --oracle <id>", file=sys.stderr)
        sys.exit(1)

    registry = oe.OracleRegistry()
    registry.load(args.get("oracles_dir", "oracles"))
    oracle = registry.get(oracle_id)
    if not oracle:
        print(f"Oracle not found: {oracle_id}", file=sys.stderr)
        sys.exit(1)

    # Query recent events from txtai
    client = tx.get_client()
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    events = client.query_events(
        {"event_type": oracle.event_type, "since": since.isoformat().replace("+00:00", "Z")},
        top_k=100,
    )

    print(f"Found {len(events)} {oracle.event_type} events in last 24h")
    for event in events:
        result = oe.evaluate_conditions(oracle, event)
        status = "PASS" if result.passed else f"FAIL: {result.deviation}"
        eid = event.get("event_id", "?")
        print(f"  {eid}: {status}")


def _cmd_batch(args: list[str]) -> None:
    since_str = args.get("since")
    if since_str:
        since = datetime.fromisoformat(since_str).replace(tzinfo=timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    registry = oe.OracleRegistry()
    registry.load(args.get("oracles_dir", "oracles"))

    # Get all on_cron oracles
    cron_oracles = [o for o in registry.list() if o.trigger == "on_cron"]
    if not cron_oracles:
        print("No on_cron oracles found")
        return

    client = tx.get_client()
    all_results = []
    for oracle in cron_oracles:
        events = client.query_events(
            {"event_type": oracle.event_type, "since": since.isoformat().replace("+00:00", "Z")},
            top_k=500,
        )
        for event in events:
            result = oe.evaluate_conditions(oracle, event)
            all_results.append({
                "oracle_id": oracle.oracle_id,
                "event_id": event.get("event_id"),
                "passed": result.passed,
                "deviation": result.deviation,
                "failed_conditions": result.failed_conditions,
            })

    output = {
        "since": since.isoformat(),
        "total_evaluated": len(all_results),
        "passed": len([r for r in all_results if r["passed"]]),
        "failed": len([r for r in all_results if not r["passed"]]),
        "results": all_results,
    }
    print(json.dumps(output, indent=2))


def _cmd_report(args: list[str]) -> None:
    since_str = args.get("since")
    if since_str:
        since = datetime.fromisoformat(since_str).replace(tzinfo=timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    registry = oe.OracleRegistry()
    registry.load(args.get("oracles_dir", "oracles"))

    client = tx.get_client()
    summary: dict[str, dict] = {}

    for oracle in registry.list():
        events = client.query_events(
            {"event_type": oracle.event_type, "since": since.isoformat().replace("+00:00", "Z")},
            top_k=500,
        )
        passed = 0
        failed = 0
        for event in events:
            result = oe.evaluate_conditions(oracle, event)
            if result.passed:
                passed += 1
            else:
                failed += 1
        if events:
            summary[oracle.oracle_id] = {
                "event_type": oracle.event_type,
                "trigger": oracle.trigger,
                "severity": oracle.severity,
                "total": len(events),
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / len(events) * 100, 1),
            }

    output = {
        "since": since.isoformat(),
        "oracles_evaluated": len(summary),
        "summary": summary,
    }
    print(json.dumps(output, indent=2))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        _print_help()
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd in ("--help", "-h"):
        _print_help()
        sys.exit(0)

    # Parse subcommands
    if cmd == "evaluate":
        parser = argparse.ArgumentParser(prog="aieval evaluate")
        parser.add_argument("event_file", nargs="?")
        parser.add_argument("--stdin", action="store_true")
        parser.add_argument("--oracles-dir", default="oracles")
        parsed, remaining = parser.parse_known_args(sys.argv[2:])
        args = vars(parsed)
        args["oracles_dir"] = args.pop("oracles_dir")
        _cmd_evaluate(args)

    elif cmd == "oracle":
        sub = sys.argv[2] if len(sys.argv) > 2 else ""
        if sub == "list":
            _cmd_oracle_list({"oracles_dir": "oracles"})
        elif sub == "validate":
            _cmd_oracle_validate({"oracles_dir": "oracles"})
        elif sub == "run":
            parser = argparse.ArgumentParser(prog="aieval oracle run")
            parser.add_argument("--oracle", required=True)
            parser.add_argument("--oracles-dir", default="oracles")
            parsed, _ = parser.parse_known_args(sys.argv[3:] if len(sys.argv) > 3 else ["--help"])
            args = vars(parsed)
            _cmd_oracle_run(args)
        else:
            print(f"Unknown oracle subcommand: {sub}", file=sys.stderr)
            _print_help()
            sys.exit(1)

    elif cmd == "batch":
        parser = argparse.ArgumentParser(prog="aieval batch")
        parser.add_argument("--since")
        parser.add_argument("--oracles-dir", default="oracles")
        parsed, _ = parser.parse_known_args(sys.argv[2:])
        args = vars(parsed)
        args["oracles_dir"] = args.pop("oracles_dir")
        _cmd_batch(args)

    elif cmd == "report":
        parser = argparse.ArgumentParser(prog="aieval report")
        parser.add_argument("--since")
        parser.add_argument("--oracles-dir", default="oracles")
        parsed, _ = parser.parse_known_args(sys.argv[2:])
        args = vars(parsed)
        args["oracles_dir"] = args.pop("oracles_dir")
        _cmd_report(args)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        _print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
