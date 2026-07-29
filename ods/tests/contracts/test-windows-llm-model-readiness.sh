#!/usr/bin/env bash
# Windows LLM model-readiness contract.
# Guards the "registered but missing GGUF" false-green: /v1/models lists a model
# whose backing file is absent, so completions 500 while install reports healthy.
# The Windows installer must prove (a) the file exists and (b) a minimal completion
# succeeds before it may report healthy.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

LIB="installers/windows/lib/llm-endpoint.ps1"
INSTALLER="installers/windows/install-windows.ps1"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# 1. The readiness function exists and gates on BOTH file + completion.
# ---------------------------------------------------------------------------
echo "[contract] readiness function exists and checks file + completion"
if grep -q 'function Test-WindowsLlmModelReadiness' "$LIB"; then
    pass "Test-WindowsLlmModelReadiness defined in $LIB"
else
    fail "Test-WindowsLlmModelReadiness missing from $LIB"
fi
if grep -q 'Test-Path \$modelPath' "$LIB"; then
    pass "readiness checks the GGUF backing file exists on disk"
else
    fail "readiness must verify the GGUF backing file exists (Test-Path)"
fi
if grep -q 'ChatCompletionsUrl' "$LIB" && grep -q 'CompletionOk' "$LIB"; then
    pass "readiness performs a real completion (ChatCompletionsUrl -> CompletionOk)"
else
    fail "readiness must POST a minimal completion and record CompletionOk"
fi
if grep -Eq '\$result\.Ok = \$true' "$LIB" && grep -q 'FileExists -and \$result.CompletionOk' "$LIB"; then
    pass "Ok requires BOTH file present AND completion success"
else
    fail "Ok must require file present AND completion success"
fi
if grep -q '\$isLemonadeEndpoint' "$LIB" && grep -q 'Endpoint.ContainsKey("Backend")' "$LIB"; then
    pass "readiness derives Lemonade model ids from the resolved endpoint"
else
    fail "readiness must key Lemonade model-id prefixing off the resolved endpoint"
fi
if grep -q 'GpuBackend.ToLowerInvariant() -eq "amd"' "$LIB"; then
    fail "readiness must not prefix every AMD backend with extra. (llama-server fallback uses plain GGUF id)"
else
    pass "AMD llama-server fallback is not treated as Lemonade for model ids"
fi

# ---------------------------------------------------------------------------
# 2. The installer wires the gate and fails loud (flips healthy) on failure.
# ---------------------------------------------------------------------------
echo "[contract] installer calls the gate and flips healthy on failure"
if grep -q 'Test-WindowsLlmModelReadiness' "$INSTALLER"; then
    pass "install-windows.ps1 invokes the readiness gate"
else
    fail "install-windows.ps1 must invoke Test-WindowsLlmModelReadiness"
fi
# The gate block must set $allHealthy = $false when not Ok, and healthy = $allHealthy
# must flow into the install summary.
if awk '/Test-WindowsLlmModelReadiness/{f=1} f&&/allHealthy = \$false/{print; exit}' "$INSTALLER" | grep -q 'allHealthy'; then
    pass "readiness failure sets \$allHealthy = \$false"
else
    fail "readiness failure must set \$allHealthy = \$false (fail loud)"
fi
if grep -Eq 'healthy\s*=\s*\$allHealthy' "$INSTALLER"; then
    pass "install summary healthy = \$allHealthy (gate flows to summary)"
else
    fail "install summary must derive healthy from \$allHealthy"
fi
if grep -q 'function Get-WindowsActiveModelSelection' "$INSTALLER" \
    && grep -q 'Get-WindowsODSEnvValue -EnvMap \$EnvMap -Keys @("GGUF_FILE")' "$INSTALLER" \
    && grep -q 'Get-WindowsODSEnvValue -EnvMap \$EnvMap -Keys @("LLM_MODEL")' "$INSTALLER"; then
    pass "installer can refresh active GGUF/LLM model from .env"
else
    fail "installer must refresh active GGUF/LLM model from .env after background swaps"
fi
if awk '/Verifying the LLM can actually serve/{f=1} f&&/-GgufFile/{print; exit}' "$INSTALLER" | grep -q -- '-GgufFile \$activeModel.GgufFile'; then
    pass "readiness verifies the active env GGUF, not the original tier GGUF"
else
    fail "readiness must pass the active env GGUF to Test-WindowsLlmModelReadiness"
fi
if awk '/Configuring Perplexica/{f=1} f&&/Set-PerplexicaConfig/{print; exit}' "$INSTALLER" | grep -q '\$perplexicaModel'; then
    pass "Perplexica configuration uses refreshed active model selection"
else
    fail "Perplexica configuration must use refreshed active model selection"
fi
perplexica_config_block="$(awk '/Configuring Perplexica/{f=1} f{print} f&&/Set-PerplexicaConfig/{exit}' "$INSTALLER")"
if grep -q 'ODS_MODEL_SWITCHBOARD' <<<"$perplexica_config_block" \
    && grep -q '\$perplexicaModel = "ods/current"' <<<"$perplexica_config_block" \
    && grep -q '\$perplexicaBaseUrl = "http://litellm:4000/v1"' <<<"$perplexica_config_block"; then
    pass "Perplexica configuration keeps switchboard installs on the stable alias"
else
    fail "Perplexica switchboard installs must configure ods/current through LiteLLM"
fi
if grep -Eq 'model\s*=\s*\$activeModel\.LlmModel' "$INSTALLER"; then
    pass "summary JSON records the active env model"
else
    fail "summary JSON must report the active env model"
fi

# ---------------------------------------------------------------------------
# 3. Behavioral (pwsh): a registered model whose file is missing fails readiness.
# ---------------------------------------------------------------------------
if command -v pwsh >/dev/null 2>&1; then
    echo "[contract] behavioral: missing backing file => readiness NOT ok"
    OUT="$(pwsh -NoProfile -Command "
        . '$ROOT_DIR/$LIB'
        # Dead endpoint so the completion cannot succeed; nonexistent GGUF file.
        \$ep = @{ ChatCompletionsUrl = 'http://127.0.0.1:1/api/v1/chat/completions' }
        \$r = Test-WindowsLlmModelReadiness -Endpoint \$ep -InstallDir 'C:\\__ods_nope__' \`
                -GgufFile 'Qwen3.5-2B-Q4_K_M.gguf' -TimeoutSec 2
        Write-Output (\"OK=\" + \$r.Ok + \";FILE=\" + \$r.FileExists + \";DETAIL=\" + \$r.Detail)
    " 2>/dev/null || true)"
    if echo "$OUT" | grep -q 'OK=False' && echo "$OUT" | grep -qi 'backing file is missing'; then
        pass "missing file => Ok=False with 'backing file is missing' detail"
    else
        fail "expected Ok=False + missing-file detail, got: $OUT"
    fi
else
    echo "[skip] pwsh not available — behavioral readiness check skipped (source invariants still enforced)"
fi

# ---------------------------------------------------------------------------
echo
echo "Windows LLM model-readiness contract: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
