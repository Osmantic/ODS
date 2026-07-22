#!/usr/bin/env bash
# ============================================================================
# ODS installer context parity tests
# ============================================================================
# Locks the model-context defaults that let Hermes work during first-run
# bootstrap and after full model upgrade across Linux, macOS, and Windows.
# ============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PASS=0

pass() {
    echo "  PASS: $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "  FAIL: $1" >&2
    exit 1
}

assert_grep() {
    local file="$1"
    local pattern="$2"
    local label="$3"

    [[ -f "$file" ]] || fail "missing $file"
    if grep -Eq -- "$pattern" "$file"; then
        pass "$label"
    else
        fail "$label"
    fi
}

assert_not_grep() {
    local file="$1"
    local pattern="$2"
    local label="$3"

    [[ -f "$file" ]] || fail "missing $file"
    if grep -Eq -- "$pattern" "$file"; then
        fail "$label"
    else
        pass "$label"
    fi
}

echo "=== Installer context parity ==="

echo ""
echo "Bootstrap context floor:"
assert_grep "installers/lib/bootstrap-model.sh" '^BOOTSTRAP_MAX_CONTEXT=65536$' \
    "Linux bootstrap context is 64K"
assert_grep "installers/macos/lib/tier-map.sh" '^BOOTSTRAP_MAX_CONTEXT=65536$' \
    "macOS bootstrap context is 64K"
assert_grep "installers/windows/lib/tier-map.ps1" 'BOOTSTRAP_MAX_CONTEXT[[:space:]]*=[[:space:]]*65536' \
    "Windows bootstrap context is 64K"

echo ""
echo "Hermes target context:"
assert_grep "installers/phases/03-features.sh" 'HERMES_CONTEXT_SIZE=.*65536' \
    "Linux Hermes target context floor is 64K"
assert_grep "installers/macos/install-macos.sh" '^HERMES_CONTEXT_SIZE=65536$' \
    "macOS Hermes target context floor is 64K"
assert_grep "installers/windows/phases/03-features.ps1" 'hermesContextSize[[:space:]]*=[[:space:]]*65536' \
    "Windows Hermes target context floor is 64K"

echo ""
echo ".env context parity:"
assert_grep "installers/phases/06-directories.sh" '^MAX_CONTEXT=\$\{MAX_CONTEXT\}$' \
    "Linux .env generator writes MAX_CONTEXT"
assert_grep "installers/phases/06-directories.sh" '^CTX_SIZE=\$\{MAX_CONTEXT\}$' \
    "Linux .env generator writes CTX_SIZE from MAX_CONTEXT"
assert_grep "installers/macos/lib/env-generator.sh" '^MAX_CONTEXT=\$\{MAX_CONTEXT\}$' \
    "macOS .env generator writes MAX_CONTEXT"
assert_grep "installers/macos/lib/env-generator.sh" '^CTX_SIZE=\$\{MAX_CONTEXT\}$' \
    "macOS .env generator writes CTX_SIZE from MAX_CONTEXT"
assert_grep "installers/macos/lib/env-generator.sh" '^LLM_BACKEND=llama-server$' \
    "macOS .env generator declares native llama-server backend"
assert_grep "installers/windows/lib/env-generator.ps1" '^MAX_CONTEXT=\$\(\$TierConfig\.MaxContext\)' \
    "Windows .env generator writes MAX_CONTEXT"
assert_grep "installers/windows/lib/env-generator.ps1" '^CTX_SIZE=\$\(\$TierConfig\.MaxContext\)' \
    "Windows .env generator writes CTX_SIZE from MaxContext"

echo ""
echo "Bootstrap context rewrites:"
assert_grep "installers/phases/11-services.sh" '"MAX_CONTEXT=\$MAX_CONTEXT"[[:space:]]+"CTX_SIZE=\$MAX_CONTEXT"' \
    "Linux bootstrap rewrite updates MAX_CONTEXT and CTX_SIZE together"
assert_grep "installers/macos/install-macos.sh" 'MAX_CONTEXT=.*MAX_CONTEXT' \
    "macOS bootstrap rewrite updates MAX_CONTEXT"
assert_grep "installers/macos/install-macos.sh" 'CTX_SIZE=.*MAX_CONTEXT' \
    "macOS bootstrap rewrite updates CTX_SIZE"
assert_grep "installers/windows/install-windows.ps1" 'MAX_CONTEXT=\$\(\$tierConfig\.MaxContext\)' \
    "Windows bootstrap rewrite updates MAX_CONTEXT"
assert_grep "installers/windows/install-windows.ps1" 'CTX_SIZE=\$\(\$tierConfig\.MaxContext\)' \
    "Windows bootstrap rewrite updates CTX_SIZE"

echo ""
echo "Hermes config patch paths:"
assert_grep "installers/phases/11-services.sh" '_hermes_context="\$\{MAX_CONTEXT:-65536\}"' \
    "Linux Hermes patcher uses selected context with 64K fallback"
