#!/bin/bash
# ============================================================================
# ODS compose failure report tests
# ============================================================================
# Behavioral test for the install-time report writer using a mocked docker CLI.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORT_LIB="$ROOT_DIR/installers/lib/compose-failure-report.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

assert_contains() {
    local file="$1" needle="$2" label="$3"
    if grep -Fq -- "$needle" "$file"; then
        pass "$label"
    else
        fail "$label"
        echo "    missing: $needle"
    fi
}

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

INSTALL_DIR="$TMP_DIR/ods"
mkdir -p "$INSTALL_DIR/logs" "$TMP_DIR/bin"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=core
GPU_BACKEND=nvidia
LLM_MODEL=gemma-4-e2b-it
GGUF_FILE=gemma-4-E2B-it-Q4_K_M.gguf
LLAMA_SERVER_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda-b8648
CTX_SIZE=32768
OLLAMA_PORT=39134
WEBUI_PORT=39000
DASHBOARD_PORT=39001
DASHBOARD_API_PORT=39002
LITELLM_PORT=39040
SEARXNG_PORT=39888
DASHBOARD_API_KEY=super-secret-dashboard-key
OPENCLAW_TOKEN=super-secret-openclaw-token
EOF

cat > "$INSTALL_DIR/.compose-flags" <<'EOF'
--env-file .env -f docker-compose.base.yml -f docker-compose.nvidia.yml
EOF

COMPOSE_LOG="$INSTALL_DIR/logs/compose-up.log"
cat > "$COMPOSE_LOG" <<'EOF'
Image ghcr.io/ggml-org/llama.cpp:server-cuda-b8648 Pulling
Error response from daemon: failed to resolve reference "ghcr.io/ggml-org/llama.cpp:server-cuda-b8648": not found
EOF

cat > "$TMP_DIR/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "version" ]]; then
  echo "Client: Docker Engine"
  echo " Version: 29.2.1"
  exit 0
fi
if [[ "$1" == "info" ]]; then
  echo "Server Version: 29.2.1"
  echo "Operating System: Docker Desktop"
  exit 0
fi
if [[ "$1" == "compose" ]]; then
  if [[ "$*" == *" config"* ]]; then
    echo "services:"
    echo "  llama-server:"
    echo "    image: ghcr.io/ggml-org/llama.cpp:server-cuda-b8648"
    echo "  dashboard-api:"
    echo "    environment:"
    echo "      DASHBOARD_API_KEY: super-secret-dashboard-key"
    echo "      OPENCLAW_TOKEN: super-secret-openclaw-token"
    exit 0
  fi
  if [[ "$*" == *" ps -a"* ]]; then
    echo "NAME IMAGE COMMAND SERVICE CREATED STATUS PORTS"
    exit 0
  fi
fi
exit 0
EOF
chmod +x "$TMP_DIR/bin/docker"

export PATH="$TMP_DIR/bin:$PATH"

source "$REPORT_LIB"

echo ""
echo "=== Compose failure report tests ==="
echo ""

COMPOSE_FLAGS_REPORT="--env-file .env -f docker-compose.base.yml -f docker-compose.nvidia.yml"
report_path="$(
    COMPOSE_FLAGS_REPORT="$COMPOSE_FLAGS_REPORT" write_compose_failure_report \
        "$INSTALL_DIR" \
        "install-core phase 11 docker compose up" \
        "docker compose $COMPOSE_FLAGS_REPORT up -d --remove-orphans --no-build --pull never" \
        "$COMPOSE_LOG" \
        "nvidia" \
        "Fix the missing image tag, then re-run ./install.sh." |
        tail -n 1
)"

if [[ -f "$report_path" ]]; then
    pass "report file is created"
else
    fail "report file is missing"
fi

assert_contains "$report_path" "ODS install failure report" "report has title"
assert_contains "$report_path" "Phase: install-core phase 11 docker compose up" "report records phase"
assert_contains "$report_path" "GPU backend: nvidia" "report records GPU backend"
assert_contains "$report_path" "Compose command: docker compose --env-file .env -f docker-compose.base.yml -f docker-compose.nvidia.yml up -d --remove-orphans --no-build --pull never" "report records compose command"
assert_contains "$report_path" "LLAMA_SERVER_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda-b8648" "report records configured image"
assert_contains "$report_path" "- ghcr.io/ggml-org/llama.cpp:server-cuda-b8648" "report extracts failed image"
assert_contains "$report_path" "- dashboard:39001" "report includes port checks"
assert_contains "$report_path" "Docker version" "report includes docker version section"
assert_contains "$report_path" "Compose config tail (redacted)" "report includes redacted compose config section"
assert_contains "$report_path" "DASHBOARD_API_KEY: [REDACTED]" "report redacts compose config secret fields"
if grep -Fq "super-secret-dashboard-key" "$report_path" || grep -Fq "super-secret-openclaw-token" "$report_path"; then
    fail "report leaks sensitive compose config values"
else
    pass "report does not leak sensitive compose config values"
fi
assert_contains "$report_path" "Fix the missing image tag, then re-run ./install.sh." "report records next step"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
