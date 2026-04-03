# aie_heartbeat.lobster — Autonomous AIE monitoring flow
# Owner: zoul main session
# Purpose: Drift scan + oracle evaluation + health check
# Schedule: every 6h via cron

name: aie_heartbeat
owner: zoul
description: "Autonomous drift scan + oracle evaluation + health check"

steps:

  # ── Step 1: DRIFT_SCAN ──────────────────────────────────────
  - id: drift_scan
    run: bash -c "cd /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo && PYTHONPATH=src python3 -m evaluator.drift scan --all"
    stdin: null

  - id: drift_check
    run: python3 /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/evaluator/flows/check_drift.py
    stdin: $drift_scan.stdout

  # ── Step 2: ORACLE_BATCH ─────────────────────────────────────
  - id: oracle_batch
    run: bash -c "cd /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo && PYTHONPATH=src python3 -m evaluator.aieval batch"
    stdin: null

  - id: oracle_check
    run: python3 /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/evaluator/flows/check_oracle.py
    stdin: $oracle_batch.stdout

  # ── Step 3: AIE HEALTH CHECK ─────────────────────────────────
  - id: health_check
    run: bash -c "cd /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo && PYTHONPATH=src AILOGGER_SOCKET=evaluator/data/ailogger.sock python3 -m evaluator.logger status"
    stdin: null

  - id: health_parse
    run: python3 /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/evaluator/flows/check_health.py
    stdin: $health_check.stdout

output:
  drift_status: $drift_check.stdout
  oracle_status: $oracle_check.stdout
  health_status: $health_parse.stdout
