#!/bin/bash
# check_oracle.sh — read oracle batch JSON from stdin, print CLEAN|HAS_FAILURES|HAS_CRITICAL_FAILURES
exec python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
critical = [r for r in results if r.get('oracle', {}).get('severity') == 'critical' and not r.get('passed', True)]
if critical:
    print('HAS_CRITICAL_FAILURES')
elif any(not r.get('passed', True) for r in results):
    print('HAS_FAILURES')
else:
    print('CLEAN')
"
