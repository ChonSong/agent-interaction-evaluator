#!/bin/bash
# check_oracle.sh — read oracle batch JSON from stdin, print CLEAN|HAS_FAILURES|HAS_CRITICAL_FAILURES
# Writes result JSON to data/_oracle_status.json for alert_and_halt.py
REPO_DIR="/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo"
exec python3 -c "
import sys, json, os

data = json.load(sys.stdin)
results = data.get('results', [])

critical = [r for r in results if r.get('oracle', {}).get('severity') == 'critical' and not r.get('passed', True)]
if critical:
    status = 'HAS_CRITICAL_FAILURES'
elif any(not r.get('passed', True) for r in results):
    status = 'HAS_FAILURES'
else:
    status = 'CLEAN'

repo_dir = '$REPO_DIR'
status_file = os.path.join(repo_dir, 'data', '_oracle_status.json')
os.makedirs(os.path.dirname(status_file), exist_ok=True)
with open(status_file, 'w') as f:
    json.dump({'status': status, 'results': results}, f)

print(status)
"
