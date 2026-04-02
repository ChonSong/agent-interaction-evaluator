# AIE Autonomous Operation

This document describes how the Agent Interaction Evaluator runs autonomously once set up.

## Architecture Overview

```
Cron (every 6h) → ClawFlow (aie_heartbeat) → Steps:
  1. aidrift scan       → drift detection
  2. aieval batch      → oracle evaluation  
  3. aiaudit trail     → audit trail generation
  4. health_check      → system health
  → wait 30 min → repeat
```

## Cron Trigger

The cron entry wakes the ClawFlow if idle:

```bash
0 */6 * * * openclaw flow trigger aie_heartbeat --if-idle >> evaluator/data/logs/cron_trigger.log 2>&1
```

The actual work lives in the ClawFlow, not in crontab. The cron only wakes the flow.

## Managing the Cron

```bash
# Install (or reinstall) the cron trigger
./evaluator/scripts/cron_setup.sh --install

# Uninstall (stop autonomous runs)
./evaluator/scripts/cron_setup.sh --uninstall

# Dry run (see what would be installed)
./evaluator/scripts/cron_setup.sh --dry-run
```

## ClawFlow Flow Management

```bash
# Check if aie_heartbeat is running
openclaw flows list

# See current state of the flow
openclaw flows show aie_heartbeat

# Cancel the flow
openclaw flows cancel aie_heartbeat

# Manually trigger (bypasses --if-idle)
openclaw flow trigger aie_heartbeat
```

## Manual Commands

If you need to run things manually:

```bash
# Drift scan
aidrift scan --all --since 6h
aidrift report

# Oracle evaluation
aieval oracle list
aieval batch --since 2026-04-01 --trigger on_cron

# Audit trails
aiaudit trail <session_id>
aiaudit export --format md --session <session_id>

# Logger status
ailogger status

# Health check
./evaluator/scripts/health_check.sh
```

## Alert Routing

| Severity | Action |
|---|---|
| `critical` | Discord `#evaluator-alerts` + halt |
| `warning` | Discord `#evaluator-alerts` |
| `info` | Logged to `evaluator/data/logs/info.log` |

## Log Locations

| Log | Location |
|---|---|
| Cron trigger | `evaluator/data/logs/cron_trigger.log` |
| Drift reports | `evaluator/data/logs/drift_reports/YYYY-MM-DD.json` |
| Oracle reports | `evaluator/data/logs/oracle_reports/YYYY-MM-DD.json` |
| Info logs | `evaluator/data/logs/info.log` |
| Alert failures | `evaluator/data/logs/alert_failures.log` |
| Health checks | `evaluator/data/logs/health.log` |
| Raw events | `evaluator/data/logs/YYYY-MM-DD.jsonl` |

## Data Retention

| Data | Retention | Prune |
|---|---|---|
| Event JSONL | 30 days | `aiaudit prune --before 2026-03-01` |
| txtai index | 90 days | Rebuild from logs |
| Audit trails | 30 days | Weekly via ClawFlow |
| Drift reports | 30 days | Manual |
| Health logs | 7 days | Rotated daily |

## Verifying the Setup

```bash
# 1. Verify cron is installed
crontab -l | grep aie_heartbeat

# 2. Verify all CLIs are working
ailogger status
aidrift stats
aieval oracle list
aiaudit --help

# 3. Verify txtai index is accessible
python3 -c "from evaluator.txtai_client import AIETxtaiClient; c = AIETxtaiClient(); print(c.get_stats())"

# 4. Check data directories exist
ls evaluator/data/logs/
ls evaluator/data/audit_trails/
```

## Troubleshooting

**Flow not triggering:**
- Check cron is installed: `crontab -l | grep aie_heartbeat`
- Check the flow isn't already running: `openclaw flows list`
- Manually trigger: `openclaw flow trigger aie_heartbeat`

**No events appearing:**
- Check logger is running: `ailogger status`
- Check data/logs/ for recent JSONL files
- Check txtai stats: `aidrift stats`

**Alerts not sending:**
- Check alert_failures.log
- Verify Discord channel `#evaluator-alerts` exists
- Test manually: `openclaw message --channel #evaluator-alerts "test"`

## Setting Up for the First Time

```bash
# 1. Install the cron trigger
./evaluator/scripts/cron_setup.sh --install

# 2. Verify
crontab -l | grep aie_heartbeat

# 3. Run a manual drift scan to populate initial data
aidrift scan --all --since 24h

# 4. Verify oracle count
aieval oracle list | wc -l  # should show 8 oracles

# 5. Check aiaudit works
aieval oracle validate  # should pass all 8 oracles
```
