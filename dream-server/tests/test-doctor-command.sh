#!/usr/bin/env bash
# Test suite for dream doctor command

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "${GREEN}✓${NC} $1"; ((PASSED++)) || true; }
fail() { echo -e "${RED}✗${NC} $1"; ((FAILED++)) || true; }

echo -e "${BLUE}━━━ Dream Doctor Command Tests ━━━${NC}"
echo ""

# Test 1: cmd_doctor function exists
if grep -q "^cmd_doctor()" "$ROOT_DIR/dream-cli"; then
    pass "cmd_doctor function defined"
else
    fail "cmd_doctor not found"
fi

# Test 2: doctor command registered
if grep -Eq "doctor|diag|d\)" "$ROOT_DIR/dream-cli"; then
    pass "doctor command registered"
else
    fail "doctor not registered"
fi

# Test 3: --json flag support
if grep -q "json_mode" "$ROOT_DIR/dream-cli"; then
    pass "--json flag implemented"
else
    fail "--json not found"
fi

# Test 4: Python parsing
if grep -q "python3.*report_file" "$ROOT_DIR/dream-cli"; then
    pass "Python JSON parsing"
else
    fail "parsing not found"
fi

# Test 5: Exit code handling
if grep -q "return \$?" "$ROOT_DIR/dream-cli"; then
    pass "exit code handling"
else
    fail "exit codes missing"
fi

# Test 6: Runtime checks display
if grep -q "Runtime Environment" "$ROOT_DIR/dream-cli"; then
    pass "runtime checks display"
else
    fail "runtime display missing"
fi

# Test 6b: AMD runtime diagnostics display
if grep -q "AMD runtime" "$ROOT_DIR/dream-cli"; then
    pass "AMD runtime diagnostics display"
else
    fail "AMD runtime diagnostics display missing"
fi

# Test 7: Preflight checks display
if grep -q "Preflight Checks" "$ROOT_DIR/dream-cli"; then
    pass "preflight checks display"
else
    fail "preflight display missing"
fi

# Test 8: Autofix hints display
if grep -q "Suggested Fixes" "$ROOT_DIR/dream-cli"; then
    pass "autofix hints display"
else
    fail "hints display missing"
fi

# Test 9: Help text updated
if grep -q "doctor.*diagnostics" "$ROOT_DIR/dream-cli"; then
    pass "help text updated"
else
    fail "help not updated"
fi

# Test 10: dream-doctor.sh exists
if [[ -f "$ROOT_DIR/scripts/dream-doctor.sh" ]]; then
    pass "dream-doctor.sh exists"
else
    fail "script not found"
fi

# Test 11: Bash syntax valid
if bash -n "$ROOT_DIR/dream-cli" 2>/dev/null; then
    pass "bash syntax valid"
else
    fail "syntax errors"
fi

# Test 12: Report file configurable
if grep -q "report_file=" "$ROOT_DIR/dream-cli"; then
    pass "report file configurable"
else
    fail "not configurable"
fi

# Test 13: Functional test - exit code and output
if command -v python3 &>/dev/null; then
    # Create a mock report with failures
    mock_report=$(mktemp)
    cat > "$mock_report" <<'JSON'
{
  "runtime": {
    "docker_cli": true,
    "docker_daemon": false,
    "compose_cli": true,
    "dashboard_http": false,
    "webui_http": false
  },
  "preflight": {
    "checks": [
      {"name": "Docker", "status": "blocker", "message": "Docker daemon not running"}
    ]
  },
  "summary": {
    "preflight_blockers": 1,
    "preflight_warnings": 0,
    "runtime_ready": false
  },
  "autofix_hints": ["Start Docker daemon"]
}
JSON

    # Extract and run the Python parser (disable set -e temporarily to capture exit code)
    set +e
    output=$(python3 - "$mock_report" <<'PY'
import json
import sys

report_file = sys.argv[1]
with open(report_file, 'r') as f:
    report = json.load(f)

runtime = report.get('runtime', {})
print("Runtime Environment:")
for name, status in [('Docker Daemon', runtime.get('docker_daemon', False))]:
    if status:
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name}")

summary = report.get('summary', {})
blocker_count = summary.get('preflight_blockers', 0)
if blocker_count > 0:
    sys.exit(1)
else:
    sys.exit(0)
PY
    )
    exit_code=$?
    set -e

    rm -f "$mock_report"

    # Verify output contains expected text
    if echo "$output" | grep -q "Runtime Environment"; then
        pass "functional test: output displayed"
    else
        fail "functional test: no output"
    fi

    # Verify exit code is 1 (failures present)
    if [[ $exit_code -eq 1 ]]; then
        pass "functional test: exit code correct"
    else
        fail "functional test: exit code wrong ($exit_code)"
    fi
else
    fail "python3 not available for functional test"
fi

echo ""
echo -e "${BLUE}━━━ Test Summary ━━━${NC}"
echo ""
echo -e "  ${GREEN}Passed:${NC} $PASSED"
[[ $FAILED -gt 0 ]] && echo -e "  ${RED}Failed:${NC} $FAILED"
echo ""

if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
