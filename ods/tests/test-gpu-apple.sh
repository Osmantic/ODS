#!/usr/bin/env bash
# ============================================================================
# ODS CLI — Apple Silicon GPU subcommand coverage
# ============================================================================
# Mock-based coverage of the Apple Silicon branches added in
# fix/ods-cli-apple-silicon-coverage to:
#   * _gpu_status      — Chip/Unified memory/GPU cores panel
#   * _gpu_topology    — "Single integrated GPU" message
#   * _gpu_validate    — clean 0/0 pass-through
#   * _gpu_reassign    — "not applicable on Apple Silicon" (exit 1)
#   * cmd_status_json  — JSON .gpu object with backend/chip/unified_memory_gb/gpu_cores
#
# Verifies each subcommand under GPU_BACKEND=apple and includes negative
# cases asserting nvidia/amd backends do NOT take the apple path.
#
# External binaries (sysctl, system_profiler, nvidia-smi, curl, docker) are
# stubbed via a PATH-prepended fixture directory so the test is hermetic and
# runs on any host.
#
# Usage: ./tests/test-gpu-apple.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   ODS CLI GPU_BACKEND=apple Coverage        ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ods-cli requires Bash 4+. `/usr/bin/env bash` picks the first bash on
# PATH; on stock macOS that's /bin/bash (3.2). Bail cleanly with guidance
# rather than producing a confusing failure downstream.
if ! env bash -c '(( BASH_VERSINFO[0] >= 4 ))' 2>/dev/null; then
    # SC2016: single-quoted string is evaluated by the child bash, not this shell
    # shellcheck disable=SC2016
    _probe=$(env bash -c 'echo "${BASH_VERSION:-unknown}"' 2>/dev/null || echo "unknown")
    echo "[SKIP] ods-cli requires Bash 4+ on PATH (env bash -> $_probe)."
    echo "       Install via 'brew install bash' or run with PATH including bash 4+."
    exit 0
fi

[[ -x "$ODS_CLI" ]] || { fail "ods-cli not executable at $ODS_CLI"; exit 1; }
command -v jq >/dev/null 2>&1 || { fail "jq is required"; exit 1; }

# ----------------------------------------------------------------------------
# Fixture: fake ODS_HOME satisfying check_install + a stub PATH
# ----------------------------------------------------------------------------
FIXTURE=$(mktemp -d /tmp/test-gpu-apple.XXXXXX)
FAKE_INSTALL="$FIXTURE/install"
STUB_BIN="$FIXTURE/stubs"
mkdir -p "$FAKE_INSTALL" "$STUB_BIN"
trap 'rm -rf "$FIXTURE"' EXIT

# check_install only requires ODS_HOME dir to exist with docker-compose.base.yml
: > "$FAKE_INSTALL/docker-compose.base.yml"

# Helper: rewrite the fixture .env with a given GPU_BACKEND value.
# load_env_file (safe-env.sh) re-exports any key present in .env, so the
# .env value overrides the caller's env. We swap .env per test case.
write_env() {
    local backend="$1"
    cat > "$FAKE_INSTALL/.env" <<EOF
GPU_BACKEND=$backend
HOST_RAM_GB=32
ODS_VERSION=test
ODS_MODE=local
TIER=1
LLM_MODEL=test-model
EOF
}

# ----------------------------------------------------------------------------
# Stubs — prepend to PATH so real tools never run.
# Each stub is intentionally minimal and deterministic.
# ----------------------------------------------------------------------------
cat > "$STUB_BIN/sysctl" <<'STUB'
#!/usr/bin/env bash
# Test stub: emulate macOS sysctl -n <key>
case "${*}" in
    *"hw.memsize"*)              echo "34359738368" ;;  # 32 GiB exactly
    *"machdep.cpu.brand_string"*) echo "Apple M2 Max (stub)" ;;
    *) exit 0 ;;
esac
STUB

