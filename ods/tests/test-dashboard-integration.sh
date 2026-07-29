#!/bin/bash
# ODS Dashboard Integration Test
# Validates Dashboard API endpoints and connectivity

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

source "$(dirname "${BASH_SOURCE[0]}")/lib/auth-env.sh"

# Config with environment variable support
API_URL="${API_URL:-http://localhost:${DASHBOARD_API_PORT}}"
CURL_TIMEOUT=10  # seconds

# Auth header for dashboard-api
AUTH_HEADER=("${AE_AUTH_HEADER[@]}")
PASS_FILE=$(mktemp)
FAIL_FILE=$(mktemp)

# Cleanup temp files on exit
cleanup() {
    rm -f "$PASS_FILE" "$FAIL_FILE"
}
trap cleanup EXIT

# Initialize counters
echo "0" > "$PASS_FILE"
echo "0" > "$FAIL_FILE"

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

# Test function
test_endpoint() {
    local name=$1
    local endpoint=$2
    local expected_field=$3
    
    echo -n "  Testing $name ($endpoint)... "
    
    # Fetch with timeout
    response=$(curl -sf "${AUTH_HEADER[@]}" -m "$CURL_TIMEOUT" "${API_URL}${endpoint}" 2>/dev/null) || {
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

# Test for JSON array response
test_array_endpoint() {
    local name=$1
    local endpoint=$2
    
    echo -n "  Testing $name ($endpoint)... "
    
    # Fetch with timeout
    response=$(curl -sf "${AUTH_HEADER[@]}" -m "$CURL_TIMEOUT" "${API_URL}${endpoint}" 2>/dev/null) || {
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

# Test response structure
test_status_structure() {
    echo -n "  Testing /api/status structure... "
    
    # Fetch with timeout
    response=$(curl -sf "${AUTH_HEADER[@]}" -m "$CURL_TIMEOUT" "${API_URL}/api/status" 2>/dev/null) || {
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

# Run tests
echo -e "${CYAN}Core Endpoints:${NC}"
test_endpoint "Health" "/health" "status"

if _ae_require_key; then
    test_endpoint "Disk" "/disk" "used_gb"
    test_endpoint "Bootstrap" "/bootstrap" "active"
    test_array_endpoint "Services" "/services"

    echo ""
    echo -e "${CYAN}Dashboard Endpoint:${NC}"
    test_status_structure

    echo ""
    echo -e "${CYAN}Optional Endpoints (may fail without GPU/services):${NC}"
    test_endpoint "GPU" "/gpu" "name" || true
    test_endpoint "Model" "/model" "name" || true
fi

# Summary
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Results: ${GREEN}$(get_passed) passed${NC}, ${RED}$(get_failed) failed${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ "$(get_failed)" -gt 0 ]; then
    exit 1
fi

echo -e "${GREEN}All critical tests passed!${NC}"
