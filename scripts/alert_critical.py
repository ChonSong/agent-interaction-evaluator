#!/usr/bin/env python3
"""alert_critical.py — emit a critical alert to the alerts log and Discord.

Used by lobster workflow (aie_heartbeat.lobster) when:
  - drift_check == HAS_CRITICAL
  - oracle_check == HAS_CRITICAL_FAILURES

Message is read from stdin.  Exit 0 always (non-blocking alert).
Alert is appended to: data/alerts.log
Sends Discord alert via openclaw message tool.
"""

import sys
import os
import subprocess
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALERT_LOG = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "alerts.log"))
DISCORD_CHANNEL = "#evaluator-alerts"


def _send_discord(message: str) -> bool:
    """Send alert to Discord via openclaw message tool."""
    try:
        result = subprocess.run(
            ["openclaw", "message", "send", "--channel", "discord", "--target", DISCORD_CHANNEL, "--message", message],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def main() -> None:
    msg = sys.stdin.read().strip()
    if not msg:
        print("NO_MESSAGE", file=sys.stderr)
        sys.exit(0)

    os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] CRITICAL_ALERT: {msg}\n"

    # Write to alerts.log
    try:
        with open(ALERT_LOG, "a") as f:
            f.write(line)
        print("LOGGED")
    except Exception as e:
        print(f"WRITE_ERROR: {e}", file=sys.stderr)

    # Send to Discord (non-blocking, don't fail if this errors)
    discord_msg = f"🚨 CRITICAL ALERT: {msg}"
    _send_discord(discord_msg)
    print("DISCORD_SENT")

    sys.exit(0)


if __name__ == "__main__":
    main()