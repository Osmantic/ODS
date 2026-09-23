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

    # Keep CI fast while preserving the requested retry delays for assertions.
    sleep() {
      if [[ -n "${SLEEP_LOG:-}" ]]; then
        printf "%s " "$1" >> "$SLEEP_LOG"
      fi
    }

    source "$ROOT_DIR/installers/lib/ui.sh"
    spin_task() { local pid="$1"; wait "$pid"; }
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

if [[ "$1" == "pull" ]]; then
  count=$(cat "$state_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$state_file"
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

cat >"$TMP_DIR/docker-always-timeout" <<'EOF'
#!/usr/bin/env bash
state_file="${DOCKER_MOCK_STATE_FILE}"
: "${state_file:?DOCKER_MOCK_STATE_FILE required}"

if [[ "$1" == "pull" ]]; then
  count=$(cat "$state_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$state_file"
  echo "network timeout" >&2
  exit 1
fi
exit 0
EOF
chmod +x "$TMP_DIR/docker-always-timeout"

cat >"$TMP_DIR/docker-unauthorized" <<'EOF'
#!/usr/bin/env bash
state_file="${DOCKER_MOCK_STATE_FILE}"
: "${state_file:?DOCKER_MOCK_STATE_FILE required}"

if [[ "$1" == "pull" ]]; then
  count=$(cat "$state_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$state_file"
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
export SLEEP_LOG="$TMP_DIR/sleep-transient.log"
rm -f "$DOCKER_MOCK_STATE_FILE"
rm -f "$SLEEP_LOG"
if run_pull_with_progress "$TMP_DIR/docker-fail-then-succeed" "img" "label"; then
  attempts=$(cat "$DOCKER_MOCK_STATE_FILE" 2>/dev/null || echo 0)
  sleeps=$(sed 's/[[:space:]]*$//' "$SLEEP_LOG" 2>/dev/null || true)
  if [[ "$attempts" == "3" && "$sleeps" == "5 15" ]]; then
    print_pass
  else
    print_fail "(attempts=$attempts, expected 3; sleeps='$sleeps', expected '5 15')"
  fi
else
  print_fail
fi
unset SLEEP_LOG

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

printf "  %-60s " "uses full default retry schedule before giving up..."
rm -f "$TMP_DIR/stdout.log" "$TMP_DIR/stderr.log"
export DOCKER_MOCK_STATE_FILE="$TMP_DIR/state-default-timeout"
export SLEEP_LOG="$TMP_DIR/sleep-default-timeout.log"
rm -f "$DOCKER_MOCK_STATE_FILE" "$SLEEP_LOG"
if run_pull_with_progress "$TMP_DIR/docker-always-timeout" "img" "label"; then
  print_fail "(unexpected success)"
else
  attempts=$(cat "$DOCKER_MOCK_STATE_FILE" 2>/dev/null || echo 0)
  sleeps=$(sed 's/[[:space:]]*$//' "$SLEEP_LOG" 2>/dev/null || true)
  if [[ "$attempts" == "4" && "$sleeps" == "5 15 30" ]]; then
    print_pass
  else
    print_fail "(attempts=$attempts, expected 4; sleeps='$sleeps', expected '5 15 30')"
  fi
fi
unset SLEEP_LOG

printf "  %-60s " "supports configurable retry delays and attempts..."
rm -f "$TMP_DIR/stdout.log" "$TMP_DIR/stderr.log"
export DOCKER_MOCK_STATE_FILE="$TMP_DIR/state-configurable"
export SLEEP_LOG="$TMP_DIR/sleep-configurable.log"
export ODS_DOCKER_PULL_MAX_ATTEMPTS=4
export ODS_DOCKER_PULL_RETRY_DELAYS="1 2"
rm -f "$DOCKER_MOCK_STATE_FILE" "$SLEEP_LOG"
if run_pull_with_progress "$TMP_DIR/docker-always-timeout" "img" "label"; then
  print_fail "(unexpected success)"
else
  attempts=$(cat "$DOCKER_MOCK_STATE_FILE" 2>/dev/null || echo 0)
  sleeps=$(sed 's/[[:space:]]*$//' "$SLEEP_LOG" 2>/dev/null || true)
  if [[ "$attempts" == "4" && "$sleeps" == "1 2 4" ]]; then
    print_pass
  else
    print_fail "(attempts=$attempts, expected 4; sleeps='$sleeps', expected '1 2 4')"
  fi
fi
unset ODS_DOCKER_PULL_MAX_ATTEMPTS ODS_DOCKER_PULL_RETRY_DELAYS SLEEP_LOG

echo ""
echo "2. Integration Tests"
echo "────────────────────"

printf "  %-60s " "Phase 08 calls pull_with_progress..."
if grep -q "pull_with_progress" "$ROOT_DIR/installers/phases/08-images.sh"; then
  print_pass
else
  print_fail
fi

printf "  %-60s " "Phase 08 audits resolved Compose images..."
if grep -q "ods_compose_external_images" "$ROOT_DIR/installers/phases/08-images.sh"; then
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

printf "  %-60s " "Phase 08 fails before service startup on pull failures..."
if grep -q "Phase 5 will not perform unprotected Docker pulls" "$ROOT_DIR/installers/phases/08-images.sh"; then
  print_pass
else
  print_fail
fi

printf "  %-60s " "Phase 11 preflights Compose images with retry..."
if grep -q "_phase11_pre_pull_compose_images" "$ROOT_DIR/installers/phases/11-services.sh" \
  && grep -q "pull_with_progress" "$ROOT_DIR/installers/phases/11-services.sh"; then
  print_pass
else
  print_fail
fi

printf "  %-60s " "Phase 11 disables implicit Compose pulls..."
if grep -q -- "--pull never" "$ROOT_DIR/installers/phases/11-services.sh"; then
  print_pass
else
  print_fail
fi

echo ""
echo "3. Phase 08 selection (complete phase, inert Docker/config/pull fixtures)"
echo "────────────────────────────────────────────────────────────────────"

run_phase08_selection() (
  set -euo pipefail
  local scenario="$1" backend="$2"
  local case_dir="$TMP_DIR/phase08-$scenario-$backend"
  export INSTALL_DIR="$case_dir/install with spaces"
  export PHASE08_CASE_DIR="$case_dir"
  mkdir -p "$INSTALL_DIR/scripts" "$case_dir/source"
  : > "$INSTALL_DIR/.env"
  : > "$case_dir/pulled"
  : > "$case_dir/validated"
  : > "$case_dir/warnings"
  export LOG_FILE="$case_dir/install.log"

  # Only the installed resolver knows the overlays written by phase 06. Check
  # its exact directory/hardware arguments, then return a distinct installed
  # overlay so using the stale source flags cannot accidentally pass.
  cat > "$INSTALL_DIR/scripts/resolve-compose-stack.sh" <<'RESOLVER'
#!/usr/bin/env bash
[[ $# == 10 && $1 == --script-dir && $2 == "$INSTALL_DIR" &&
   $3 == --tier && $4 == 2 && $5 == --gpu-backend && $6 == "$GPU_BACKEND" &&
   $7 == --gpu-count && $8 == 2 && $9 == --ods-mode && ${10} == "$ODS_MODE" ]] || exit 92
printf '%s\n' "$@" > "$PHASE08_CASE_DIR/resolver.args"
printf '%s\n' '-f docker-compose.base.yml -f docker-compose.fixture-installed.yml'
RESOLVER
  chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"
  cat > "$case_dir/docker" <<'DOCKER'
#!/usr/bin/env bash
[[ $PWD == "$INSTALL_DIR" && -f .env && $1 == compose ]] || exit 93
[[ "$*" == 'compose -f docker-compose.base.yml -f docker-compose.fixture-installed.yml config --format json' ]] || exit 94
printf '%s\n' "$PWD" > "$PHASE08_CASE_DIR/compose.cwd"
printf '%s\n' '{"services":{"selected":{"image":"example.invalid/selected:v1"},"built":{"image":"ods-built:local","build":{"context":"."}}}}'
DOCKER
  chmod +x "$case_dir/docker"

  export GPU_BACKEND="$backend" ODS_MODE=local
  TIER=2; GPU_COUNT=2; DRY_RUN=false
  LEMONADE_EXTERNAL=false; EXTERNAL_LLM_URL=''
  ENABLE_PERPLEXICA=false; ENABLE_COMFYUI=false; ENABLE_VOICE=false
  ENABLE_WORKFLOWS=false; ENABLE_RAG=false; ENABLE_QDRANT=false
  ENABLE_EMBEDDINGS=false; ENABLE_HERMES=false; ENABLE_OPENCLAW=false
  LLAMA_SERVER_IMAGE="example.invalid/llama-$backend:v1"
  LEMONADE_SERVER_IMAGE='example.invalid/lemonade:v1'
  DOCKER_COMPOSE_CMD="$case_dir/docker compose"
  COMPOSE_FLAGS='-f docker-compose.base.yml -f docker-compose.stale-source.yml'
  BGRN=''; AMB=''; NC=''
  case "$scenario" in
    external) EXTERNAL_LLM_URL='http://127.0.0.1:9999/v1' ;;
    lemonade-external) LEMONADE_EXTERNAL=true ;;
    cloud) ODS_MODE=cloud ;;
    local) ENABLE_PERPLEXICA=true ;;
    optional) EXTERNAL_LLM_URL='http://127.0.0.1:9999/v1'; ENABLE_PERPLEXICA=true
      ENABLE_VOICE=true; ENABLE_WORKFLOWS=true; ENABLE_QDRANT=true
      ENABLE_EMBEDDINGS=true; ENABLE_HERMES=true; ENABLE_OPENCLAW=true ;;
    *) return 90 ;;
  esac
  ods_progress() { :; }; show_phase() { :; }; bootline() { :; }; signal() { :; }
  ai() { :; }; ai_ok() { :; }; ai_bad() { :; }
  ai_warn() { printf '%s\n' "$*" >> "$case_dir/warnings"; }
  pull_with_progress() { printf '%s\n' "$1" >> "$case_dir/pulled"; }
  validate_docker_image_or_fallback() {
    printf '%s\n' "$2" >> "$case_dir/validated"
    printf -v "$1" '%s' "$2"
  }
  source "$ROOT_DIR/installers/lib/compose-images.sh"
  cd "$case_dir/source"
  source "$ROOT_DIR/installers/phases/08-images.sh" > "$case_dir/output"

  [[ "$PWD" == "$case_dir/source" && ! -s "$case_dir/warnings" ]]
  [[ "$(cat "$case_dir/compose.cwd")" == "$INSTALL_DIR" ]]
  [[ -s "$case_dir/resolver.args" ]]
  grep -Fxq 'example.invalid/selected:v1' "$case_dir/pulled"
  grep -Fxq 'ghcr.io/open-webui/open-webui:v0.7.2' "$case_dir/pulled"
  if grep -q '^ods-built:' "$case_dir/pulled"; then return 1; fi
  if [[ "$scenario" == local ]]; then
    if [[ "$backend" == amd ]]; then
      grep -Fxq 'example.invalid/lemonade:v1' "$case_dir/pulled"
    else
      grep -Fxq "$LLAMA_SERVER_IMAGE" "$case_dir/pulled"
      grep -Fxq "$LLAMA_SERVER_IMAGE" "$case_dir/validated"
    fi
  else
    if grep -Eq '^example\.invalid/(llama-|lemonade:)' "$case_dir/pulled"; then return 1; fi
    if grep -Eq '^example\.invalid/(llama-|lemonade:)' "$case_dir/validated"; then return 1; fi
  fi
  if [[ "$ENABLE_PERPLEXICA" == true ]]; then
    grep -q '^itzcrazykns1337/perplexica:.*@sha256:' "$case_dir/pulled"
  else
    if grep -q '^itzcrazykns1337/perplexica:' "$case_dir/pulled"; then return 1; fi
  fi
  if [[ "$scenario" == optional ]]; then
    for image in ghcr.io/speaches-ai/speaches ghcr.io/remsky/kokoro-fastapi-cpu n8nio/n8n \
      qdrant/qdrant ghcr.io/huggingface/text-embeddings-inference nousresearch/hermes-agent caddy ghcr.io/openclaw/openclaw; do
      grep -q "^$image:" "$case_dir/pulled"
    done
  elif [[ "$scenario" != local ]]; then
    [[ $(wc -l < "$case_dir/pulled") -eq 2 ]]
  fi
)

