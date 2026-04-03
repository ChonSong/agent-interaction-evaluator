name: aie_heartbeat
description: "Autonomous drift scan + oracle evaluation + health check"

steps:

  # ── Step 1: DRIFT_SCAN ──────────────────────────────────────
  - id: drift_scan
    run: bash -c "cd /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo && PYTHONPATH=src python3 -m evaluator.drift scan --all"
    stdin: null

  - id: drift_check
    run: bash /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/check_drift.sh
    stdin: $drift_scan.stdout

  # ── Step 2: ORACLE_BATCH ─────────────────────────────────────
  - id: oracle_batch
    run: bash -c "cd /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo && PYTHONPATH=src python3 -m evaluator.aieval batch"
    stdin: null

  - id: oracle_check
    run: bash /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/check_oracle.sh
    stdin: $oracle_batch.stdout

  # ── Step 3: AIE HEALTH CHECK ─────────────────────────────────
  - id: health_check
    run: bash -c "cd /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo && PYTHONPATH=src AILOGGER_SOCKET=evaluator/data/ailogger.sock python3 -m evaluator.logger status"
    stdin: null

  - id: health_parse
    run: bash /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/check_health.sh
    stdin: $health_check.stdout
