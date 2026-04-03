#!/bin/bash
# check_drift.sh — read drift scan JSON from stdin, print CLEAN|HAS_DRIFT|HAS_CRITICAL
# Writes result JSON to data/_drift_status.json for alert_and_halt.py
REPO_DIR="/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo"
exec python3 -c "
import sys, json, os

data = json.load(sys.stdin)
drifts = data.get('drifts', []) if isinstance(data, dict) else data
critical = [d for d in drifts if float(d.get('drift_score', 0)) >= 0.9]

if critical:
    status = 'HAS_CRITICAL'
elif drifts:
    status = 'HAS_DRIFT'
else:
    status = 'CLEAN'

repo_dir = '$REPO_DIR'
status_file = os.path.join(repo_dir, 'data', '_drift_status.json')
os.makedirs(os.path.dirname(status_file), exist_ok=True)
with open(status_file, 'w') as f:
    json.dump({'status': status, 'critical_sessions': list(set(d.get('session_id', '') for d in critical))}, f)

print(status)
"
