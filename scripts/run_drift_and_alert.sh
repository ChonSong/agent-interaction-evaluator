#!/bin/bash
# run_drift_and_alert.sh — run drift scan and alert on HAS_CRITICAL
cd /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo
PYTHONPATH=src python3 -m evaluator.drift scan --all | python3 -c "
import sys, json
data = json.load(sys.stdin)
drifts = data.get('drifts', []) if isinstance(data, dict) else data
critical = [d for d in drifts if float(d.get('drift_score', 0)) >= 0.9]
# Forward the JSON to stdout for logging
print(sys.stdin.read().strip())
if critical:
    import subprocess
    # Re-send the JSON to alert_critical.sh
    print(json.dumps(critical), file=sys.stderr)
    alert_result = subprocess.run(
        ['bash', '/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/alert_critical.sh'],
        input=json.dumps({'drifts': critical}),
        capture_output=True
    )
"
