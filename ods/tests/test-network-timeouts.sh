#!/bin/bash
# ============================================================================
# ODS Network Timeout Test Suite
# ============================================================================
# Tests that all network operations have appropriate timeout protection
#
# Usage: ./tests/test-network-timeouts.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

echo ""
echo "╔═══════════════════════════════════════════╗"
echo "║   Network Timeout Protection Test Suite   ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

# Test helper
test_file_has_timeout() {
    local file="$1"
    local pattern="$2"
    local timeout_pattern="$3"
    local description="$4"

    printf "  %-50s " "$description..."

    if ! [[ -f "$file" ]]; then
        echo -e "${YELLOW}SKIP (file not found)${NC}"
        return
    fi

    # Find lines matching the pattern
    local matches
    matches=$(grep -n "$pattern" "$file" 2>/dev/null || true)

    if [[ -z "$matches" ]]; then
        echo -e "${YELLOW}SKIP (no matches)${NC}"
        return
    fi

    # Check if timeout is present on the same line or nearby
    local has_timeout=false
    while IFS= read -r line; do
        local line_num line_content
        line_num=$(echo "$line" | cut -d: -f1)
        line_content=$(echo "$line" | cut -d: -f2-)

        # Check if timeout is on the same line (use grep -F for fixed string)
        if echo "$line_content" | grep -qF -- "$timeout_pattern"; then
            has_timeout=true
            break
        fi

        # Check next 2 lines for multiline commands
        for offset in 1 2; do
            local check_line next_line
            check_line=$((line_num + offset))
            next_line=$(sed -n "${check_line}p" "$file" 2>/dev/null || true)
            if echo "$next_line" | grep -qF -- "$timeout_pattern"; then
                has_timeout=true
                break 2
            fi
        done
    done <<< "$matches"

    if $has_timeout; then
        echo -e "${GREEN}✓ PASS${NC}"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}✗ FAIL${NC}"
        FAILED=$((FAILED + 1))
        echo "     Missing timeout in: $file"
        echo "     Pattern: $pattern"
    fi
}

echo "1. Installer Phase Scripts"
echo "───────────────────────────"

# Phase 05 - Docker
test_file_has_timeout \
    "$ROOT_DIR/installers/phases/05-docker.sh" \
    "curl.*https://get.docker.com" \
    "--max-time" \
    "Docker install script download"

test_file_has_timeout \
    "$ROOT_DIR/installers/phases/05-docker.sh" \
    "curl.*nvidia.github.io.*gpgkey" \
    "--max-time" \
    "NVIDIA GPG key download"

test_file_has_timeout \
    "$ROOT_DIR/installers/phases/05-docker.sh" \
    "curl.*nvidia.github.io.*deb.*list" \
    "--max-time" \
    "NVIDIA repo list download (apt)"

test_file_has_timeout \
    "$ROOT_DIR/installers/phases/05-docker.sh" \
    "curl.*nvidia.github.io.*rpm.*repo" \
    "--max-time" \
    "NVIDIA repo list download (dnf)"

# Phase 07 - Devtools
test_file_has_timeout \
    "$ROOT_DIR/installers/phases/07-devtools.sh" \
    "curl.*nodesource.com" \
    "--max-time" \
    "NodeSource setup script"

test_file_has_timeout \
    "$ROOT_DIR/installers/phases/07-devtools.sh" \
    "curl.*opencode.ai/install" \
    "--max-time" \
    "OpenCode install script"

# Phase 09 - Offline
test_file_has_timeout \
    "$ROOT_DIR/installers/phases/09-offline.sh" \
    "curl.*nomic-embed" \
    "--max-time" \
    "Embeddings download"

# Phase 11 - Services
test_file_has_timeout \
    "$ROOT_DIR/installers/phases/11-services.sh" \
    "curl.*GGUF_URL" \
    "--max-time" \
    "GGUF model download"

