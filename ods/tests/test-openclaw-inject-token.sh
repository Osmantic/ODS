#!/bin/bash
# ============================================================================
# OpenClaw inject-token.js Hardening Test Suite
# ============================================================================
# Validates that config/openclaw/inject-token.js produces a merged runtime
# config WITHOUT the dangerouslyAllowHostHeaderOriginFallback flag, while
# preserving the flags Docker auto-connect actually needs.
#
# Why this test matters:
#   - inject-token.js is load-bearing: it re-injects controlUi flags at every
#     container start, so any cosmetic edit to openclaw.json/pro.json/
#     openclaw-strix-halo.json is overridden by this script. The test must
#     guard the *runtime* output, not just the static JSON files.
#   - Device auth defaults ON (#1270): with the opt-in env unset,
#     dangerouslyDisableDeviceAuth must be false/absent in BOTH the merged
#     config and the ~/.openclaw home config. Setting
#     OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH=true deliberately disables it
#     and must emit a loud startup warning.
#   - allowedOrigins must be populated so the gateway no longer needs the
#     Host-header fallback to accept cross-origin requests from the Control UI.
#
# Usage: ./tests/test-openclaw-inject-token.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   OpenClaw inject-token.js Hardening Test    ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# Preconditions
if ! command -v node >/dev/null 2>&1; then
    skip "node not available — cannot exercise inject-token.js"
    echo ""
    echo "Result: 0 passed, 0 failed (skipped: node missing)"
    exit 0
fi
if ! command -v jq >/dev/null 2>&1; then
    skip "jq not available — cannot inspect merged config"
    echo ""
    echo "Result: 0 passed, 0 failed (skipped: jq missing)"
    exit 0
fi

INJECT_SCRIPT="$ROOT_DIR/config/openclaw/inject-token.js"
SOURCE_CONFIG="$ROOT_DIR/config/openclaw/openclaw.json"

if [[ ! -f "$INJECT_SCRIPT" ]]; then
    fail "inject-token.js not found at $INJECT_SCRIPT"
    exit 1
fi
if [[ ! -f "$SOURCE_CONFIG" ]]; then
    fail "source config openclaw.json not found at $SOURCE_CONFIG"
    exit 1
fi

# Sandbox: HOME points at a tempdir so Part 1 can write ~/.openclaw/openclaw.json
# without touching the developer's real home. Part 3 writes to the hardcoded
# /tmp/openclaw-config.json — we treat that as our test artifact.
TEST_HOME="$(mktemp -d -t ods-openclaw-XXXXXXXX)"
mkdir -p "$TEST_HOME/.openclaw"
MERGED_PATH="/tmp/openclaw-config.json"
TEST_UI_DIR="$TEST_HOME/control-ui"
TEST_HTML="$TEST_UI_DIR/index.html"
TEST_JS="$TEST_UI_DIR/auto-token.js"
mkdir -p "$TEST_UI_DIR"

cleanup() {
    rm -rf "$TEST_HOME"
    rm -f "$MERGED_PATH"
}
trap cleanup EXIT

run_inject() {
    local bind_address="${1:-127.0.0.1}"
    local lemonade_model="${2:-}"
    local gguf_file="${3:-}"
    local ollama_url="${4:-}"
    HOME="$TEST_HOME" \
    OPENCLAW_GATEWAY_TOKEN="test-token-abc123" \
    OPENCLAW_EXTERNAL_PORT="7860" \
    OPENCLAW_CONFIG="$SOURCE_CONFIG" \
    OPENCLAW_CONTROL_UI_HTML="$TEST_HTML" \
    OPENCLAW_AUTO_TOKEN_JS="$TEST_JS" \
    BIND_ADDRESS="$bind_address" \
    LLM_MODEL="test-model" \
    GGUF_FILE="$gguf_file" \
    LEMONADE_MODEL="$lemonade_model" \
    OLLAMA_URL="$ollama_url" \
    OPENCLAW_LLM_URL="" \
    LITELLM_KEY="" \
        node "$INJECT_SCRIPT" >/dev/null 2>&1
}

write_test_html() {
    printf '%s\n' '<!doctype html><html><head></head><body></body></html>' >"$TEST_HTML"
    rm -f "$TEST_JS"
}

write_test_html
if ! run_inject; then
    fail "inject-token.js exited non-zero"
    exit 1
fi
pass "inject-token.js ran without error"

if [[ ! -f "$MERGED_PATH" ]]; then
    fail "merged config $MERGED_PATH was not created"
    exit 1
fi
pass "merged config written to $MERGED_PATH"

