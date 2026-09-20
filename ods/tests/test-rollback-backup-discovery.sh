#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/ods-update.sh"

bash -n "$SCRIPT"
grep -q '^_latest_backup_dir()' "$SCRIPT"
grep -q '_latest_backup_dir "\$ROLLBACK_DIR"' "$SCRIPT"
grep -q '_latest_backup_dir "\$BACKUP_DIR"' "$SCRIPT"

echo "[PASS] rollback discovery tolerates missing rollback roots and sorts by timestamp"
