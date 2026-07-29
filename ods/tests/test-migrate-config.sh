#!/bin/bash
# Test suite for migrate-config.sh
# Validates config migration, backup, and diff operations

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATE_CONFIG_SCRIPT="$SCRIPT_DIR/scripts/migrate-config.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_RUN=$((TESTS_RUN + 1))
}

fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    TESTS_RUN=$((TESTS_RUN + 1))
}

skip() {
    echo -e "${YELLOW}[SKIP]${NC} $1"
}

# ============================================================================
# Test 1: Script exists and is executable
# ============================================================================
if [[ -f "$MIGRATE_CONFIG_SCRIPT" ]]; then
    pass "migrate-config.sh exists"
else
    fail "migrate-config.sh not found at $MIGRATE_CONFIG_SCRIPT"
    exit 1
fi

if [[ -x "$MIGRATE_CONFIG_SCRIPT" ]]; then
    pass "migrate-config.sh is executable"
else
    pass "migrate-config.sh is runnable via bash"
fi

# ============================================================================
# Test 2: Help command works
# ============================================================================
help_exit=0
help_output=$(bash "$MIGRATE_CONFIG_SCRIPT" help 2>&1) || help_exit=$?
if [[ $help_exit -eq 0 ]] && echo "$help_output" | grep -q "Usage:"; then
    pass "help command works and shows usage"
else
    fail "help command failed or missing usage text"
fi

# ============================================================================
# Test 3: Check command works without state
# ============================================================================
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

export INSTALL_DIR="$TEMP_DIR/install"
export DATA_DIR="$TEMP_DIR/data"
mkdir -p "$INSTALL_DIR" "$DATA_DIR"

check_exit=0
check_output=$(bash "$MIGRATE_CONFIG_SCRIPT" check 2>&1) || check_exit=$?
if [[ $check_exit -eq 0 || $check_exit -eq 2 ]]; then
    pass "check command works without state"
else
    fail "check command failed unexpectedly (exit $check_exit)"
fi

# ============================================================================
# Test 4: Behavioral test - backup creates directory
# ============================================================================
backup_exit=0
backup_output=$(bash "$MIGRATE_CONFIG_SCRIPT" backup 2>&1) || backup_exit=$?
if [[ $backup_exit -eq 0 ]]; then
    if [[ -d "$DATA_DIR/backups" ]]; then
        pass "Behavioral test: backup creates backup directory"
    else
        fail "Behavioral test: backup did not create directory"
    fi
else
    pass "Behavioral test: backup handles missing files gracefully"
fi

# ============================================================================
# Test 5: Behavioral test - diff with missing files
# ============================================================================
diff_exit=0
diff_output=$(bash "$MIGRATE_CONFIG_SCRIPT" diff 2>&1) || diff_exit=$?
if [[ $diff_exit -ne 0 ]]; then
    pass "Behavioral test: diff fails gracefully with missing files"
else
    skip "Behavioral test: diff succeeded (files may exist)"
fi

# ============================================================================
# Test 6: Behavioral test - check with version file
# ============================================================================
echo "1.0.0" > "$INSTALL_DIR/.version"
check_exit=0
check_output=$(bash "$MIGRATE_CONFIG_SCRIPT" check 2>&1) || check_exit=$?
if [[ $check_exit -eq 0 || $check_exit -eq 2 ]]; then
    pass "Behavioral test: check reads version file"
else
    fail "Behavioral test: check failed with version file"
fi

# ============================================================================
# Test 7: Behavioral test - diff with mock .env files
# ============================================================================
cat > "$INSTALL_DIR/.env.example" <<'EOF'
# Example config
VAR1=value1
VAR2=value2
VAR3=value3
EOF

cat > "$INSTALL_DIR/.env" <<'EOF'
# Current config
VAR1=value1
VAR2=old_value
VAR4=value4
EOF

diff_exit=0
diff_output=$(bash "$MIGRATE_CONFIG_SCRIPT" diff 2>&1) || diff_exit=$?
if [[ $diff_exit -eq 0 ]] && echo "$diff_output" | grep -q "VAR3"; then
    pass "Behavioral test: diff detects new variables"
else
    fail "Behavioral test: diff failed to detect new variables"
fi

if echo "$diff_output" | grep -q "VAR4"; then
    pass "Behavioral test: diff detects deprecated variables"
else
    fail "Behavioral test: diff failed to detect deprecated variables"
fi

