#!/bin/bash
# alert_critical.sh — emit a critical alert to alerts.log
# Used by lobster workflow when drift_check==HAS_CRITICAL or oracle_check==HAS_CRITICAL_FAILURES
exec python3 /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/alert_critical.py
