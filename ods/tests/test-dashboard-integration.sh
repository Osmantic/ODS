#!/bin/bash
# ODS Dashboard Integration Test
# Validates Dashboard API endpoints and connectivity
#
# Auth: dashboard-api routes require Authorization: Bearer $DASHBOARD_API_KEY
# (see security.py). Key resolved via tests/lib/auth-env.sh from shell env or
# installer .env. Missing key → auth-required checks SKIP rather than FAIL.

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth-env.sh
. "$SCRIPT_DIR/lib/auth-env.sh"
ae_resolve "$SCRIPT_DIR"

# API_URL override still honored (external test rigs); default uses the
# resolver's host+port, which reflects .env overrides + shell env precedence.
API_URL="${API_URL:-$ae_api_base}"
CURL_TIMEOUT=10  # seconds
PASS_FILE=$(mktemp)
FAIL_FILE=$(mktemp)
SKIP_FILE=$(mktemp)

# Cleanup temp files on exit
cleanup() {
    rm -f "$PASS_FILE" "$FAIL_FILE" "$SKIP_FILE" \
          "$PASS_FILE.lock" "$FAIL_FILE.lock" "$SKIP_FILE.lock"
}
trap cleanup EXIT

# Initialize counters
echo "0" > "$PASS_FILE"
echo "0" > "$FAIL_FILE"
echo "0" > "$SKIP_FILE"

# Thread-safe counter increment using file locking
increment_pass() {
    (
        flock -x 200
        local count
        count=$(cat "$PASS_FILE")
        echo $((count + 1)) > "$PASS_FILE"
    ) 200>"$PASS_FILE.lock"
}

increment_fail() {
    (
        flock -x 200
        local count
        count=$(cat "$FAIL_FILE")
        echo $((count + 1)) > "$FAIL_FILE"
    ) 200>"$FAIL_FILE.lock"
}