cat > "$STUB_BIN/system_profiler" <<'STUB'
#!/usr/bin/env bash
# Test stub: emulate `system_profiler SPDisplaysDataType -json`.
# Returns a minimal payload whose .SPDisplaysDataType[0].sppci_cores is "38".
if [[ "${1:-}" == "SPDisplaysDataType" ]]; then
    cat <<'JSON'
{"SPDisplaysDataType":[{"sppci_cores":"38"}]}
JSON
    exit 0
fi
exit 0
STUB

cat > "$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
# Test stub: always unhealthy so status probes fail fast and don't hang.
exit 1
STUB

cat > "$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
# Test stub: emit empty output for any subcommand so compose ps returns
# no running services without actually talking to a daemon.
exit 0
STUB

chmod +x "$STUB_BIN"/*

# Deliberately do NOT stub nvidia-smi. `command -v nvidia-smi` must return
# false under GPU_BACKEND=apple so the apple branch is the only one taken.
# For negative tests we rely on nvidia-smi being absent from PATH too.

STUB_PATH="$STUB_BIN:$PATH"

# Run ods-cli under the fixture. Returns captured stdout+stderr via $OUT,
# rc via $RC (using globals to keep set -e friendly).
run_ods_cli() {
    set +e
    OUT=$(ODS_HOME="$FAKE_INSTALL" PATH="$STUB_PATH" "$ODS_CLI" "$@" 2>&1)
    RC=$?
    set -e
}

# ----------------------------------------------------------------------------
# Case 1: ods gpu status under GPU_BACKEND=apple
# ----------------------------------------------------------------------------
echo "── 1. gpu status (apple) ──"
write_env apple
run_ods_cli gpu status

if [[ $RC -eq 0 ]]; then
    pass "ods gpu status exits 0"
else
    fail "ods gpu status exited $RC; output: $OUT"
fi

# Header: nvidia-smi absent → gpu_count=0 so header reads "GPU Status (0 GPUs)".
# We only assert the "━━━ GPU Status" prefix is present; the count is a
# cosmetic artefact of the shared header, not a regression target.
if echo "$OUT" | grep -q "━━━ GPU Status"; then
    pass "output contains '━━━ GPU Status' header"
else
    fail "missing '━━━ GPU Status' header; output: $OUT"
fi

for expected in "Chip:" "Unified memory:" "GPU cores:"; do
    if echo "$OUT" | grep -qF "$expected"; then
        pass "output body contains '$expected'"
    else
        fail "output body missing '$expected'; output: $OUT"
    fi
done

# Stubbed sysctl exposes brand_string "Apple M2 Max (stub)"; assert the
# apple branch actually consumed the stub (not a canned fallback string).
if echo "$OUT" | grep -q "Apple M2 Max (stub)"; then
    pass "output reflects sysctl stub chip value (apple branch consumed stub)"
else
    fail "output does not reflect sysctl stub; apple branch may be reading elsewhere"
fi

# ----------------------------------------------------------------------------
# Case 2: ods gpu topology under GPU_BACKEND=apple
# ----------------------------------------------------------------------------
echo "── 2. gpu topology (apple) ──"
run_ods_cli gpu topology

if [[ $RC -eq 0 ]]; then
    pass "ods gpu topology exits 0"
else
    fail "ods gpu topology exited $RC; output: $OUT"
fi

if echo "$OUT" | grep -q "Single integrated GPU"; then
    pass "output contains 'Single integrated GPU'"
else
    fail "output missing 'Single integrated GPU'; output: $OUT"
fi

# ----------------------------------------------------------------------------
# Case 3: ods gpu validate under GPU_BACKEND=apple
# ----------------------------------------------------------------------------
echo "── 3. gpu validate (apple) ──"
run_ods_cli gpu validate

if [[ $RC -eq 0 ]]; then
    pass "ods gpu validate exits 0"
else
    fail "ods gpu validate exited $RC; output: $OUT"
fi

if echo "$OUT" | grep -q "Result: 0 check(s) passed, 0 failed"; then
    pass "output contains 'Result: 0 check(s) passed, 0 failed'"
else
    fail "output missing expected apple validate summary; output: $OUT"
fi

# ----------------------------------------------------------------------------
# Case 4: ods gpu reassign under GPU_BACKEND=apple (must exit 1)
# ----------------------------------------------------------------------------
echo "── 4. gpu reassign (apple) ──"
run_ods_cli gpu reassign

if [[ $RC -ne 0 ]]; then
    pass "ods gpu reassign exits non-zero on Apple Silicon (rc=$RC)"
else
    fail "ods gpu reassign should exit non-zero on Apple Silicon; got rc=0; output: $OUT"
fi

if echo "$OUT" | grep -qE "not applicable on Apple Silicon"; then
    pass "output contains 'not applicable on Apple Silicon'"
else
    fail "output missing 'not applicable on Apple Silicon'; output: $OUT"
fi

# ----------------------------------------------------------------------------
# Case 5: ods status-json under GPU_BACKEND=apple
# ----------------------------------------------------------------------------
echo "── 5. status-json (apple) ──"
run_ods_cli status-json

if [[ $RC -eq 0 ]]; then
    pass "ods status-json exits 0"
else
    fail "ods status-json exited $RC; output: $OUT"
fi

# status-json emits informational lines on stderr (merged into OUT). Extract
# the JSON document by peeling off everything before the first '{'.
JSON_ONLY=$(echo "$OUT" | awk '/^[[:space:]]*\{/{flag=1} flag')
if echo "$JSON_ONLY" | jq empty >/dev/null 2>&1; then
    pass "status-json output contains valid JSON"

    backend=$(echo "$JSON_ONLY" | jq -r '.gpu.backend // "MISSING"')
    chip=$(echo "$JSON_ONLY" | jq -r '.gpu.chip // "MISSING"')
    mem_type=$(echo "$JSON_ONLY" | jq -r '.gpu.unified_memory_gb | type')
    cores=$(echo "$JSON_ONLY" | jq -r '.gpu.gpu_cores // "MISSING"')

    if [[ "$backend" == "apple" ]]; then
        pass ".gpu.backend == 'apple'"
    else
        fail ".gpu.backend expected 'apple', got '$backend'"
    fi

    if [[ "$chip" == "Apple M2 Max (stub)" ]]; then
        pass ".gpu.chip reflects sysctl stub ('$chip')"
    else
        fail ".gpu.chip expected 'Apple M2 Max (stub)', got '$chip'"
    fi

    if [[ "$mem_type" == "number" ]]; then
        pass ".gpu.unified_memory_gb is a JSON number"
    else
        fail ".gpu.unified_memory_gb expected number, got type '$mem_type'"
    fi

    if [[ "$cores" == "38" ]]; then
        pass ".gpu.gpu_cores reflects system_profiler stub ('38')"
    else
        fail ".gpu.gpu_cores expected '38', got '$cores'"
    fi
else
    fail "status-json did not produce valid JSON; output: $OUT"
fi

# ----------------------------------------------------------------------------
# Case 6 (negative): GPU_BACKEND=nvidia with no nvidia-smi → no apple output
# ----------------------------------------------------------------------------
echo "── 6. gpu status (nvidia, negative) ──"
write_env nvidia
run_ods_cli gpu status

if echo "$OUT" | grep -qE "Unified memory:|Single integrated GPU|Apple Silicon"; then
    fail "nvidia backend incorrectly produced Apple-branch output"
else
    pass "nvidia backend does not produce Apple-branch output"
fi

if echo "$OUT" | grep -qE "nvidia-smi not found|GPU status unavailable"; then
    pass "nvidia backend without nvidia-smi emits fallthrough warning"
else
    fail "nvidia backend fallthrough warning missing; output: $OUT"
fi

# ----------------------------------------------------------------------------
# Case 7 (negative): GPU_BACKEND=amd with no AMD tooling → no apple output
# ----------------------------------------------------------------------------
echo "── 7. gpu status (amd, negative) ──"
write_env amd
run_ods_cli gpu status

if echo "$OUT" | grep -qE "Unified memory:|Single integrated GPU|Apple Silicon"; then
    fail "amd backend incorrectly produced Apple-branch output"
else
    pass "amd backend does not produce Apple-branch output"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
