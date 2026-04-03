#!/usr/bin/env python3
"""alert_and_halt.py — Read check outputs from JSON files, send Discord alerts, increment halt counter.

Reads _drift_status.json and _oracle_status.json from data/
Sends Discord DM to Alto (user 291686310714933258) if critical conditions found.
Increments circuit breaker halt counters for affected sessions.
Writes alerts to: data/alerts.log
"""
import sys, json, os, subprocess, datetime, threading

REPO_DIR = "/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo"
DATA_DIR = os.path.join(REPO_DIR, "data")
ALERT_LOG = os.path.join(DATA_DIR, "alerts.log")
DRIFT_STATUS = os.path.join(DATA_DIR, "_drift_status.json")
ORACLE_STATUS = os.path.join(DATA_DIR, "_oracle_status.json")


def send_discord(msg: str) -> bool:
    try:
        r = subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "discord",
             "--target", "#291686310714933258",
             "--message", msg],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def write_alert_log(msg: str) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(ALERT_LOG, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def increment_halt_counter(session_id: str) -> None:
    if not session_id:
        return
    try:
        import asyncio
        async def _do():
            sys.path.insert(0, os.path.join(REPO_DIR, "src"))
            from evaluator import db as _db
            db_path = os.environ.get("AIE_DB_PATH")
            conn = await _db.init_db(db_path)
            await _db.increment_halt_counter(session_id)
            await conn.close()
        asyncio.run(_do())
    except Exception:
        pass


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

    drift = load_json(DRIFT_STATUS)
    oracle = load_json(ORACLE_STATUS)

    drift_status = drift.get("status", "").strip()
    oracle_status = oracle.get("status", "").strip()

    has_critical_drift = "HAS_CRITICAL" in drift_status
    has_critical_oracle = "HAS_CRITICAL_FAILURES" in oracle_status

    alerts = []

    if has_critical_drift:
        msg = "🚨 CRITICAL DRIFT detected (score >= 0.9). See drift reports."
        send_discord(msg)
        write_alert_log(msg)
        alerts.append("CRITICAL_DRIFT")
        for session_id in set(drift.get("critical_sessions", [])):
            if session_id:
                increment_halt_counter(session_id)

    if has_critical_oracle:
        oracle_results = oracle.get("results", [])
        failed_ids = [r.get("oracle_id", "?") for r in oracle_results if not r.get("passed", True)]
        msg = f"🚨 CRITICAL ORACLE FAILURE: {len(failed_ids)} oracle(s) failed — {failed_ids}"
        send_discord(msg)
        write_alert_log(msg)
        alerts.append("CRITICAL_ORACLE")
        for r in oracle_results:
            if not r.get("passed", True):
                sid = r.get("session_id", "")
                if sid:
                    increment_halt_counter(sid)

    if alerts:
        result = f"ALERT_SENT:{','.join(alerts)}"
    else:
        result = "CLEAN"
        write_alert_log("Heartbeat OK — no critical conditions")

    print(result)


if __name__ == "__main__":
    main()
