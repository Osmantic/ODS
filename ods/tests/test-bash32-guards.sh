#!/usr/bin/env bash
# Regression coverage for scripts that use Bash 4 associative arrays.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

first_line() {
    local pattern="$1"
    local file="$2"
    grep -n "$pattern" "$file" | head -n 1 | cut -d: -f1
}

assert_guard_before_declare() {
    local file="$1"
    local mode="$2"
    local label="$3"
    local guard_line declare_line

    guard_line="$(first_line 'BASH_VERSINFO\[0\] < 4' "$file")"
    declare_line="$(first_line 'declare -A' "$file")"

    if [[ -n "$guard_line" && -n "$declare_line" && "$guard_line" -lt "$declare_line" ]]; then
        pass "$label guard appears before declare -A"
    else
        fail "$label guard must appear before first declare -A"
    fi

    if [[ "$mode" == "sourced" ]]; then
        if grep -q 'return 1 2>/dev/null || exit 1' "$file"; then
            pass "$label guard is safe when sourced"
        else
            fail "$label guard should return when sourced"
        fi
    else
        if awk '/BASH_VERSINFO\[0\] < 4/{in_guard=1} in_guard && /exit 1/{found=1} /^fi$/{if(in_guard) exit} END{exit found ? 0 : 1}' "$file"; then
            pass "$label guard exits for direct execution"
        else
            fail "$label guard should exit for direct execution"
        fi
    fi
}

assert_guard_before_declare "$ROOT_DIR/lib/progress.sh" sourced "lib/progress.sh"
assert_guard_before_declare "$ROOT_DIR/installers/phases/03-features.sh" sourced "03-features.sh"
assert_guard_before_declare "$ROOT_DIR/scripts/pre-download.sh" direct "pre-download.sh"
assert_guard_before_declare "$ROOT_DIR/scripts/ods-test-functional.sh" direct "ods-test-functional.sh"

if grep -q '"$BASH" "$INSTALL_DIR/scripts/validate-env.sh"' "$ROOT_DIR/ods-cli"; then
    pass "ods-cli config validate runs validate-env.sh through active Bash"
else
    fail "ods-cli config validate should run validate-env.sh through active Bash"
fi

if grep -q '"$BASH" "$INSTALL_DIR/scripts/validate-manifests.sh"' "$ROOT_DIR/ods-cli"; then
    pass "ods-cli config validate runs validate-manifests.sh through active Bash"
else
    fail "ods-cli config validate should run validate-manifests.sh through active Bash"
fi

echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
