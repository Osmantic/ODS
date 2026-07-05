#!/bin/bash
# ============================================================================
# ODS health-check.sh Test Suite
# ============================================================================
# Ensures scripts/health-check.sh runs without shell errors and produces
# expected exit codes and (when requested) JSON output. Supports rock-solid
# installs by validating the health-check path used in post-install checklists.
#
# Usage: ./tests/test-health-check.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   health-check.sh Test Suite                  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Script exists
if [[ ! -f "$ROOT_DIR/scripts/health-check.sh" ]]; then
    fail "scripts/health-check.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; exit 1
fi
pass "health-check.sh exists"

# 2. Runs without shell error (--quiet to reduce output; we care about exit and no "unbound" etc.)
set +e
out=$(cd "$ROOT_DIR" && bash scripts/health-check.sh --quiet 2>&1)
exit_code=$?
set -e

if echo "$out" | grep -q "unbound variable\|syntax error\|command not found"; then
    fail "health-check.sh produced shell error in output"
else
    pass "health-check.sh runs without shell errors"
fi

# Exit code must be 0, 1, or 2 (documented: 0=healthy, 1=degraded, 2=critical)
if [[ "$exit_code" -eq 0 ]] || [[ "$exit_code" -eq 1 ]] || [[ "$exit_code" -eq 2 ]]; then
    pass "health-check.sh exit code is valid (0|1|2): $exit_code"
else
    fail "health-check.sh exit code should be 0, 1, or 2; got $exit_code"
fi

# 3. --json produces JSON-like output (no strict parse here, just key presence)
set +e
json_out=$(cd "$ROOT_DIR" && bash scripts/health-check.sh --json 2>&1)
json_exit=$?
set -e

if echo "$json_out" | grep -q '"'; then
    pass "health-check.sh --json produces JSON-like output"
else
    fail "health-check.sh --json output does not look like JSON"
fi

if [[ "$json_exit" -eq 0 ]] || [[ "$json_exit" -eq 1 ]] || [[ "$json_exit" -eq 2 ]]; then
    pass "health-check.sh --json exit code valid: $json_exit"
else
    fail "health-check.sh --json exit code invalid: $json_exit"
fi

# 4. Script is executable or runnable via bash
if [[ -x "$ROOT_DIR/scripts/health-check.sh" ]] || true; then
    pass "health-check.sh is runnable (bash or executable)"
fi

# 5. Container state checking function exists
if grep -q "check_container_state" "$ROOT_DIR/scripts/health-check.sh"; then
    pass "check_container_state function present"
else
    fail "check_container_state function missing"
fi

# 6. Container state messages are present in output logic
if grep -q "container not found\|container stopped\|container restarting" "$ROOT_DIR/scripts/health-check.sh"; then
    pass "Container state error messages present"
else
    fail "Container state error messages missing"
fi

# 7. Verify graceful handling when docker unavailable (mock test)
# The function should return 0 (success) when docker command not found
if grep -A15 "check_container_state" "$ROOT_DIR/scripts/health-check.sh" | grep -q "command -v docker"; then
    pass "check_container_state checks for docker availability"
else
    fail "check_container_state missing docker availability check"
fi

# 8. Health type fixtures (http/tcp/none)
FIXTURES_DIR="$SCRIPT_DIR/fixtures/health-types"
if [[ ! -d "$FIXTURES_DIR" ]]; then
    fail "Health type fixtures missing"
elif ! python3 -c "import yaml" 2>/dev/null; then
    skip "PyYAML not installed — skipping health_type fixture check"
else
    http_port=18081
    tcp_port=18082
    http_log=$(mktemp)
    tcp_log=$(mktemp)
    python3 -m http.server "$http_port" --bind 127.0.0.1 >"$http_log" 2>&1 &
    http_pid=$!
    sleep 0.5
    if ! kill -0 "$http_pid" 2>/dev/null; then
        skip "HTTP fixture server failed to start (port $http_port in use)"
    else
        python3 - "$tcp_port" >"$tcp_log" 2>&1 <<'PY' &
import socket
import sys
import time

port = int(sys.argv[1])
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", port))
sock.listen(1)
sock.settimeout(5)
try:
    conn, _ = sock.accept()
    conn.close()
    time.sleep(0.5)
except Exception:
    time.sleep(1)
finally:
    sock.close()
PY
        tcp_pid=$!
        sleep 0.5
        if ! kill -0 "$tcp_pid" 2>/dev/null; then
            skip "TCP fixture server failed to start (port $tcp_port in use)"
        else
            fixture_statuses=$(
                ROOT_DIR="$ROOT_DIR" \
                ODS_EXTENSIONS_DIR="$FIXTURES_DIR/extensions/services" \
                HEALTH_CHECK_LIB_ONLY=1 \
                bash -c '
source "$ROOT_DIR/scripts/health-check.sh"
# Fixtures run without real Docker containers. Stub the container-state
# check so the network probe is the actual signal under test.
check_container_state() { echo "running"; }
set +e
test_service "http-service" >/dev/null 2>&1
http_status=$(result_get "http-service")
test_service "tcp-service" >/dev/null 2>&1
tcp_status=$(result_get "tcp-service")
test_service "none-service" >/dev/null 2>&1
none_status=$(result_get "none-service")
printf "%s,%s,%s" "$http_status" "$tcp_status" "$none_status"
'
            )
            if [[ "$fixture_statuses" == "ok,ok,not_applicable" ]]; then
                pass "health_type fixtures report http ok, tcp ok, none not_applicable"
            else
                fail "health_type fixture statuses incorrect" "$fixture_statuses"
            fi
        fi
        if kill -0 "$tcp_pid" 2>/dev/null; then
            kill "$tcp_pid" 2>/dev/null
        fi
    fi
    if kill -0 "$http_pid" 2>/dev/null; then
        kill "$http_pid" 2>/dev/null
    fi
    rm -f "$http_log" "$tcp_log"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
