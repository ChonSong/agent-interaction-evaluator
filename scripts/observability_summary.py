#!/usr/bin/env python3
"""observability_summary.py — Post AIE observability report to Discord.

Reads current snapshot from the logger socket via JSON-RPC.
Posts formatted summary to #lurker Discord channel.
Run after alert_and_halt in the lobster heartbeat.
"""
import json
import os
import socket
import subprocess
import sys
import time

REPO_DIR = "/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo"
DATA_DIR = os.path.join(REPO_DIR, "data")
SOCKET_PATH = os.environ.get(
    "AILOGGER_SOCKET",
    os.path.join(REPO_DIR, "evaluator", "data", "ailogger.sock"),
)


def send_discord(msg: str, channel: str = "#lurker") -> bool:
    """Send a message to a Discord channel."""
    if not msg or len(msg.strip()) == 0:
        return False
    try:
        r = subprocess.run(
            [
                "openclaw", "message", "send",
                "--channel", "discord",
                "--target", channel,
                "--message", msg,
            ],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception as e:
        print(f"Discord send failed: {e}", file=sys.stderr)
        return False


def get_observability_discord() -> str | None:
    """Call observability_discord JSON-RPC method on the logger socket."""
    req = json.dumps({
        "jsonrpc": "2.0",
        "method": "observability_discord",
        "params": {},
        "id": 99,
    }).encode() + b"\n"

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(SOCKET_PATH)
        s.sendall(req)
        resp = s.recv(16384)
        s.close()
        data = json.loads(resp.decode())
        result = data.get("result", "")
        if isinstance(result, str):
            return result
        # result might be a dict
        return str(result)
    except FileNotFoundError:
        print(f"Socket not found: {SOCKET_PATH}", file=sys.stderr)
        return None
    except socket.timeout:
        print("Socket timeout", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Socket error: {e}", file=sys.stderr)
        return None


def write_status(msg: str) -> None:
    """Write the status to data/_observability_status.json."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        status_file = os.path.join(DATA_DIR, "_observability_status.json")
        with open(status_file, "w") as f:
            json.dump({"status": "OK", "summary": msg}, f)
    except Exception:
        pass


def main() -> None:
    summary = get_observability_discord()

    if not summary:
        summary = "AIE observability: socket unreachable or empty response"

    write_status(summary)

    # Always post to #lurker
    sent = send_discord(summary, "#lurker")
    print(f"Observability summary posted to #lurker: {sent}")
    print(summary)


if __name__ == "__main__":
    main()
