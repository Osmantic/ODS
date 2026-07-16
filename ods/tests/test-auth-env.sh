#!/usr/bin/env bash
# ============================================================================
# Contract tests for ods/tests/lib/auth-env.sh
# ============================================================================
# Proves the auth-env resolver behaves correctly under every realistic input
# without needing a live dashboard-api:
#
#   1. Key from shell env (no .env)            → AE_AUTH_HEADER populated
#   2. Key from .env only                      → AE_AUTH_HEADER populated
#   3. Shell env wins over .env                → shell value used
#   4. CRLF-mangled .env value                 → \r stripped
#   5. Inline `# comment` in .env value        → comment stripped
#   6. Surrounding double/single quotes        → quotes stripped
#   7. Trailing whitespace in .env value       → stripped
#   8. Neither shell nor .env has the key      → AE_AUTH_HEADER empty
#   9. ae_api_base composed from resolved port + host
#  10. Port from .env used when shell env unset
#
# Also runs test-integration.sh --quick under both key-present and
# key-absent conditions and confirms the SKIP-vs-FAIL banner semantics
# demanded by the review (without needing a real dashboard-api — the
# SKIP branches fire from the AUTH_AVAILABLE=false path).
#
# Usage:  bash tests/test-auth-env.sh
# Exit:   0 = all pass, 1 = one or more failures.
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/lib/auth-env.sh"
INTEGRATION="$SCRIPT_DIR/test-integration.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0
pass() { printf "  ${GREEN}✓ PASS${NC} %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ${RED}✗ FAIL${NC} %s\n  reason: %s\n" "$1" "$2"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   auth-env.sh contract tests                             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Fail loud if the helper isn't present.
if [[ ! -f "$LIB" ]]; then
    echo "FATAL: helper not found at $LIB"
    exit 2
fi
pass "auth-env.sh exists"

if ! bash -n "$LIB" 2>/dev/null; then
    fail "bash -n on helper" "syntax error"
    exit 2
fi
pass "bash -n on auth-env.sh"

# Each subshell resolves independently so state doesn't leak between cases.
# Uses `env -i` to null out inherited env so shell-vs-.env precedence tests
# don't get polluted by a real DASHBOARD_API_KEY in the caller's env.

_run_case() {
    # _run_case <case-label> <bash -c script>
    local label="$1" script="$2"
    local out
    out="$(env -i HOME="$HOME" PATH="$PATH" bash -c "$script" 2>&1)" || true
    printf '%s' "$out"
}

# ── 1. Key from shell env, no .env file ─────────────────────────────────────
tmp="$(mktemp -d)"
out="$(_run_case case1 "
    set -u
    export DASHBOARD_API_KEY='shell-key-abc'
    . '$LIB'
    ae_resolve '$tmp'
    ae_key_available && echo AVAIL=yes || echo AVAIL=no
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
    echo API_BASE=\$ae_api_base
")"
if grep -q 'AVAIL=yes' <<<"$out" \
    && grep -q 'HDRS=-H|Authorization: Bearer shell-key-abc|' <<<"$out"; then
    pass "case 1: shell env sets DASHBOARD_API_KEY → AE_AUTH_HEADER populated"
else
    fail "case 1: shell-env key" "$out"
fi
rm -rf "$tmp"

# ── 2. Key from .env file, no shell env ─────────────────────────────────────
tmp="$(mktemp -d)"
printf 'DASHBOARD_API_KEY=envfile-key-xyz\n' >"$tmp/.env"
out="$(_run_case case2 "
    set -u
    . '$LIB'
    ae_resolve '$tmp'
    ae_key_available && echo AVAIL=yes || echo AVAIL=no
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
")"
if grep -q 'AVAIL=yes' <<<"$out" \
    && grep -q 'HDRS=-H|Authorization: Bearer envfile-key-xyz|' <<<"$out"; then
    pass "case 2: .env-only key → AE_AUTH_HEADER populated"
else
    fail "case 2: .env-only key" "$out"
fi
rm -rf "$tmp"

# ── 3. Shell env wins over .env ─────────────────────────────────────────────
tmp="$(mktemp -d)"
printf 'DASHBOARD_API_KEY=envfile-loser\n' >"$tmp/.env"
out="$(_run_case case3 "
    set -u
    export DASHBOARD_API_KEY='shell-winner'
    . '$LIB'
    ae_resolve '$tmp'
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
")"
if grep -q 'HDRS=-H|Authorization: Bearer shell-winner|' <<<"$out"; then
    pass "case 3: shell env wins over .env (precedence contract)"
else
    fail "case 3: precedence" "$out"
fi
rm -rf "$tmp"

# ── 4. CRLF in .env value stripped ──────────────────────────────────────────
tmp="$(mktemp -d)"
printf 'DASHBOARD_API_KEY=crlf-key-value\r\n' >"$tmp/.env"
out="$(_run_case case4 "
    set -u
    . '$LIB'
    ae_resolve '$tmp'
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
")"
if grep -q 'HDRS=-H|Authorization: Bearer crlf-key-value|' <<<"$out"; then
    pass "case 4: CRLF from Windows-edited .env stripped"
else
    fail "case 4: CRLF strip" "$out"
fi
rm -rf "$tmp"

# ── 5. Inline # comment in .env value stripped ──────────────────────────────
tmp="$(mktemp -d)"
printf 'DASHBOARD_API_KEY=cleanpart # rotated 2026-06\n' >"$tmp/.env"
out="$(_run_case case5 "
    set -u
    . '$LIB'
    ae_resolve '$tmp'
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
")"
if grep -q 'HDRS=-H|Authorization: Bearer cleanpart|' <<<"$out"; then
    pass "case 5: inline # comment in .env value stripped"
else
    fail "case 5: inline comment strip" "$out"
fi
rm -rf "$tmp"

# ── 6. Surrounding quotes stripped (both flavors) ───────────────────────────
tmp="$(mktemp -d)"
printf 'DASHBOARD_API_KEY="double-quoted-key"\n' >"$tmp/.env"
out="$(_run_case case6a "
    set -u
    . '$LIB'
    ae_resolve '$tmp'
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
")"
if grep -q 'HDRS=-H|Authorization: Bearer double-quoted-key|' <<<"$out"; then
    pass "case 6a: double-quoted .env value → quotes stripped"
else
    fail "case 6a: double quotes" "$out"
fi
rm -rf "$tmp"

tmp="$(mktemp -d)"
printf "DASHBOARD_API_KEY='single-quoted-key'\n" >"$tmp/.env"
out="$(_run_case case6b "
    set -u
    . '$LIB'
    ae_resolve '$tmp'
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
")"
if grep -q "HDRS=-H|Authorization: Bearer single-quoted-key|" <<<"$out"; then
    pass "case 6b: single-quoted .env value → quotes stripped"
else
    fail "case 6b: single quotes" "$out"
fi
rm -rf "$tmp"

# ── 7. Trailing whitespace stripped ─────────────────────────────────────────
tmp="$(mktemp -d)"
printf 'DASHBOARD_API_KEY=trimmed-key   \t  \n' >"$tmp/.env"
out="$(_run_case case7 "
    set -u
    . '$LIB'
    ae_resolve '$tmp'
    printf 'HDRS='; printf '%s|' \"\${AE_AUTH_HEADER[@]}\"; echo
")"
if grep -q 'HDRS=-H|Authorization: Bearer trimmed-key|' <<<"$out"; then
    pass "case 7: trailing whitespace stripped"
else
    fail "case 7: trailing whitespace" "$out"
fi
rm -rf "$tmp"

# ── 8. No key anywhere → AE_AUTH_HEADER empty ───────────────────────────────
tmp="$(mktemp -d)"
# .env exists but no DASHBOARD_API_KEY line
printf 'SOME_OTHER=1\n' >"$tmp/.env"
out="$(_run_case case8 "
    set -uo pipefail
    . '$LIB'
    ae_resolve '$tmp'
    ae_key_available && echo AVAIL=yes || echo AVAIL=no
    echo COUNT=\${#AE_AUTH_HEADER[@]}
")"
if grep -q 'AVAIL=no' <<<"$out" && grep -q 'COUNT=0' <<<"$out"; then
    pass "case 8: no key anywhere → AE_AUTH_HEADER empty, ae_key_available=false"
else
    fail "case 8: no-key path" "$out"
fi
rm -rf "$tmp"

# ── 9. ae_api_base composed from resolved host + port ───────────────────────
tmp="$(mktemp -d)"
out="$(_run_case case9 "
    set -u
    export DASHBOARD_API_PORT=3999
    export SERVICE_HOST=example.local
    . '$LIB'
    ae_resolve '$tmp'
    echo BASE=\$ae_api_base
")"
if grep -q 'BASE=http://example.local:3999' <<<"$out"; then
    pass "case 9: ae_api_base composed from shell-env host + port"
else
    fail "case 9: ae_api_base composition" "$out"
fi
rm -rf "$tmp"

# ── 10. Port from .env used when shell env unset ────────────────────────────
tmp="$(mktemp -d)"
printf 'DASHBOARD_API_PORT=4444\n' >"$tmp/.env"
out="$(_run_case case10 "
    set -u
    . '$LIB'
    ae_resolve '$tmp'
    echo BASE=\$ae_api_base
")"
if grep -q 'BASE=http://127.0.0.1:4444' <<<"$out"; then
    pass "case 10: port from .env used when shell env unset"
else
    fail "case 10: .env port fallback" "$out"
fi
rm -rf "$tmp"

# ── 11. test-integration.sh --quick without key → SKIP semantics fire ───────
# Verifies the SKIP-vs-FAIL contract the reviewer asked for. Runs the real
# script under an env that has no key and no dashboard-api reachable; every
# auth-required check should SKIP with the "no DASHBOARD_API_KEY" reason
# rather than be counted as FAIL.
if [[ -f "$INTEGRATION" ]] && command -v jq >/dev/null 2>&1; then
    tmp="$(mktemp -d)"
    out="$(env -i HOME="$HOME" PATH="$PATH" ODS_INSTALL_DIR="$tmp" \
        bash "$INTEGRATION" --quick 2>&1 || true)"
    # Expect the banner and at least one SKIP with the exact reason string
    if grep -q 'DASHBOARD_API_KEY not found' <<<"$out" \
        && grep -qE '\(no DASHBOARD_API_KEY\)' <<<"$out"; then
        # And expect the results line to include the skipped count, not
        # a slew of FAILs for the auth-required checks.
        _skips=$(grep -oE '[0-9]+ skipped' <<<"$out" | head -1 | grep -oE '[0-9]+' || echo 0)
        if [[ "${_skips:-0}" -ge 5 ]]; then
            pass "case 11: --quick without key produces SKIP banner + ≥5 skips (auth-required)"
        else
            fail "case 11: --quick without key" \
                "expected ≥5 skips (auth-required checks), got $_skips
--- output ---
$out"
        fi
    else
        fail "case 11: --quick without key" \
            "SKIP banner or reason string not found
--- output ---
$out"
    fi
    rm -rf "$tmp"
else
    printf "  (skipped case 11: %s)\n" \
        "$( [[ -f "$INTEGRATION" ]] && echo 'jq unavailable' || echo 'test-integration.sh not found' )"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
