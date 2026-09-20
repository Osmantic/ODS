#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/ods-backup.sh"

bash -n "$SCRIPT"
grep -A2 -F 'if [[ -n "$delete_id" ]]; then' "$SCRIPT" \
  | grep -q 'exit $?'

echo "[PASS] backup delete propagates validation and lookup failures"
