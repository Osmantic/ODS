#!/bin/bash
# ==========================================================================
# ODS Docker Image Pull Retry Test Suite
# ==========================================================================
# Behavioral tests for pull_with_progress() using a mocked docker command.
# This avoids grep-based “string presence” tests and validates retry behavior.
#
# Usage: ./ods/tests/test-docker-image-pull-retry.sh
# ==========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

TMP_DIR=$(mktemp -d)
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

export LOG_FILE="$TMP_DIR/install.log"
touch "$LOG_FILE"

print_pass() { echo -e "${GREEN}✓ PASS${NC}"; PASSED=$((PASSED + 1)); }
print_fail() { echo -e "${RED}✗ FAIL${NC} ${1:-}"; FAILED=$((FAILED + 1)); }

run_pull_with_progress() {
  local docker_cmd=$1
  local img=$2
  local label=$3
  local count=${4:-1}
  local total=${5:-1}

  # Run in a clean bash so we can stub sleep/spin_task without affecting this test harness.
  bash -c '
    set -euo pipefail
    DOCKER_CMD="$1"; export DOCKER_CMD
    IMG="$2"; LABEL="$3"; COUNT="$4"; TOTAL="$5"; ROOT_DIR="$6"
    export LOG_FILE

    # Minimal vars expected by ui.sh under -u
    GRN=""; BGRN=""; DGRN=""; AMB=""; WHT=""; RED=""; NC=""; CURSOR=""
    VERSION="test"; INTERACTIVE="false"; DRY_RUN=false

    # Keep CI fast
    sleep() { :; }
    spin_task() { local pid="$1"; wait "$pid"; }

    source "$ROOT_DIR/installers/lib/ui.sh"
    pull_with_progress "$IMG" "$LABEL" "$COUNT" "$TOTAL"
  ' _ "$docker_cmd" "$img" "$label" "$count" "$total" "$ROOT_DIR" \
    >>"$TMP_DIR/stdout.log" 2>>"$TMP_DIR/stderr.log"
}

echo ""
echo "╔═══════════════════════════════════════════╗"
echo "║   Docker Image Pull Retry Test Suite     ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

echo "1. Behavioral Tests (mocked docker)"
echo "──────────────────────────────────"

# --------------------------------------------------------------------------
# Mock docker scripts
# --------------------------------------------------------------------------

cat >"$TMP_DIR/docker-success" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "pull" ]]; then
  echo "Pulled"
  exit 0
fi
exit 0
EOF
chmod +x "$TMP_DIR/docker-success"

cat >"$TMP_DIR/docker-fail-then-succeed" <<'EOF'
#!/usr/bin/env bash
state_file="${DOCKER_MOCK_STATE_FILE}"
: "${state_file:?DOCKER_MOCK_STATE_FILE required}"
count=$(cat "$state_file" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$state_file"

if [[ "$1" == "pull" ]]; then
  if [[ $count -lt 3 ]]; then
    echo "network timeout" >&2
    exit 1
  fi
  echo "Pulled"
  exit 0
fi
exit 0
EOF
chmod +x "$TMP_DIR/docker-fail-then-succeed"

cat >"$TMP_DIR/docker-unauthorized" <<'EOF'
#!/usr/bin/env bash
state_file="${DOCKER_MOCK_STATE_FILE}"
: "${state_file:?DOCKER_MOCK_STATE_FILE required}"
count=$(cat "$state_file" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$state_file"

if [[ "$1" == "pull" ]]; then
  echo "Error response from daemon: unauthorized: authentication required" >&2
  exit 1
fi
exit 0
EOF
chmod +x "$TMP_DIR/docker-unauthorized"

# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

printf "  %-60s " "succeeds on first attempt..."
rm -f "$TMP_DIR/stdout.log" "$TMP_DIR/stderr.log"
if run_pull_with_progress "$TMP_DIR/docker-success" "img" "label"; then
  print_pass
else
  print_fail
fi

printf "  %-60s " "retries transient failures and succeeds..."
rm -f "$TMP_DIR/stdout.log" "$TMP_DIR/stderr.log"
export DOCKER_MOCK_STATE_FILE="$TMP_DIR/state-transient"
rm -f "$DOCKER_MOCK_STATE_FILE"
if run_pull_with_progress "$TMP_DIR/docker-fail-then-succeed" "img" "label"; then
  attempts=$(cat "$DOCKER_MOCK_STATE_FILE" 2>/dev/null || echo 0)
  if [[ "$attempts" == "3" ]]; then
    print_pass
  else
    print_fail "(attempts=$attempts, expected 3)"
  fi
else
  print_fail
fi

printf "  %-60s " "fails fast on unauthorized (no retries)..."
rm -f "$TMP_DIR/stdout.log" "$TMP_DIR/stderr.log"
export DOCKER_MOCK_STATE_FILE="$TMP_DIR/state-unauth"
rm -f "$DOCKER_MOCK_STATE_FILE"
if run_pull_with_progress "$TMP_DIR/docker-unauthorized" "img" "label"; then
  print_fail "(unexpected success)"
else
  attempts=$(cat "$DOCKER_MOCK_STATE_FILE" 2>/dev/null || echo 0)
  if [[ "$attempts" == "1" ]]; then
    print_pass
  else
    print_fail "(attempts=$attempts, expected 1)"
  fi
fi

echo ""
echo "2. Integration Tests"
echo "────────────────────"

printf "  %-60s " "Phase 08 calls pull_with_progress..."
if grep -q "pull_with_progress" "$ROOT_DIR/installers/phases/08-images.sh"; then
  print_pass
else
  print_fail
fi

printf "  %-60s " "Phase 08 tracks pull_failed count..."
if grep -q "pull_failed" "$ROOT_DIR/installers/phases/08-images.sh"; then
  print_pass
else
  print_fail
fi

printf "  %-60s " "Phase 08 shows warning for failures..."
if grep -q "ai_warn.*failed" "$ROOT_DIR/installers/phases/08-images.sh"; then
  print_pass
else
  print_fail
fi

echo ""
echo "3. Retry Strategy Tests (source-level sanity checks)"
echo "────────────────────────────────────────────────────"

printf "  %-60s " "max_attempts is 3..."
retry_count=$(grep -A 15 "^pull_with_progress()" "$ROOT_DIR/installers/lib/ui.sh" | grep -m1 "max_attempts=" | grep -oE '[0-9]+' || true)
retry_count=${retry_count:-0}
if [[ "$retry_count" == "3" ]]; then
  print_pass
else
  print_fail "(found $retry_count, expected 3)"
fi

printf "  %-60s " "backoff uses exponential formula..."
if grep -A 30 "^pull_with_progress()" "$ROOT_DIR/installers/lib/ui.sh" | grep -Fq "5 * (2 ** (attempt - 2))"; then
  print_pass
else
  print_fail
fi

echo ""
echo "═══════════════════════════════════════════"
if [[ $FAILED -eq 0 ]]; then
  echo -e "${GREEN}✓ All tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
  echo ""
  exit 0
else
  echo -e "${RED}✗ Some tests failed${NC} ($PASSED passed, $FAILED failed)"
  echo ""
  exit 1
fi