assert_grep "installers/phases/11-services.sh" '--context-length "\$_hermes_context"' \
    "Linux Hermes patcher receives context length"
assert_grep "installers/phases/11-services.sh" '_hermes_switchboard_mode=.*ODS_MODEL_SWITCHBOARD' \
    "Linux Hermes patcher reads switchboard mode"
assert_grep "installers/phases/11-services.sh" '_hermes_model="ods/current"' \
    "Linux Hermes patcher uses the stable switchboard model alias"
assert_grep "installers/phases/11-services.sh" '_hermes_base_url=.*http://litellm:4000/v1' \
    "Linux Hermes patcher routes switchboard mode through LiteLLM"
assert_grep "installers/macos/install-macos.sh" '--context-length "\$MAX_CONTEXT"' \
    "macOS Hermes patcher receives context length"
assert_grep "installers/macos/ods-macos.sh" 'ENV_CTX_SIZE:-65536' \
    "macOS native llama restart defaults to 64K context"
assert_grep "installers/phases/07-devtools.sh" '"context": \$\{MAX_CONTEXT:-65536\}' \
    "Linux OpenCode config defaults to 64K context"
assert_grep "installers/phases/07-devtools.sh" 'ODS_MODEL_SWITCHBOARD' \
    "Linux OpenCode config reads switchboard mode"
assert_grep "installers/phases/07-devtools.sh" '_opencode_model_id="ods/current"' \
    "Linux OpenCode config uses stable switchboard alias"
assert_grep "installers/phases/07-devtools.sh" 'OpenCode config updated \(model, API key, and URL refreshed\)' \
    "Linux OpenCode reinstall migrates stale model route"
assert_grep "installers/macos/install-macos.sh" '_opencode_switchboard_mode=.*ODS_MODEL_SWITCHBOARD' \
    "macOS OpenCode config reads switchboard mode"
assert_grep "installers/macos/install-macos.sh" '_opencode_model="ods/current"' \
    "macOS OpenCode config uses stable switchboard alias"
assert_grep "installers/macos/lib/env-generator.sh" 'ODS_MODEL_SWITCHBOARD=\$\{switchboard_mode\}' \
    "macOS .env generation persists switchboard mode"
assert_grep "installers/macos/lib/env-generator.sh" 'OPEN_WEBUI_LLM_BASE_URL=\$\{open_webui_llm_base_url\}' \
    "macOS .env generation carries the Open WebUI switchboard route"
assert_grep "installers/macos/docker-compose.macos.yml" 'OPENAI_API_BASE_URL: "\$\{OPEN_WEBUI_LLM_BASE_URL:-http://\$\{ODS_MACOS_HOST_GATEWAY:-host\.docker\.internal\}:8080/v1\}"' \
    "macOS Open WebUI compose route honors switchboard override"
assert_grep "installers/macos/docker-compose.macos.yml" 'OPENAI_API_KEY: "\$\{OPEN_WEBUI_LLM_API_KEY:-\}"' \
    "macOS Open WebUI compose route carries switchboard API key"
assert_grep "installers/macos/install-macos.sh" 'render-runtime-configs\.py' \
    "macOS installer renders model-router runtime configs"
assert_grep "installers/macos/install-macos.sh" 'PERPLEXICA_MODEL="ods/current"' \
    "macOS Perplexica config uses stable switchboard alias"
assert_grep "installers/macos/install-macos.sh" 'PERPLEXICA_BASE_URL="http://litellm:4000"' \
    "macOS Perplexica config routes switchboard mode through LiteLLM"
assert_grep "installers/phases/12-health.sh" 'ODS_MODEL_SWITCHBOARD' \
    "Linux Perplexica config reads switchboard mode"
assert_grep "installers/phases/12-health.sh" 'PERPLEXICA_MODEL="ods/current"' \
    "Linux Perplexica config uses stable switchboard alias"
assert_grep "installers/phases/12-health.sh" 'PERPLEXICA_LLM_BASE_URL="http://litellm:4000/v1"' \
    "Linux Perplexica config routes switchboard mode through LiteLLM"
assert_grep "installers/windows/install-windows.ps1" 'ODS_MODEL_SWITCHBOARD' \
    "Windows Perplexica config reads switchboard mode"
assert_grep "installers/windows/install-windows.ps1" '\$perplexicaModel = "ods/current"' \
    "Windows Perplexica config uses stable switchboard alias"
assert_grep "installers/windows/install-windows.ps1" '\$perplexicaBaseUrl = "http://litellm:4000/v1"' \
    "Windows Perplexica config routes switchboard mode through LiteLLM"
assert_grep "scripts/repair/repair-perplexica.sh" 'ODS_MODEL_SWITCHBOARD' \
    "Perplexica repair reads switchboard mode"
assert_grep "scripts/repair/repair-perplexica.sh" 'PERPLEXICA_MODEL:=ods/current' \
    "Perplexica repair uses stable switchboard alias"
