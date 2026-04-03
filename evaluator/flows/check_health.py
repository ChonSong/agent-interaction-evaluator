#!/usr/bin/env python3
"""check_health.py — parse logger status output, print health."""
import sys, json, os

# Use correct socket path for this environment
SOCKET = os.environ.get(
    "AILOGGER_SOCKET",
    "/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/evaluator/data/ailogger.sock"
)

text = sys.stdin.read()
try:
    data = json.loads(text)
    healthy = data.get("healthy", False)
    buffered = int(data.get("buffered", 0))
    print("HEALTHY" if healthy and buffered < 100 else "UNHEALTHY")
except Exception:
    print("PARSE_ERROR")
