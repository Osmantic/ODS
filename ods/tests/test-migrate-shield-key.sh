#!/bin/bash
# Test suite for migrations/migrate-v2.4.1.sh (SHIELD_API_KEY backfill)
#
# Covers the key-material integrity contract: the migration must never write
# an empty key, must never clobber an existing non-empty key (including a
# later duplicate line), and must fail loudly when no random source exists —
# a silent empty key leaves Privacy Shield auth broken while ods-update.sh
# treats the migration as successful.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATION="$SCRIPT_DIR/migrations/migrate-v2.4.1.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

TESTS_RUN=0
TESTS_FAILED=0

pass() { echo -e "${GREEN}[PASS]${NC} $1"; TESTS_RUN=$((TESTS_RUN + 1)); }
fail() { echo -e "${RED}[FAIL]${NC} $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_RUN=$((TESTS_RUN + 1)); }

[[ -f "$MIGRATION" ]] || { fail "migrate-v2.4.1.sh not found"; exit 1; }

FIXTURE_ROOT="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_ROOT"' EXIT

# Each case gets its own fake install dir; the migration writes INSTALL_DIR/.env.
run_migration() {
    local dir="$1" extra_path="${2:-}"
    local rc=0
    if [[ -n "$extra_path" ]]; then
        PATH="$extra_path:$PATH" INSTALL_DIR="$dir" \
            bash "$MIGRATION" > "$dir/out.log" 2>&1 || rc=$?
    else
        INSTALL_DIR="$dir" bash "$MIGRATION" > "$dir/out.log" 2>&1 || rc=$?
    fi
    return "$rc"
}

mkcase() {
    local dir="$FIXTURE_ROOT/$1"
    mkdir -p "$dir"
    printf '%b' "$2" > "$dir/.env"
    cp "$dir/.env" "$dir/.env.orig"
    echo "$dir"
}

# 1. Missing key is appended with a 64-hex value
d=$(mkcase missing 'OTHER_VAR=1\n')
if run_migration "$d" && grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}$' "$d/.env"; then
    pass "missing key is backfilled with a 64-hex value"
else
    fail "missing key backfill (log: $(tail -1 "$d/out.log"))"
fi

# 2. Empty key value is filled in place
d=$(mkcase empty 'SHIELD_API_KEY=\n')
if run_migration "$d" && grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}$' "$d/.env"; then
    pass "empty key value is filled in place"
else
    fail "empty key fill (log: $(tail -1 "$d/out.log"))"
fi

# 3. Duplicate lines: empty first, real second — the real key must survive
d=$(mkcase dup 'SHIELD_API_KEY=\nOTHER=1\nSHIELD_API_KEY=abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789\n')
if run_migration "$d" && cmp -s "$d/.env" "$d/.env.orig"; then
    pass "later non-empty duplicate key is preserved"
else
    fail "duplicate real key must not be clobbered"
    cat "$d/.env"
fi

# 4. Single real key is left alone (idempotent)
d=$(mkcase real 'SHIELD_API_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd\n')
if run_migration "$d" && cmp -s "$d/.env" "$d/.env.orig"; then
    pass "existing real key is left untouched"
else
    fail "existing real key was modified"
fi

# 5. CRLF-terminated empty value counts as empty and is filled
d=$(mkcase crlf 'SHIELD_API_KEY=\r\nOTHER=1\n')
if run_migration "$d" && grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}$' "$d/.env"; then
    pass "CRLF empty value is filled"
else
    fail "CRLF empty value handling"
fi

# 6. No usable random source: fail loudly, never write an empty key
d=$(mkcase norand 'OTHER_VAR=1\n')
mkdir -p "$FIXTURE_ROOT/stubbin"
printf '#!/bin/sh\nexit 1\n' > "$FIXTURE_ROOT/stubbin/openssl"
printf '#!/bin/sh\nexit 1\n' > "$FIXTURE_ROOT/stubbin/od"
printf '#!/bin/sh\nexit 1\n' > "$FIXTURE_ROOT/stubbin/xxd"
chmod +x "$FIXTURE_ROOT/stubbin"/{openssl,od,xxd}
if run_migration "$d" "$FIXTURE_ROOT/stubbin"; then
    if grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}$' "$d/.env"; then
        pass "no random source: fell back to another generator, key still valid"
    else
        fail "migration must fail when no random source exists"
    fi
elif cmp -s "$d/.env" "$d/.env.orig" && ! grep -q 'SHIELD_API_KEY' "$d/.env"; then
    pass "no random source: non-zero exit, .env untouched, no empty key written"
else
    fail "failed migration left .env modified"
    cat "$d/.env"
fi

echo ""
echo "Results: $((TESTS_RUN - TESTS_FAILED))/$TESTS_RUN passed"
[[ "$TESTS_FAILED" == 0 ]]