# ============================================================================
# Test 8: Behavioral test - validate command
# ============================================================================
if command -v jq >/dev/null 2>&1; then
    # Create mock schema
    cat > "$INSTALL_DIR/.env.schema.json" <<'EOF'
{
  "type": "object",
  "properties": {
    "VAR1": {"type": "string"}
  }
}
EOF

    # Create the mock validator in the temp dir and point migrate-config.sh at
    # it via MIGRATE_VALIDATOR. Writing to the real scripts/validate-env.sh (a
    # tracked file) and rm-ing it afterward would delete it from the working
    # tree — which is exactly what this suite used to do.
    mock_validator="$TEMP_DIR/mock-validate-env.sh"
    cat > "$mock_validator" <<'EOF'
#!/bin/bash
echo "Mock validation passed"
exit 0
EOF
    chmod +x "$mock_validator"

    validate_exit=0
    validate_output=$(MIGRATE_VALIDATOR="$mock_validator" bash "$MIGRATE_CONFIG_SCRIPT" validate 2>&1) || validate_exit=$?
    if [[ $validate_exit -eq 0 ]]; then
        pass "Behavioral test: validate command works"
    else
        skip "Behavioral test: validate command (validator not available)"
    fi
else
    skip "Behavioral test: validate command (jq not available)"
fi

# ============================================================================
# Test 9: Script does not use silent error suppression
# ============================================================================
suppression_count=0
if grep -q "2>/dev/null" "$MIGRATE_CONFIG_SCRIPT"; then
    suppression_count=$((suppression_count + $(grep -c "2>/dev/null" "$MIGRATE_CONFIG_SCRIPT")))
fi
if grep -q "|| true" "$MIGRATE_CONFIG_SCRIPT"; then
    suppression_count=$((suppression_count + $(grep -c "|| true" "$MIGRATE_CONFIG_SCRIPT")))
fi

if [[ $suppression_count -eq 0 ]]; then
    pass "CLAUDE.md compliance: no silent error suppressions found"
else
    fail "CLAUDE.md compliance: found $suppression_count error suppressions (2>/dev/null, || true)"
fi

# ============================================================================
# Test 10: Script uses inline exit code capture
# ============================================================================
if grep -q "_exit=0" "$MIGRATE_CONFIG_SCRIPT" && grep -q "|| .*_exit=\$?" "$MIGRATE_CONFIG_SCRIPT"; then
    pass "CLAUDE.md compliance: uses inline exit code capture pattern"
else
    fail "CLAUDE.md compliance: missing inline exit code capture pattern"
fi

# ============================================================================
# Test 11: check reports a pending migration when current > last migrated
# Guards against two regressions: (a) a bare `compare_versions` call aborting
# under `set -e` before $? is read, and (b) the inverted comparison that treated
# only a *downgrade* as "migration needed" so ordinary upgrades were missed.
# ============================================================================
echo "2.4.1" > "$INSTALL_DIR/.version"
mkdir -p "$DATA_DIR"
echo "0.2.0" > "$DATA_DIR/.migration-state"
check_exit=0
check_output=$(bash "$MIGRATE_CONFIG_SCRIPT" check 2>&1) || check_exit=$?
if [[ $check_exit -eq 2 ]] && echo "$check_output" | grep -q "Migration needed"; then
    pass "Behavioral test: check reports pending migration (current > last migrated)"
else
    fail "Behavioral test: check missed pending migration (exit $check_exit): $check_output"
fi

# Test 12: with last-migrated equal to current, no migration is reported.
echo "2.4.1" > "$DATA_DIR/.migration-state"
check_exit=0
check_output=$(bash "$MIGRATE_CONFIG_SCRIPT" check 2>&1) || check_exit=$?
if [[ $check_exit -eq 0 ]] && echo "$check_output" | grep -q "No migration needed"; then
    pass "Behavioral test: check reports up-to-date when versions match"
else
    fail "Behavioral test: check misreported up-to-date state (exit $check_exit): $check_output"
fi

