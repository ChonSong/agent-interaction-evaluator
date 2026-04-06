name: aie_heartbeat
description: "Autonomous drift scan + oracle evaluation + health check with circuit breaker"

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

  # ── Step 3: ALERT & HALT ─────────────────────────────────────
  # Reads _drift_status.json + _oracle_status.json from data/
  # Sends Discord alert if critical conditions found, increments halt counters
  - id: alert_and_halt
    run: bash /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/alert_and_halt.py
    stdin: null

  # ── Step 4: OBSERVABILITY SUMMARY ───────────────────────────
  # Post structured metrics report to #lurker — always fires, even on CLEAN
  - id: observability_report
    run: bash /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/post_observability.sh
    stdin: null

  # ── Step 5: AIE HEALTH CHECK ─────────────────────────────────
  - id: health_check
    run: bash /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/check_health.sh
    stdin: null

  - id: health_parse
    run: bash /home/osboxes/.openclaw/workspace/zoul/agent-interaction-evaluator-repo/scripts/check_health.sh
    stdin: $health_check.stdout
