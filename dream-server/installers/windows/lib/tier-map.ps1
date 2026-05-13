# ============================================================================
# Dream Server Windows Installer -- Tier Map
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Map hardware tier to model name, GGUF file, URL, and context size
#
# Canonical source: installers/lib/tier-map.sh (keep values byte-identical)
#
# Modder notes:
#   Add new tiers or change model assignments here.
#   Each tier maps to a specific GGUF quantization and context window.
# ============================================================================

function Normalize-ModelProfile {
    param([string]$ModelProfile = $env:MODEL_PROFILE)

    if (-not $ModelProfile) { return "qwen" }

    switch ($ModelProfile.ToLowerInvariant()) {
        "auto" { return "auto" }
        "gemma" { return "gemma4" }
        "gemma4" { return "gemma4" }
        "gemma-4" { return "gemma4" }
        default { return "qwen" }
    }
}

function Resolve-EffectiveModelProfile {
    param(
        [string]$Tier,
        [string]$RequestedProfile
    )

    if ($RequestedProfile -eq "auto") {
        switch ($Tier) {
            "CLOUD" { return "qwen" }
            "0" { return "qwen" }
            default { return "gemma4" }
        }
    }

    return $RequestedProfile
}

function Resolve-QwenTierConfig {
    param([string]$Tier)

    switch ($Tier) {
        "CLOUD" {
            return @{
                TierName   = "Cloud (API)"
                LlmModel   = "anthropic/claude-sonnet-4-5-20250514"
                GgufFile   = ""
                GgufUrl    = ""
                GgufSha256 = ""
                MaxContext = 200000
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "NV_ULTRA" {
            return @{
                TierName   = "NVIDIA Ultra (90GB+)"
                LlmModel   = "qwen3-coder-next"
                GgufFile   = "qwen3-coder-next-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/resolve/main/Qwen3-Coder-Next-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 131072
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "SH_LARGE" {
            return @{
                TierName   = "Strix Halo 90+"
                LlmModel   = "qwen3-coder-next"
                GgufFile   = "qwen3-coder-next-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/resolve/main/Qwen3-Coder-Next-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 131072
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "SH_COMPACT" {
            return @{
                TierName   = "Strix Halo Compact"
                LlmModel   = "qwen3-30b-a3b"
                GgufFile   = "Qwen3-30B-A3B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
                GgufSha256 = "9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
                MaxContext = 131072
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "0" {
            return @{
                TierName   = "Lightweight"
                LlmModel   = "qwen3.5-2b"
                GgufFile   = "Qwen3.5-2B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 8192
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "1" {
            return @{
                TierName   = "Entry Level"
                LlmModel   = "qwen3.5-9b"
                GgufFile   = "Qwen3.5-9B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
                GgufSha256 = "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
                MaxContext = 16384
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "2" {
            return @{
                TierName   = "Prosumer"
                LlmModel   = "qwen3.5-9b"
                GgufFile   = "Qwen3.5-9B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
                GgufSha256 = "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
                MaxContext = 32768
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "3" {
            return @{
                TierName   = "Pro"
                LlmModel   = "qwen3-30b-a3b"
                GgufFile   = "Qwen3-30B-A3B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
                GgufSha256 = "84b5f7f112156d63836a01a69dc3f11a6ba63b10a23b8ca7a7efaf52d5a2d806"
                MaxContext = 32768
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "4" {
            return @{
                TierName   = "Enterprise"
                LlmModel   = "qwen3-30b-a3b"
                GgufFile   = "Qwen3-30B-A3B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
                GgufSha256 = "9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
                MaxContext = 131072
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        default {
            throw "Invalid tier: $Tier. Valid tiers: 0, 1, 2, 3, 4, CLOUD, NV_ULTRA, SH_LARGE, SH_COMPACT"
        }
    }
}

function Resolve-GemmaTierConfig {
    param(
        [string]$Tier,
        [string]$RequestedProfile
    )

    # Keep this aligned with docker-compose.nvidia.yml so preflight validates
    # the same CUDA runtime image compose will start.
    $runtimeImage = "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014"
    $runtimeTag = "b9014"

    switch ($Tier) {
        "CLOUD" {
            return @{
                TierName   = "Cloud (API)"
                LlmModel   = "anthropic/claude-sonnet-4-5-20250514"
                GgufFile   = ""
                GgufUrl    = ""
                GgufSha256 = ""
                MaxContext = 200000
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "NV_ULTRA" {
            return @{
                TierName   = "NVIDIA Ultra (90GB+)"
                LlmModel   = "gemma-4-31b-it"
                GgufFile   = "gemma-4-31B-it-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/ggml-org/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 131072
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "gemma4"
                LlamaServerImage = $runtimeImage
                LlamaCppReleaseTag = $runtimeTag
            }
        }
        "SH_LARGE" {
            return @{
                TierName   = "Strix Halo 90+"
                LlmModel   = "gemma-4-31b-it"
                GgufFile   = "gemma-4-31B-it-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/ggml-org/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 131072
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "gemma4"
                LlamaServerImage = $runtimeImage
                LlamaCppReleaseTag = $runtimeTag
            }
        }
        "SH_COMPACT" {
            return @{
                TierName   = "Strix Halo Compact"
                LlmModel   = "gemma-4-26b-a4b-it"
                GgufFile   = "gemma-4-26B-A4B-it-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 65536
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "gemma4"
                LlamaServerImage = $runtimeImage
                LlamaCppReleaseTag = $runtimeTag
            }
        }
        "0" {
            return @{
                TierName   = "Lightweight"
                LlmModel   = "qwen3.5-2b"
                GgufFile   = "Qwen3.5-2B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 8192
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "1" {
            return @{
                TierName   = "Entry Level"
                LlmModel   = "gemma-4-e2b-it"
                GgufFile   = "gemma-4-E2B-it-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 16384
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "gemma4"
                LlamaServerImage = $runtimeImage
                LlamaCppReleaseTag = $runtimeTag
            }
        }
        "2" {
            return @{
                TierName   = "Prosumer"
                LlmModel   = "gemma-4-e4b-it"
                GgufFile   = "gemma-4-E4B-it-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 32768
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "gemma4"
                LlamaServerImage = $runtimeImage
                LlamaCppReleaseTag = $runtimeTag
            }
        }
        "3" {
            return @{
                TierName   = "Pro"
                LlmModel   = "gemma-4-26b-a4b-it"
                GgufFile   = "gemma-4-26B-A4B-it-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 16384
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "gemma4"
                LlamaServerImage = $runtimeImage
                LlamaCppReleaseTag = $runtimeTag
            }
        }
        "4" {
            return @{
                TierName   = "Enterprise"
                LlmModel   = "gemma-4-31b-it"
                GgufFile   = "gemma-4-31B-it-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/ggml-org/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
                GgufSha256 = ""
                MaxContext = 65536
                ModelProfileRequested = $RequestedProfile
                ModelProfileEffective = "gemma4"
                LlamaServerImage = $runtimeImage
                LlamaCppReleaseTag = $runtimeTag
            }
        }
        default {
            throw "Invalid tier: $Tier. Valid tiers: 0, 1, 2, 3, 4, CLOUD, NV_ULTRA, SH_LARGE, SH_COMPACT"
        }
    }
}

function Resolve-TierConfig {
    param([string]$Tier)

    $requestedProfile = Normalize-ModelProfile
    $effectiveProfile = Resolve-EffectiveModelProfile -Tier $Tier -RequestedProfile $requestedProfile

    switch ($effectiveProfile) {
        "gemma4" { return Resolve-GemmaTierConfig -Tier $Tier -RequestedProfile $requestedProfile }
        default { return Resolve-QwenTierConfig -Tier $Tier }
    }
}

function Get-CatalogModelSelectorMemory {
    param(
        [hashtable]$GpuInfo,
        [int]$SystemRamGB
    )

    $backend = "$($GpuInfo.Backend)".ToLowerInvariant()
    $memoryType = "$($GpuInfo.MemoryType)".ToLowerInvariant()
    if ($backend -eq "apple" -or $memoryType -eq "unified") {
        return @{
            CapacityGB = [Math]::Max([double]$SystemRamGB * 0.55, 2.0)
            Label = "unified system memory"
        }
    }
    if ($backend -eq "cpu" -or $backend -eq "none" -or $backend -eq "unknown" -or [int]$GpuInfo.VramMB -le 0) {
        return @{
            CapacityGB = [Math]::Min([Math]::Max([double]$SystemRamGB * 0.35, 3.0), 8.0)
            Label = "system RAM"
        }
    }
    return @{
        CapacityGB = ([double]$GpuInfo.VramMB / 1024.0)
        Label = "GPU VRAM"
    }
}

function Test-CatalogModelFamilyAllowed {
    param(
        [object]$Model,
        [string]$ModelProfileName
    )

    $family = "$($Model.family)".ToLowerInvariant()
    if ($ModelProfileName -eq "gemma4") {
        return ($family -eq "gemma4" -or $Model.id -eq "qwen3.5-2b-q4")
    }
    return ($family -ne "gemma4")
}

function Get-CatalogModelEstimatedParamBillions {
    param([object]$Model)

    foreach ($key in @("total_params_b", "params_b")) {
        $prop = $Model.PSObject.Properties[$key]
        if ($prop -and $prop.Value) {
            try {
                $value = [double]$prop.Value
                if ($value -gt 0) { return $value }
            } catch {}
        }
    }

    $numbers = @()
    foreach ($text in @($Model.id, $Model.name, $Model.llm_model_name, $Model.gguf_file)) {
        if (-not $text) { continue }
        foreach ($match in [regex]::Matches([string]$text, "(\d+(?:\.\d+)?)\s*b", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
            $numbers += [double]$match.Groups[1].Value
        }
    }
    if ($numbers.Count -gt 0) {
        return [double]($numbers | Measure-Object -Maximum).Maximum
    }

    try {
        $sizeMb = [double]$Model.size_mb
        if ($sizeMb -gt 0) { return [Math]::Max(($sizeMb / 600.0), 1.0) }
    } catch {}
    return 4.0
}

function Get-CatalogModelEstimatedContextKvGB {
    param([object]$Model)

    $context = [Math]::Max([int]$Model.context_length, 8192)
    $paramsB = Get-CatalogModelEstimatedParamBillions -Model $Model
    $kvPer32kGb = [Math]::Min([Math]::Max(($paramsB * 0.12), 0.35), 3.5)
    return [Math]::Round(($kvPer32kGb * ([double]$context / 32768.0)), 2)
}

function Get-CatalogModelSelectorRequiredGB {
    param([object]$Model)

    $declared = [double]$Model.vram_required_gb
    $sizeGb = ([double]$Model.size_mb / 1024.0)
    if ($sizeGb -le 0) { return [Math]::Round($declared, 2) }
    $withContext = $sizeGb + (Get-CatalogModelEstimatedContextKvGB -Model $Model)
    return [Math]::Round([Math]::Max($declared, $withContext), 2)
}

function Get-CatalogModelScore {
    param(
        [object]$Model,
        [double]$CapacityGB,
        [string]$ModelProfileName
    )

    $specialtyWeights = @{
        Code = 4.4
        Quality = 4.1
        General = 3.8
        Balanced = 3.5
        Reasoning = 3.3
        Fast = 2.0
        Bootstrap = 1.0
    }
    $specialty = if ($Model.specialty) { [string]$Model.specialty } else { "General" }
    $specialtyWeight = if ($specialtyWeights.ContainsKey($specialty)) { [double]$specialtyWeights[$specialty] } else { 2.5 }
    $family = "$($Model.family)".ToLowerInvariant()
    $familyBonus = 0.0
    if ($ModelProfileName -eq "gemma4" -and $family -eq "gemma4") { $familyBonus += 0.35 }
    if (($ModelProfileName -eq "qwen" -or $ModelProfileName -eq "auto") -and $family -eq "qwen") { $familyBonus += 0.25 }
    $sizeMb = [Math]::Max([double]$Model.size_mb, 1.0)
    $context = [Math]::Max([int]$Model.context_length, 8192)
    $required = Get-CatalogModelSelectorRequiredGB -Model $Model
    $contextBonus = [Math]::Min(([double]$context / 32768.0), 4.0) * 0.18
    $capability = [Math]::Min(($sizeMb / 1024.0), 48.0) * 0.24
    $fitRatio = $required / [Math]::Max($CapacityGB, 1.0)
    $headroomPenalty = 0.0
    if ($fitRatio -gt 0.98) { $headroomPenalty = 0.35 }
    elseif ($fitRatio -gt 0.92) { $headroomPenalty = 0.15 }
    return $specialtyWeight + $familyBonus + $contextBonus + $capability - $headroomPenalty
}

function Resolve-CatalogModelRecommendation {
    param(
        [hashtable]$TierConfig,
        [string]$Tier,
        [hashtable]$GpuInfo,
        [int]$SystemRamGB,
        [string]$SourceRoot
    )

    if ($env:DREAM_DISABLE_CATALOG_MODEL_SELECTOR -eq "true" -or $Tier -eq "CLOUD") {
        return $TierConfig
    }

    $catalogPath = Join-Path $SourceRoot "config\model-library.json"
    if (-not (Test-Path $catalogPath)) {
        return $TierConfig
    }

    try {
        $catalog = Get-Content $catalogPath -Raw | ConvertFrom-Json
    } catch {
        return $TierConfig
    }

    $modelProfileName = Normalize-ModelProfile -ModelProfile $TierConfig.ModelProfileEffective
    if ($modelProfileName -eq "auto") {
        $modelProfileName = Resolve-EffectiveModelProfile -Tier $Tier -RequestedProfile $modelProfileName
    }
    $memory = Get-CatalogModelSelectorMemory -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB
    $capacityGb = [double]$memory.CapacityGB
    $candidates = @()
    foreach ($model in $catalog.models) {
        if (-not $model.gguf_url) { continue }
        if (-not (Test-CatalogModelFamilyAllowed -Model $model -ModelProfileName $modelProfileName)) { continue }
        $requiredGb = Get-CatalogModelSelectorRequiredGB -Model $model
        if ($requiredGb -gt ($capacityGb + 0.25)) { continue }
        $candidates += [pscustomobject]@{
            Model = $model
            Score = Get-CatalogModelScore -Model $model -CapacityGB $capacityGb -ModelProfileName $modelProfileName
            RequiredGB = $requiredGb
        }
    }
    if ($candidates.Count -eq 0) {
        return $TierConfig
    }

    $ranked = $candidates | Sort-Object -Property `
        @{ Expression = { $_.Score }; Descending = $true }, `
        @{ Expression = { [double]$_.RequiredGB }; Descending = $true }, `
        @{ Expression = { [int]$_.Model.context_length }; Descending = $true }
    $selected = $ranked[0].Model
    $alternatives = @($ranked | Select-Object -First 3 | ForEach-Object {
        "$($_.Model.id):$([int]$_.Model.context_length):$([double]$_.RequiredGB)"
    }) -join ";"
    $confidence = if ($capacityGb -gt 0 -and $GpuInfo.Backend -and $GpuInfo.Backend -ne "unknown") { "high" } else { "medium" }
    $contextK = [int]([int]$selected.context_length / 1024)
    $selectedRequiredGb = Get-CatalogModelSelectorRequiredGB -Model $selected
    $reason = "Catalog fit (context-aware-largest-capable-general-v1): $($selected.name) needs about ${selectedRequiredGb}GB including context/KV, fits $([Math]::Round($capacityGb, 1))GB $($memory.Label) on $($GpuInfo.Backend), and gives ${contextK}K context. Throughput requires a local benchmark after first launch."

    $TierConfig["LlmModel"] = $selected.llm_model_name
    $TierConfig["GgufFile"] = $selected.gguf_file
    $TierConfig["GgufUrl"] = $selected.gguf_url
    $TierConfig["GgufSha256"] = $selected.gguf_sha256
    $TierConfig["MaxContext"] = [int]$selected.context_length
    $TierConfig["ModelSizeMB"] = [int][Math]::Round([double]$selected.size_mb)
    if ($selected.llama_server_image) {
        $TierConfig["LlamaServerImage"] = $selected.llama_server_image
    }
    $TierConfig["RecommendationSource"] = "catalog_fit_pre_download"
    $TierConfig["RecommendationPolicy"] = "context-aware-largest-capable-general-v1"
    $TierConfig["RecommendationConfidence"] = $confidence
    $TierConfig["RecommendationReason"] = $reason
    $TierConfig["RecommendationAlternatives"] = $alternatives
    return $TierConfig
}

function ConvertTo-TierFromGpu {
    param(
        [hashtable]$GpuInfo,
        [int]$SystemRamGB
    )

    $backend = $GpuInfo.Backend
    $vramMB  = $GpuInfo.VramMB

    # No GPU detected -- use CPU-only local inference.
    # CLOUD mode requires the explicit --Cloud flag; never auto-select it
    # because it needs an API key the user may not have.
    if ($backend -eq "none") {
        if ($SystemRamGB -lt 8) { return "0" }
        return "1"
    }

    # AMD Strix Halo -- tier based on system RAM (unified memory)
    if ($backend -eq "amd" -and $GpuInfo.MemoryType -eq "unified") {
        if ($SystemRamGB -ge 90) { return "SH_LARGE" }
        if ($SystemRamGB -ge 64) { return "SH_COMPACT" }
        if ($SystemRamGB -lt 12) { return "0" }
        return "1"  # Fallback for small unified memory
    }

    # NVIDIA -- tier based on VRAM
    $vramGB = [math]::Floor($vramMB / 1024)

    if ($vramGB -ge 90) { return "NV_ULTRA" }
    if ($vramGB -ge 40) { return "4" }
    if ($vramGB -ge 20) { return "3" }
    if ($vramGB -ge 12) { return "2" }
    if ($vramGB -lt 4 -and $SystemRamGB -lt 12) { return "0" }
    return "1"
}

# Map a tier name to its LLM_MODEL value (used by dream model swap)
function ConvertTo-ModelFromTier {
    param(
        [string]$Tier,
        [string]$ModelProfile = $env:MODEL_PROFILE
    )

    $requestedProfile = Normalize-ModelProfile -ModelProfile $ModelProfile
    $effectiveProfile = Resolve-EffectiveModelProfile -Tier $Tier -RequestedProfile $requestedProfile

    if ($effectiveProfile -eq "gemma4") {
        switch -Regex ($Tier) {
            "^CLOUD$"                { return "anthropic/claude-sonnet-4-5-20250514" }
            "^NV_ULTRA$"             { return "gemma-4-31b-it" }
            "^SH_LARGE$"             { return "gemma-4-31b-it" }
            "^(SH_COMPACT|SH)$"      { return "gemma-4-26b-a4b-it" }
            "^(0|T0)$"               { return "qwen3.5-2b" }
            "^(1|T1)$"               { return "gemma-4-e2b-it" }
            "^(2|T2)$"               { return "gemma-4-e4b-it" }
            "^(3|T3)$"               { return "gemma-4-26b-a4b-it" }
            "^(4|T4)$"               { return "gemma-4-31b-it" }
            default                  { return "" }
        }
    }

    switch -Regex ($Tier) {
        "^CLOUD$"                { return "anthropic/claude-sonnet-4-5-20250514" }
        "^NV_ULTRA$"             { return "qwen3-coder-next" }
        "^SH_LARGE$"             { return "qwen3-coder-next" }
        "^(SH_COMPACT|SH)$"      { return "qwen3-30b-a3b" }
        "^(0|T0)$"               { return "qwen3.5-2b" }
        "^(1|T1)$"               { return "qwen3.5-9b" }
        "^(2|T2)$"               { return "qwen3.5-9b" }
        "^(3|T3)$"               { return "qwen3-30b-a3b" }
        "^(4|T4)$"               { return "qwen3-30b-a3b" }
        default                  { return "" }
    }
}

# ============================================================================
# Bootstrap Fast-Start
# ============================================================================
# Tiny model for instant chat while the full tier model downloads in background.

$script:BOOTSTRAP_GGUF_FILE    = "Qwen3.5-2B-Q4_K_M.gguf"
$script:BOOTSTRAP_GGUF_URL     = "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
$script:BOOTSTRAP_LLM_MODEL    = "qwen3.5-2b"
$script:BOOTSTRAP_MAX_CONTEXT   = 8192

function Get-TierRank {
    param([string]$Tier)
    switch ($Tier) {
        { $_ -in "NV_ULTRA","SH_LARGE" } { return 5 }
        "4"                                { return 4 }
        { $_ -in "SH_COMPACT","3" }       { return 3 }
        { $_ -in "ARC","2" }              { return 2 }
        { $_ -in "ARC_LITE","1" }         { return 1 }
        "0"                                { return 0 }
        default                            { return 1 }
    }
}

function Should-UseBootstrap {
    param(
        [string]$Tier,
        [string]$InstallDir,
        [string]$GgufFile,
        [bool]$CloudMode = $false,
        [bool]$OfflineMode = $false,
        [bool]$NoBootstrap = $false
    )
    if ($NoBootstrap)  { return $false }
    if ($CloudMode)    { return $false }
    if ($OfflineMode)  { return $false }
    if ((Get-TierRank $Tier) -le 0) { return $false }
    $modelPath = Join-Path (Join-Path $InstallDir "data\models") $GgufFile
    if (Test-Path $modelPath) { return $false }
    return $true
}
