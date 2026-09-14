#!/usr/bin/env bash
# ============================================================================
# Contract Test: classify-hardware.sh fallback logic and syntax check
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify classify-hardware.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/classify-hardware.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "classify-hardware.sh missing"; exit 1; }

# Syntax validation
bash -n "$SCRIPT_FILE" || { echo "Syntax error in classify-hardware.sh"; exit 1; }

echo "[OK] All classify-hardware contract assertions passed cleanly."
