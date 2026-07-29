#!/bin/bash
# ============================================================================
# ODS CLI preset save/load user-extension test
# ============================================================================
# `ods preset load` must restore dashboard-installed user extensions
# (data/user-extensions/) and must not abort mid-restore. Two defects:
#   - validate/restore looked only under extensions/services/, so user
#     extensions were reported "no longer available" and never restored
#   - the ((enabled++)) counters return status 1 on their first increment,
#     which set -e treats as failure — load died after flipping the first
#     extension, skipping the rest, the summary, and the success message
#
# Strategy: build a throwaway INSTALL_DIR fixture (ODS_HOME) with one
# built-in and one user extension, save/craft presets, and run the real
# CLI against it. No docker required on these paths.
#
# Usage: ./tests/test-cli-preset-restore-user-extensions.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-preset-user-ext.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

# ---------------------------------------------------------------------------
# Fixture: minimal install dir the CLI accepts (check_install + sr_load)
# ---------------------------------------------------------------------------
mkdir -p "$FIXTURE/lib" "$FIXTURE/extensions/services" "$FIXTURE/data/user-extensions"
cp "$ROOT_DIR/ods-cli" "$FIXTURE/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$FIXTURE/lib/"
: > "$FIXTURE/docker-compose.base.yml"
echo "GPU_BACKEND=nvidia" > "$FIXTURE/.env"

# write_ext <dir> <id>
write_ext() {
    local dir="$1" id="$2"
    mkdir -p "$dir"
    cat > "$dir/manifest.yaml" <<EOF
schema_version: ods.services.v1
service:
  id: $id
  name: $id
  container_name: ods-$id
  health: /health
  type: docker
  gpu_backends: [all]
  compose_file: compose.yaml
  category: optional
  depends_on: []
EOF
    echo "services: {}" > "$dir/compose.yaml"
}

BUILTIN="$FIXTURE/extensions/services/bsvc"
USEREXT="$FIXTURE/data/user-extensions/usvc"
write_ext "$BUILTIN" bsvc
write_ext "$USEREXT" usvc

run_cli() {
    # Never let a non-zero CLI exit kill the test; callers assert on output/state
    ODS_HOME="$FIXTURE" bash "$FIXTURE/ods-cli" "$@" 2>&1 || true
}

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   CLI preset user-extension restore test      ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. preset save records the state of user extensions
# ---------------------------------------------------------------------------
output=$(run_cli preset save both-on)
if grep -q "^enabled:usvc$" "$FIXTURE/presets/both-on/extensions.list" 2>/dev/null; then
    pass "preset save records enabled user extension"
else
    fail "preset save missed user extension: $output"
fi

# ---------------------------------------------------------------------------
# 2. preset load does not report an existing user extension as unavailable
# ---------------------------------------------------------------------------
mv "$BUILTIN/compose.yaml" "$BUILTIN/compose.yaml.disabled"
mv "$USEREXT/compose.yaml" "$USEREXT/compose.yaml.disabled"

output=$(printf 'y\n' | run_cli preset load both-on)
if echo "$output" | grep -q "no longer available"; then
    fail "preset load reported existing user extension unavailable: $output"
else
    pass "preset load recognizes user extension as available"
fi

# ---------------------------------------------------------------------------
# 3. preset load restores both trees and survives past the first flip
# ---------------------------------------------------------------------------
if [[ -f "$BUILTIN/compose.yaml" ]]; then
    pass "preset load re-enabled built-in extension"
else
    fail "preset load did not re-enable built-in extension: $output"
fi
if [[ -f "$USEREXT/compose.yaml" ]]; then
    pass "preset load re-enabled user extension"
else
    fail "preset load did not re-enable user extension: $output"
fi
if echo "$output" | grep -q "Extensions: 2 enabled, 0 disabled"; then
    pass "preset load completed with a full summary"
else
    fail "preset load aborted before the summary: $output"
fi

# ---------------------------------------------------------------------------
# 4. preset load also disables user extensions when the preset says so
# ---------------------------------------------------------------------------
printf 'disabled:bsvc\ndisabled:usvc\n' > "$FIXTURE/presets/both-on/extensions.list"

output=$(printf 'y\n' | run_cli preset load both-on)
if [[ -f "$BUILTIN/compose.yaml.disabled" && -f "$USEREXT/compose.yaml.disabled" ]]; then
    pass "preset load disabled both extension trees"
else
    fail "preset load failed to disable extensions: $output"
fi
if echo "$output" | grep -q "Extensions: 0 enabled, 2 disabled"; then
    pass "preset load reports disable counts"
else
    fail "preset load aborted during disable pass: $output"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]] || exit 1
exit 0
