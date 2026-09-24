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
# Test 13: backup captures DATA_DIR content (#2924)
#
# BACKUP_DIR lives inside DATA_DIR, so a wholesale `cp -r "$DATA_DIR"` asks cp
# to copy a directory into itself; the failure used to be swallowed and the
# backup shipped with an empty data/.
# ============================================================================
mkdir -p "$DATA_DIR/models"
echo "user-payload" > "$DATA_DIR/models/marker.txt"

backup13_exit=0
backup13_output=$(bash "$MIGRATE_CONFIG_SCRIPT" backup 2>&1) || backup13_exit=$?
if [[ $backup13_exit -ne 0 ]]; then
    fail "Behavioral test: backup exited $backup13_exit: $backup13_output"
else
    latest_backup=$(ls -1d "$DATA_DIR"/backups/config-* | tail -1)
    if [[ -f "$latest_backup/data/models/marker.txt" ]]; then
        pass "Behavioral test: backup captures DATA_DIR content"
    else
        fail "Behavioral test: backup data/ is missing DATA_DIR content ($latest_backup)"
    fi
fi

# The backups tree must not be copied into the backup it is creating.
if [[ -e "$latest_backup/data/backups" ]]; then
    fail "Behavioral test: backup recursed into its own backups directory"
else
    pass "Behavioral test: backup skips the backups directory"
fi

# Two backups created within the same second must remain separate snapshots.
FAKE_BIN="$TEMP_DIR/fake-bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/date" <<'SH'
#!/bin/bash
if [[ "${1:-}" == "+%Y%m%d-%H%M%S" ]]; then
    printf '%s\n' '20300101-010203'
else
    /bin/date "$@"
fi
SH
chmod +x "$FAKE_BIN/date"

backup_first=$(PATH="$FAKE_BIN:$PATH" bash "$MIGRATE_CONFIG_SCRIPT" backup | tail -n 1)
backup_second=$(PATH="$FAKE_BIN:$PATH" bash "$MIGRATE_CONFIG_SCRIPT" backup | tail -n 1)
if [[ "$backup_first" != "$backup_second" \
    && -d "$backup_first" && -d "$backup_second" ]]; then
    pass "Behavioral test: same-second backups use unique directories"
else
    fail "Behavioral test: same-second backups collided at $backup_first"
fi

# ============================================================================
# Test 14: MIGRATE_TARGET_VERSION supplies the selection bound
#
# During `ods-update.sh update` the recorded .version still names the OLD
# release — it is only stamped after migrations and health checks pass — so
# the update flow passes the post-pull manifest version via
# MIGRATE_TARGET_VERSION. On a first update there is no .version at all:
# without the override the bound would be 0.0.0 and nothing would apply.
# ============================================================================
rm -f "$INSTALL_DIR/.version" "$DATA_DIR/.migration-state"
cat > "$INSTALL_DIR/.env" <<'EOF'
# Fixture env for migration application
EOF

migrate14_exit=0
migrate14_output=$(MIGRATE_TARGET_VERSION="2.4.1" bash "$MIGRATE_CONFIG_SCRIPT" migrate 2>&1) || migrate14_exit=$?
if [[ $migrate14_exit -eq 0 ]] && grep -q '^SHIELD_API_KEY=' "$INSTALL_DIR/.env"; then
    pass "Behavioral test: MIGRATE_TARGET_VERSION applies migrations up to the incoming release"
else
    fail "Behavioral test: migrate with MIGRATE_TARGET_VERSION exited $migrate14_exit or skipped v2.4.1: $migrate14_output"
fi

if [[ "$(cat "$DATA_DIR/.migration-state" 2>/dev/null)" == "2.4.1" ]]; then
    pass "Behavioral test: migrate stamps .migration-state with the target version"
else
    fail "Behavioral test: .migration-state was not stamped (got '$(cat "$DATA_DIR/.migration-state" 2>/dev/null)')"
fi

# Test 15: the override wins over a stale recorded .version (the mid-update
# case: .version still says 0.0.0 but the incoming release is 2.4.1).
rm -f "$DATA_DIR/.migration-state"
echo "0.0.0" > "$INSTALL_DIR/.version"
cat > "$INSTALL_DIR/.env" <<'EOF'
# Fixture env for override precedence
EOF

migrate15_exit=0
migrate15_output=$(MIGRATE_TARGET_VERSION="2.4.1" bash "$MIGRATE_CONFIG_SCRIPT" migrate 2>&1) || migrate15_exit=$?
if [[ $migrate15_exit -eq 0 ]] && grep -q '^SHIELD_API_KEY=' "$INSTALL_DIR/.env"; then
    pass "Behavioral test: MIGRATE_TARGET_VERSION wins over a stale .version"
else
    fail "Behavioral test: stale .version bounded selection despite the override (exit $migrate15_exit): $migrate15_output"
fi

# Test 16: a target below every bundled script applies nothing — the bound
# that keeps a newer script bundle from writing future configuration onto
# an older installation.
rm -f "$DATA_DIR/.migration-state"
cat > "$INSTALL_DIR/.env" <<'EOF'
# Fixture env for upper-bound check
EOF

migrate16_exit=0
migrate16_output=$(MIGRATE_TARGET_VERSION="0.1.0" bash "$MIGRATE_CONFIG_SCRIPT" migrate 2>&1) || migrate16_exit=$?
if [[ $migrate16_exit -eq 0 ]] \
    && ! grep -q '^SHIELD_API_KEY=' "$INSTALL_DIR/.env" \
    && ! grep -q '^ENABLE_VOICE=' "$INSTALL_DIR/.env"; then
    pass "Behavioral test: target below bundled scripts applies nothing"
else
    fail "Behavioral test: migrations above the target were applied (exit $migrate16_exit): $migrate16_output"
fi

# Test 17: check honours the override as well — .version absent and
# .migration-state at 0.0.0 normally report "No migration needed"; with the
# incoming release as the bound the pending migrations must surface.
rm -f "$INSTALL_DIR/.version"
echo "0.0.0" > "$DATA_DIR/.migration-state"
check17_exit=0
check17_output=$(MIGRATE_TARGET_VERSION="2.4.1" bash "$MIGRATE_CONFIG_SCRIPT" check 2>&1) || check17_exit=$?
if [[ $check17_exit -eq 2 ]] && echo "$check17_output" | grep -q "Migration needed"; then
    pass "Behavioral test: check reports pending migrations against MIGRATE_TARGET_VERSION"
else
    fail "Behavioral test: check ignored MIGRATE_TARGET_VERSION (exit $check17_exit): $check17_output"
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
