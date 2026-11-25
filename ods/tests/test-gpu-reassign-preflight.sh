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
mkdir -p "$FIXTURE/home"
ENV_BIN=$(type -P env)
UNEXPECTED_CALLS="$FIXTURE/unexpected-calls"

: > "$FAKE_INSTALL/docker-compose.base.yml"
echo "GPU_BACKEND=nvidia" > "$FAKE_INSTALL/.env"

# Expose only startup utilities, including bash for ods-cli's env shebang.
# Never append system PATH directories: an installed nvidia-smi must remain
# undiscoverable. Missing fixture prerequisites are setup failures, not skips.
ln -s "$BASH" "$STUB_BIN/bash"
for cmd in awk sed grep cut tr date basename uname dirname cat; do
    utility=$(type -P "$cmd") || { echo "Missing fixture utility: $cmd" >&2; exit 1; }
    ln -s "$utility" "$STUB_BIN/$cmd"
done
# The preflight only discovers jq; executing it would mean the test crossed
# its intended guard. Do not depend on jq being installed on the test host.
cat > "$STUB_BIN/jq" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' 'unexpected jq invocation' >> "$GPU_PREFLIGHT_TRACE"
exit 99
STUB
chmod +x "$STUB_BIN/jq"

run_cli() {
    "$ENV_BIN" -i HOME="$FIXTURE/home" ODS_HOME="$FAKE_INSTALL" \
        PATH="$1" NO_COLOR=1 GPU_PREFLIGHT_TRACE="$UNEXPECTED_CALLS" \
        "$ODS_CLI" gpu reassign
}

PASSED=0
FAILED=0

pass() { echo "  ✓ PASS: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  ✗ FAIL: $1"; FAILED=$((FAILED + 1)); }

echo "── 1. ods gpu reassign with nvidia-smi missing ──"
set +e
OUT=$(run_cli "$STUB_BIN" 2>&1)
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
OUT_APPLE=$(run_cli "$STUB_BIN" 2>&1)
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
for cmd in bash awk sed grep cut tr date basename uname dirname cat; do
    ln -s "$STUB_BIN/$cmd" "$BIN_NO_JQ/$cmd"
done

set +e
OUT_JQ=$(run_cli "$BIN_NO_JQ" 2>&1)
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

if [[ ! -e "$UNEXPECTED_CALLS" ]]; then
    pass "all cases stop before jq execution or topology detection"
else
    fail "preflight continued past the intended guard"
fi

echo ""
echo "Summary: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
