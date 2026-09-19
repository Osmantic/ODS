#!/bin/bash
# ============================================================================
# ODS Network Security Test Suite
# ============================================================================
# Validates network security configurations:
# - Port binding security (127.0.0.1 vs 0.0.0.0)
# - Service exposure validation
# - Network isolation checks
# - TLS/SSL configuration validation
# - Firewall and access control verification
#
# Usage: ./tests/test-network-security.sh
# Exit 0 if all pass, 1 if any fail
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

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
    echo -e "  ${GREEN}✓${NC} $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
    [[ -n "${2:-}" ]] && echo -e "        ${RED}→ $2${NC}"
    FAIL=$((FAIL + 1))
}

skip() {
    echo -e "  ${YELLOW}⊘${NC} $1"
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..70})${NC}"
}

# ============================================
# TEST 1: Port Binding Security
# ============================================
header "1/5" "Port Binding Security Validation"

# Check docker-compose files for secure port bindings
compose_files=(docker-compose.base.yml docker-compose.*.yml extensions/services/*/compose*.yaml)
insecure_bindings=()
secure_bindings=0

for compose_file in "${compose_files[@]}"; do
    if [[ -f "$compose_file" ]]; then
        service_name=$(basename "$(dirname "$compose_file")" | sed 's/compose//')

        # Check for 0.0.0.0 bindings (insecure) — literal or as a
        # ${VAR:-0.0.0.0} default, which is insecure when the var is unset.
        if grep -qE '(0\.0\.0\.0:|\$\{[A-Za-z_]+:-0\.0\.0\.0\})' "$compose_file"; then
            if [[ "$service_name" == "ods-proxy" ]]; then
                # ods-proxy is the LAN-facing reverse proxy; ODS_PROXY_BIND
                # intentionally defaults to 0.0.0.0.
                skip "ods-proxy binds 0.0.0.0 by default" "Intentional: LAN-facing reverse proxy"
            else
                insecure_bindings+=("$compose_file")
                fail "Insecure port binding in $compose_file" "Uses 0.0.0.0 (exposes to all interfaces)"
            fi
        fi

        # Check for loopback bindings (secure): literal 127.0.0.1: or the
        # project convention ${BIND_ADDRESS:-127.0.0.1}:.
        if grep -qE '(127\.0\.0\.1:|\$\{[A-Za-z_]+:-127\.0\.0\.1\})' "$compose_file"; then
            secure_bindings=$((secure_bindings + 1))
            pass "Secure port binding in $(basename "$compose_file")"
        fi

        # Check for port mappings without explicit IP (potentially insecure)
        if grep -E "^\s*-\s*[\"']?[0-9]+:[0-9]+" "$compose_file" | grep -v "127.0.0.1\|0.0.0.0"; then
            skip "Port binding without explicit IP in $(basename "$compose_file")" "Consider using 127.0.0.1"
        fi
    fi
done

echo ""
echo -e "    ${BOLD}Summary:${NC} $secure_bindings secure bindings, ${#insecure_bindings[@]} insecure bindings"

# ============================================
# TEST 2: Service Exposure Analysis
# ============================================
header "2/5" "Service Exposure Analysis"

# Check which services are exposed externally
external_services=()
internal_services=0

for compose_file in "${compose_files[@]}"; do
    if [[ -f "$compose_file" ]]; then
        service_name=$(basename "$(dirname "$compose_file")")

        # Services with external port mappings. `ports:` must be a real key
        # (indented, line-initial) — comment prose mentioning "ports:" is not
        # a mapping.
        if grep -qE '^[[:space:]]+ports:[[:space:]]*$' "$compose_file"; then
            # Check if ports are bound to localhost only: literal 127.0.0.1
            # or the ${BIND_ADDRESS:-127.0.0.1} default-loopback convention.
            if grep -A10 -E '^[[:space:]]+ports:[[:space:]]*$' "$compose_file" \
                | grep -qE '(127\.0\.0\.1:|\$\{[A-Za-z_]+:-127\.0\.0\.1\})'; then
                internal_services=$((internal_services + 1))
                pass "Service '$service_name' exposed only to localhost"
            else
                external_services+=("$service_name")
                # Check if this is intentional (dashboard, API endpoints)
                case "$service_name" in
                    dashboard|dashboard-api|open-webui|ods-proxy)
                        skip "Service '$service_name' externally exposed (expected for UI/API/LAN proxy)"
                        ;;
                    *)
                        fail "Service '$service_name' may be externally exposed" "Review port binding configuration"
                        ;;
                esac
            fi
        fi
    fi
done

# ============================================
# TEST 3: Network Isolation Validation
# ============================================
header "3/5" "Network Isolation Validation"

# Check for custom networks (good for isolation)
networks_defined=0
for compose_file in "${compose_files[@]}"; do
    if [[ -f "$compose_file" ]]; then
        if grep -q "networks:" "$compose_file"; then
            networks_defined=$((networks_defined + 1))
        fi
    fi
done

if [[ $networks_defined -gt 0 ]]; then
    pass "Custom networks defined for service isolation"
else
    skip "No custom networks defined (using default bridge network)"
fi

# Check for host networking (insecure). tailscale is a host VPN daemon —
# network_mode: host is its documented requirement, not an isolation bug.
host_network_count=0
for compose_file in "${compose_files[@]}"; do
    if [[ -f "$compose_file" ]]; then
        service_dir="$(basename "$(dirname "$compose_file")")"
        if grep -qE '^[[:space:]]+network_mode:[[:space:]]*host' "$compose_file"; then
            host_network_count=$((host_network_count + 1))
            if [[ "$service_dir" == "tailscale" ]]; then
                skip "Service 'tailscale' uses host networking (required for a VPN daemon)"
            else
                fail "Service uses host networking in $(basename "$compose_file")" "Breaks container isolation"
            fi
        fi
    fi
done

if [[ $host_network_count -eq 0 ]]; then
    pass "No services use host networking mode"
fi

# ============================================
# TEST 4: TLS/SSL Configuration
# ============================================
header "4/5" "TLS/SSL Configuration Validation"

# Check for TLS configuration in nginx configs
nginx_configs=(extensions/services/*/nginx.conf extensions/services/*/config/nginx.conf)
tls_configured=0

for nginx_config in "${nginx_configs[@]}"; do
    if [[ -f "$nginx_config" ]]; then
        service_name=$(basename "$(dirname "$(dirname "$nginx_config")")")

        # Check for SSL/TLS configuration
        if grep -q "ssl_certificate\|listen.*ssl\|https" "$nginx_config"; then
            tls_configured=$((tls_configured + 1))
            pass "Service '$service_name' has TLS configuration"
        else
            skip "Service '$service_name' nginx config lacks TLS" "Consider adding HTTPS support"
        fi

        # Check for security headers
        if grep -q "add_header.*Security\|add_header.*X-Frame-Options\|add_header.*Content-Security-Policy" "$nginx_config"; then
            pass "Service '$service_name' includes security headers"
        else
            fail "Service '$service_name' missing security headers" "Add X-Frame-Options, CSP, etc."
        fi
    fi
done

# Check for insecure WebSocket connections
js_files=(extensions/services/*/src/**/*.js extensions/services/*/public/*.js extensions/services/*/templates/*.html)
insecure_ws=0

for js_file in "${js_files[@]}"; do
    if [[ -f "$js_file" ]]; then
        if grep -q "ws://" "$js_file"; then
            insecure_ws=$((insecure_ws + 1))
            fail "Insecure WebSocket connection in $(basename "$js_file")" "Use wss:// instead of ws://"
        fi
    fi
done

if [[ $insecure_ws -eq 0 ]]; then
    pass "No insecure WebSocket connections found"
fi

# ============================================
# TEST 5: Access Control Validation
# ============================================
header "5/5" "Access Control Validation"

# Check for authentication middleware
auth_middleware=0
api_files=(extensions/services/*/main.py extensions/services/*/app.py extensions/services/*/routers/*.py)

for api_file in "${api_files[@]}"; do
    if [[ -f "$api_file" ]]; then
        service_name=$(basename "$(dirname "$(dirname "$api_file")")")

        # Check for authentication decorators/middleware
        if grep -q "@.*auth\|authenticate\|verify_token\|require.*auth" "$api_file"; then
            auth_middleware=$((auth_middleware + 1))
            pass "Service '$service_name' implements authentication"
        fi

        # Check for CORS configuration
        if grep -q "CORS\|cors\|Access-Control-Allow" "$api_file"; then
            pass "Service '$service_name' has CORS configuration"
        fi
    fi
done

# Check for API key validation
api_key_validation=0
for api_file in "${api_files[@]}"; do
    if [[ -f "$api_file" ]]; then
        if grep -q "api_key\|API_KEY\|X-API-Key\|Authorization.*Bearer" "$api_file"; then
            api_key_validation=$((api_key_validation + 1))
        fi
    fi
done

if [[ $api_key_validation -gt 0 ]]; then
    pass "API key validation implemented in $api_key_validation services"
else
    skip "No explicit API key validation found"
fi

# Check for rate limiting
rate_limiting=0
for api_file in "${api_files[@]}"; do
    if [[ -f "$api_file" ]]; then
        if grep -q "rate.*limit\|throttle\|slowapi" "$api_file"; then
            rate_limiting=$((rate_limiting + 1))
            pass "Rate limiting found in $(basename "$(dirname "$(dirname "$api_file")")")"
        fi
    fi
done

if [[ $rate_limiting -eq 0 ]]; then
    skip "No rate limiting implementation found"
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC} ${BOLD}($TOTAL total)${NC}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}Network security issues found. Review and harden before deployment.${NC}"
    exit 1
else
    echo -e "${GREEN}All network security checks passed!${NC}"
    exit 0
fi