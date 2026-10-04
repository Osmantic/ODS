#!/usr/bin/env bash
# test-bootstrap-mode.sh must not fail on a pre-install checkout (#5633),
# and must still fail on a genuinely invalid resolved stack.
#
# Test 2 hard-coded `docker compose -f docker-compose.yml config`. That file is
# the resolved stack the installer writes, so on a bare clone the command fails
# because the file is absent — and the suite, listed as a QA step in
# docs/OSS-LAUNCH-CHECKLIST.md, could only ever report
# "Invalid compose configuration".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUITE="$ROOT/tests/test-bootstrap-mode.sh"

PASS=0; FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# A fixture ODS_DIR with everything the suite checks except the resolved stack.
stage() {
    local dir="$1"
    rm -rf "$dir"; mkdir -p "$dir/scripts" "$dir/tests"
    cp "$ROOT/docker-compose.base.yml" "$dir/" 2>/dev/null || echo "services: {}" > "$dir/docker-compose.base.yml"
    printf '#!/bin/sh\nexit 0\n' > "$dir/scripts/upgrade-model.sh"
    chmod +x "$dir/scripts/upgrade-model.sh"
    printf 'LLM_MODEL=qwen2.5-1.5b-instruct\n' > "$dir/.env.example"
}

run_suite() {
    set +e
    ODS_DIR="$1" bash "$SUITE" > "$TMP/out" 2>&1
    echo $?
    set -e
}

# ── 1. pre-install checkout: no resolved docker-compose.yml ─────────────────
stage "$TMP/pre"
rc="$(run_suite "$TMP/pre")"
if [[ "$rc" == "0" ]]; then
    pass "pre-install checkout passes (exit 0)"
else
    fail "pre-install checkout still fails (exit $rc)"
    sed 's/^/       /' "$TMP/out"
fi
if grep -q "skipping compose validation" "$TMP/out"; then
    pass "validation is reported as skipped, not silently dropped"
else
    fail "no skip message; the operator cannot tell validation did not run"
fi
if grep -q "Invalid compose configuration" "$TMP/out"; then
    fail "still reports the false 'Invalid compose configuration'"
else
    pass "no false invalid-compose failure"
fi

# ── 2. a resolved stack that is genuinely broken must still fail ────────────
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    stage "$TMP/bad"
    printf 'services:\n  broken: [this is not a mapping\n' > "$TMP/bad/docker-compose.yml"
    rc="$(run_suite "$TMP/bad")"
    if [[ "$rc" != "0" ]] && grep -q "Invalid compose configuration" "$TMP/out"; then
        pass "an invalid resolved stack is still caught"
    else
        fail "invalid stack was NOT caught (exit $rc) — the guard is too broad"
        sed 's/^/       /' "$TMP/out"
    fi

    # ── 3. a valid resolved stack still validates ──────────────────────────
    stage "$TMP/good"
    printf 'services:\n  hello:\n    image: alpine:3.20\n' > "$TMP/good/docker-compose.yml"
    rc="$(run_suite "$TMP/good")"
    if [[ "$rc" == "0" ]]; then
        pass "a valid resolved stack still passes"
    else
        fail "valid stack rejected (exit $rc)"
        sed 's/^/       /' "$TMP/out"
    fi
else
    echo "[SKIP] docker compose unavailable; cannot exercise the validating paths"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
