#!/bin/bash
# post_observability.sh — fetch and post AIE observability summary to Discord #lurker
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
exec python3 "$REPO_DIR/scripts/observability_summary.py"