get_passed() { cat "$PASS_FILE"; }
get_failed() { cat "$FAIL_FILE"; }

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  Dashboard API Integration Tests${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

increment_skip() {
    # Skipped auth-required checks — separate counter so summary is honest.
    (
        flock -x 200
        local count
        count=$(cat "$SKIP_FILE")
        echo $((count + 1)) > "$SKIP_FILE"
    ) 200>"$SKIP_FILE.lock"
}
get_skipped() { cat "$SKIP_FILE"; }

# Test function. Always splats AE_AUTH_HEADER — harmless on /health (extra
# header ignored), required on every other dashboard-api endpoint.
test_endpoint() {
    local name=$1
    local endpoint=$2
    local expected_field=$3

    echo -n "  Testing $name ($endpoint)... "

    # Fetch with timeout
    response=$(curl -sf -m "$CURL_TIMEOUT" "${AE_AUTH_HEADER[@]}" "${API_URL}${endpoint}" 2>/dev/null) || {
        echo -e "${RED}FAIL${NC} (connection error)"
        increment_fail
        return 1
    }

    # Validate response is valid JSON before jq processing
    if ! echo "$response" | jq empty 2>/dev/null; then
        echo -e "${RED}FAIL${NC} (invalid JSON)"
        increment_fail
        return 1
    fi

    if echo "$response" | jq -e ".$expected_field" > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        increment_pass
        return 0
    else
        echo -e "${RED}FAIL${NC} (missing field: $expected_field)"
        increment_fail
        return 1
    fi
}

# Auth-required wrapper: skips (does NOT fail) when no bearer key is
# available, so a working install without .env in the test environment
# doesn't produce spurious FAILs for endpoints that are behaving correctly
# but locked. Returns 0 in either case so `set -e` doesn't abort the suite.
test_endpoint_auth() {
    local name=$1 endpoint=$2 expected_field=$3
    if ae_key_available; then
        test_endpoint "$name" "$endpoint" "$expected_field" || true
    else
        echo -e "  Testing $name ($endpoint)... ${YELLOW}SKIP${NC} (no DASHBOARD_API_KEY)"
        increment_skip
    fi
    return 0
}

# Test for JSON array response
test_array_endpoint() {
    local name=$1
    local endpoint=$2

    echo -n "  Testing $name ($endpoint)... "

    # Fetch with timeout
    response=$(curl -sf -m "$CURL_TIMEOUT" "${AE_AUTH_HEADER[@]}" "${API_URL}${endpoint}" 2>/dev/null) || {
        echo -e "${RED}FAIL${NC} (connection error)"
        increment_fail
        return 1
    }

    # Validate response is valid JSON
    if ! echo "$response" | jq empty 2>/dev/null; then
        echo -e "${RED}FAIL${NC} (invalid JSON)"
        increment_fail
        return 1
    fi

    if echo "$response" | jq -e 'type == "array"' > /dev/null 2>&1; then
        count=$(echo "$response" | jq 'length')
        echo -e "${GREEN}PASS${NC} ($count items)"
        increment_pass
        return 0
    else
        echo -e "${RED}FAIL${NC} (expected array)"
        increment_fail
        return 1
    fi
}

test_array_endpoint_auth() {
    local name=$1 endpoint=$2
    if ae_key_available; then
        test_array_endpoint "$name" "$endpoint" || true
    else
        echo -e "  Testing $name ($endpoint)... ${YELLOW}SKIP${NC} (no DASHBOARD_API_KEY)"
        increment_skip
    fi
    return 0
}

# Test response structure
test_status_structure() {
    echo -n "  Testing /api/status structure... "

    if ! ae_key_available; then
        echo -e "${YELLOW}SKIP${NC} (no DASHBOARD_API_KEY)"
        increment_skip
        return 0
    fi

    # Fetch with timeout
    response=$(curl -sf -m "$CURL_TIMEOUT" "${AE_AUTH_HEADER[@]}" "${API_URL}/api/status" 2>/dev/null) || {
        echo -e "${RED}FAIL${NC} (connection error)"
        increment_fail
        return 1
    }
    
    # Validate response is valid JSON
    if ! echo "$response" | jq empty 2>/dev/null; then
        echo -e "${RED}FAIL${NC} (invalid JSON)"
        increment_fail
        return 1
    fi
    
    # Check required fields
    required_fields=("services" "uptime" "version" "tier")
    missing=""
    
    for field in "${required_fields[@]}"; do
        if ! echo "$response" | jq -e ".$field" > /dev/null 2>&1; then
            missing="$missing $field"
        fi
    done
    
    if [ -z "$missing" ]; then
        echo -e "${GREEN}PASS${NC}"
        increment_pass
        return 0
    else
        echo -e "${RED}FAIL${NC} (missing:$missing)"
        increment_fail
        return 1
    fi
}

if ! ae_key_available; then
    echo -e "${YELLOW}Note:${NC} DASHBOARD_API_KEY not found (checked shell env + $ae_env_file)."
    echo -e "${YELLOW}      Auth-required checks will be skipped (not failed).${NC}"
    echo ""
fi

# Run tests. /health is the only public endpoint on dashboard-api — everything
# else (see security.py) requires Authorization: Bearer $DASHBOARD_API_KEY.
echo -e "${CYAN}Core Endpoints:${NC}"
test_endpoint "Health" "/health" "status"
test_endpoint_auth "Disk" "/disk" "used_gb"
test_endpoint_auth "Bootstrap" "/bootstrap" "active"
test_array_endpoint_auth "Services" "/services"

echo ""
echo -e "${CYAN}Dashboard Endpoint:${NC}"
test_status_structure

echo ""
echo -e "${CYAN}Optional Endpoints (may fail without GPU/services):${NC}"
test_endpoint_auth "GPU" "/gpu" "name"
test_endpoint_auth "Model" "/model" "name"

# Summary
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Results: ${GREEN}$(get_passed) passed${NC}, ${RED}$(get_failed) failed${NC}, ${YELLOW}$(get_skipped) skipped${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ "$(get_failed)" -gt 0 ]; then
    exit 1
fi

echo -e "${GREEN}All critical tests passed!${NC}"
