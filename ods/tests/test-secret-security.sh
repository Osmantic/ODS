#!/bin/bash
# ============================================================================
# ODS Secret Management Security Test Suite
# ============================================================================
# Validates secret management and credential security:
# - Hardcoded secrets detection
# - Environment variable usage validation
# - API key and token security
# - Configuration file security
# - Secret rotation capabilities
#
# Usage: ./tests/test-secret-security.sh
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
# TEST 1: Hardcoded Secrets Detection
# ============================================
header "1/6" "Hardcoded Secrets Detection"

# Patterns that indicate potential hardcoded secrets
secret_patterns=(
    "password\s*=\s*['\"][^'\"]{8,}"
    "api_key\s*=\s*['\"][^'\"]{16,}"
    "secret\s*=\s*['\"][^'\"]{16,}"
    "token\s*=\s*['\"][^'\"]{20,}"
    "key\s*=\s*['\"][a-zA-Z0-9]{32,}"
)

hardcoded_secrets=0
files_with_secrets=()

# Check Python files
while IFS= read -r -d '' pyfile; do
    for pattern in "${secret_patterns[@]}"; do
        if grep -qiE "$pattern" "$pyfile"; then
            # Exclude test files and examples
            if [[ ! "$pyfile" =~ test.*\.py$ ]] && [[ ! "$pyfile" =~ example.*\.py$ ]]; then
                hardcoded_secrets=$((hardcoded_secrets + 1))
                files_with_secrets+=("$pyfile")
                fail "Potential hardcoded secret in $(basename "$pyfile")" "$(grep -iE "$pattern" "$pyfile" | head -1 | sed 's/^[[:space:]]*//')"
            fi
        fi
    done
done < <(find extensions/services -name "*.py" -print0)

# Check JavaScript files
while IFS= read -r -d '' jsfile; do
    for pattern in "${secret_patterns[@]}"; do
        if grep -qiE "$pattern" "$jsfile"; then
            hardcoded_secrets=$((hardcoded_secrets + 1))
            files_with_secrets+=("$jsfile")
            fail "Potential hardcoded secret in $(basename "$jsfile")" "$(grep -iE "$pattern" "$jsfile" | head -1 | sed 's/^[[:space:]]*//')"
        fi
    done
done < <(find extensions/services -name "*.js" -print0)

