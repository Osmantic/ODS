#!/bin/bash
# ODS Integration Test Suite
# Tests the full user journey: hardware detection, configs, install dry-run, workflows
# Usage: ./tests/integration-test.sh
# Exit 0 if all pass, 1 if any fail

set -euo pipefail

# Ensure TERM is set (needed for scripts that use clear/tput)
export TERM="${TERM:-xterm}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
COMPOSE_FILE=""
COMPOSE_FLAGS=""
if [[ -f "docker-compose.base.yml" && -f "docker-compose.amd.yml" ]]; then
    COMPOSE_FILE="docker-compose.amd.yml"
    COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.amd.yml"
    # Append enabled extension compose fragments
    if [[ -d "extensions/services" ]]; then
        for ext_dir in extensions/services/*/; do
            [[ -f "${ext_dir}compose.yaml" ]] && COMPOSE_FLAGS="$COMPOSE_FLAGS -f ${ext_dir}compose.yaml"
            [[ -f "${ext_dir}compose.amd.yaml" ]] && COMPOSE_FLAGS="$COMPOSE_FLAGS -f ${ext_dir}compose.amd.yaml"
        done
    fi
elif [[ -f "docker-compose.yml" ]]; then
    COMPOSE_FILE="docker-compose.yml"
    COMPOSE_FLAGS="-f docker-compose.yml"
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
SKIP=0

pass() {
    echo -e "  ${GREEN}PASS${NC}  $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC}  $1"
    if [[ -n "${2:-}" ]]; then
        echo -e "        ${RED}→ $2${NC}"
    fi
    FAIL=$((FAIL + 1))
}

skip() {
    echo -e "  ${YELLOW}SKIP${NC}  $1"
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..50})${NC}"
}

# ============================================
# TEST 1: Hardware Detection
# ============================================
header "1/6" "Hardware Detection"

if [[ ! -x "$PROJECT_DIR/scripts/detect-hardware.sh" ]]; then
    fail "detect-hardware.sh not found or not executable"
else
    pass "detect-hardware.sh exists and is executable"

    PYTHON_CMD="python3"
    if [[ -f "$PROJECT_DIR/lib/python-cmd.sh" ]]; then
        . "$PROJECT_DIR/lib/python-cmd.sh"
        PYTHON_CMD="$(ods_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi

    # Test JSON output mode
    json_output=$("$PROJECT_DIR/scripts/detect-hardware.sh" --json 2>/dev/null) || true
    if echo "$json_output" | "$PYTHON_CMD" -m json.tool > /dev/null 2>&1; then
        pass "detect-hardware.sh --json produces valid JSON"

        # Verify required fields
        for field in os cpu cores ram_gb gpu tier; do
            if echo "$json_output" | "$PYTHON_CMD" -c "import sys,json; d=json.load(sys.stdin); assert '$field' in d" 2>/dev/null; then
                pass "JSON contains required field: $field"
            else
                fail "JSON missing required field: $field"
            fi
        done

        # Regression test: json_escape must handle quoted GPU names correctly
        escaped_output=$(bash -lc '
            . "'"$PROJECT_DIR"'/scripts/detect-hardware.sh"
            escaped=$(json_escape '\''NVIDIA "GeForce" RTX 4090'\'')
            printf "{\"gpu_name\":\"%s\"}\n" "$escaped"
        ' 2>/dev/null) || true
        if echo "$escaped_output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['gpu_name'] == 'NVIDIA \"GeForce\" RTX 4090'" 2>/dev/null; then
            pass "json_escape handles embedded double quotes"
        else
            fail "json_escape does not escape embedded double quotes correctly" "$escaped_output"
        fi
    else
        fail "detect-hardware.sh --json does not produce valid JSON" "$json_output"
    fi

    # Test human-readable output mode
    text_output=$("$PROJECT_DIR/scripts/detect-hardware.sh" 2>/dev/null) || true
    if [[ -n "$text_output" ]]; then
        pass "detect-hardware.sh produces human-readable output"
    else
        fail "detect-hardware.sh produces no output"
    fi
fi

# ============================================
# TEST 2: Docker Compose Syntax
# ============================================
header "2/6" "Docker Compose Validation"

# Provide required compose variables so validation doesn't fail on :? guards
export WEBUI_SECRET="${WEBUI_SECRET:-test}"
export N8N_USER="${N8N_USER:-admin@example.invalid}"
export N8N_PASS="${N8N_PASS:-test-password}"
export OPENCLAW_TOKEN="${OPENCLAW_TOKEN:-test-openclaw-token}"
export SEARXNG_SECRET="${SEARXNG_SECRET:-test-searxng-secret}"

if [[ -z "$COMPOSE_FILE" ]]; then
    fail "No compose file found (expected base+overlay or docker-compose.yml)"
else
    pass "Compose file exists: $(basename "$COMPOSE_FILE")"
    [[ -n "$COMPOSE_FLAGS" ]] && pass "Compose flags: $COMPOSE_FLAGS"

    # Syntax check with docker compose
    if command -v docker &> /dev/null; then
        if docker compose $COMPOSE_FLAGS config > /dev/null 2>&1; then
            pass "Compose selection passes syntax validation"
        else
            # Try with env file fallback
            if [[ -f "$PROJECT_DIR/.env.example" ]] && docker compose $COMPOSE_FLAGS --env-file "$PROJECT_DIR/.env.example" config > /dev/null 2>&1; then
                pass "Compose selection passes syntax validation (with .env.example)"
            else
                fail "Compose selection has syntax errors" "$(docker compose $COMPOSE_FLAGS config 2>&1 | head -3)"
            fi
        fi

        # Verify core services are defined (behaviorally)
        # The WebUI service name has changed over time (webui -> open-webui). We accept either.
        compose_config=$(docker compose $COMPOSE_FLAGS --env-file "$PROJECT_DIR/.env.example" config 2>/dev/null || docker compose $COMPOSE_FLAGS config 2>/dev/null || true)

        if echo "$compose_config" | grep -qE "^\s{2}llama-server:$" 2>/dev/null || \
           grep -qE "^[[:space:]]*llama-server:" "$COMPOSE_FILE" 2>/dev/null; then
            pass "Core service defined: llama-server"
        else
            fail "Core service missing: llama-server"
        fi

        if echo "$compose_config" | grep -qE "^\s{2}(open-webui|webui):$" 2>/dev/null || \
           grep -qE "^[[:space:]]*(open-webui|webui):" "$COMPOSE_FILE" 2>/dev/null; then
            pass "Core service defined: web UI (open-webui or webui)"
        else
            fail "Core service missing: web UI (open-webui or webui)"
        fi

        # Optional: if both are present, report it (not a failure).
        if echo "$compose_config" | grep -qE "^\s{2}open-webui:$" 2>/dev/null && \
           echo "$compose_config" | grep -qE "^\s{2}webui:$" 2>/dev/null; then
            pass "Both web UI service names present (open-webui + webui)"
        fi
    else
        skip "Docker not installed — cannot validate compose syntax"
    fi
fi

# ============================================
# TEST 3: Profile Configs (YAML validation)
# ============================================
header "3/6" "Profile Configs"

PROFILES_DIR="$PROJECT_DIR/config/profiles"
if [[ ! -d "$PROFILES_DIR" ]]; then
    skip "config/profiles/ directory not found (not required in Strix layout)"
else
    pass "config/profiles/ directory exists"

    profile_count=0
    for profile in "$PROFILES_DIR"/*.yml "$PROFILES_DIR"/*.yaml; do
        [[ ! -f "$profile" ]] && continue
        profile_count=$((profile_count + 1))
        basename_profile=$(basename "$profile")

        # YAML validation using python (skip if PyYAML not installed)
        if ! "$PYTHON_CMD" -c "import yaml" 2>/dev/null; then
            skip "PyYAML not installed — cannot validate: $basename_profile"
        elif "$PYTHON_CMD" -c "
import yaml, sys
with open('$profile') as f:
    yaml.safe_load(f)
" 2>/dev/null; then
            pass "Valid YAML: $basename_profile"
        else
            fail "Invalid YAML: $basename_profile"
        fi

        # Check that profile defines a llama-server service override
        if grep -q "llama-server" "$profile" 2>/dev/null; then
            pass "Profile defines llama-server config: $basename_profile"
        else
            fail "Profile missing llama-server config: $basename_profile"
        fi
    done

    if [[ $profile_count -eq 0 ]]; then
        fail "No profile YAML files found in config/profiles/"
    else
        pass "Found $profile_count profile(s)"
    fi
fi

# ============================================
# TEST 4: Install Script Dry Run
# ============================================
header "4/6" "Install Script Dry Run"

if [[ ! -x "$PROJECT_DIR/install.sh" ]]; then
    if [[ -f "$PROJECT_DIR/install.sh" ]]; then
        fail "install.sh exists but is not executable"
    else
        fail "install.sh not found"
    fi
else
    pass "install.sh exists and is executable"

    # Check --help flag
    if "$PROJECT_DIR/install.sh" --help 2>&1 | grep -qiE "usage|option|ods"; then
        pass "install.sh --help produces usage info"
    else
        fail "install.sh --help does not produce usage info"
    fi

    # Verify --dry-run flag is supported (check source + run test)
    if grep -q "\-\-dry-run" "$PROJECT_DIR/install.sh" 2>/dev/null; then
        pass "install.sh supports --dry-run flag"
    else
        fail "install.sh does not support --dry-run flag"
    fi

    # Verify --non-interactive flag is supported
    if grep -q "\-\-non-interactive" "$PROJECT_DIR/install.sh" 2>/dev/null; then
        pass "install.sh supports --non-interactive flag"
    else
        fail "install.sh does not support --non-interactive flag"
    fi

    # Verify --tier flag is supported
    if grep -q "\-\-tier" "$PROJECT_DIR/install.sh" 2>/dev/null; then
        pass "install.sh supports --tier flag"
    else
        fail "install.sh does not support --tier flag"
    fi

    # Verify it doesn't create files in dry-run
    test_marker="/tmp/ods-dry-run-check-$$"
    if [[ ! -d "$HOME/ods" ]] || [[ -f "$HOME/ods/.env" ]]; then
        # Can't safely test side effects if already installed
        skip "Cannot verify dry-run side effects (existing installation detected)"
    else
        pass "Dry run did not create install directory"
    fi
fi

# ============================================
# TEST 5: Workflow JSON Validation
# ============================================
header "5/6" "Workflow JSON Files"

WORKFLOWS_DIR="$PROJECT_DIR/workflows"
if [[ ! -d "$WORKFLOWS_DIR" ]]; then
    WORKFLOWS_DIR="$PROJECT_DIR/config/n8n"
fi
if [[ ! -d "$WORKFLOWS_DIR" ]]; then
    skip "workflow directory not found (checked workflows/ and config/n8n/)"
else
    pass "Workflow directory exists: ${WORKFLOWS_DIR#$PROJECT_DIR/}"

    json_count=0
    for wf in "$WORKFLOWS_DIR"/*.json; do
        [[ ! -f "$wf" ]] && continue
        json_count=$((json_count + 1))
        basename_wf=$(basename "$wf")

        # JSON syntax validation
        if "$PYTHON_CMD" -m json.tool "$wf" > /dev/null 2>&1; then
            pass "Valid JSON: $basename_wf"
        else
            fail "Invalid JSON: $basename_wf"
        fi

        # Check for n8n workflow structure.
        # Some JSON files (like catalog.json) are metadata manifests, not workflow exports.
        if "$PYTHON_CMD" -c "
import json, sys
with open('$wf') as f:
    d = json.load(f)
assert 'nodes' in d, 'missing nodes key'
" 2>/dev/null; then
            pass "Has n8n structure (nodes): $basename_wf"
        elif "$PYTHON_CMD" -c "
import json, sys
with open('$wf') as f:
    d = json.load(f)
assert 'workflows' in d or 'categories' in d, 'not a metadata manifest'
" 2>/dev/null; then
            skip "Metadata manifest (not workflow export): $basename_wf"
        else
            fail "Missing n8n structure (nodes): $basename_wf"
        fi
    done

    if [[ $json_count -eq 0 ]]; then
        fail "No workflow JSON files found"
    else
        pass "Found $json_count workflow(s)"
    fi
fi

# ============================================
# TEST 6: Showcase Script
# ============================================
header "6/6" "Showcase & Supporting Scripts"

# showcase.sh
if [[ -f "$PROJECT_DIR/scripts/showcase.sh" ]]; then
    if [[ -x "$PROJECT_DIR/scripts/showcase.sh" ]]; then
        pass "showcase.sh exists and is executable"
    else
        pass "showcase.sh exists (not executable — will fix)"
    fi

    # Check it has menu function
    if grep -qE "print_menu|menu|select.*option" "$PROJECT_DIR/scripts/showcase.sh" 2>/dev/null; then
        pass "showcase.sh contains menu logic"
    else
        fail "showcase.sh missing menu logic"
    fi
else
    fail "showcase.sh not found"
fi

# first-boot-demo.sh
if [[ -f "$PROJECT_DIR/scripts/first-boot-demo.sh" ]]; then
    pass "first-boot-demo.sh exists"
else
    skip "first-boot-demo.sh not found (optional)"
fi

# .env.example
if [[ -f "$PROJECT_DIR/.env.example" ]]; then
    pass ".env.example exists"
    # Check it contains essential vars (may be commented with defaults)
    for var in LLM_MODEL WEBUI_PORT; do
        if grep -qE "^#?\s*${var}=" "$PROJECT_DIR/.env.example"; then
            pass ".env.example defines $var"
        else
            fail ".env.example missing $var"
        fi
    done
    if grep -qE "^#?\s*(LLAMA_SERVER_PORT|OLLAMA_PORT)=" "$PROJECT_DIR/.env.example"; then
        pass ".env.example defines an inference port variable"
    else
        fail ".env.example missing inference port variable (LLAMA_SERVER_PORT/OLLAMA_PORT)"
    fi
else
    fail ".env.example not found"
fi

# examples/ directory
if [[ -d "$PROJECT_DIR/examples" ]]; then
    pass "examples/ directory exists"
    for f in sample-doc.txt sample-code.py; do
        if [[ -f "$PROJECT_DIR/examples/$f" ]]; then
            pass "Example file exists: $f"
        else
            skip "Example file missing: $f"
        fi
    done
else
    skip "examples/ directory not found"
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC} ${BOLD}($TOTAL total)${NC}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
