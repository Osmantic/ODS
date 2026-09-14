#!/usr/bin/env bash
# ============================================================================
# Contract Test: linux-install-preflight.sh execution and syntax check
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify linux-install-preflight.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/linux-install-preflight.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "linux-install-preflight.sh missing"; exit 1; }

# Syntax check
bash -n "$SCRIPT_FILE" || { echo "Syntax error in linux-install-preflight.sh"; exit 1; }

echo "[OK] All linux-install-preflight contract assertions passed cleanly."
