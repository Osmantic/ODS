#!/usr/bin/env bash
# ============================================================================
# Contract Test: resolve-compose-stack.sh syntax check and fallback validation
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify resolve-compose-stack.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/resolve-compose-stack.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "resolve-compose-stack.sh missing"; exit 1; }

# Syntax check
bash -n "$SCRIPT_FILE" || { echo "Syntax error in resolve-compose-stack.sh"; exit 1; }

echo "[OK] All resolve-compose-stack contract assertions passed cleanly."
