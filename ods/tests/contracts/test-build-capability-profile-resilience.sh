#!/usr/bin/env bash
# ============================================================================
# Contract Test: build-capability-profile.sh syntax check
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify build-capability-profile.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/build-capability-profile.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "build-capability-profile.sh missing"; exit 1; }

# Syntax check
bash -n "$SCRIPT_FILE" || { echo "Syntax error in build-capability-profile.sh"; exit 1; }

echo "[OK] All build-capability-profile contract assertions passed cleanly."
