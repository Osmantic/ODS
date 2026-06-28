#!/usr/bin/env bash
# Regression guard for #519: ensure LiteLLM auth is enforced on AMD installs
# and that open-webui no longer ships a hardcoded "no-key" credential.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "[guard] LiteLLM AMD overlay must not unset LITELLM_MASTER_KEY"
if grep -qE '^[[:space:]]*unset[[:space:]]+LITELLM_MASTER_KEY' \
        extensions/services/litellm/compose.amd.yaml; then
    fail "compose.amd.yaml: 'unset LITELLM_MASTER_KEY' present — auth bypass regression"
else
    pass "compose.amd.yaml: no 'unset LITELLM_MASTER_KEY'"
fi

echo "[guard] open-webui must not hardcode OPENAI_API_KEY=no-key on AMD"
if grep -qE '^[[:space:]]*-[[:space:]]*OPENAI_API_KEY=no-key' docker-compose.amd.yml; then
    fail "docker-compose.amd.yml: hardcoded OPENAI_API_KEY=no-key — open-webui will fail auth"
else
    pass "docker-compose.amd.yml: no hardcoded OPENAI_API_KEY=no-key"
fi

# Bundled extension fixes (#519 downstream consumers): when LiteLLM auth is
# enforced on AMD-local, every extension that routes through LLM_API_URL must
# present LITELLM_KEY by default. Use a fallback chain so user-supplied keys
# still win, and so non-AMD/non-LiteLLM installs are unchanged.
echo "[guard] perplexica must use LITELLM_KEY fallback chain"
if grep -qF 'OPENAI_API_KEY=${LITELLM_KEY:-${OPENAI_API_KEY:-no-key}}' \
        extensions/services/perplexica/compose.yaml; then
    pass "perplexica: OPENAI_API_KEY uses LITELLM_KEY fallback chain"
else
    fail "perplexica: OPENAI_API_KEY missing LITELLM_KEY fallback — would 401 on AMD-local"
fi

echo "[guard] privacy-shield must use LITELLM_KEY fallback chain"
if grep -qF 'TARGET_API_KEY=${LITELLM_KEY:-${TARGET_API_KEY:-not-needed}}' \
        extensions/services/privacy-shield/compose.yaml; then
    pass "privacy-shield: TARGET_API_KEY uses LITELLM_KEY fallback chain"
else
    fail "privacy-shield: TARGET_API_KEY missing LITELLM_KEY fallback — would 401 on AMD-local"
fi

echo
echo "Passed: $PASS  Failed: $FAIL"
[[ $FAIL -eq 0 ]]