# ── Assertion 1: dangerous Host-header fallback is GONE ─────────────────────
if jq -e '.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback' "$MERGED_PATH" >/dev/null 2>&1; then
    fail "dangerouslyAllowHostHeaderOriginFallback is present in merged config (should be removed)"
else
    pass "dangerouslyAllowHostHeaderOriginFallback absent from merged config"
fi

# ── Assertion 2: device auth ENABLED by default (#1270 security guard) ──────
# With OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH unset, inject-token.js must NOT
# disable device auth. The flag must be false or absent in BOTH the merged
# config (Part 3) and the ~/.openclaw home config (Part 1).
HOME_CONFIG="$TEST_HOME/.openclaw/openclaw.json"
merged_da="$(jq -r '.gateway.controlUi.dangerouslyDisableDeviceAuth // "ABSENT"' "$MERGED_PATH")"
if [[ "$merged_da" == "false" || "$merged_da" == "ABSENT" ]]; then
    pass "merged config: device auth enabled by default (dangerouslyDisableDeviceAuth=$merged_da)"
else
    fail "merged config: device auth must default ON — dangerouslyDisableDeviceAuth=$merged_da (expected false/absent)"
fi
if [[ -f "$HOME_CONFIG" ]]; then
    home_da="$(jq -r '.gateway.controlUi.dangerouslyDisableDeviceAuth // "ABSENT"' "$HOME_CONFIG")"
    if [[ "$home_da" == "false" || "$home_da" == "ABSENT" ]]; then
        pass "home config: device auth enabled by default (dangerouslyDisableDeviceAuth=$home_da)"
    else
        fail "home config: device auth must default ON — dangerouslyDisableDeviceAuth=$home_da (expected false/absent)"
    fi
else
    fail "home config ~/.openclaw/openclaw.json not written (Part 1)"
fi

# ── Assertion 2b: explicit opt-in disables device auth AND warns ───────────
# OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH=true must (a) set the flag true in
# both configs and (b) emit a loud startup warning on stderr.
OPTIN_HOME="$(mktemp -d -t ods-openclaw-optin-XXXXXXXX)"
mkdir -p "$OPTIN_HOME/.openclaw"
printf '%s\n' '<!doctype html><html><head></head><body></body></html>' >"$OPTIN_HOME/index.html"
rm -f "$MERGED_PATH"
optin_stderr="$(
    HOME="$OPTIN_HOME" \
    OPENCLAW_GATEWAY_TOKEN="test-token-abc123" \
    OPENCLAW_EXTERNAL_PORT="7860" \
    OPENCLAW_CONFIG="$SOURCE_CONFIG" \
    OPENCLAW_CONTROL_UI_HTML="$OPTIN_HOME/index.html" \
    OPENCLAW_AUTO_TOKEN_JS="$OPTIN_HOME/auto-token.js" \
    BIND_ADDRESS="127.0.0.1" \
    LLM_MODEL="test-model" \
    GGUF_FILE="" \
    OLLAMA_URL="" \
    OPENCLAW_LLM_URL="" \
    LITELLM_KEY="" \
    OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH="true" \
        node "$INJECT_SCRIPT" 2>&1 >/dev/null
)"
OPTIN_HOME_CONFIG="$OPTIN_HOME/.openclaw/openclaw.json"
if [[ "$(jq -r '.gateway.controlUi.dangerouslyDisableDeviceAuth' "$MERGED_PATH")" == "true" ]]; then
    pass "opt-in: merged config disables device auth when env=true"
else
    fail "opt-in: merged config should disable device auth when OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH=true"
fi
if [[ -f "$OPTIN_HOME_CONFIG" ]] && [[ "$(jq -r '.gateway.controlUi.dangerouslyDisableDeviceAuth' "$OPTIN_HOME_CONFIG")" == "true" ]]; then
    pass "opt-in: home config disables device auth when env=true"
else
    fail "opt-in: home config should disable device auth when OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH=true"
fi
if grep -Eqi 'SECURITY WARNING.*DEVICE AUTH DISABLED' <<<"$optin_stderr"; then
    pass "opt-in: loud device-auth-disabled warning emitted"
else
    fail "opt-in: expected a loud device-auth-disabled startup warning on stderr"
fi
rm -rf "$OPTIN_HOME"
rm -f "$MERGED_PATH"
# Restore the default (no-opt-in) merged + home config for later assertions.
write_test_html
run_inject

# ── Assertion 3: allowInsecureAuth still TRUE (HTTP-only deployment guard) ──
if [[ "$(jq -r '.gateway.controlUi.allowInsecureAuth' "$MERGED_PATH")" == "true" ]]; then
    pass "allowInsecureAuth=true preserved (HTTP deployment intact)"
