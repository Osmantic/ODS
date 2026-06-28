#!/usr/bin/env bash
# ============================================================================
# Contract test: ods-doctor.sh Darwin fixture
# ============================================================================
# Regression shields for the Darwin branch of scripts/ods-doctor.sh added in
# fix/ods-cli-apple-silicon-coverage:
#   * sysctl hw.memsize → RAM_GB (no /proc/meminfo on macOS)
#   * POSIX df -k on $HOME → DISK_GB (GNU df -BG is not portable)
#   * empty sysctl output falls back to .env HOST_RAM_GB
#   * GPU_BACKEND=apple suppresses "gpu_backend_incompatible" autofix hints
#
# Guarded with `uname -s` — silently skips on Linux/CI runners.
# ============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[SKIP] ods-doctor Darwin fixture — macOS only"
    exit 0
fi

command -v jq >/dev/null 2>&1 || { echo "[FAIL] jq is required"; exit 1; }

# Defensive: never clobber a pre-existing .env in the repo worktree. The
# fallback case writes a throw-away .env — refuse to run if one already
# exists to avoid destroying a developer's local environment.
if [[ -f "$ROOT_DIR/.env" ]]; then
    echo "[SKIP] ods-doctor Darwin fixture — pre-existing $ROOT_DIR/.env (would clobber)"
    exit 0
fi

PASS=0
FAIL=0
pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

TMPDIR_TEST=$(mktemp -d /tmp/test-ods-doctor-darwin.XXXXXX)
STUB_BIN="$TMPDIR_TEST/stubs"
mkdir -p "$STUB_BIN"
CREATED_ENV=""
cleanup() {
    rm -rf "$TMPDIR_TEST"
    if [[ -n "$CREATED_ENV" && -f "$CREATED_ENV" ]]; then
        rm -f "$CREATED_ENV"
    fi
}
trap cleanup EXIT

REPORT="$TMPDIR_TEST/report.json"

# ----------------------------------------------------------------------------
# Case 1: live run under GPU_BACKEND=apple
# Asserts sysctl-based RAM and POSIX df-based disk paths produce positive
# integers, and the apple-backend guard suppresses gpu_backend_incompatible
# hints emitted by collect_extension_diagnostics.
# ----------------------------------------------------------------------------
set +e
GPU_BACKEND=apple bash scripts/ods-doctor.sh "$REPORT" \
    >"$TMPDIR_TEST/live.log" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
    pass "ods-doctor.sh exits 0 on Darwin under GPU_BACKEND=apple"
else
    fail "ods-doctor.sh exited non-zero on Darwin (rc=$rc); see $TMPDIR_TEST/live.log"
fi

if [[ -f "$REPORT" ]]; then
    ram_gb=$(jq -r '.preflight.inputs.ram_gb' "$REPORT")
    if [[ "$ram_gb" =~ ^[0-9]+$ ]] && (( ram_gb > 0 )); then
        pass "preflight.inputs.ram_gb is a positive integer ($ram_gb) via sysctl hw.memsize"
    else
        fail "preflight.inputs.ram_gb expected positive integer, got: '$ram_gb'"
    fi

    disk_gb=$(jq -r '.preflight.inputs.disk_gb' "$REPORT")
    if [[ "$disk_gb" =~ ^[0-9]+$ ]] && (( disk_gb > 0 )); then
        pass "preflight.inputs.disk_gb is a positive integer ($disk_gb) via POSIX df -k on \$HOME"
    else
        fail "preflight.inputs.disk_gb expected positive integer, got: '$disk_gb'"
    fi

    incompat=$(jq '[.autofix_hints[] | select(contains("incompatible with current GPU backend"))] | length' "$REPORT")
    if [[ "$incompat" == "0" ]]; then
        pass "autofix_hints has zero 'incompatible with current GPU backend' entries under GPU_BACKEND=apple"
    else
        fail "autofix_hints has $incompat 'incompatible with current GPU backend' entries (expected 0 under apple)"
    fi
else
    fail "ods-doctor.sh did not produce report at $REPORT"
fi

# ----------------------------------------------------------------------------
# Case 2: empty sysctl output → fall back to .env HOST_RAM_GB
# Simulates the edge case where `sysctl -n hw.memsize` returns empty (e.g.
# sandboxed process without entitlements). Script must then consult .env.
# ----------------------------------------------------------------------------
cat > "$STUB_BIN/sysctl" <<'STUB'
#!/usr/bin/env bash
# Test stub: returns empty string (exit 0) for any sysctl query.
# Drives the RAM_BYTES=empty → RAM_GB=0 → .env fallback branch.
exit 0
STUB
chmod +x "$STUB_BIN/sysctl"

CREATED_ENV="$ROOT_DIR/.env"
cat > "$CREATED_ENV" <<'ENV'
HOST_RAM_GB=7777
ENV

rm -f "$REPORT"
set +e
PATH="$STUB_BIN:$PATH" GPU_BACKEND=apple \
    bash scripts/ods-doctor.sh "$REPORT" \
    >"$TMPDIR_TEST/fallback.log" 2>&1
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
    fail "ods-doctor.sh exited non-zero under empty-sysctl stub (rc=$rc); see $TMPDIR_TEST/fallback.log"
elif [[ -f "$REPORT" ]]; then
    ram_gb_fallback=$(jq -r '.preflight.inputs.ram_gb' "$REPORT")
    if [[ "$ram_gb_fallback" == "7777" ]]; then
        pass "empty sysctl output falls back to .env HOST_RAM_GB (got 7777)"
    else
        fail "expected fallback to .env HOST_RAM_GB=7777, got: '$ram_gb_fallback'"
    fi
else
    fail "ods-doctor.sh did not produce report under fallback test"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
