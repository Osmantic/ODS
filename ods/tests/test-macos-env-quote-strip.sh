#!/usr/bin/env bash
# Fixture tests for read_ods_env in installers/macos/ods-macos.sh:
# .env values must only lose a MATCHING pair of surrounding quotes.
# Mirrors the lib/safe-env.sh quote contract used by the Linux CLI.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0
pass() { printf "  ${GREEN}✓ PASS${NC} %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ${RED}✗ FAIL${NC} %s\n" "$1"; FAILED=$((FAILED + 1)); }

echo ""
echo "=== macOS CLI .env quote handling tests ==="
echo ""

if bash -n "$CLI" 2>/dev/null; then
    pass "ods-macos.sh passes bash -n"
else
    fail "ods-macos.sh bash -n failed"
fi

# Extract just read_ods_env so the CLI's dispatch never runs. The function
# is delimited by its definition line and the first column-0 brace.
READ_ODS_ENV_SRC="$(sed -n '/^read_ods_env()/,/^}/p' "$CLI")"
if [[ -z "$READ_ODS_ENV_SRC" ]]; then
    fail "could not extract read_ods_env from ods-macos.sh"
    echo "Result: $PASSED passed, $FAILED failed"
    exit 1
fi
eval "$READ_ODS_ENV_SRC"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat > "$TMP_DIR/.env" <<'EOF'
PLAIN=plain-value
DQ="hello world"
SQ='single quoted'
DQ_INNER_SQ="'literal'"
MISMATCH=trailing-quote"
CROSS="abc'
LONE_DQ="
EMPTY_DQ=""
EOF

INSTALL_DIR="$TMP_DIR"
read_ods_env

assert_env() {
    local var="ENV_$1" expected="$2" actual
    actual="${!var-<unset>}"
    if [[ "$actual" == "$expected" ]]; then
        pass "$1 parsed as <$expected>"
    else
        fail "$1 parsed as <$actual>, expected <$expected>"
    fi
}

assert_env "PLAIN" 'plain-value'
assert_env "DQ" 'hello world'
assert_env "SQ" 'single quoted'
assert_env "DQ_INNER_SQ" "'literal'"
assert_env "MISMATCH" 'trailing-quote"'
assert_env "CROSS" '"abc'\'''
assert_env "LONE_DQ" '"'
assert_env "EMPTY_DQ" ''

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
