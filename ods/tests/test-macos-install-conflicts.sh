#!/usr/bin/env bash
# Static contract for macOS neutral Docker conflict detection.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/installers/macos/install-macos.sh"

fail() {
    echo "[FAIL] $*"
    exit 1
}

pass() {
    echo "[PASS] $*"
}

grep -qF 'source "${SOURCE_ROOT}/installers/lib/install-conflicts.sh"' "$TARGET" \
    || fail "macOS installer must source the shared conflict detector"
pass "macOS installer sources shared detector"

grep -qF 'INSTALL_CONFLICT_COMPOSE_FLAGS="${COMPOSE_FLAGS[*]}"' "$TARGET" \
    || fail "macOS detector must inspect the rendered Compose selection"
pass "macOS detector receives selected Compose flags"

grep -qF 'INSTALL_CONFLICT_ENV_FILE="$INSTALL_DIR/.env"' "$TARGET" \
    || fail "macOS detector must render the generated environment"
pass "macOS detector receives generated environment"

grep -qF 'INSTALL_CONFLICT_UPDATE_MODE="$env_existed"' "$TARGET" \
    || fail "macOS update ownership must use pre-generation .env evidence"
pass "macOS update mode preserves pre-generation evidence"

grep -qF 'ods_run_install_conflict_check || _macos_conflict_status=$?' "$TARGET" \
    || fail "macOS installer must execute the shared detector"
pass "macOS installer executes shared detector"

grep -qF 'Installation stopped because the planned macOS Docker stack conflicts' "$TARGET" \
    || fail "macOS confirmed conflicts must stop installation"
grep -qF 'Installation stopped because conflict detection could not establish' "$TARGET" \
    || fail "macOS probe errors must fail closed"
pass "macOS conflicts and probe errors fail closed"

echo "macOS install conflict wiring checks passed."