assert_not_grep "installers/macos/install-macos.sh" '\$LOG_FILE' \
    "macOS installer uses ODS_LOG_FILE, not undefined LOG_FILE"
assert_grep "installers/windows/install-windows.ps1" 'Update-HermesConfigFile.*ContextLength \(\[int\]\$tierConfig\.MaxContext\)' \
    "Windows Hermes patcher receives context length"

echo ""
echo "Hermes config patcher behavior:"
python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
[[ -n "$python_cmd" ]] || fail "python is required to test Hermes config patcher"

tmp_hermes="$(mktemp)"
tmp_hermes_custom="$(mktemp)"
trap 'rm -f "$tmp_hermes" "$tmp_hermes_custom"' EXIT
cat > "$tmp_hermes" <<'HERMES_EOF'
model:
  default: "old-model"
  provider: "custom"
  base_url: "http://old.example/v1"
  context_length: 8192

compression:
  threshold: 10000
  target_ratio: 0.5
terminal:
  backend: "local"
HERMES_EOF

"$python_cmd" scripts/patch-hermes-config.py "$tmp_hermes" \
    --model "Qwen3.5-2B-Q4_K_M.gguf" \
    --base-url "http://llama-server:8080/v1" \
    --context-length 65536 >/dev/null

grep -q 'default: "Qwen3.5-2B-Q4_K_M.gguf"' "$tmp_hermes" \
    || fail "Hermes patcher updates model.default"
pass "Hermes patcher updates model.default"
grep -q 'base_url: "http://llama-server:8080/v1"' "$tmp_hermes" \
    || fail "Hermes patcher updates base_url"
pass "Hermes patcher updates base_url"
grep -q '^  context_length: 65536$' "$tmp_hermes" \
    || fail "Hermes patcher updates model.context_length"
pass "Hermes patcher updates model.context_length"
grep -q '^    request_timeout_seconds: 180$' "$tmp_hermes" \
    || fail "Hermes patcher writes local provider request timeout"
pass "Hermes patcher writes local provider request timeout"

tmp_hermes_windows="$(mktemp)"
cat > "$tmp_hermes_windows" <<'HERMES_WINDOWS_EOF'
model:
  default: "old-model"
providers:
  custom:
    request_timeout_seconds: 180
HERMES_WINDOWS_EOF

"$python_cmd" scripts/patch-hermes-config.py "$tmp_hermes_windows" \
    --request-timeout-seconds 900 >/dev/null

grep -q '^    request_timeout_seconds: 900$' "$tmp_hermes_windows" \
    || fail "Hermes patcher upgrades ODS default provider timeout when requested"
pass "Hermes patcher upgrades ODS default provider timeout when requested"
grep -q '^    context_length: 65536$' "$tmp_hermes" \
    || fail "Hermes patcher writes auxiliary compression context"
pass "Hermes patcher writes auxiliary compression context"
grep -q '^  threshold: 0.75$' "$tmp_hermes" \
    || fail "Hermes patcher normalizes compression threshold"
pass "Hermes patcher normalizes compression threshold"
grep -q '^  target_ratio: 0.50$' "$tmp_hermes" \
    || fail "Hermes patcher normalizes compression target_ratio"
pass "Hermes patcher normalizes compression target_ratio"
grep -q '^  protect_last_n: 40$' "$tmp_hermes" \
    || fail "Hermes patcher writes protect_last_n"
pass "Hermes patcher writes protect_last_n"
grep -q '^      bridge_port: 3010$' "$tmp_hermes" \
    || fail "Hermes patcher writes WhatsApp bridge port away from Open WebUI"
pass "Hermes patcher writes WhatsApp bridge port away from Open WebUI"

cat > "$tmp_hermes_custom" <<'HERMES_CUSTOM_EOF'
model:
  default: "old-model"
platforms:
  whatsapp:
    enabled: true
    extra:
      bridge_port: 3456
providers:
  custom:
    request_timeout_seconds: 360
compression:
  threshold: 0.75
HERMES_CUSTOM_EOF

"$python_cmd" scripts/patch-hermes-config.py "$tmp_hermes_custom" \
    --request-timeout-seconds 900 >/dev/null

grep -q '^    enabled: true$' "$tmp_hermes_custom" \
    || fail "Hermes patcher preserves user-enabled WhatsApp"
pass "Hermes patcher preserves user-enabled WhatsApp"
grep -q '^      bridge_port: 3456$' "$tmp_hermes_custom" \
    || fail "Hermes patcher preserves custom WhatsApp bridge port"
pass "Hermes patcher preserves custom WhatsApp bridge port"
grep -q '^    request_timeout_seconds: 360$' "$tmp_hermes_custom" \
    || fail "Hermes patcher preserves custom provider request timeout"
pass "Hermes patcher preserves custom provider request timeout"

echo ""
echo "Results: $PASS passed"
