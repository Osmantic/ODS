#!/usr/bin/env bash
# ============================================================================
# Contract Test: ods-doctor.sh execution and syntax check
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Verify ods-doctor.sh script
SCRIPT_FILE="$REPO_ROOT/ods/scripts/ods-doctor.sh"
[[ -f "$SCRIPT_FILE" ]] || { echo "ods-doctor.sh missing"; exit 1; }

# Syntax check
bash -n "$SCRIPT_FILE" || { echo "Syntax error in ods-doctor.sh"; exit 1; }

echo "[OK] All ods-doctor contract assertions passed cleanly."
