# aie_heartbeat.lobster — Autonomous AIE monitoring flow
# Owner: zoul main session
# Purpose: Drift scan + oracle evaluation + audit trail generation
# Resume: every 6h via cron → set_flow_waiting(1800)

name: aie_heartbeat
owner: zoul
description: "Autonomous drift scan + oracle evaluation + audit trail generation"

steps:

  # ── Step 1: DRIFT_SCAN ──────────────────────────────────────
  - id: drift_scan
    command: >
      aidrift scan --all --since 6h
    stdin: null

  - id: drift_check
    command: >
      python3 -c "
      import sys, json
      try:
          data = json.loads(sys.stdin.read())
          drifts = data if isinstance(data, list) else data.get('drifts', [])
          critical = [d for d in drifts if float(d.get('drift_score', 0)) >= 0.9]
          if critical:
              print('HAS_CRITICAL')
          elif drifts:
              print('HAS_DRIFT')
          else:
              print('CLEAN')
      except Exception:
          print('PARSE_ERROR')
      "
    stdin: $drift_scan.stdout

  # Alert on critical drift
  - id: drift_alert_critical
    command: >
      openclaw.invoke --tool message --action send --args-json
      '{"provider":"discord","channel":"#evaluator-alerts","content":"🚨 CRITICAL DRIFT detected (score >= 0.9) in session. See drift_reports for details."}'
    condition: $drift_check.stdout == "HAS_CRITICAL"

  # Log all drifts to daily drift report
  - id: drift_log
    command: >
      bash -c 'DATE=$(date +%Y-%m-%d); mkdir -p evaluator/data/logs/drift_reports; aidrift scan --all --since 6h > /tmp/drift_scan_out.json 2>&1; python3 -c "
      import sys, json, datetime
      try:
          data = json.load(open(\"/tmp/drift_scan_out.json\"))
          drifts = data if isinstance(data, list) else data.get(\"drifts\", [])
          logfile = f\"evaluator/data/logs/drift_reports/{datetime.date.today()}.json\"
          existing = []
          try:
              existing = json.load(open(logfile))
          except: pass
          existing.extend(drifts)
          json.dump(existing, open(logfile, \"w\"), indent=2)
          print(f\"Logged {len(drifts)} drift(s) to {logfile}\")
      except Exception as e:
          print(f\"Log error: {e}\")
      "'
    condition: $drift_check.stdout == "HAS_DRIFT" || $drift_check.stdout == "HAS_CRITICAL"

  # ── Step 2: ORACLE_BATCH ─────────────────────────────────────
  - id: oracle_batch
    command: >
      aieval batch --since 24h --trigger on_cron
    stdin: null

  - id: oracle_check
    command: >
      python3 -c "
      import sys, json
      try:
          data = json.loads(sys.stdin.read())
          results = data if isinstance(data, list) else data.get('results', data.get('evaluations', []))
          critical_failures = [r for r in results
            if r.get('oracle', {}).get('severity') == 'critical' and not r.get('passed', True)]
          if critical_failures:
              print('HAS_CRITICAL_FAILURES')
          elif any(not r.get('passed', True) for r in results):
              print('HAS_FAILURES')
          else:
              print('CLEAN')
      except Exception:
          print('PARSE_ERROR')
      "
    stdin: $oracle_batch.stdout

  # Alert on critical oracle failure
  - id: oracle_alert_critical
    command: >
      openclaw.invoke --tool message --action send --args-json
      '{"provider":"discord","channel":"#evaluator-alerts","content":"🚨 ORACLE FAILURE: critical oracle failed on batch evaluation. See oracle_reports for details."}'
    condition: $oracle_check.stdout == "HAS_CRITICAL_FAILURES"

  # Log oracle failures to daily report
  - id: oracle_log
    command: >
      bash -c 'DATE=$(date +%Y-%m-%d); mkdir -p evaluator/data/logs/oracle_reports; python3 -c "
      import sys, json, datetime, subprocess
      try:
          result = subprocess.run(["aieval", "batch", "--since", "24h", "--trigger", "on_cron", "--json"],
            capture_output=True, text=True, cwd="/home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo")
          data = json.loads(result.stdout)
          results = data if isinstance(data, list) else data.get(\"results\", data.get(\"evaluations\", []))
          failures = [r for r in results if not r.get(\"passed\", True)]
          logfile = f\"evaluator/data/logs/oracle_reports/{datetime.date.today()}.json\"
          existing = []
          try: existing = json.load(open(logfile))
          except: pass
          existing.extend(failures)
          json.dump(existing, open(logfile, \"w\"), indent=2)
          print(f\"Logged {len(failures)} failure(s) to {logfile}\")
      except Exception as e:
          print(f\"Log error: {e}\")
      "'
    condition: $oracle_check.stdout == "HAS_FAILURES" || $oracle_check.stdout == "HAS_CRITICAL_FAILURES"

  # ── Step 3: AUDIT_TRAILS ─────────────────────────────────────
  # Find sessions with drift or failures in last 24h and generate trails
  - id: audit_trails
    command: >
      bash -c '
      DATE=$(date +%Y-%m-%d)
      mkdir -p evaluator/data/audit_trails
      # Get list of sessions with issues (from drift reports + oracle reports)
      SESSIONS=$(python3 -c "
      import json, datetime, os, glob
      sessions = set()
      today = datetime.date.today().isoformat()
      # From drift reports
      for f in glob.glob(f\"evaluator/data/logs/drift_reports/{today}.json\"):
          try:
              for d in json.load(open(f)):
                  if d.get(\"session_id\"):
                      sessions.add(d[\"session_id\"])
          except: pass
      # From oracle reports
      for f in glob.glob(f\"evaluator/data/logs/oracle_reports/{today}.json\"):
          try:
              for r in json.load(open(f)):
                  if r.get(\"session_id\"):
                      sessions.add(r[\"session_id\"])
          except: pass
      print(\"\n\".join(sessions))
      " 2>/dev/null)
      
      if [ -z "$SESSIONS" ]; then
        echo "No sessions with drift or failures — skipping audit trails"
      else
        for session in $SESSIONS; do
          echo "Generating audit trail for session: $session"
          aiaudit trail $session --export json 2>/dev/null && echo "OK: $session" || echo "WARN: $session"
        done
      fi
      '
    stdin: null

  # ── Step 4: HEALTH_CHECK ─────────────────────────────────────
  - id: health_check
    command: >
      ailogger status
    stdin: null

  - id: health_check_parse
    command: >
      python3 -c "
      import sys, json
      try:
          data = json.loads(sys.stdin.read())
          healthy = data.get('healthy', False)
          buffered = int(data.get('buffered', 0))
          if healthy and buffered < 100:
              print('HEALTHY')
          else:
              print('UNHEALTHY')
              if not healthy: print('Logger unhealthy')
              if buffered >= 100: print(f'Backpressure: {buffered} buffered events')
      except Exception as e:
          print(f'PARSE_ERROR: {e}')
      "
    stdin: $health_check.stdout

  # Alert on health check failure
  - id: health_alert
    command: >
      openclaw.invoke --tool message --action send --args-json
      '{"provider":"discord","channel":"#evaluator-alerts","content":"⚠️ AIE health check failed — see evaluator/data/logs/health.log"}'
    condition: $health_check_parse.stdout == "UNHEALTHY"

  # Log health check
  - id: health_log
    command: >
      bash -c 'python3 -c "
      import sys, json, datetime
      try:
          data = json.loads(sys.stdin.read())
          logline = f\"[{datetime.datetime.now().isoformat()}] {json.dumps(data)}\n\"
          with open(\"evaluator/data/logs/health.log\", \"a\") as f:
              f.write(logline)
          print(\"Health check logged\")
      except Exception as e:
          print(f\"Log error: {e}\")
      "'
    condition: $health_check_parse.stdout == "HEALTHY" || $health_check_parse.stdout == "UNHEALTHY"

  # ── Step 5: SET_WAITING (loop back) ──────────────────────────
  - id: set_waiting
    command: >
      echo "Flow complete — setting waiting state for 1800s (30 min)"
    stdin: null

output:
  drift_status: $drift_check.stdout
  oracle_status: $oracle_check.stdout
  health_status: $health_check_parse.stdout
  timestamp: ${{ now }}
