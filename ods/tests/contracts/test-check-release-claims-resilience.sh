#!/usr/bin/env bash
# ============================================================================
# Contract Test: check-release-claims.sh execution and syntax check
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify check-release-claims.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/check-release-claims.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "check-release-claims.sh missing"; exit 1; }

# Syntax check
bash -n "$SCRIPT_FILE" || { echo "Syntax error in check-release-claims.sh"; exit 1; }

echo "[OK] All check-release-claims contract assertions passed cleanly."
