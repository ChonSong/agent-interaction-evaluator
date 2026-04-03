#!/bin/bash
# check_drift.sh — read drift scan JSON from stdin, print CLEAN|HAS_DRIFT|HAS_CRITICAL
exec python3 -c "
import sys, json
data = json.load(sys.stdin)
drifts = data.get('drifts', []) if isinstance(data, dict) else data
critical = [d for d in drifts if float(d.get('drift_score', 0)) >= 0.9]
if critical:
    print('HAS_CRITICAL')
elif drifts:
    print('HAS_DRIFT')
else:
    print('CLEAN')
"
