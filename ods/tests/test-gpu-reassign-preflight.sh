#!/usr/bin/env bash
# ==============================================================================
# Regression test: _gpu_reassign preflight failure exit code
#
# Verifies that ods gpu reassign exits non-zero (rc=1) when nvidia-smi is missing
# under GPU_BACKEND=nvidia, and confirms companion preflight guards are intact.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_CLI="$(cd "$SCRIPT_DIR/.." && pwd)/ods-cli"

FIXTURE=$(mktemp -d /tmp/test-gpu-reassign-preflight.XXXXXX)
FAKE_INSTALL="$FIXTURE/install"
STUB_BIN="$FIXTURE/bin"
mkdir -p "$FAKE_INSTALL" "$STUB_BIN"
trap 'rm -rf "$FIXTURE"' EXIT

: > "$FAKE_INSTALL/docker-compose.base.yml"
echo "GPU_BACKEND=nvidia" > "$FAKE_INSTALL/.env"

# Populate isolated STUB_BIN with required utilities
if command -v jq >/dev/null 2>&1; then
    ln -s "$(command -v jq)" "$STUB_BIN/jq"
fi
for cmd in awk sed grep cut tr date basename uname dirname cat; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ln -s "$(command -v "$cmd")" "$STUB_BIN/$cmd"
    fi
done

PASSED=0
FAILED=0

pass() { echo "  ✓ PASS: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  ✗ FAIL: $1"; FAILED=$((FAILED + 1)); }

echo "── 1. ods gpu reassign with nvidia-smi missing ──"
set +e
OUT=$(ODS_HOME="$FAKE_INSTALL" PATH="$STUB_BIN:/usr/bin:/bin" "$ODS_CLI" gpu reassign 2>&1)
RC=$?
set -e

if [[ $RC -eq 1 ]]; then
    pass "ods gpu reassign exits 1 when nvidia-smi is missing"
else
    fail "expected rc=1 when nvidia-smi is missing, got rc=$RC (output: $OUT)"
fi

if echo "$OUT" | grep -q "nvidia-smi not found — GPU reassignment unavailable"; then
    pass "emits expected unavailable warning"
else
    fail "missing warning message: $OUT"
fi

echo "── 2. companion guard: apple silicon exit 1 ──"
echo "GPU_BACKEND=apple" > "$FAKE_INSTALL/.env"
set +e
OUT_APPLE=$(ODS_HOME="$FAKE_INSTALL" PATH="$STUB_BIN:/usr/bin:/bin" "$ODS_CLI" gpu reassign 2>&1)
RC_APPLE=$?
set -e

if [[ $RC_APPLE -eq 1 ]]; then
    pass "apple silicon guard exits 1"
else
    fail "expected rc=1 for apple backend, got rc=$RC_APPLE"
fi

if echo "$OUT_APPLE" | grep -q "not applicable on Apple Silicon"; then
    pass "emits apple silicon warning"
else
    fail "missing apple silicon warning: $OUT_APPLE"
fi

echo "── 3. companion guard: missing jq exit 1 ──"
echo "GPU_BACKEND=nvidia" > "$FAKE_INSTALL/.env"
BIN_NO_JQ="$FIXTURE/bin_no_jq"
mkdir -p "$BIN_NO_JQ"
if command -v bash >/dev/null 2>&1; then
    ln -s "$(command -v bash)" "$BIN_NO_JQ/bash"
fi
for cmd in awk sed grep cut tr date basename uname dirname cat; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ln -s "$(command -v "$cmd")" "$BIN_NO_JQ/$cmd"
    fi
done

set +e
OUT_JQ=$(ODS_HOME="$FAKE_INSTALL" PATH="$BIN_NO_JQ" "$ODS_CLI" gpu reassign 2>&1)
RC_JQ=$?
set -e

if [[ $RC_JQ -eq 1 ]]; then
    pass "missing jq guard exits 1"
else
    fail "expected rc=1 when jq is missing, got rc=$RC_JQ"
fi

if echo "$OUT_JQ" | grep -q "jq not found — required for GPU reassignment"; then
    pass "emits missing jq warning"
else
    fail "missing jq warning: $OUT_JQ"
fi

echo ""
echo "Summary: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
