#!/bin/bash
# check_health.sh — read logger status JSON from stdin, print HEALTHY|UNHEALTHY
exec python3 -c "
import sys, json
data = json.load(sys.stdin)
res = data.get('result', data)
buffered = int(res.get('buffered', 0))
txtai_ok = res.get('txtai_available', False)
print('HEALTHY' if txtai_ok and buffered < 100 else 'UNHEALTHY')
"
