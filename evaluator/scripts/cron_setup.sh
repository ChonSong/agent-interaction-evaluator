#!/bin/bash
# cron_setup.sh — Install/uninstall AIE autonomous heartbeat crontab
# Usage: ./cron_setup.sh [--install|--uninstall|--dry-run]
# Default: --install

set -e

CRON_ENTRY="0 */6 * * * openclaw flow trigger aie_heartbeat --if-idle >> evaluator/data/logs/cron_trigger.log 2>&1"

install() {
    # Add TZ line if not already present
    if ! crontab -l 2>/dev/null | grep -q "^TZ="; then
        echo "TZ=Australia/Sydney" | crontab -
        echo "Added TZ=Australia/Sydney"
    fi
    # Remove any existing aie_heartbeat entry
    crontab -l 2>/dev/null | grep -v "aie_heartbeat" | crontab - 2>/dev/null || true
    # Install new entry
    echo "$CRON_ENTRY" | crontab -
    echo "Installed: $CRON_ENTRY"
}

uninstall() {
    crontab -l 2>/dev/null | grep -v "aie_heartbeat" | crontab - 2>/dev/null || true
    echo "Uninstalled aie_heartbeat cron"
}

case "${1:-install}" in
    --install)  install ;;
    --uninstall) uninstall ;;
    --dry-run)  echo "Would install: $CRON_ENTRY" ;;
    *)          echo "Usage: $0 [--install|--uninstall|--dry-run]" ;;
esac
