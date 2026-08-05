#!/bin/bash
# Contracts for scripts that pick a Python interpreter for themselves.
#
# `command -v python3` succeeding is not proof of a usable interpreter. On
# Windows the Microsoft Store app-execution alias ships on PATH by default: it
# resolves like a real binary, then refuses to run and prints an advert. Any
# script that selects an interpreter must probe that it executes — that is what
# lib/python-cmd.sh is for.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK_SH="$ROOT_DIR/scripts/check-offline-models.sh"
BUNDLE_SH="$ROOT_DIR/scripts/ods-support-bundle.sh"
PYTHON_CMD_LIB="$ROOT_DIR/lib/python-cmd.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS + 1)); }
skip() { echo -e "${YELLOW}[SKIP]${NC} $1"; }
fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    [[ -n "${2:-}" ]] && echo "       $2"
    FAIL=$((FAIL + 1))
}

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# A `python3` shaped like the Store alias: found by `command -v`, refuses to run.
STUB_BIN="$TMP_ROOT/stubbin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/python3" <<'STUB'
#!/bin/sh
echo "Python was not found; run without arguments to install from the Microsoft Store," >&2
echo "or disable this shortcut from Settings > Apps > App execution aliases." >&2
exit 49
STUB
chmod +x "$STUB_BIN/python3"

# The fallback the resolver is expected to reach instead.
REAL_PYTHON=""
for _candidate in python3 python; do
    if command -v "$_candidate" >/dev/null 2>&1 \
       && "$_candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
        REAL_PYTHON="$(command -v "$_candidate")"
        break
    fi
done

if [[ -z "$REAL_PYTHON" ]]; then
    skip "no working Python on this host — resolver contracts need one"
    echo ""
    echo "Passed: $PASS  Failed: $FAIL"
    exit 0
fi

# ---------------------------------------------------------------------------
# Both entry points route through the shared resolver
# ---------------------------------------------------------------------------
for script in "$CHECK_SH" "$BUNDLE_SH"; do
    name="$(basename "$script")"
    if grep -q 'lib/python-cmd.sh' "$script"; then
        pass "$name sources the shared Python resolver"
    else
        fail "$name hand-rolls Python detection instead of sourcing lib/python-cmd.sh"
    fi
done

# ---------------------------------------------------------------------------
# check-offline-models.sh: end to end with the alias shadowing python3
# ---------------------------------------------------------------------------
FIXTURE="$TMP_ROOT/install"
mkdir -p "$FIXTURE/scripts" "$FIXTURE/lib" "$FIXTURE/data/models"
cp "$CHECK_SH" "$ROOT_DIR/scripts/validate-models.py" "$FIXTURE/scripts/"
cp "$PYTHON_CMD_LIB" "$FIXTURE/lib/"

run_checker() {
    local extra_path="$1"
    shift
    ( cd "$FIXTURE/scripts" \
      && PATH="$extra_path:$PATH" env "$@" bash check-offline-models.sh ) \
        >"$TMP_ROOT/checker.out" 2>&1
}

set +e
run_checker "$STUB_BIN"
CHECKER_RC=$?
set -e
CHECKER_OUT="$(cat "$TMP_ROOT/checker.out")"

if [[ "$CHECKER_RC" -ne 49 ]]; then
    pass "check-offline-models.sh does not exec the unusable python3"
else
    fail "check-offline-models.sh exec'd the unusable python3" "rc=$CHECKER_RC"
fi

if ! grep -q "Microsoft Store" <<<"$CHECKER_OUT"; then
    pass "check-offline-models.sh does not surface the Store advert"
else
    fail "check-offline-models.sh surfaced the Store advert instead of a report" \
         "$CHECKER_OUT"
fi

if grep -q "ODS Offline Mode" <<<"$CHECKER_OUT"; then
    pass "check-offline-models.sh still produces a readiness report"
else
    fail "check-offline-models.sh produced no readiness report" "$CHECKER_OUT"
fi

# ODS_PYTHON_CMD is the documented override and must win over PATH order.
set +e
run_checker "$STUB_BIN" "ODS_PYTHON_CMD=$REAL_PYTHON"
OVERRIDE_RC=$?
set -e
if [[ "$OVERRIDE_RC" -ne 49 ]] && grep -q "ODS Offline Mode" "$TMP_ROOT/checker.out"; then
    pass "check-offline-models.sh honours ODS_PYTHON_CMD"
else
    fail "check-offline-models.sh ignored ODS_PYTHON_CMD" \
         "rc=$OVERRIDE_RC$(printf '\n')$(cat "$TMP_ROOT/checker.out")"
fi

# ---------------------------------------------------------------------------
# ods-support-bundle.sh: detect_python in isolation
# ---------------------------------------------------------------------------
FN_FILE="$TMP_ROOT/detect_python.sh"
awk '/^detect_python\(\) \{/,/^\}/' "$BUNDLE_SH" > "$FN_FILE"

if [[ -s "$FN_FILE" ]] && grep -q '^}' "$FN_FILE"; then
    pass "detect_python() extracted from ods-support-bundle.sh"
else
    fail "could not extract detect_python() from ods-support-bundle.sh"
fi

RESOLVED="$(
    PATH="$STUB_BIN:$PATH" ROOT_DIR="$ROOT_DIR" bash -c '
        . "$0"
        detect_python
    ' "$FN_FILE" 2>/dev/null || true
)"

if [[ -n "$RESOLVED" ]] && "$RESOLVED" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    pass "ods-support-bundle.sh detect_python returns a runnable interpreter"
else
    fail "ods-support-bundle.sh detect_python returned an unusable interpreter" \
         "got: ${RESOLVED:-<empty>}"
fi

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
echo "[PASS] python resolver entry-point contracts"
