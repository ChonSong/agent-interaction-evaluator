#!/usr/bin/env python3
"""check_oracle.py — parse oracle batch output, print status."""
import sys, json

text = sys.stdin.read()
try:
    data = json.loads(text)
    results = data.get("results", [])
    critical_failures = [
        r for r in results
        if r.get("oracle", {}).get("severity") == "critical" and not r.get("passed", True)
    ]
    if critical_failures:
        print("HAS_CRITICAL_FAILURES")
    elif any(not r.get("passed", True) for r in results):
        print("HAS_FAILURES")
    else:
        print("CLEAN")
except Exception:
    print("PARSE_ERROR")
