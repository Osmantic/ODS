#!/usr/bin/env bash
# Windows Strix Halo tier-map contract.
# Guards first-run recovery: SH_LARGE on AMD unified-memory Windows hosts must
# select the fleet-proven Qwen3.6 35B-A3B MoE, not Coder Next or dense 70B.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

TIER_MAP="installers/windows/lib/tier-map.ps1"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "[contract] Windows SH_LARGE static tier uses Qwen3.6 35B-A3B"
qwen_block="$(awk '
    /"SH_LARGE" \{/ { in_block=1 }
    in_block { print }
    in_block && /^\s*\}/ { exit }
' "$TIER_MAP")"

if grep -q 'LlmModel   = "qwen3.6-35b-a3b"' <<<"$qwen_block"; then
    pass "Resolve-QwenTierConfig SH_LARGE uses qwen3.6-35b-a3b"
else
    fail "Resolve-QwenTierConfig SH_LARGE must use qwen3.6-35b-a3b"
fi

if grep -q 'GgufFile   = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"' <<<"$qwen_block"; then
    pass "Resolve-QwenTierConfig SH_LARGE uses Qwen3.6 A3B GGUF"
else
    fail "Resolve-QwenTierConfig SH_LARGE must use Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
fi

if grep -q 'qwen3-coder-next\|DeepSeek-R1-Distill-Llama-70B' <<<"$qwen_block"; then
    fail "Resolve-QwenTierConfig SH_LARGE must not default to Coder Next or dense 70B"
else
    pass "Resolve-QwenTierConfig SH_LARGE avoids Coder Next and dense 70B"
fi

echo "[contract] Windows catalog selector has AMD unified-memory SH_LARGE override"
if grep -q '\$script:UNIFIED_MEMORY_POLICY = "unified-memory-coder-next-a3b-v1"' "$TIER_MAP" \
    && grep -q '\$script:UNIFIED_MEMORY_MODEL_ID = "qwen3.6-35b-a3b-ud-q4"' "$TIER_MAP"; then
    pass "unified-memory A3B policy constants exist"
else
    fail "Windows tier map must define unified-memory A3B policy constants"
fi

if grep -q '\$isAmdUnifiedStrixLarge' "$TIER_MAP" \
    && grep -q 'Get-CatalogModelById -Catalog \$catalog -ModelId \$script:UNIFIED_MEMORY_MODEL_ID' "$TIER_MAP"; then
    pass "catalog selector forces Qwen3.6 A3B for AMD unified SH_LARGE"
else
    fail "catalog selector must force Qwen3.6 A3B for AMD unified SH_LARGE before generic ranking"
fi

if awk '/function ConvertTo-ModelFromTier/,/^}/' "$TIER_MAP" | grep -q '\^SH_LARGE\$.*qwen3.6-35b-a3b'; then
    pass "ConvertTo-ModelFromTier SH_LARGE returns qwen3.6-35b-a3b"
else
    fail "ConvertTo-ModelFromTier SH_LARGE must return qwen3.6-35b-a3b"
fi

echo "[contract] Windows catalog selector honors install_recommendation=false"
if grep -q 'Test-CatalogModelInstallRecommendationAllowed' "$TIER_MAP" \
    && grep -q 'install_recommendation' "$TIER_MAP"; then
    pass "catalog selector filters models that are not install recommendations"
else
    fail "Windows catalog selector must filter install_recommendation=false models"
fi

ps_bin=""
for candidate in pwsh powershell powershell.exe; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ps_bin="$candidate"
        break
    fi
done

if [[ -n "$ps_bin" ]]; then
    echo "[contract] behavioral: Windows resolver returns Qwen3.6 A3B for Strix Halo"
    ps_code='
        $ErrorActionPreference = "Stop"
        $env:MODEL_PROFILE = "qwen"
        $env:HOST_ARCH = "amd64"
        . "./installers/windows/lib/tier-map.ps1"
        $tierConfig = Resolve-TierConfig -Tier "SH_LARGE"
        $gpu = @{ Backend = "amd"; MemoryType = "unified"; VramMB = 0 }
        $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig -Tier "SH_LARGE" -GpuInfo $gpu -SystemRamGB 124 -SourceRoot (Resolve-Path ".")
        Write-Output ("STATIC={0}|{1}" -f $tierConfig.LlmModel, $tierConfig.GgufFile)
        Write-Output ("RESOLVED={0}|{1}|{2}" -f $resolved.LlmModel, $resolved.GgufFile, $resolved.RecommendationPolicy)
    '
    OUT="$("$ps_bin" -NoProfile -ExecutionPolicy Bypass -Command "$ps_code" 2>/dev/null || true)"
    if grep -q 'STATIC=qwen3.6-35b-a3b|Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' <<<"$OUT" \
        && grep -q 'RESOLVED=qwen3.6-35b-a3b|Qwen3.6-35B-A3B-UD-Q4_K_M.gguf|context-aware-largest-capable-general-v1+unified-memory-coder-next-a3b-v1' <<<"$OUT"; then
        pass "PowerShell resolver selects Qwen3.6 A3B with unified-memory policy"
    else
        fail "PowerShell resolver should select Qwen3.6 A3B; got: $OUT"
    fi

    echo "[contract] behavioral: Windows Tier 1 avoids non-recommended Qwen Coder"
    ps_code='
        $ErrorActionPreference = "Stop"
        $env:MODEL_PROFILE = "qwen"
        $env:HOST_ARCH = "amd64"
        . "./installers/windows/lib/tier-map.ps1"
        $tierConfig = Resolve-TierConfig -Tier "1"
        $gpu = @{ Backend = "nvidia"; MemoryType = "dedicated"; VramMB = 8000 }
        $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig -Tier "1" -GpuInfo $gpu -SystemRamGB 31 -SourceRoot (Resolve-Path ".")
        Write-Output ("RESOLVED={0}|{1}|{2}" -f $resolved.LlmModel, $resolved.GgufFile, $resolved.RecommendationAlternatives)
    '
    OUT="$("$ps_bin" -NoProfile -ExecutionPolicy Bypass -Command "$ps_code" 2>/dev/null || true)"
    if grep -q '^RESOLVED=' <<<"$OUT" \
        && ! grep -q 'qwen2.5-coder-3b-instruct\|qwen2.5-coder-3b-128k-q4' <<<"$OUT"; then
        pass "PowerShell resolver excludes non-recommended Qwen Coder for Tier 1 NVIDIA"
    else
        fail "PowerShell resolver must exclude non-recommended Qwen Coder; got: $OUT"
    fi
else
    echo "[skip] PowerShell not available - behavioral resolver check skipped"
fi

echo
echo "Windows Strix tier-map contract: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