test_file_has_timeout \
    "$ROOT_DIR/installers/phases/11-services.sh" \
    "curl.*SDXL_URL" \
    "--max-time" \
    "SDXL Lightning model download"

# Phase 12 - Health checks
test_file_has_timeout \
    "$ROOT_DIR/installers/phases/12-health.sh" \
    "curl.*PERPLEXICA_URL.*api/config" \
    "--max-time" \
    "Perplexica config API"

test_file_has_timeout \
    "$ROOT_DIR/installers/phases/12-health.sh" \
    "curl.*WHISPER_URL.*v1/models" \
    "--max-time" \
    "Whisper STT model check"

echo ""
echo "2. Management Scripts"
echo "─────────────────────"

# ods-preflight.sh
# ods-preflight.sh
# Note: this script centralizes curl flags in CURL_HEALTH_FLAGS, so the timeout
# flags may not appear on the same line as the curl invocation.
test_file_has_timeout \
    "$ROOT_DIR/scripts/ods-preflight.sh" \
    "CURL_HEALTH_FLAGS=" \
    "--connect-timeout" \
    "Preflight defines connect timeout"

test_file_has_timeout \
    "$ROOT_DIR/scripts/ods-preflight.sh" \
    "CURL_HEALTH_FLAGS=" \
    "--max-time" \
    "Preflight defines total timeout"

# validate.sh
test_file_has_timeout \
    "$ROOT_DIR/scripts/validate.sh" \
    "curl.*llama-server health" \
    "--max-time" \
    "Validate LLM health check"

test_file_has_timeout \
    "$ROOT_DIR/scripts/validate.sh" \
    "curl.*v1/chat/completions" \
    "--max-time" \
    "Validate inference test"

# upgrade-model.sh
test_file_has_timeout \
    "$ROOT_DIR/scripts/upgrade-model.sh" \
    "curl.*INFERENCE_PORT/health" \
    "--max-time" \
    "Upgrade model health check"

test_file_has_timeout \
    "$ROOT_DIR/scripts/upgrade-model.sh" \
    "curl.*INFERENCE_PORT/v1/models" \
    "--max-time" \
    "Upgrade model inference test"

# ods-doctor.sh
test_file_has_timeout \
    "$ROOT_DIR/scripts/ods-doctor.sh" \
    "curl.*DASHBOARD_PORT" \
    "--max-time" \
    "Doctor dashboard check"

test_file_has_timeout \
    "$ROOT_DIR/scripts/ods-doctor.sh" \
    "curl.*WEBUI_PORT" \
    "--max-time" \
    "Doctor WebUI check"

echo ""
echo "3. macOS Installers"
echo "───────────────────"

# install-macos.sh
test_file_has_timeout \
    "$ROOT_DIR/installers/macos/install-macos.sh" \
    "curl.*localhost:8080/health" \
    "--max-time" \
    "macOS llama-server health check"

test_file_has_timeout \
    "$ROOT_DIR/installers/macos/install-macos.sh" \
    'curl.*HTTP_CODE.*\|HTTP_CODE=.*curl' \
    "--max-time" \
    "macOS final host-service health checks"

test_file_has_timeout \
    "$ROOT_DIR/installers/macos/install-macos.sh" \
    "curl.*opencode.ai/install" \
    "--max-time" \
    "macOS OpenCode install"

# ods-macos.sh
test_file_has_timeout \
    "$ROOT_DIR/installers/macos/ods-macos.sh" \
    "curl.*localhost:8080/health" \
    "--max-time" \
    "macOS CLI health check"

test_file_has_timeout \
    "$ROOT_DIR/installers/macos/ods-macos.sh" \
    'curl.*"\$url"' \
    "--max-time" \
    "macOS CLI status checks"

echo ""
echo "═══════════════════════════════════════════"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    echo ""
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC} ($PASSED passed, $FAILED failed)"
    echo ""
    exit 1
fi