else
    fail "allowInsecureAuth must remain true — HTTP-only stack would refuse to connect"
fi

# ── Assertion 4: allowedOrigins populated with the expected localhost entries ─
ORIGINS_COUNT="$(jq '.gateway.controlUi.allowedOrigins | length' "$MERGED_PATH")"
if [[ "$ORIGINS_COUNT" -ge 2 ]]; then
    pass "allowedOrigins populated ($ORIGINS_COUNT entries)"
else
    fail "allowedOrigins should contain at least the 2 localhost entries (got $ORIGINS_COUNT)"
fi

for origin in "http://localhost:7860" "http://127.0.0.1:7860"; do
    if jq -e --arg o "$origin" '.gateway.controlUi.allowedOrigins | index($o)' "$MERGED_PATH" >/dev/null 2>&1; then
        pass "allowedOrigins contains $origin"
    else
        fail "allowedOrigins missing expected entry: $origin"
    fi
done

# ── Assertion 5: gateway.auth.mode === 'token' (auth-flow regression guard) ──
if [[ "$(jq -r '.gateway.mode' "$MERGED_PATH")" == "local" ]]; then
    pass "gateway.mode=local preserved"
else
    fail "gateway.mode must be 'local' (required by OpenClaw v2026.3.8+)"
fi

# Exact Lemonade IDs must win over the pre-10.7 extra.<GGUF_FILE> fallback.
rm -f "$MERGED_PATH"
write_test_html
run_inject "127.0.0.1" "Modern-Model" "Modern-Model.gguf" "http://host.docker.internal:8080"
if [[ "$(jq -r '.models.providers["local-llama"].models[0].id' "$MERGED_PATH")" == "Modern-Model" ]] \
   && [[ "$(jq -r '.models.providers["local-llama"].baseUrl' "$MERGED_PATH")" == "http://host.docker.internal:8080/api/v1" ]] \
   && [[ "$(jq -r '.agents.defaults.model.primary' "$MERGED_PATH")" == "local-llama/Modern-Model" ]]; then
    pass "Windows Lemonade model ID and /api/v1 route reach OpenClaw"
else
    fail "OpenClaw must use LEMONADE_MODEL and the Windows Lemonade /api/v1 route"
fi

# With no persisted ID, retain the current Linux/legacy naming contract.
rm -f "$MERGED_PATH"
write_test_html
run_inject "127.0.0.1" "" "Legacy-Model.gguf" "http://llama-server:8080/api"
if [[ "$(jq -r '.models.providers["local-llama"].models[0].id' "$MERGED_PATH")" == "extra.Legacy-Model.gguf" ]]; then
    pass "OpenClaw retains extra.<GGUF_FILE> fallback without a persisted ID"
else
    fail "OpenClaw legacy Lemonade fallback changed unexpectedly"
fi

# Restore the default merged config for the remaining assertions.
rm -f "$MERGED_PATH"
write_test_html
run_inject

# Note: gateway.auth is patched into ~/.openclaw/openclaw.json (Part 1), not
# the merged config (Part 3). Verify the Part 1 output instead.
HOME_CONFIG="$TEST_HOME/.openclaw/openclaw.json"
if [[ -f "$HOME_CONFIG" ]]; then
    if [[ "$(jq -r '.gateway.auth.mode' "$HOME_CONFIG")" == "token" ]]; then
        pass "gateway.auth.mode=token written to ~/.openclaw/openclaw.json"
    else
        fail "gateway.auth.mode must be 'token' in ~/.openclaw/openclaw.json"
    fi
    if [[ "$(jq -r '.gateway.auth.token' "$HOME_CONFIG")" == "test-token-abc123" ]]; then
        pass "gateway.auth.token populated from OPENCLAW_GATEWAY_TOKEN"
    else
        fail "gateway.auth.token did not pick up OPENCLAW_GATEWAY_TOKEN"
    fi
    # Persistent-volume defang: home config (Part 1 output) must NOT carry
    # dangerouslyAllowHostHeaderOriginFallback. The home config lives in a
    # named Docker volume, so a residual flag from a pre-PR install would
    # otherwise persist across upgrades.
    if jq -e '.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback // empty' "$HOME_CONFIG" >/dev/null 2>&1; then
        fail "home config STILL contains dangerouslyAllowHostHeaderOriginFallback (Part 1 not defanging)"
    else
        pass "home config lacks dangerouslyAllowHostHeaderOriginFallback (Part 1 defanged)"
    fi
else
    fail "Part 1 output ~/.openclaw/openclaw.json not written"