for scenario in external lemonade-external cloud local; do
  for backend in cpu nvidia amd; do
    printf "  %-60s " "Phase 08 $scenario / $backend selects only requested images..."
    # Do not invoke the fixture in an `if` condition: Bash would suppress its
    # errexit and a later assertion could hide an earlier failed assertion.
    set +e
    (set -e; run_phase08_selection "$scenario" "$backend")
    phase08_status=$?
    set -e
    if [[ $phase08_status -eq 0 ]]; then
      print_pass
    else
      print_fail "(see $TMP_DIR/phase08-$scenario-$backend)"
    fi
  done
done
printf "  %-60s " "external LLM preserves explicitly enabled optional modules..."
set +e
(set -e; run_phase08_selection optional cpu)
phase08_status=$?
set -e
if [[ $phase08_status -eq 0 ]]; then
  print_pass
else
  print_fail
fi

echo ""
echo "4. Retry Strategy Tests (source-level sanity checks)"
echo "────────────────────────────────────────────────────"

printf "  %-60s " "max_attempts is 4..."
retry_count=$(grep -A 15 "^pull_with_progress()" "$ROOT_DIR/installers/lib/ui.sh" | grep -m1 "max_attempts=" | grep -oE '[0-9]+' || true)
retry_count=${retry_count:-0}
if [[ "$retry_count" == "4" ]]; then
  print_pass
else
  print_fail "(found $retry_count, expected 4)"
fi

printf "  %-60s " "default retry delays are 5s, 15s, 30s..."
default_delays=$(
  bash -c '
    set -euo pipefail
    source "$1"
    _docker_pull_retry_delay 1
    _docker_pull_retry_delay 2
    _docker_pull_retry_delay 3
  ' _ "$ROOT_DIR/installers/lib/ui.sh" | paste -sd ' '
)
if [[ "$default_delays" == "5 15 30" ]]; then
  print_pass
else
  print_fail "(found '$default_delays')"
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