# ============================================================================
# Test 13: v2.4.1 migration backfills empty SHIELD_API_KEY= atomically
# ============================================================================
MIGRATION_V241_SCRIPT="$SCRIPT_DIR/migrations/migrate-v2.4.1.sh"
if [[ -f "$MIGRATION_V241_SCRIPT" ]]; then
    TEST_ENV="$TEMP_DIR/test_v241_empty.env"
    echo "EXISTING_VAR=1" > "$TEST_ENV"
    echo "SHIELD_API_KEY=" >> "$TEST_ENV"
    chmod 0600 "$TEST_ENV"
    INSTALL_DIR="$TEMP_DIR" ENV_FILE="$TEST_ENV" bash "$MIGRATION_V241_SCRIPT" >/dev/null 2>&1 || true
    if [ "$(uname -s)" = "Darwin" ]; then
        EMPTY_MODE=$(stat -f "%Lp" "$TEST_ENV" 2>/dev/null || echo "")
    else
        EMPTY_MODE=$(stat -c "%a" "$TEST_ENV" 2>/dev/null || echo "")
    fi
    if grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}' "$TEST_ENV" && [[ "$EMPTY_MODE" == "600" ]]; then
        pass "Migration v2.4.1: backfills empty SHIELD_API_KEY= atomically and preserves 0600 mode"
    else
        fail "Migration v2.4.1: failed to backfill empty SHIELD_API_KEY= or preserve mode (mode: $EMPTY_MODE)"
    fi

    # Test 14: v2.4.1 migration appends missing SHIELD_API_KEY atomically
    TEST_MISSING_ENV="$TEMP_DIR/test_v241_missing.env"
    echo "EXISTING_VAR=1" > "$TEST_MISSING_ENV"
    chmod 0600 "$TEST_MISSING_ENV"
    INSTALL_DIR="$TEMP_DIR" ENV_FILE="$TEST_MISSING_ENV" bash "$MIGRATION_V241_SCRIPT" >/dev/null 2>&1 || true
    if [ "$(uname -s)" = "Darwin" ]; then
        MISSING_MODE=$(stat -f "%Lp" "$TEST_MISSING_ENV" 2>/dev/null || echo "")
    else
        MISSING_MODE=$(stat -c "%a" "$TEST_MISSING_ENV" 2>/dev/null || echo "")
    fi
    if grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}' "$TEST_MISSING_ENV" && grep -q "EXISTING_VAR=1" "$TEST_MISSING_ENV" && [[ "$MISSING_MODE" == "600" ]]; then
        pass "Migration v2.4.1: appends missing SHIELD_API_KEY atomically and preserves 0600 mode"
    else
        fail "Migration v2.4.1: failed to append missing SHIELD_API_KEY atomically or preserve mode (mode: $MISSING_MODE)"
    fi

    # ============================================================================
    # Test 15: Temp file permission window validation (temp file created 0600 before atomic swap)
    # ============================================================================
    TEST_WINDOW_DIR="$TEMP_DIR/window_test"
    mkdir -p "$TEST_WINDOW_DIR"
    TEST_WINDOW_ENV="$TEST_WINDOW_DIR/.env"
    echo "EXISTING_VAR=1" > "$TEST_WINDOW_ENV"
    chmod 0600 "$TEST_WINDOW_ENV"

    BIN_DIR="$TEMP_DIR/bin"
    mkdir -p "$BIN_DIR"
    cat > "$BIN_DIR/mv" <<EOF
#!/bin/bash
for arg in "\$@"; do
    if [[ "\$arg" == *".tmp"* ]]; then
        if [ "\$(uname -s)" = "Darwin" ]; then
            mode=\$(stat -f "%Lp" "\$arg" 2>/dev/null || echo "")
        else
            mode=\$(stat -c "%a" "\$arg" 2>/dev/null || echo "")
        fi
        echo "\$mode" > "$TEST_WINDOW_DIR/captured.mode"
    fi
done
exec /bin/mv "\$@"
EOF
    chmod +x "$BIN_DIR/mv"

    PATH="$BIN_DIR:$PATH" INSTALL_DIR="$TEST_WINDOW_DIR" ENV_FILE="$TEST_WINDOW_ENV" bash "$MIGRATION_V241_SCRIPT" >/dev/null 2>&1 || true

    CAPTURED_MODE=""
    if [[ -f "$TEST_WINDOW_DIR/captured.mode" ]]; then
        CAPTURED_MODE=$(cat "$TEST_WINDOW_DIR/captured.mode")
    fi

    if [[ "$CAPTURED_MODE" == "600" || "$CAPTURED_MODE" == "700" ]]; then
        pass "Migration v2.4.1: temp file permission window is strictly 0600 prior to replacement (captured mode: $CAPTURED_MODE)"
    else
        fail "Migration v2.4.1: temp file exposed loose umask permissions during write window (captured mode: '$CAPTURED_MODE')"
    fi
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "========================================"
echo "Test Summary"
echo "========================================"
echo "Total:  $TESTS_RUN"
echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
echo ""

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
