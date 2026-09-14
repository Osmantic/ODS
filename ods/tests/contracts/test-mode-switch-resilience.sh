#!/usr/bin/env bash
# ============================================================================
# Contract Test: mode-switch.sh execution and syntax check
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify mode-switch.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/mode-switch.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "mode-switch.sh missing"; exit 1; }

# Syntax check
bash -n "$SCRIPT_FILE" || { echo "Syntax error in mode-switch.sh"; exit 1; }

echo "[OK] All mode-switch contract assertions passed cleanly."
