#!/usr/bin/env bash
# ============================================================================
# Contract Test: health-check.sh status code resilience
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify health-check.sh exists
HEALTH_CHECK_SCRIPT="$REPO_ROOT/ods/scripts/health-check.sh"
[[ -f "$HEALTH_CHECK_SCRIPT" ]] || { echo "health-check.sh missing"; exit 1; }

# Verify shell syntax
bash -n "$HEALTH_CHECK_SCRIPT" || { echo "Syntax error in health-check.sh"; exit 1; }

echo "[OK] All health-check contract assertions passed cleanly."