# Check configuration files
config_files=(config/**/*.json config/**/*.yaml config/**/*.yml extensions/services/*/config/*.json)
for config_file in "${config_files[@]}"; do
    if [[ -f "$config_file" ]]; then
        # Check for suspicious long strings that might be secrets
        if grep -qE '"[a-zA-Z0-9+/]{32,}"' "$config_file"; then
            # Exclude known safe patterns (model names, checksums)
            if ! grep -qE "model|checksum|sha256|hash" "$config_file"; then
                hardcoded_secrets=$((hardcoded_secrets + 1))
                files_with_secrets+=("$config_file")
                fail "Potential hardcoded secret in $(basename "$config_file")" "Long encoded string detected"
            fi
        fi
    fi
done

if [[ $hardcoded_secrets -eq 0 ]]; then
    pass "No hardcoded secrets detected in source files"
else
    echo ""
    echo -e "    ${RED}Files with potential secrets:${NC} ${#files_with_secrets[@]}"
fi

# ============================================
# TEST 2: Environment Variable Usage
# ============================================
header "2/6" "Environment Variable Usage Validation"

# Check that services use environment variables for secrets
env_usage=0
services_using_env=()

while IFS= read -r -d '' pyfile; do
    service_name=$(basename "$(dirname "$(dirname "$pyfile")")")

    if grep -q "os\.environ\|getenv\|ENV\[" "$pyfile"; then
        if [[ ! " ${services_using_env[*]} " =~ " ${service_name} " ]]; then
            services_using_env+=("$service_name")
            env_usage=$((env_usage + 1))
            pass "Service '$service_name' uses environment variables"
        fi
    fi
done < <(find extensions/services -name "*.py" -print0)

# Check .env.example for secret placeholders
if [[ -f ".env.example" ]]; then
    secret_env_vars=0
    while IFS= read -r line; do
        if [[ "$line" =~ ^[A-Z_]+.*=.*$ ]] && [[ "$line" =~ (KEY|SECRET|TOKEN|PASSWORD) ]]; then
            secret_env_vars=$((secret_env_vars + 1))
        fi
    done < .env.example

    if [[ $secret_env_vars -gt 0 ]]; then
        pass ".env.example defines $secret_env_vars secret environment variables"
    else
        skip ".env.example should define secret environment variables"
    fi
else
    fail ".env.example file not found" "Should provide template for secret configuration"
fi

# ============================================
# TEST 3: API Key Security Implementation
# ============================================
header "3/6" "API Key Security Implementation"

# Check for API key validation in services
api_key_validation=0
services_with_auth=()

while IFS= read -r -d '' pyfile; do
    service_name=$(basename "$(dirname "$(dirname "$pyfile")")")

    # Check for authentication/authorization patterns
    if grep -q "verify.*key\|validate.*token\|check.*auth\|@.*auth\|require.*auth" "$pyfile"; then
        if [[ ! " ${services_with_auth[*]} " =~ " ${service_name} " ]]; then
            services_with_auth+=("$service_name")
            api_key_validation=$((api_key_validation + 1))
            pass "Service '$service_name' implements API key validation"
        fi
    fi
done < <(find extensions/services -name "*.py" -print0)

# Check for secure key generation
key_generation=0
while IFS= read -r -d '' pyfile; do
    if grep -q "secrets\.\|os\.urandom\|uuid\.uuid4\|random\.SystemRandom" "$pyfile"; then
        key_generation=$((key_generation + 1))
    fi
done < <(find extensions/services -name "*.py" -print0)

if [[ $key_generation -gt 0 ]]; then
    pass "Secure key generation methods found in $key_generation files"
else
    skip "No secure key generation methods found"
fi

# ============================================
# TEST 4: Configuration Security
# ============================================
header "4/6" "Configuration File Security"

# Check for secure configuration practices
secure_configs=0

# Check that sensitive configs are not in version control
sensitive_files=(.env config/secrets.json config/private.key)
for sensitive_file in "${sensitive_files[@]}"; do
    if [[ -f "$sensitive_file" ]]; then
        fail "Sensitive file '$sensitive_file' exists in repository" "Should be in .gitignore"
    else
        pass "Sensitive file '$sensitive_file' not in repository"
    fi
done

# Check .gitignore for secret patterns
if [[ -f ".gitignore" ]]; then
    secret_patterns_in_gitignore=0
    gitignore_patterns=("*.key" "*.pem" ".env" "secrets.*" "private.*")

    for pattern in "${gitignore_patterns[@]}"; do
        if grep -q "$pattern" .gitignore; then
            secret_patterns_in_gitignore=$((secret_patterns_in_gitignore + 1))
        fi
    done

    if [[ $secret_patterns_in_gitignore -ge 3 ]]; then
        pass ".gitignore includes secret file patterns ($secret_patterns_in_gitignore/5)"
    else
        fail ".gitignore missing secret file patterns" "Add *.key, *.pem, .env, secrets.*, private.*"
    fi
else
    fail ".gitignore file not found"
fi

# Check for configuration validation
config_validation=0
while IFS= read -r -d '' pyfile; do
    if grep -q "validate.*config\|schema.*validation\|config.*check" "$pyfile"; then
        config_validation=$((config_validation + 1))
    fi
done < <(find extensions/services -name "*.py" -print0)

if [[ $config_validation -gt 0 ]]; then
    pass "Configuration validation found in $config_validation files"
else
    skip "No explicit configuration validation found"
fi

# ============================================
# TEST 5: Secret Rotation Capabilities
# ============================================
header "5/6" "Secret Rotation Capabilities"

# Check for secret rotation mechanisms
rotation_mechanisms=0

# Check for key rotation in services
while IFS= read -r -d '' pyfile; do
    if grep -q "rotate.*key\|refresh.*token\|renew.*secret\|update.*credential" "$pyfile"; then
        rotation_mechanisms=$((rotation_mechanisms + 1))
        service_name=$(basename "$(dirname "$(dirname "$pyfile")")")
        pass "Service '$service_name' supports secret rotation"
    fi
done < <(find extensions/services -name "*.py" -print0)

# Check for token expiration handling
expiration_handling=0
while IFS= read -r -d '' pyfile; do
    if grep -q "expir\|ttl\|timeout\|valid.*until" "$pyfile"; then
        expiration_handling=$((expiration_handling + 1))
    fi
done < <(find extensions/services -name "*.py" -print0)

if [[ $expiration_handling -gt 0 ]]; then
    pass "Token expiration handling found in $expiration_handling files"
else
    skip "No explicit token expiration handling found"
fi

if [[ $rotation_mechanisms -eq 0 ]]; then
    skip "No explicit secret rotation mechanisms found"
fi

# ============================================
# TEST 6: Database Security for Secrets
# ============================================
header "6/6" "Database Security for Secrets"

# Check for encrypted storage of sensitive data
encryption_usage=0
while IFS= read -r -d '' pyfile; do
    if grep -q "encrypt\|decrypt\|cipher\|crypto\|hash.*password" "$pyfile"; then
        encryption_usage=$((encryption_usage + 1))
        service_name=$(basename "$(dirname "$(dirname "$pyfile")")")
        pass "Service '$service_name' uses encryption/hashing"
    fi
done < <(find extensions/services -name "*.py" -print0)

# Check for SQL injection prevention in token-spy (from security audit)
token_spy_db="extensions/services/token-spy/db.py"
if [[ -f "$token_spy_db" ]]; then
    # Check for the specific SQL injection pattern mentioned in security audit
    if grep -q "f\"ALTER TABLE.*{col}.*{typedef}\"" "$token_spy_db"; then
        # Check if there's allowlist validation to mitigate the risk
        if grep -q "ALLOWED_COLUMNS\|allowlist" "$token_spy_db"; then
            pass "Token-spy SQL injection mitigated with allowlist validation"
        else
            fail "SQL injection pattern in token-spy db.py" "Line 77: f-string in ALTER TABLE (see SECURITY_AUDIT.md M1)"
        fi
    else
        pass "No SQL injection patterns found in token-spy"
    fi

    # Check for parameterized queries
    if grep -q "execute.*?" "$token_spy_db" || grep -q "executemany" "$token_spy_db"; then
        pass "Token-spy uses parameterized queries"
    else
        skip "Token-spy should use parameterized queries"
    fi
else
    skip "Token-spy db.py not found"
fi

token_spy_main="extensions/services/token-spy/main.py"
if [[ -f "$token_spy_main" ]]; then
    if grep -Fq '@app.post("/v1/messages", dependencies=[Depends(verify_api_key)])' "$token_spy_main" && \
       grep -Fq '@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])' "$token_spy_main" && \
       grep -Fq '@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], dependencies=[Depends(verify_api_key)])' "$token_spy_main"; then
        pass "Token-spy proxy routes require API key auth"
    else
        fail "Token-spy proxy routes missing API key protection" "Expected verify_api_key on /v1/* and catch-all proxy routes"
    fi

    if grep -Fq 'for key in (' "$token_spy_main" && \
       grep -Fq '"anthropic-dangerous-direct-browser-access"' "$token_spy_main" && \
       grep -Fq 'for key in ("content-type", "accept", "user-agent", "openai-organization", "openai-project"):' "$token_spy_main" && \
       ! grep -Fq 'for key in ("authorization", "content-type", "accept", "user-agent"):' "$token_spy_main"; then
        pass "Token-spy upstream headers exclude proxy Authorization"
    else
        fail "Token-spy upstream headers may leak proxy Authorization" "Proxy auth should stop at Token Spy and not be forwarded upstream"
    fi
else
    skip "Token-spy main.py not found"
fi

# Check for password hashing
password_hashing=0
while IFS= read -r -d '' pyfile; do
    if grep -q "bcrypt\|scrypt\|pbkdf2\|argon2" "$pyfile"; then
        password_hashing=$((password_hashing + 1))
        service_name=$(basename "$(dirname "$(dirname "$pyfile")")")
        pass "Service '$service_name' uses secure password hashing"
    fi
done < <(find extensions/services -name "*.py" -print0)

if [[ $password_hashing -eq 0 ]]; then
    skip "No secure password hashing libraries found"
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
    echo -e "${RED}Secret management security issues found. Address before deployment.${NC}"
    exit 1
else
    echo -e "${GREEN}All secret management security checks passed!${NC}"
    exit 0
fi
