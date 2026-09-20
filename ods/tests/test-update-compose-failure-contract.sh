#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/ods-update.sh"

bash -n "$SCRIPT"
grep -q '^_compose_up_or_fail()' "$SCRIPT"
grep -q 'docker-compose "\${compose_args\[@\]}" up -d' "$SCRIPT"
grep -q '_compose_up_or_fail .*||' "$SCRIPT"

echo "[PASS] compose restart failures propagate from update and rollback"
