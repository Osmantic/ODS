# ============================================================================
# ODS Windows Installer -- Tier Map
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

$script:CATALOG_SELECTOR_POLICY = "context-aware-largest-capable-general-v1"
$script:SPARK_AARCH64_POLICY = "spark-aarch64-nv-ultra-a3b-v1"
$script:SPARK_AARCH64_MODEL_ID = "qwen3.6-35b-a3b-ud-q4"
$script:UNIFIED_MEMORY_POLICY = "unified-memory-coder-next-a3b-v1"
$script:UNIFIED_MEMORY_MODEL_ID = "qwen3.6-35b-a3b-ud-q4"

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

function Normalize-HostArchitecture {
    param([string]$HostArchitecture)

    if (-not $HostArchitecture) { return "unknown" }
    switch ($HostArchitecture.ToLowerInvariant()) {
        "aarch64" { return "arm64" }
        "arm64" { return "arm64" }
        "x86_64" { return "amd64" }
        "x64" { return "amd64" }
        "amd64" { return "amd64" }
        default { return $HostArchitecture.ToLowerInvariant() }
    }
}

function Get-HostArchitecture {
    try {
        return Normalize-HostArchitecture -HostArchitecture ([System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString())
    } catch {
        return Normalize-HostArchitecture -HostArchitecture $env:PROCESSOR_ARCHITECTURE
    }
}

function Test-CatalogModelSourceAllowed {
    param([object]$Model)

    $prop = $Model.PSObject.Properties["source"]
    if (-not $prop -or $null -eq $prop.Value) { return $true }
    return ("$($prop.Value)".Trim().ToLowerInvariant() -in @("", "curated"))
}

function Get-CatalogModelById {
    param(
        [object]$Catalog,
        [string]$ModelId
    )

    foreach ($model in $Catalog.models) {
        if (-not (Test-CatalogModelSourceAllowed -Model $model)) { continue }
        if ("$($model.id)".ToLowerInvariant() -eq $ModelId) {
            return $model
        }
    }
    return $null
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
            # Strix Halo (AMD Ryzen AI MAX+ 395, 124GB unified) should stay
            # on the A3B MoE path proven by fleet. Dense 70B/Coder Next choices
            # are too aggressive for Windows Lemonade first-run recovery.
            return @{
                TierName   = "Strix Halo 90+"
                LlmModel   = "qwen3.6-35b-a3b"
                GgufFile   = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GgufSha256 = "ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61"
                MaxContext = 131072
                ModelSizeMB = 21110
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
                GgufSha256 = "9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
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
    param(
        [string]$Tier,
        [string]$ModelProfile = $env:MODEL_PROFILE
    )

    $requestedProfile = Normalize-ModelProfile -ModelProfile $ModelProfile
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

function Get-CatalogRuntimeProfile {
    param(
        [object]$Model,
        [hashtable]$GpuInfo,
        [int]$SystemRamGB
    )

    if (-not $Model.PSObject.Properties["runtime_profiles"]) { return $null }
    $profiles = @($Model.runtime_profiles)
    if ($profiles.Count -eq 0) { return $null }

    $backend = "$($GpuInfo.Backend)".ToLowerInvariant()
    $memoryType = "$($GpuInfo.MemoryType)".ToLowerInvariant()
    if (-not $memoryType) { $memoryType = "discrete" }
    $hostArch = Get-HostArchitecture
    $vramGB = [double]$GpuInfo.VramMB / 1024.0

    foreach ($runtimeProfile in $profiles) {
        if (-not $runtimeProfile) { continue }
        if ($runtimeProfile.backend -and "$($runtimeProfile.backend)".ToLowerInvariant() -ne $backend) { continue }
        if ($runtimeProfile.host_arch) {
            $arches = @($runtimeProfile.host_arch | ForEach-Object { Normalize-HostArchitecture -HostArchitecture $_ })
            if ($arches.Count -gt 0 -and $arches -notcontains $hostArch) { continue }
        }
        if ($runtimeProfile.memory_type -and "$($runtimeProfile.memory_type)".ToLowerInvariant() -ne $memoryType) { continue }
        try {
            if ($null -ne $runtimeProfile.vram_min_gb -and $vramGB -lt [double]$runtimeProfile.vram_min_gb) { continue }
            if ($null -ne $runtimeProfile.vram_max_gb -and $vramGB -gt [double]$runtimeProfile.vram_max_gb) { continue }
            if ($null -ne $runtimeProfile.system_ram_min_gb -and [double]$SystemRamGB -lt [double]$runtimeProfile.system_ram_min_gb) { continue }
        } catch {
            continue
        }
        return $runtimeProfile
    }
    return $null
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

function Test-CatalogModelInstallRecommendationAllowed {
    param([object]$Model)

    $prop = $Model.PSObject.Properties["install_recommendation"]
    if (-not $prop) { return $true }
    $value = $prop.Value
    if ($null -eq $value) { return $true }
    if ($value -is [bool]) { return [bool]$value }
    $text = "$value".Trim().ToLowerInvariant()
    return -not ($text -in @("0", "false", "no", "off"))
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
    param(
        [object]$Model,
        [object]$RuntimeProfile = $null
    )

    $context = if ($RuntimeProfile -and $RuntimeProfile.context_length) { [int]$RuntimeProfile.context_length } else { [int]$Model.context_length }
    $context = [Math]::Max($context, 8192)
    $paramsB = Get-CatalogModelEstimatedParamBillions -Model $Model
    $kvPer32kGb = [Math]::Min([Math]::Max(($paramsB * 0.12), 0.35), 3.5)
    return [Math]::Round(($kvPer32kGb * ([double]$context / 32768.0)), 2)
}

function Get-CatalogModelSelectorRequiredGB {
    param(
        [object]$Model,
        [object]$RuntimeProfile = $null
    )

    if ($RuntimeProfile -and $null -ne $RuntimeProfile.estimated_required_gb) {
        return [Math]::Round([double]$RuntimeProfile.estimated_required_gb, 2)
    }
    $declared = [double]$Model.vram_required_gb
    $sizeGb = ([double]$Model.size_mb / 1024.0)
    if ($sizeGb -le 0) { return [Math]::Round($declared, 2) }
    $withContext = $sizeGb + (Get-CatalogModelEstimatedContextKvGB -Model $Model -RuntimeProfile $RuntimeProfile)
    return [Math]::Round([Math]::Max($declared, $withContext), 2)
}

function Get-CatalogModelScore {
    param(
        [object]$Model,
        [double]$CapacityGB,
        [string]$ModelProfileName,
        [object]$RuntimeProfile = $null
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
    $context = if ($RuntimeProfile -and $RuntimeProfile.context_length) { [int]$RuntimeProfile.context_length } else { [int]$Model.context_length }
    $context = [Math]::Max($context, 8192)
    $required = Get-CatalogModelSelectorRequiredGB -Model $Model -RuntimeProfile $RuntimeProfile
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

    if ($env:ODS_DISABLE_CATALOG_MODEL_SELECTOR -eq "true" -or $Tier -eq "CLOUD") {
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
    $hostArchName = Normalize-HostArchitecture -HostArchitecture $env:HOST_ARCH
    if ($hostArchName -eq "unknown") {
        $hostArchName = Get-HostArchitecture
    }

    $backendName = "$($GpuInfo.Backend)".ToLowerInvariant()
    $memoryTypeName = "$($GpuInfo.MemoryType)".ToLowerInvariant()
    $isAmdUnifiedStrixLarge = (
        $Tier -eq "SH_LARGE" -and
        $modelProfileName -eq "qwen" -and
        $backendName -eq "amd" -and
        $memoryTypeName -eq "unified"
    )

    if ($Tier -eq "NV_ULTRA" -and $modelProfileName -eq "qwen" -and $hostArchName -eq "arm64") {
        $selectedArchModel = Get-CatalogModelById -Catalog $catalog -ModelId $script:SPARK_AARCH64_MODEL_ID
        if ($selectedArchModel -and $selectedArchModel.gguf_url) {
            $selectedRequiredGb = Get-CatalogModelSelectorRequiredGB -Model $selectedArchModel
            $contextK = [int]([int]$selectedArchModel.context_length / 1024)
            $reason = "Arch-aware catalog policy ($script:SPARK_AARCH64_POLICY): $($selectedArchModel.name) is selected for arm64 NV_ULTRA Spark-class NVIDIA hosts because qwen3-coder-next is excluded on this architecture by the tier map. It needs about ${selectedRequiredGb}GB including context/KV, fits $([Math]::Round($capacityGb, 1))GB $($memory.Label), and gives ${contextK}K context. Throughput requires a local benchmark after first launch."

            $TierConfig["LlmModel"] = $selectedArchModel.llm_model_name
            $TierConfig["GgufFile"] = $selectedArchModel.gguf_file
            $TierConfig["GgufUrl"] = $selectedArchModel.gguf_url
            $TierConfig["GgufSha256"] = $selectedArchModel.gguf_sha256
            $TierConfig["MaxContext"] = [int]$selectedArchModel.context_length
            $TierConfig["ModelSizeMB"] = [int][Math]::Round([double]$selectedArchModel.size_mb)
            if ($selectedArchModel.llama_server_image) {
                $TierConfig["LlamaServerImage"] = $selectedArchModel.llama_server_image
            }
            $TierConfig["RecommendationSource"] = "catalog_arch_policy_pre_download"
            $TierConfig["RecommendationPolicy"] = "$script:CATALOG_SELECTOR_POLICY+$script:SPARK_AARCH64_POLICY"
            $TierConfig["RecommendationConfidence"] = "high"
            $TierConfig["RecommendationReason"] = $reason
            $TierConfig["RecommendationAlternatives"] = "$($selectedArchModel.id):$([int]$selectedArchModel.context_length):$([double]$selectedRequiredGb)"
            return $TierConfig
        }
    }

    if ($isAmdUnifiedStrixLarge) {
        $selectedUnifiedModel = Get-CatalogModelById -Catalog $catalog -ModelId $script:UNIFIED_MEMORY_MODEL_ID
        if ($selectedUnifiedModel -and $selectedUnifiedModel.gguf_url) {
            $selectedRequiredGb = Get-CatalogModelSelectorRequiredGB -Model $selectedUnifiedModel
            $contextK = [int]([int]$selectedUnifiedModel.context_length / 1024)
            $reason = "Arch-aware catalog policy ($script:UNIFIED_MEMORY_POLICY): $($selectedUnifiedModel.name) is selected for AMD unified-memory SH_LARGE hosts because Qwen3.6-35B-A3B is the fleet-proven Windows Lemonade target. Dense 70B and Coder Next defaults are avoided for first-run recovery. It needs about ${selectedRequiredGb}GB including context/KV, fits $([Math]::Round($capacityGb, 1))GB $($memory.Label), and gives ${contextK}K context. Throughput requires a local benchmark after first launch."

            $TierConfig["LlmModel"] = $selectedUnifiedModel.llm_model_name
            $TierConfig["GgufFile"] = $selectedUnifiedModel.gguf_file
            $TierConfig["GgufUrl"] = $selectedUnifiedModel.gguf_url
            $TierConfig["GgufSha256"] = $selectedUnifiedModel.gguf_sha256
            $TierConfig["MaxContext"] = [int]$selectedUnifiedModel.context_length
            $TierConfig["ModelSizeMB"] = [int][Math]::Round([double]$selectedUnifiedModel.size_mb)
            if ($selectedUnifiedModel.llama_server_image) {
                $TierConfig["LlamaServerImage"] = $selectedUnifiedModel.llama_server_image
            }
            $TierConfig["RecommendationSource"] = "catalog_arch_policy_pre_download"
            $TierConfig["RecommendationPolicy"] = "$script:CATALOG_SELECTOR_POLICY+$script:UNIFIED_MEMORY_POLICY"
            $TierConfig["RecommendationConfidence"] = "high"
            $TierConfig["RecommendationReason"] = $reason
            $TierConfig["RecommendationAlternatives"] = "$($selectedUnifiedModel.id):$([int]$selectedUnifiedModel.context_length):$([double]$selectedRequiredGb)"
            return $TierConfig
        }
    }

    $candidates = @()
    foreach ($model in $catalog.models) {
        if (-not (Test-CatalogModelSourceAllowed -Model $model)) { continue }
        if (-not $model.gguf_url) { continue }
        if (-not (Test-CatalogModelInstallRecommendationAllowed -Model $model)) { continue }
        if (-not (Test-CatalogModelFamilyAllowed -Model $model -ModelProfileName $modelProfileName)) { continue }
        $runtimeProfile = Get-CatalogRuntimeProfile -Model $model -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB
        $requiredGb = Get-CatalogModelSelectorRequiredGB -Model $model -RuntimeProfile $runtimeProfile
        if ($requiredGb -gt ($capacityGb + 0.25)) { continue }
        $candidates += [pscustomobject]@{
            Model = $model
            RuntimeProfile = $runtimeProfile
            Score = Get-CatalogModelScore -Model $model -CapacityGB $capacityGb -ModelProfileName $modelProfileName -RuntimeProfile $runtimeProfile
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
        $altContext = if ($_.RuntimeProfile -and $_.RuntimeProfile.context_length) { [int]$_.RuntimeProfile.context_length } else { [int]$_.Model.context_length }
        "$($_.Model.id):${altContext}:$([double]$_.RequiredGB)"
    }) -join ";"
    $confidence = if ($capacityGb -gt 0 -and $GpuInfo.Backend -and $GpuInfo.Backend -ne "unknown") { "high" } else { "medium" }
    $selectedRuntimeProfile = $ranked[0].RuntimeProfile
    $selectedContext = if ($selectedRuntimeProfile -and $selectedRuntimeProfile.context_length) { [int]$selectedRuntimeProfile.context_length } else { [int]$selected.context_length }
    $contextK = [int]($selectedContext / 1024)
    $selectedRequiredGb = Get-CatalogModelSelectorRequiredGB -Model $selected -RuntimeProfile $selectedRuntimeProfile
    if ($selectedRuntimeProfile) {
        $reason = "Catalog runtime fit (context-aware-largest-capable-general-v1): $($selected.name) uses $($selectedRuntimeProfile.label) via $($selectedRuntimeProfile.runtime), needs about ${selectedRequiredGb}GB GPU headroom plus $($selectedRuntimeProfile.system_ram_min_gb)GB system RAM, fits $([Math]::Round($capacityGb, 1))GB $($memory.Label) on $($GpuInfo.Backend), and gives ${contextK}K context. Throughput requires a local benchmark after first launch."
    } else {
        $reason = "Catalog fit (context-aware-largest-capable-general-v1): $($selected.name) needs about ${selectedRequiredGb}GB including context/KV, fits $([Math]::Round($capacityGb, 1))GB $($memory.Label) on $($GpuInfo.Backend), and gives ${contextK}K context. Throughput requires a local benchmark after first launch."
    }

    $TierConfig["LlmModel"] = $selected.llm_model_name
    $TierConfig["GgufFile"] = $selected.gguf_file
    $TierConfig["GgufUrl"] = $selected.gguf_url
    $TierConfig["GgufSha256"] = $selected.gguf_sha256
    $TierConfig["MaxContext"] = $selectedContext
    $TierConfig["ModelSizeMB"] = [int][Math]::Round([double]$selected.size_mb)
    if ($selectedRuntimeProfile) {
        $TierConfig["RuntimeProfile"] = $selectedRuntimeProfile.id
        $TierConfig["RuntimeProfileLabel"] = $selectedRuntimeProfile.label
        $TierConfig["RuntimeProfileSource"] = $selectedRuntimeProfile.source_url
        if ($selectedRuntimeProfile.llama_server_image) {
            $TierConfig["LlamaServerImage"] = $selectedRuntimeProfile.llama_server_image
        }
        if ($selectedRuntimeProfile.env) {
            foreach ($prop in $selectedRuntimeProfile.env.PSObject.Properties) {
                $TierConfig[$prop.Name] = [string]$prop.Value
            }
        }
    } elseif ($selected.llama_server_image) {
        $TierConfig["LlamaServerImage"] = $selected.llama_server_image
    }
    $TierConfig["RecommendationSource"] = if ($selectedRuntimeProfile) { "catalog_runtime_profile_pre_download" } else { "catalog_fit_pre_download" }
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

# Map a tier name to its LLM_MODEL value (used by ods model swap)
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
        "^SH_LARGE$"             { return "qwen3.6-35b-a3b" }
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
$script:BOOTSTRAP_GGUF_SHA256  = "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223"
$script:BOOTSTRAP_LLM_MODEL    = "qwen3.5-2b"
# Hermes requires at least a 64K context window. Keep the fast-start model at
# that floor so Hermes works during the first-run bootstrap experience too.
$script:BOOTSTRAP_MAX_CONTEXT   = 65536

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
        [bool]$NoBootstrap = $false,
        [string]$ModelsDirOverride
    )
    if ($NoBootstrap)  { return $false }
    if ($CloudMode)    { return $false }
    if ($OfflineMode)  { return $false }
    if ((Get-TierRank $Tier) -le 0) { return $false }
    $modelsDir = Get-ODSModelsDir -InstallDir $InstallDir -ModelsDirOverride $ModelsDirOverride
    $modelPath = Join-Path $modelsDir $GgufFile
    if (Test-Path $modelPath) { return $false }
    return $true
}
