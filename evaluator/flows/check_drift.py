#!/usr/bin/env python3
"""check_drift.py — parse drift scan output, print status."""
import sys, json

text = sys.stdin.read()
try:
    data = json.loads(text)
    drifts = data if isinstance(data, list) else data.get("drifts", [])
    critical = [d for d in drifts if float(d.get("drift_score", 0)) >= 0.9]
    if critical:
        print("HAS_CRITICAL")
    elif drifts:
        print("HAS_DRIFT")
    else:
        print("CLEAN")
except Exception:
    print("PARSE_ERROR")
