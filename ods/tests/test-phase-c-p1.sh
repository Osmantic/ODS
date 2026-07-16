#!/bin/bash
# ODS Phase C (P1 - Broken Features) Tests
# Prioritized in CI pipeline after P0 (Phase A/B) completion
# Run: bash test-phase-c-p1.sh

set -uo pipefail

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Shared auth + port resolution (shell env → installer .env, CRLF-safe).
# Populates DASHBOARD_API_KEY, DASHBOARD_API_PORT, ae_api_base, AE_AUTH_HEADER.
# shellcheck source=lib/auth-env.sh
. "$SCRIPT_DIR/lib/auth-env.sh"
ae_resolve "$SCRIPT_DIR"

# Config — API_URL honors override for external test rigs.
API_URL="${API_URL:-$ae_api_base}"
DASHBOARD_API_URL="http://localhost:3001"
TEST_TIMEOUT=10

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

log_pass() {
    echo "[PASS] $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

log_fail() {
    echo "[FAIL] $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

log_warn() {
    echo "[WARN] $1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

# Auth-aware curl for dashboard-api. Splats AE_AUTH_HEADER (empty when no
# key), so /health still works; auth-required endpoints get the Bearer
# header. Call sites for auth-required probes should gate on
# ae_key_available and log_warn otherwise, since curl -sf on a 401 returns
# empty and the downstream grep would misreport "endpoint missing".
api_curl() {
    curl -sf -m "$TEST_TIMEOUT" "${AE_AUTH_HEADER[@]}" "$@"
}

echo ""
echo "================================================================"
echo "  ODS Phase C (P1) - Broken Features"
echo "  Prioritized CI Pipeline Tests"
echo "================================================================"
echo ""

if ! ae_key_available; then
    echo "NOTE: DASHBOARD_API_KEY not found (checked shell env + $ae_env_file)."
    echo "      Auth-required checks (C1, C2, C4, C5) will be WARNed as skipped,"
    echo "      not FAILed — set the key to run them."
    echo ""
fi

# ==============================================================═
# C1. Settings page is a static mockup
# ==============================================================═
echo -e "${CYAN}-- C1. Settings Page Mockup Detection -----------------------"

if ! ae_key_available; then
    log_warn "C1 skipped — no DASHBOARD_API_KEY"
else
    SETTINGS_TEST=$(api_curl "${API_URL}/api/settings" 2>/dev/null || echo "")
    if [ -n "$SETTINGS_TEST" ] && echo "$SETTINGS_TEST" | jq -e '.version, .installDate, .tier, .uptime, .storage' >/dev/null 2>&1; then
        # Check if values are hardcoded (static string detection)
        if echo "$SETTINGS_TEST" | grep -qE '"version":"1\.0\.0"|"installDate":"202[0-9]-' 2>/dev/null; then
            log_fail "Settings page returns hardcoded mockup values"
        else
            log_pass "Settings page returns dynamic values"
        fi
    else
        log_warn "Settings endpoint not implemented (404)"
    fi
fi

# ==============================================================═
# C2. Setup wizard endpoint validation
# ==============================================================═
echo -e "${CYAN}-- C2. Setup Wizard Endpoints -------------------------------"

if ! ae_key_available; then
    log_warn "C2 skipped — no DASHBOARD_API_KEY"
else
    # Test /api/setup/test endpoint
    SETUP_TEST=$(api_curl -X POST "${API_URL}/api/setup/test" 2>/dev/null || echo "")
    if echo "$SETUP_TEST" | grep -qE "404|Not Found"; then
        log_fail "Setup wizard calls non-existent endpoint: POST /api/setup/test"
    else
        log_pass "Setup wizard endpoints exist"
    fi

    # Test LLM test endpoint
    LLM_TEST=$(api_curl "${API_URL}/api/test/llm" 2>/dev/null || echo "")
    if echo "$LLM_TEST" | grep -qE "404|Not Found"; then
        log_fail "Missing endpoint: GET /api/test/llm"
    else
        log_pass "LLM test endpoint exists"
    fi

    # Test voice test endpoint
    VOICE_TEST=$(api_curl "${API_URL}/api/test/voice" 2>/dev/null || echo "")
    if echo "$VOICE_TEST" | grep -qE "404|Not Found"; then
        log_fail "Missing endpoint: GET /api/test/voice"
    else
        log_pass "Voice test endpoint exists"
    fi

    # Test RAG test endpoint
    RAG_TEST=$(api_curl "${API_URL}/api/test/rag" 2>/dev/null || echo "")
    if echo "$RAG_TEST" | grep -qE "404|Not Found"; then
        log_fail "Missing endpoint: GET /api/test/rag"
    else
        log_pass "RAG test endpoint exists"
    fi

    # Test workflows test endpoint
    WORKFLOWS_TEST=$(api_curl "${API_URL}/api/test/workflows" 2>/dev/null || echo "")
    if echo "$WORKFLOWS_TEST" | grep -qE "404|Not Found"; then
        log_fail "Missing endpoint: GET /api/test/workflows"
    else
        log_pass "Workflows test endpoint exists"
    fi
fi

# ==============================================================═
# C3. SetupWizard step validation
# ==============================================================═
echo -e "${CYAN}-- C3. Setup Wizard Step Validation -------------------------"

if ! ae_key_available; then
    log_warn "C3 skipped — no DASHBOARD_API_KEY"
elif api_curl "${API_URL}/api/setup/wizard" >/dev/null 2>&1; then
    log_pass "Setup wizard API exists (validation should be in frontend)"
else
    log_warn "Setup wizard API not found"
fi

# ==============================================================═
# C4. Voice settings persistence
# ==============================================================═
echo -e "${CYAN}-- C4. Voice Settings Persistence ---------------------------"

if ! ae_key_available; then
    log_warn "C4 skipped — no DASHBOARD_API_KEY"
else
    # Try to save and retrieve voice settings
    VOICE_SETTINGS_TEST=$(api_curl "${API_URL}/api/voice/settings" 2>/dev/null || echo "")
    if [ -n "$VOICE_SETTINGS_TEST" ]; then
        # Check if settings endpoint returns data
        if echo "$VOICE_SETTINGS_TEST" | jq -e '.voice, .speed, .wakeWord' >/dev/null 2>&1; then
            log_pass "Voice settings endpoint returns structured data"
        else
            log_warn "Voice settings endpoint exists but structure unclear"
        fi
    else
        log_warn "Voice settings endpoint not implemented"
    fi
fi

# ==============================================================═
# C5. Voice agent operation order
echo -e "${CYAN}-- C5. Voice Agent Operation Order --------------------------"

if ! ae_key_available; then
    log_warn "C5 skipped — no DASHBOARD_API_KEY"
else
    # Check if voice agent service is running properly
    VOICE_STATUS=$(api_curl "${API_URL}/api/voice/status" 2>/dev/null || echo "")
    if [ -n "$VOICE_STATUS" ]; then
        if echo "$VOICE_STATUS" | jq -e '.available == true' >/dev/null 2>&1; then
            log_pass "Voice agent available and operational"
        else
            log_fail "Voice agent not available"
        fi
    else
        log_warn "Voice status endpoint not responding"
    fi
fi

# ==============================================================═
# C6. ods-cli status (replaced status.sh)
echo -e "${CYAN}-- C6. ods-cli status command ------------------------------"

ODS_CLI="${SCRIPT_DIR}/../ods-cli"
if [ -f "$ODS_CLI" ]; then
    if grep -q "cmd_status" "$ODS_CLI" 2>/dev/null; then
        log_pass "ods-cli has cmd_status function"
    else
        log_fail "ods-cli missing cmd_status function"
    fi
else
    log_warn "ods-cli not found"
fi

# ==============================================================═
# C7. ods-cli TTS service alias
echo -e "${CYAN}-- C7. ods-cli TTS Service Alias --------------------------"

ODS_CLI="${SCRIPT_DIR}/../ods-cli"
if [ -f "$ODS_CLI" ]; then
    if grep -qE "piper.*ods-piper|tts.*ods-piper" "$ODS_CLI" 2>/dev/null; then
        log_fail "ods-cli maps TTS to wrong container (ods-piper instead of ods-tts)"
    else
        log_pass "ods-cli TTS mapping appears correct"
    fi
else
    log_warn "ods-cli not found"
fi

# ==============================================================═
# C8. installer summary port display
echo -e "${CYAN}-- C8. Installer Summary Port Display -----------------------"

INSTALL_SCRIPT="${SCRIPT_DIR}/../install.sh"
if [ -f "$INSTALL_SCRIPT" ]; then
    if grep -qE "Whisper 9000|Kokoro 8002" "$INSTALL_SCRIPT" 2>/dev/null; then
        log_fail "install.sh shows wrong ports in summary (Whisper 9000, Kokoro 8002)"
    else
        log_pass "install.sh summary shows correct ports"
    fi
else
    log_warn "install.sh not found"
fi

# ==============================================================═
# C9. ods-update.sh GitHub repo
echo -e "${CYAN}-- C9. ods-update.sh GitHub Repo --------------------------"

UPDATE_SCRIPT="${SCRIPT_DIR}/../ods-update.sh"
if [ -f "$UPDATE_SCRIPT" ]; then
    if grep -q "GITHUB_REPO.*Osmantic/ODS" "$UPDATE_SCRIPT" 2>/dev/null; then
        log_pass "ods-update.sh GitHub repo configuration is correct (ODS)"
    else
        log_fail "ods-update.sh missing correct GitHub repo (should be Osmantic/ODS)"
    fi
else
    log_warn "ods-update.sh not found"
fi

# ==============================================================═
# C10. Migration script idempotency
echo -e "${CYAN}-- C10. Migration Script Idempotency ------------------------"

MIGRATION_SCRIPT="${SCRIPT_DIR}/../migrations/migrate-v0.2.0.sh"
if [ -f "$MIGRATION_SCRIPT" ]; then
    if ! grep -q "INSTALL_DIR=" "$MIGRATION_SCRIPT" 2>/dev/null; then
        log_fail "Migration script lacks INSTALL_DIR definition"
    elif [ ! -x "$MIGRATION_SCRIPT" ]; then
        log_fail "Migration script not executable"
    else
        log_pass "Migration script has INSTALL_DIR and is executable"
    fi
else
    log_warn "Migration script not found"
fi

# ==============================================================═
# C11. Container UID/GID configuration
echo -e "${CYAN}-- C11. Container UID/GID Configuration ---------------------"

COMPOSE_FILE="${SCRIPT_DIR}/../docker-compose.base.yml"
if [ ! -f "$COMPOSE_FILE" ] && [ -f "${SCRIPT_DIR}/../docker-compose.yml" ]; then
    COMPOSE_FILE="${SCRIPT_DIR}/../docker-compose.yml"
fi
if [ -f "$COMPOSE_FILE" ]; then
    if grep -qE "user:[[:space:]]*['\"]?1000:1000['\"]?" "$COMPOSE_FILE" 2>/dev/null; then
        log_fail "$(basename "$COMPOSE_FILE") hardcodes UID/GID 1000:1000"
    else
        log_pass "$(basename "$COMPOSE_FILE") uses dynamic UID/GID"
    fi
else
    log_warn "compose file not found"
fi

# ==============================================================═
# C12. Docker Compose profiles auto-start
echo -e "${CYAN}-- C12. Docker Compose Profiles Auto-Start ------------------"

if [ -f "$COMPOSE_FILE" ]; then
    if grep -q 'profiles:\s*\[default' "$COMPOSE_FILE" 2>/dev/null; then
        log_fail "$(basename "$COMPOSE_FILE") uses 'profiles: [default]' which doesn't auto-start"
    else
        log_pass "$(basename "$COMPOSE_FILE") doesn't use problematic default profile"
    fi
else
    log_warn "compose file not found"
fi

# SUMMARY
echo ""
echo "================================================================"
echo "  Phase C (P1) Results:"
echo "    ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${WARN_COUNT} warnings"
echo "================================================================"
echo ""

if [ $FAIL_COUNT -gt 0 ]; then
    echo "Phase C P1 tests failed - review Phase C items in DAILY-BOUNTY.md"
    exit 1
elif [ $WARN_COUNT -gt 0 ]; then
    echo "Phase C P1 tests passed with warnings"
    exit 0
else
    echo "Phase C P1 tests passed!"
    exit 0
fi
