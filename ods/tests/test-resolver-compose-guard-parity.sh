#!/usr/bin/env bash
# ============================================================================
# Test resolver compose guard parity with the API
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║      resolver compose guard parity tests     ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# Run a test case
# Args:
#   $1: Test description
#   $2: Compose file content
#   $3: Expected to be accepted (0 for reject, 1 for accept)
run_test_case() {
    local desc="$1"
    local content="$2"
    local expected_ok="$3"
    
    local test_dir
    test_dir=$(mktemp -d)
    
    # Create required base compose files so resolver doesn't fail on missing base files
    touch "$test_dir/docker-compose.base.yml"
    echo "$content" > "$test_dir/docker-compose.override.yml"
    
    # Run the resolver
    local stdout
    local stderr
    local exit_code=0
    
    # Run resolver separating stdout and stderr
    stdout=$(bash "$RESOLVER" --script-dir "$test_dir" 2>/tmp/test_resolver_stderr) || exit_code=$?
    stderr=$(cat /tmp/test_resolver_stderr)
    rm -f /tmp/test_resolver_stderr
    
    # Check if the override file was merged/resolved (printed in stdout flags)
    local is_merged=0
    if grep -q "docker-compose.override.yml" <<<"$stdout"; then
        is_merged=1
    fi
    
    if [ "$expected_ok" -eq 1 ] && [ "$is_merged" -eq 1 ]; then
        pass "$desc (correctly accepted)"
    elif [ "$expected_ok" -eq 0 ] && [ "$is_merged" -eq 0 ]; then
        pass "$desc (correctly rejected)"
    else
        fail "$desc (expected_ok=$expected_ok, got merged=$is_merged)"
        echo "Resolver stdout:"
        echo "$stdout"
        echo "Resolver stderr:"
        echo "$stderr"
    fi
    
    rm -rf "$test_dir"
}

# Test 1: short-form relative bind-mount escape
run_test_case "Test 1: short-form relative bind-mount escape" '
services:
  malicious:
    image: alpine
    volumes:
      - "../../../../etc:/host-etc"
' 0

# Test 2: long-form relative bind-mount escape
run_test_case "Test 2: long-form relative bind-mount escape" '
services:
  malicious:
    image: alpine
    volumes:
      - type: bind
        source: "../../../../etc"
        target: "/host-etc"
' 0

# Test 3: CAP_ prefix capability addition
run_test_case "Test 3: CAP_ prefix capability addition" '
services:
  malicious:
    image: alpine
    cap_add:
      - "CAP_SYS_ADMIN"
' 0

# Test 4: Reserved labels case insensitivity
run_test_case "Test 4: Reserved labels case insensitivity" '
services:
  malicious:
    image: alpine
    labels:
      - "COM.DOCKER.COMPOSE.foo=bar"
' 0

# Test 5: Reserved labels io.docker. check
run_test_case "Test 5: Reserved labels io.docker. check" '
services:
  malicious:
    image: alpine
    labels:
      - "io.docker.compose.foo=bar"
' 0

# Test 6: Benign relative mount (should pass)
run_test_case "Test 6: Benign relative mount" '
services:
  benign:
    image: alpine
    volumes:
      - "./data/benign:/data"
' 1

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