fi

# ── Assertion 6: localhost-only installs auto-bootstrap the Control UI token ─
if [[ -f "$TEST_JS" ]]; then
    if grep -Fq 'test-token-abc123' "$TEST_JS" \
        && grep -Fq 'openclaw.control.settings.v1' "$TEST_JS" \
        && grep -Fq 'localStorage.setItem' "$TEST_JS"; then
        pass "localhost auto-token.js writes token into Control UI settings"
    else
        fail "localhost auto-token.js should bootstrap token and gateway URL"
    fi
else
    fail "localhost auto-token.js was not written"
fi

if grep -Fq '<script src="./auto-token.js"></script>' "$TEST_HTML"; then
    pass "Control UI HTML includes external auto-token script"
else
    fail "Control UI HTML missing external auto-token script"
fi

# ── Assertion 7: LAN-bound installs keep token out of unauthenticated asset ──
write_test_html
if ! run_inject "0.0.0.0"; then
    fail "LAN-bound inject-token.js exited non-zero"
else
    if [[ -f "$TEST_JS" ]] \
        && ! grep -Fq 'test-token-abc123' "$TEST_JS" \
        && grep -Fq 'LAN-bound' "$TEST_JS"; then
        pass "LAN-bound auto-token.js remains a token-free placeholder"
    else
        fail "LAN-bound auto-token.js must not contain the gateway token"
    fi
fi

# ── Upgrade scenario: pre-seed bad flag, confirm Part 1 strips it ───────────
# Simulate an upgrade from a pre-PR install where ~/.openclaw/openclaw.json on
# the named Docker volume already contains dangerouslyAllowHostHeaderOriginFallback.
# Re-running inject-token.js must remove that flag.
echo ""
cat >"$TEST_HOME/.openclaw/openclaw.json" <<'JSON'
{
  "gateway": {
    "mode": "local",
    "controlUi": {
      "allowInsecureAuth": true,
      "dangerouslyDisableDeviceAuth": true,
      "dangerouslyAllowHostHeaderOriginFallback": true
    }
  }
}
JSON

if ! run_inject; then
    fail "upgrade scenario: inject-token.js exited non-zero on re-run"
else
    pass "upgrade scenario: inject-token.js ran cleanly against pre-seeded home config"
    if jq -e '.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback // empty' "$HOME_CONFIG" >/dev/null 2>&1; then
        fail "upgrade scenario: pre-existing bad flag was NOT defanged (carry-over still present)"
    else
        pass "upgrade scenario: pre-existing bad flag defanged on re-run"
    fi
fi

# ── HOST_LAN_IP: positive case populates LAN origins (Part 1 + Part 3) ─────
# When BIND_ADDRESS=0.0.0.0 the installer exports HOST_LAN_IP so inject-token.js
# can append the host's LAN address to allowedOrigins. Both the home config
# (Part 1) and the merged config (Part 3) must include http+https variants.
echo ""
rm -f "$MERGED_PATH" "$HOME_CONFIG"
HOME="$TEST_HOME" \
HOST_LAN_IP="192.168.1.50" \
OPENCLAW_GATEWAY_TOKEN="test-token-abc123" \
OPENCLAW_EXTERNAL_PORT="7860" \
OPENCLAW_CONFIG="$SOURCE_CONFIG" \
LLM_MODEL="test-model" \
GGUF_FILE="" \
OLLAMA_URL="" \
OPENCLAW_LLM_URL="" \
LITELLM_KEY="" \
    node "$INJECT_SCRIPT" >/dev/null 2>&1

for origin in "http://192.168.1.50:7860" "https://192.168.1.50:7860"; do
    if jq -e --arg o "$origin" '.gateway.controlUi.allowedOrigins | index($o)' "$MERGED_PATH" >/dev/null 2>&1; then
        pass "merged config allowedOrigins contains $origin"
    else
        fail "merged config allowedOrigins missing expected entry: $origin"
    fi
    if jq -e --arg o "$origin" '.gateway.controlUi.allowedOrigins | index($o)' "$HOME_CONFIG" >/dev/null 2>&1; then
        pass "home config allowedOrigins contains $origin"
    else
        fail "home config allowedOrigins missing expected entry: $origin"
    fi
done

