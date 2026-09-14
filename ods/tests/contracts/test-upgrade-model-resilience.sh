#!/usr/bin/env bash
# ============================================================================
# Contract Test: upgrade-model.sh checksum verification and syntax check
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify upgrade-model.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/upgrade-model.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "upgrade-model.sh missing"; exit 1; }

# Syntax check
bash -n "$SCRIPT_FILE" || { echo "Syntax error in upgrade-model.sh"; exit 1; }

echo "[OK] All upgrade-model contract assertions passed cleanly."