# ── HOST_LAN_IP: empty/unset must NOT inject http:/// or https:/// ──────────
# Empty-string guard regression: a bare "if (hostLanIp)" check would let an
# unset/empty value through and produce malformed origin URLs.
for empty_case in "empty" "unset"; do
    rm -f "$MERGED_PATH" "$HOME_CONFIG"
    if [[ "$empty_case" == "empty" ]]; then
        HOME="$TEST_HOME" \
        HOST_LAN_IP="" \
        OPENCLAW_GATEWAY_TOKEN="test-token-abc123" \
        OPENCLAW_EXTERNAL_PORT="7860" \
        OPENCLAW_CONFIG="$SOURCE_CONFIG" \
        LLM_MODEL="test-model" \
        GGUF_FILE="" \
        OLLAMA_URL="" \
        OPENCLAW_LLM_URL="" \
        LITELLM_KEY="" \
            node "$INJECT_SCRIPT" >/dev/null 2>&1
    else
        # Truly unset: do not export HOST_LAN_IP at all.
        HOME="$TEST_HOME" \
        OPENCLAW_GATEWAY_TOKEN="test-token-abc123" \
        OPENCLAW_EXTERNAL_PORT="7860" \
        OPENCLAW_CONFIG="$SOURCE_CONFIG" \
        LLM_MODEL="test-model" \
        GGUF_FILE="" \
        OLLAMA_URL="" \
        OPENCLAW_LLM_URL="" \
        LITELLM_KEY="" \
            node "$INJECT_SCRIPT" >/dev/null 2>&1
    fi

    for cfg in "$MERGED_PATH" "$HOME_CONFIG"; do
        if jq -e '.gateway.controlUi.allowedOrigins | map(test("^https?:///")) | any' "$cfg" >/dev/null 2>&1; then
            fail "HOST_LAN_IP $empty_case case: $cfg has malformed http:/// or https:/// entry"
        else
            pass "HOST_LAN_IP $empty_case case: $cfg has no malformed http:/// or https:/// entry"
        fi
    done
done

# ── Negative test: verify the test would actually catch a regression ────────
# Re-run inject-token.js against a fixture that re-introduces the bad flag,
# and confirm assertion 1 would have failed.
FIXTURE_DIR="$(mktemp -d -t ods-openclaw-fix-XXXXXXXX)"
BAD_CONFIG="$FIXTURE_DIR/openclaw.json"
cat >"$BAD_CONFIG" <<'JSON'
{
  "agents": { "defaults": { "model": { "primary": "local-llama/m" }, "models": { "local-llama/m": {} }, "subagents": { "model": "local-llama/m", "maxConcurrent": 20 } } },
  "models": { "providers": { "local-llama": { "baseUrl": "http://x/v1", "apiKey": "n", "models": [ { "id": "m", "name": "m", "contextWindow": 1 } ] } } },
  "gateway": {
    "mode": "local",
    "controlUi": {
      "allowInsecureAuth": true,
      "dangerouslyDisableDeviceAuth": true,
      "dangerouslyAllowHostHeaderOriginFallback": true
    }
  }
}
JSON

# Patch a temporary copy of inject-token.js that re-injects the flag — this
# simulates the fix being reverted. If our assertion 1 still passes against
# this script, the test has no teeth. We must also strip the Part 3 `delete`
# defang (otherwise it silently wipes the re-injected flag), so we replace
# the delete line with the bad setter.
BAD_SCRIPT="$FIXTURE_DIR/inject-token.js"
cp "$INJECT_SCRIPT" "$BAD_SCRIPT"
# (sed -i portability: pass empty string on macOS / nothing on GNU).
if sed --version >/dev/null 2>&1; then
    sed -i 's|delete primary.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback;.*|primary.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback = true;|' "$BAD_SCRIPT"
else
    sed -i '' 's|delete primary.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback;.*|primary.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback = true;|' "$BAD_SCRIPT"
fi

rm -f "$MERGED_PATH"
HOME="$TEST_HOME" \
OPENCLAW_GATEWAY_TOKEN="test-token-abc123" \
OPENCLAW_EXTERNAL_PORT="7860" \
OPENCLAW_CONFIG="$BAD_CONFIG" \
LLM_MODEL="test-model" \
    node "$BAD_SCRIPT" >/dev/null 2>&1 || true

if [[ -f "$MERGED_PATH" ]] && jq -e '.gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback' "$MERGED_PATH" >/dev/null 2>&1; then
    pass "negative test: regressed script DOES re-introduce the flag (test has teeth)"
else
    fail "negative test failed — could not simulate regression; assertion 1 may be toothless"
fi

rm -rf "$FIXTURE_DIR"

echo ""
echo "─────────────────────────────────────────────────"
echo -e "Result: ${GREEN}${PASSED} passed${NC}, ${RED}${FAILED} failed${NC}"
echo "─────────────────────────────────────────────────"

if [[ "$FAILED" -gt 0 ]]; then
    exit 1
fi
exit 0
