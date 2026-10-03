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

$script:CATALOG_SELECTOR_POLICY = "context-aware-curated-fit-v2"
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
                LlmModel   = "anthropic/claude-sonnet-4-6"
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
                LlmModel   = "qwen3.6-35b-a3b"
                GgufFile   = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GgufSha256 = "ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61"
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
                LlmModel   = "qwen3.5-27b"
                GgufFile   = "Qwen3.5-27B-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/Qwen3.5-27B-Q4_K_M.gguf"
                GgufSha256 = "84b5f7f112156d63836a01a69dc3f11a6ba63b10a23b8ca7a7efaf52d5a2d806"
                MaxContext = 65536
                ModelProfileRequested = "qwen"
                ModelProfileEffective = "qwen"
                LlamaServerImage = ""
                LlamaCppReleaseTag = ""
            }
        }
        "4" {
            return @{
                TierName   = "Enterprise"
                LlmModel   = "qwen3.6-35b-a3b"
                GgufFile   = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GgufSha256 = "ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61"
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
    $runtimeImage = "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014@sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f"
    $runtimeTag = "b9014"

    switch ($Tier) {
        "CLOUD" {
            return @{
                TierName   = "Cloud (API)"
                LlmModel   = "anthropic/claude-sonnet-4-6"
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
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/c1ac76e99d5513b141e8adde7288b85c3f9c32ec/gemma-4-31B-it-Q4_K_M.gguf"
                GgufSha256 = "38bd64c852c4b460434cc7162fa9bdcf242faf86502581a754cb72956bb17f84"
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
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/c1ac76e99d5513b141e8adde7288b85c3f9c32ec/gemma-4-31B-it-Q4_K_M.gguf"
                GgufSha256 = "38bd64c852c4b460434cc7162fa9bdcf242faf86502581a754cb72956bb17f84"
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
                GgufFile   = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/c099eb48e663fd284577b04978a94ffccb261841/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
                GgufSha256 = "f2c28b3dc4776931ac6f879e11f203dec637ea0f14267a86ec8f6165f63f293f"
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
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf"
                GgufSha256 = "740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8"
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
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/bfc15c382204943c3a8fff0c750b94ae2364d7a3/gemma-4-E4B-it-Q4_K_M.gguf"
                GgufSha256 = "85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87"
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
                GgufFile   = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/c099eb48e663fd284577b04978a94ffccb261841/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
                GgufSha256 = "f2c28b3dc4776931ac6f879e11f203dec637ea0f14267a86ec8f6165f63f293f"
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
                GgufUrl    = "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/c1ac76e99d5513b141e8adde7288b85c3f9c32ec/gemma-4-31B-it-Q4_K_M.gguf"
                GgufSha256 = "38bd64c852c4b460434cc7162fa9bdcf242faf86502581a754cb72956bb17f84"
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

    $memoryClass = Get-CatalogMemoryClass -GpuInfo $GpuInfo
    if ($memoryClass -eq "unified") {
        return @{
            CapacityGB = [Math]::Max([double]$SystemRamGB * 0.55, 2.0)
            Label = "unified system memory"
            MemoryClass = $memoryClass
        }
    }
    if ($memoryClass -eq "cpu") {
        return @{
            CapacityGB = [Math]::Min([Math]::Max([double]$SystemRamGB * 0.35, 3.0), 8.0)
            Label = "system RAM"
            MemoryClass = $memoryClass
        }
    }
    return @{
        CapacityGB = ([double]$GpuInfo.VramMB / 1024.0)
        Label = "GPU VRAM"
        MemoryClass = $memoryClass
    }
}

# ---------------------------------------------------------------------------
# Shared selection policy: mirrors extensions/services/dashboard-api/
# model_memory.py (estimate) and model_selection.py (ranking). Keep the two in
# step; tests/test-windows-catalog-selector.ps1 checks parity against
# tests/fixtures/model-selection-golden.json.
# ---------------------------------------------------------------------------

$script:KV_CACHE_BYTES_PER_ELEMENT = @{
    "f32" = 4.0; "f16" = 2.0; "bf16" = 2.0; "q8_0" = (34.0 / 32.0); "q5_1" = (24.0 / 32.0)
    "q5_0" = (22.0 / 32.0); "q4_1" = (20.0 / 32.0); "q4_0" = (18.0 / 32.0); "iq4_nl" = (18.0 / 32.0)
}
$script:OVERHEAD_BASE_GIB = 0.35
$script:OVERHEAD_PER_WEIGHT_GIB = 0.015
$script:LLAMA_DEFAULT_CTX_CHECKPOINTS = 32
$script:DISCRETE_FIT_MARGIN_MIN_GIB = 0.25
$script:DISCRETE_FIT_MARGIN_FRACTION = 0.03
$script:LEGACY_FIT_TOLERANCE_GIB = 0.25
$script:DEFAULT_SELECTION_PRIORITY = 10
$script:HERMES_MIN_CONTEXT = 65536

function ConvertTo-CatalogKey {
    param([object]$Value)
    return (("$Value".ToLowerInvariant()) -replace "[^a-z0-9]+", "-").Trim("-")
}

function ConvertTo-CatalogBackend {
    param([object]$Backend)
    $key = ConvertTo-CatalogKey $Backend
    if ($key -in @("", "cpu", "none", "unknown")) { return "cpu" }
    return $key
}

function Get-CatalogMemoryClass {
    param([hashtable]$GpuInfo)
    $backend = ConvertTo-CatalogBackend $GpuInfo.Backend
    if ($backend -eq "apple" -or (ConvertTo-CatalogKey $GpuInfo.MemoryType) -eq "unified") { return "unified" }
    if ($backend -eq "cpu" -or [double]$GpuInfo.VramMB -le 0) { return "cpu" }
    return "discrete"
}

function Get-CatalogPositiveNumber {
    param([object]$Value)
    if ($null -eq $Value -or $Value -is [bool] -or $Value -is [array]) { return 0.0 }
    try { $number = [double]$Value } catch { return 0.0 }
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number) -or $number -le 0) { return 0.0 }
    return $number
}

function Get-CatalogProperty {
    param([object]$Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $value = $null
    if ($Object -is [hashtable]) {
        $value = $Object[$Name]
    } else {
        $prop = $Object.PSObject.Properties[$Name]
        if ($prop) { $value = $prop.Value }
    }
    # Keep arrays whole (a per-layer KV-head list must not be unrolled).
    if ($value -is [array]) { return ,$value }
    return $value
}

function Format-CatalogGiB {
    param([double]$Value)
    return [string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "{0:F2}", $Value)
}

function Get-CatalogKvDimensions {
    param([object]$Model)
    $headCount = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "attention_head_count")
    if ($headCount -le 0) { $headCount = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "head_count") }
    $headDimension = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "attention_head_dimension")
    if ($headDimension -le 0) { $headDimension = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "head_dimension") }
    $embedding = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "embedding_length")
    $derived = if ($embedding -gt 0 -and $headCount -gt 0) { $embedding / $headCount } else { 0.0 }
    $key = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "attention_key_length")
    $value = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "attention_value_length")
    if ($key -le 0) { $key = if ($headDimension -gt 0) { $headDimension } else { $derived } }
    if ($value -le 0) { $value = if ($headDimension -gt 0) { $headDimension } else { $derived } }
    return @($key, $value)
}

function Get-CatalogRawKvHeads {
    param([object]$Model)
    $raw = Get-CatalogProperty $Model "attention_head_count_kv"
    if ($null -eq $raw -or ($raw -isnot [array] -and (Get-CatalogPositiveNumber $raw) -le 0)) {
        $fallback = Get-CatalogProperty $Model "head_count_kv"
        if ($null -ne $fallback) { $raw = $fallback }
    }
    if ($raw -is [array]) { return ,$raw }
    return $raw
}

function Get-CatalogCompletePerLayerKvHeads {
    param([object]$Model)
    $raw = Get-CatalogRawKvHeads -Model $Model
    if ($raw -isnot [array]) { return $null }
    $blocks = [int](Get-CatalogPositiveNumber (Get-CatalogProperty $Model "block_count"))
    if ($blocks -le 0 -or $raw.Count -ne $blocks) { return $null }
    return ,@($raw | ForEach-Object { Get-CatalogPositiveNumber $_ })
}

function Get-CatalogKvLayerCount {
    param([object]$Model)
    $blocks = [int](Get-CatalogPositiveNumber (Get-CatalogProperty $Model "block_count"))
    if ($blocks -le 0) { return $null }
    $explicit = [int](Get-CatalogPositiveNumber (Get-CatalogProperty $Model "attention_layer_count"))
    if ($explicit -gt 0) { return [Math]::Min($explicit, $blocks) }
    $perLayer = Get-CatalogCompletePerLayerKvHeads -Model $Model
    if ($null -ne $perLayer) { return @($perLayer | Where-Object { $_ -gt 0 }).Count }
    if ((Get-CatalogRawKvHeads -Model $Model) -is [array]) { return $null }
    $interval = [int](Get-CatalogPositiveNumber (Get-CatalogProperty $Model "full_attention_interval"))
    if ($interval -gt 1) { return [int][Math]::Floor($blocks / $interval) }
    return $blocks
}

function Get-CatalogCacheElementBytes {
    param([object]$CacheType)
    $key = "$CacheType".Trim().ToLowerInvariant()
    if (-not $key) { $key = "f16" }
    if ($script:KV_CACHE_BYTES_PER_ELEMENT.ContainsKey($key)) { return [double]$script:KV_CACHE_BYTES_PER_ELEMENT[$key] }
    return 2.0
}

function Get-CatalogKvBytesPerToken {
    param([object]$Model, [string]$CacheTypeK = "f16", [string]$CacheTypeV = "f16")
    $perLayer = Get-CatalogCompletePerLayerKvHeads -Model $Model
    $headLayers = 0.0
    if ($null -ne $perLayer) {
        $headLayers = [double](($perLayer | Measure-Object -Sum).Sum)
    } elseif ((Get-CatalogRawKvHeads -Model $Model) -isnot [array]) {
        $heads = Get-CatalogPositiveNumber (Get-CatalogRawKvHeads -Model $Model)
        $layers = Get-CatalogKvLayerCount -Model $Model
        if ($heads -gt 0 -and $layers) { $headLayers = $heads * [double]$layers }
    }
    $dims = Get-CatalogKvDimensions -Model $Model
    if ($headLayers -le 0 -or $dims[0] -le 0 -or $dims[1] -le 0) { return $null }
    return $headLayers * ($dims[0] * (Get-CatalogCacheElementBytes $CacheTypeK) + $dims[1] * (Get-CatalogCacheElementBytes $CacheTypeV))
}

function Get-CatalogWeightsBytes {
    param([object]$Model)
    $sizeBytes = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "size_bytes")
    if ($sizeBytes -gt 0) { return $sizeBytes }
    return (Get-CatalogPositiveNumber (Get-CatalogProperty $Model "size_mb")) * 1MB
}

function Test-CatalogArchitectureMetadata {
    param([object]$Model)
    if ($null -eq (Get-CatalogKvBytesPerToken -Model $Model)) { return $false }
    $state = Get-CatalogProperty $Model "recurrent_state_bytes"
    if ($null -eq $state -or $state -is [bool]) { return $false }
    try { if ([double]$state -lt 0) { return $false } } catch { return $false }
    return ((Get-CatalogWeightsBytes -Model $Model) -gt 0)
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
        [object]$RuntimeProfile = $null,
        [int]$ContextLength = 0
    )

    $context = if ($ContextLength -gt 0) { $ContextLength } elseif ($RuntimeProfile -and $RuntimeProfile.context_length) { [int]$RuntimeProfile.context_length } else { [int]$Model.context_length }
    $context = [Math]::Max($context, 8192)
    $perToken = Get-CatalogKvBytesPerToken -Model $Model
    if ($null -ne $perToken) {
        $elementBytes = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "kv_cache_element_bytes")
        if ($elementBytes -le 0) { $elementBytes = 2.0 }
        return [Math]::Round(($perToken * ($elementBytes / 2.0) * $context / 1GB), 2)
    }
    $paramsB = Get-CatalogModelEstimatedParamBillions -Model $Model
    $kvPer32kGb = [Math]::Min([Math]::Max(($paramsB * 0.12), 0.35), 3.5)
    return [Math]::Round(($kvPer32kGb * ([double]$context / 32768.0)), 2)
}

function Get-CatalogProfileCacheSettings {
    param([object]$RuntimeProfile)
    $env = if ($RuntimeProfile) { Get-CatalogProperty $RuntimeProfile "env" } else { $null }
    $k = Get-CatalogProperty $env "LLAMA_ARG_CACHE_TYPE_K"
    $v = Get-CatalogProperty $env "LLAMA_ARG_CACHE_TYPE_V"
    $checkpoints = $null
    $raw = Get-CatalogProperty $env "LLAMA_ARG_CTX_CHECKPOINTS"
    if ($null -ne $raw) { try { $checkpoints = [int]"$raw" } catch { $checkpoints = $null } }
    $parallel = 1
    $rawParallel = Get-CatalogProperty $env "LLAMA_PARALLEL"
    if ($null -ne $rawParallel) { try { $parallel = [Math]::Max([int]"$rawParallel", 1) } catch { $parallel = 1 } }
    return @{
        CacheTypeK = if ($k) { "$k" } else { "f16" }
        CacheTypeV = if ($v) { "$v" } else { "f16" }
        CtxCheckpoints = $checkpoints
        Parallel = $parallel
    }
}

function Get-CatalogMemoryEstimate {
    param(
        [object]$Model,
        [int]$ContextLength = 0,
        [string]$CacheTypeK = "f16",
        [string]$CacheTypeV = "f16",
        [int]$Parallel = 1,
        [object]$CtxCheckpoints = $null
    )

    $context = if ($ContextLength -gt 0) { $ContextLength } else { [int]$Model.context_length }
    $context = [Math]::Max($context, 8192)
    if (Test-CatalogArchitectureMetadata -Model $Model) {
        $perToken = Get-CatalogKvBytesPerToken -Model $Model -CacheTypeK $CacheTypeK -CacheTypeV $CacheTypeV
        $weights = (Get-CatalogWeightsBytes -Model $Model) / 1GB
        $kv = $perToken * $context / 1GB
        $stateBytes = [double](Get-CatalogProperty $Model "recurrent_state_bytes")
        $sequences = [Math]::Max($Parallel, 1)
        $recurrent = $stateBytes * $sequences / 1GB
        $overhead = $script:OVERHEAD_BASE_GIB + $script:OVERHEAD_PER_WEIGHT_GIB * $weights
        $checkpoints = if ($null -eq $CtxCheckpoints) { $script:LLAMA_DEFAULT_CTX_CHECKPOINTS } else { [Math]::Max([int]$CtxCheckpoints, 0) }
        $hostState = $checkpoints * $stateBytes * $sequences / 1GB
        $device = $weights + $kv + $recurrent + $overhead
        return @{
            ContextLength = $context
            Method = "architecture"
            WeightsGiB = [Math]::Round($weights, 3)
            KvGiB = [Math]::Round($kv, 3)
            RecurrentStateGiB = [Math]::Round($recurrent, 3)
            OverheadGiB = [Math]::Round($overhead, 3)
            HostCheckpointGiB = [Math]::Round($hostState, 3)
            DeviceGiB = [Math]::Round($device, 2)
            TotalGiB = [Math]::Round($device + $hostState, 2)
        }
    }

    $kvGb = Get-CatalogModelEstimatedContextKvGB -Model $Model -ContextLength $context
    $sizeMb = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "size_mb")
    $weightsGb = $sizeMb / 1024.0
    $sizeAndKv = if ($sizeMb -gt 0) { $weightsGb + $kvGb } else { 0.0 }
    $declared = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "vram_required_gb")
    $device = [Math]::Round([Math]::Max($declared, $sizeAndKv), 2)
    return @{
        ContextLength = $context
        Method = "legacy-heuristic"
        WeightsGiB = [Math]::Round($weightsGb, 3)
        KvGiB = $kvGb
        RecurrentStateGiB = 0.0
        OverheadGiB = 0.0
        HostCheckpointGiB = 0.0
        DeviceGiB = $device
        TotalGiB = $device
    }
}

function Get-CatalogRuntimeEstimate {
    param([object]$Model, [object]$RuntimeProfile = $null, [int]$ContextLength = 0)
    $context = $ContextLength
    if ($context -le 0 -and $RuntimeProfile -and $RuntimeProfile.context_length) { $context = [int]$RuntimeProfile.context_length }
    $settings = Get-CatalogProfileCacheSettings -RuntimeProfile $RuntimeProfile
    return Get-CatalogMemoryEstimate -Model $Model -ContextLength $context -CacheTypeK $settings.CacheTypeK -CacheTypeV $settings.CacheTypeV -Parallel $settings.Parallel -CtxCheckpoints $settings.CtxCheckpoints
}

function Get-CatalogModelSelectorRequiredGB {
    param(
        [object]$Model,
        [object]$RuntimeProfile = $null,
        [int]$ContextLength = 0,
        [switch]$IncludeHostState
    )

    if ($RuntimeProfile -and $null -ne $RuntimeProfile.estimated_required_gb) {
        $authored = Get-CatalogPositiveNumber $RuntimeProfile.estimated_required_gb
        if ($authored -gt 0) { return [Math]::Round($authored, 2) }
    }
    $estimate = Get-CatalogRuntimeEstimate -Model $Model -RuntimeProfile $RuntimeProfile -ContextLength $ContextLength
    if ($IncludeHostState) { return $estimate.TotalGiB }
    return $estimate.DeviceGiB
}

function Get-CatalogFitMarginGB {
    param([double]$CapacityGB, [string]$MemoryClass)
    if ($MemoryClass -ne "discrete") { return 0.0 }
    return [Math]::Round([Math]::Max($script:DISCRETE_FIT_MARGIN_MIN_GIB, $script:DISCRETE_FIT_MARGIN_FRACTION * $CapacityGB), 2)
}

function Test-CatalogMemoryFits {
    param([double]$RequiredGB, [double]$CapacityGB, [string]$MemoryClass, [bool]$ArchitectureEstimate)
    if ($ArchitectureEstimate) {
        return ($RequiredGB -le ($CapacityGB - (Get-CatalogFitMarginGB -CapacityGB $CapacityGB -MemoryClass $MemoryClass) + 1e-9))
    }
    return ($RequiredGB -le ($CapacityGB + $script:LEGACY_FIT_TOLERANCE_GIB))
}

function Test-CatalogModelContextFit {
    <#
    .SYNOPSIS
        Would the configured model fit at ContextLength on this hardware?
    .DESCRIPTION
        Mirrors model_selection.check_fit (select-model.py --check-fit), used by
        the Hermes floor re-check in phases/03-features.ps1. Returns $true or
        $false, or $null when unknown (selector disabled, no catalog, or a
        model outside the catalog), in which case the caller raises as before.
    #>
    param(
        [hashtable]$TierConfig,
        [hashtable]$GpuInfo,
        [int]$SystemRamGB,
        [string]$SourceRoot,
        [int]$ContextLength
    )

    if ($env:ODS_DISABLE_CATALOG_MODEL_SELECTOR -eq "true") { return $null }
    $catalogPath = Join-Path $SourceRoot "config\model-library.json"
    if (-not (Test-Path $catalogPath)) { return $null }
    try {
        $catalog = Get-Content $catalogPath -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
    $model = $null
    foreach ($entry in $catalog.models) {
        if (($TierConfig.GgufFile -and "$($entry.gguf_file)" -eq "$($TierConfig.GgufFile)") -or
            (-not $TierConfig.GgufFile -and $TierConfig.LlmModel -and "$($entry.llm_model_name)" -eq "$($TierConfig.LlmModel)")) {
            $model = $entry
            break
        }
    }
    if (-not $model) { return $null }
    # Above the declared native maximum the raise can never be served
    # (llama.cpp caps the slot at the training context), whatever the memory.
    if ($model.PSObject.Properties["max_context_length"] -and $model.max_context_length -and
        $ContextLength -gt [int]$model.max_context_length) {
        return $false
    }
    $runtimeProfile = $null
    if ($TierConfig.RuntimeProfile) {
        $runtimeProfile = @($model.runtime_profiles | Where-Object { $_.id -eq $TierConfig.RuntimeProfile }) | Select-Object -First 1
    }
    $memory = Get-CatalogModelSelectorMemory -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB
    $estimate = Get-CatalogRuntimeEstimate -Model $model -RuntimeProfile $runtimeProfile -ContextLength $ContextLength
    # A profile's measured budget applies only at the profile's own context.
    $authored = 0.0
    if ($runtimeProfile -and $runtimeProfile.context_length -and [int]$runtimeProfile.context_length -eq $ContextLength -and
        $null -ne $runtimeProfile.estimated_required_gb) {
        $authored = Get-CatalogPositiveNumber $runtimeProfile.estimated_required_gb
    }
    $required = if ($authored -gt 0) { [Math]::Round($authored, 2) } elseif ($memory.MemoryClass -eq "cpu") { $estimate.TotalGiB } else { $estimate.DeviceGiB }
    $architecture = ($authored -le 0 -and $estimate.Method -eq "architecture")
    return [bool](Test-CatalogMemoryFits -RequiredGB $required -CapacityGB $memory.CapacityGB -MemoryClass $memory.MemoryClass -ArchitectureEstimate $architecture)
}

function Get-CatalogRuntimeProfile {
    param(
        [object]$Model,
        [hashtable]$GpuInfo,
        [int]$SystemRamGB,
        [switch]$IgnoreRamMinimum
    )

    if (-not $Model.PSObject.Properties["runtime_profiles"]) { return $null }
    $profiles = @($Model.runtime_profiles)
    if ($profiles.Count -eq 0) { return $null }

    $backend = ConvertTo-CatalogBackend $GpuInfo.Backend
    $memoryType = "$($GpuInfo.MemoryType)".ToLowerInvariant()
    if (-not $memoryType) { $memoryType = "discrete" }
    $hostArch = Normalize-HostArchitecture -HostArchitecture $env:HOST_ARCH
    if ($hostArch -eq "unknown") { $hostArch = Get-HostArchitecture }
    $vramGB = [double]$GpuInfo.VramMB / 1024.0

    foreach ($runtimeProfile in $profiles) {
        if (-not $runtimeProfile) { continue }
        if ($runtimeProfile.backend -and (ConvertTo-CatalogBackend $runtimeProfile.backend) -ne $backend) { continue }
        if ($runtimeProfile.host_arch) {
            $arches = @($runtimeProfile.host_arch | ForEach-Object { Normalize-HostArchitecture -HostArchitecture $_ })
            if ($arches.Count -gt 0 -and $arches -notcontains $hostArch) { continue }
        }
        if ($runtimeProfile.memory_type -and "$($runtimeProfile.memory_type)".ToLowerInvariant() -ne $memoryType) { continue }
        try {
            if ($null -ne $runtimeProfile.vram_min_gb -and $vramGB -lt [double]$runtimeProfile.vram_min_gb) { continue }
            if ($null -ne $runtimeProfile.vram_max_gb -and $vramGB -gt [double]$runtimeProfile.vram_max_gb) { continue }
            if (-not $IgnoreRamMinimum -and $null -ne $runtimeProfile.system_ram_min_gb -and [double]$SystemRamGB -lt [double]$runtimeProfile.system_ram_min_gb) { continue }
            if ($null -ne $runtimeProfile.system_ram_max_gb -and [double]$SystemRamGB -gt [double]$runtimeProfile.system_ram_max_gb) { continue }
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

function Get-CatalogSelectionPriority {
    param([object]$Model, [string]$MemoryClass)
    $selection = Get-CatalogProperty $Model "selection"
    if ($null -eq $selection) { return $script:DEFAULT_SELECTION_PRIORITY }
    $names = @($selection.PSObject.Properties | ForEach-Object { $_.Name })
    if ($names.Count -eq 0) { return $script:DEFAULT_SELECTION_PRIORITY }
    $value = Get-CatalogProperty $selection $MemoryClass
    if ($null -eq $value) { return $script:DEFAULT_SELECTION_PRIORITY }
    try { return [int]$value } catch { return $script:DEFAULT_SELECTION_PRIORITY }
}

function Get-CatalogMinimumCapacity {
    param([object]$Model, [string]$MemoryClass)
    $selection = Get-CatalogProperty $Model "selection"
    $floors = Get-CatalogProperty $selection "min_capacity_gib"
    $value = Get-CatalogProperty $floors $MemoryClass
    return (Get-CatalogPositiveNumber $value)
}

function Get-CatalogEvidenceAdjustment {
    param([object]$Model)
    $compatibility = Get-CatalogProperty $Model "app_compatibility"
    $pixel = ConvertTo-CatalogKey (Get-CatalogProperty (Get-CatalogProperty $compatibility "pixel_agent") "status")
    $agent = ConvertTo-CatalogKey (Get-CatalogProperty (Get-CatalogProperty $compatibility "agent_viability") "status")
    $negative = @("not-agent-viable", "unsupported-until-revalidated", "blocked")
    $adjustment = 0
    if ($pixel -in @("verified", "pixel-agent-viable")) { $adjustment += 5 }
    if ($pixel -in $negative -or $agent -in $negative) { $adjustment -= 5 }
    return $adjustment
}

function Get-CatalogContextCandidates {
    param([object]$Model, [int]$MinContext = 0)
    $default = [int]$Model.context_length
    if ((Get-CatalogPositiveNumber (Get-CatalogProperty $Model "block_count")) -le 0) { return @($default) }
    $native = $default
    $max = Get-CatalogProperty $Model "max_context_length"
    if ($max) { $native = [int]$max }
    $target = $default
    if ($MinContext -gt 0 -and $default -lt $MinContext -and $MinContext -le [Math]::Max($native, $default)) { $target = $MinContext }
    if ($target -le 8192) { return @($target) }
    return @(@($target, 8192, 16384, 32768, 65536, 131072, 262144) | Where-Object { $_ -le $target } | Sort-Object -Descending -Unique)
}

function Get-CatalogModelCandidate {
    param(
        [object]$Model,
        [hashtable]$GpuInfo,
        [int]$SystemRamGB,
        [double]$CapacityGB,
        [string]$MemoryClass,
        [int]$MinContext = 0,
        [int]$Priority = -1
    )

    $runtimeProfile = Get-CatalogRuntimeProfile -Model $Model -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB
    if (-not $runtimeProfile -and (Get-CatalogRuntimeProfile -Model $Model -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB -IgnoreRamMinimum)) { return $null }
    if ($Priority -lt 0) { $Priority = Get-CatalogSelectionPriority -Model $Model -MemoryClass $MemoryClass }
    $evidence = Get-CatalogEvidenceAdjustment -Model $Model
    $includeHost = ($MemoryClass -eq "cpu")

    $contexts = @()
    if ($runtimeProfile) {
        $contexts = @($(if ($runtimeProfile.context_length) { [int]$runtimeProfile.context_length } else { [int]$Model.context_length }))
    } else {
        $all = @(Get-CatalogContextCandidates -Model $Model -MinContext $MinContext)
        $contexts = @($all | Where-Object { $_ -ge $MinContext }) + @($all | Where-Object { $_ -lt $MinContext })
    }
    foreach ($context in $contexts) {
        $estimate = Get-CatalogRuntimeEstimate -Model $Model -RuntimeProfile $runtimeProfile -ContextLength $context
        $authored = 0.0
        if ($runtimeProfile -and $null -ne $runtimeProfile.estimated_required_gb) { $authored = Get-CatalogPositiveNumber $runtimeProfile.estimated_required_gb }
        $required = if ($authored -gt 0) { [Math]::Round($authored, 2) } elseif ($includeHost) { $estimate.TotalGiB } else { $estimate.DeviceGiB }
        $architecture = ($authored -le 0 -and $estimate.Method -eq "architecture")
        if (Test-CatalogMemoryFits -RequiredGB $required -CapacityGB $CapacityGB -MemoryClass $MemoryClass -ArchitectureEstimate $architecture) {
            $margin = if ($architecture) { Get-CatalogFitMarginGB -CapacityGB $CapacityGB -MemoryClass $MemoryClass } else { -$script:LEGACY_FIT_TOLERANCE_GIB }
            $contextCredit = [Math]::Min($context, 262144) / 65536.0
            if (($required / [Math]::Max($CapacityGB, 1.0)) -gt 0.95) { $contextCredit -= 0.25 }
            $sizeBytes = Get-CatalogPositiveNumber (Get-CatalogProperty $Model "size_bytes")
            $weightsGib = if ($sizeBytes -gt 0) { $sizeBytes / 1GB } else { (Get-CatalogPositiveNumber $Model.size_mb) / 1024.0 }
            return [pscustomobject]@{
                Model = $Model
                RuntimeProfile = $runtimeProfile
                ContextLength = [int]$context
                RequiredGB = [double]$required
                Estimate = $estimate
                ArchitectureEstimate = $architecture
                Authored = ($authored -gt 0)
                MeetsMinContext = ($MinContext -le 0 -or $context -ge $MinContext)
                MarginGB = $margin
                Priority = $Priority
                Evidence = $evidence
                Score = $Priority + $evidence
                ContextCredit = [Math]::Round($contextCredit, 6)
                WeightsGiB = [Math]::Round($weightsGib, 6)
            }
        }
    }
    return $null
}

function Get-CatalogRankedCandidates {
    param(
        [object]$Catalog,
        [hashtable]$GpuInfo,
        [int]$SystemRamGB,
        [double]$CapacityGB,
        [string]$MemoryClass,
        [string]$ModelProfileName,
        [int]$MinContext = 0,
        [switch]$RequireMinContext
    )

    $candidates = @()
    foreach ($model in $Catalog.models) {
        if (-not (Test-CatalogModelSourceAllowed -Model $model)) { continue }
        if (-not $model.gguf_url) { continue }
        if (-not (Test-CatalogModelInstallRecommendationAllowed -Model $model)) { continue }
        if (-not (Test-CatalogModelFamilyAllowed -Model $model -ModelProfileName $ModelProfileName)) { continue }
        $priority = Get-CatalogSelectionPriority -Model $model -MemoryClass $MemoryClass
        if ($priority -le 0) { continue }
        if ($CapacityGB -lt (Get-CatalogMinimumCapacity -Model $model -MemoryClass $MemoryClass)) { continue }
        $candidate = Get-CatalogModelCandidate -Model $model -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB -CapacityGB $CapacityGB -MemoryClass $MemoryClass -MinContext $MinContext -Priority $priority
        if (-not $candidate) { continue }
        if ($RequireMinContext -and -not $candidate.MeetsMinContext) { continue }
        $familyMatch = if ($ModelProfileName -eq "gemma4" -and "$($model.family)".ToLowerInvariant() -eq "gemma4") { 1 } else { 0 }
        $candidate | Add-Member -NotePropertyName FamilyMatch -NotePropertyValue $familyMatch
        $candidates += $candidate
    }
    return @($candidates | Sort-Object -Property `
        @{ Expression = { $_.FamilyMatch }; Descending = $true }, `
        @{ Expression = { [int]$_.MeetsMinContext }; Descending = $true }, `
        @{ Expression = { $_.Score }; Descending = $true }, `
        @{ Expression = { $_.ContextCredit }; Descending = $true }, `
        @{ Expression = { $_.WeightsGiB }; Descending = $true })
}

function Get-CatalogRequirementBreakdown {
    param([object]$Candidate)
    if ($Candidate.Authored) { return "measured runtime-profile budget" }
    $estimate = $Candidate.Estimate
    if ($estimate.Method -ne "architecture") { return "catalog estimate including context/KV" }
    $parts = @("weights $(Format-CatalogGiB $estimate.WeightsGiB)", "KV $(Format-CatalogGiB $estimate.KvGiB)")
    if ($estimate.RecurrentStateGiB -gt 0) { $parts += "recurrent state $(Format-CatalogGiB $estimate.RecurrentStateGiB)" }
    $parts += "overhead $(Format-CatalogGiB $estimate.OverheadGiB)"
    return ($parts -join " + ")
}

function Get-CatalogMarginText {
    param([object]$Candidate)
    if ($Candidate.MarginGB -lt 0) { return "within the $([Math]::Abs($Candidate.MarginGB)) GiB catalog tolerance" }
    if ($Candidate.MarginGB -gt 0) { return "leaving at least $($Candidate.MarginGB) GiB free" }
    return "within budget"
}

function Set-CatalogTierConfigFromCandidate {
    param([hashtable]$TierConfig, [object]$Candidate)
    $selected = $Candidate.Model
    $TierConfig["LlmModel"] = $selected.llm_model_name
    $TierConfig["GgufFile"] = $selected.gguf_file
    $TierConfig["GgufUrl"] = $selected.gguf_url
    $TierConfig["GgufSha256"] = $selected.gguf_sha256
    $TierConfig["MaxContext"] = [int]$Candidate.ContextLength
    $TierConfig["ModelSizeMB"] = [int][Math]::Round([double]$selected.size_mb)
    foreach ($key in @("RuntimeProfile", "RuntimeProfileLabel", "RuntimeProfileSource")) { $TierConfig.Remove($key) }
    $selectedRuntimeProfile = $Candidate.RuntimeProfile
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
}

function Resolve-CatalogModelRecommendation {
    param(
        [hashtable]$TierConfig,
        [string]$Tier,
        [hashtable]$GpuInfo,
        [int]$SystemRamGB,
        [string]$SourceRoot,
        [int]$MinContext = 0,
        [switch]$RequireMinContext
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
    $memoryClass = $memory.MemoryClass
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
    $isSparkAarch64 = ($Tier -eq "NV_ULTRA" -and $modelProfileName -eq "qwen" -and $hostArchName -eq "arm64")

    if ($isSparkAarch64 -or $isAmdUnifiedStrixLarge) {
        if ($isSparkAarch64) {
            $archPolicy = $script:SPARK_AARCH64_POLICY
            $archModel = Get-CatalogModelById -Catalog $catalog -ModelId $script:SPARK_AARCH64_MODEL_ID
        } else {
            $archPolicy = $script:UNIFIED_MEMORY_POLICY
            $archModel = Get-CatalogModelById -Catalog $catalog -ModelId $script:UNIFIED_MEMORY_MODEL_ID
        }
        $archCandidate = $null
        if ($archModel -and $archModel.gguf_url -and (Test-CatalogModelInstallRecommendationAllowed -Model $archModel)) {
            $archCandidate = Get-CatalogModelCandidate -Model $archModel -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB -CapacityGB $capacityGb -MemoryClass $memoryClass -MinContext $MinContext
            if ($archCandidate -and $RequireMinContext -and -not $archCandidate.MeetsMinContext) { $archCandidate = $null }
        }
        if ($archCandidate) {
            $contextK = [int]($archCandidate.ContextLength / 1024)
            $breakdown = Get-CatalogRequirementBreakdown -Candidate $archCandidate
            if ($isSparkAarch64) {
                $rationale = "is selected for arm64 NV_ULTRA Spark-class NVIDIA hosts because qwen3-coder-next is excluded on this architecture by the tier map"
            } else {
                $rationale = "is selected for AMD unified-memory SH_LARGE hosts because Qwen3.6-35B-A3B is the fleet-proven Windows Lemonade target. Dense 70B and Coder Next defaults are avoided for first-run recovery"
            }
            $reason = "Arch-aware catalog policy ($archPolicy): $($archModel.name) $rationale. It needs about $($archCandidate.RequiredGB) GiB ($breakdown), fits $([Math]::Round($capacityGb, 1)) GiB $($memory.Label), and gives ${contextK}K context. Throughput requires a local benchmark after first launch."
            Set-CatalogTierConfigFromCandidate -TierConfig $TierConfig -Candidate $archCandidate
            $TierConfig["RecommendationSource"] = "catalog_arch_policy_pre_download"
            $TierConfig["RecommendationPolicy"] = "$script:CATALOG_SELECTOR_POLICY+$archPolicy"
            $TierConfig["RecommendationConfidence"] = "high"
            $TierConfig["RecommendationReason"] = $reason
            $TierConfig["RecommendationAlternatives"] = "$($archModel.id):$($archCandidate.ContextLength):$([double]$archCandidate.RequiredGB)"
            return $TierConfig
        }
    }

    $ranked = @(Get-CatalogRankedCandidates -Catalog $catalog -GpuInfo $GpuInfo -SystemRamGB $SystemRamGB -CapacityGB $capacityGb -MemoryClass $memoryClass -ModelProfileName $modelProfileName -MinContext $MinContext -RequireMinContext:$RequireMinContext)
    if ($ranked.Count -eq 0) {
        if ($RequireMinContext -and $MinContext -gt 0) {
            throw "No catalog model fits the detected memory at $MinContext context. Choose a smaller model profile or use cloud mode; refusing an unsafe tier-map fallback."
        }
        throw "No catalog model fits the detected memory and selected profile. Choose a smaller model profile or use cloud mode; refusing an unsafe tier-map fallback."
    }

    $top = $ranked[0]
    $selected = $top.Model
    $alternatives = @($ranked | Select-Object -First 3 | ForEach-Object {
        "$($_.Model.id):$($_.ContextLength):$([double]$_.RequiredGB)"
    }) -join ";"
    $confidence = if ($capacityGb -gt 0 -and $GpuInfo.Backend -and $GpuInfo.Backend -ne "unknown") { "high" } else { "medium" }
    $contextK = [int]($top.ContextLength / 1024)
    $breakdown = Get-CatalogRequirementBreakdown -Candidate $top
    $marginText = Get-CatalogMarginText -Candidate $top
    if ($top.RuntimeProfile) {
        $ramNote = if ($null -ne $top.RuntimeProfile.system_ram_min_gb) { " plus $($top.RuntimeProfile.system_ram_min_gb)GB system RAM" } else { "" }
        $runtimeName = if ($top.RuntimeProfile.runtime) { $top.RuntimeProfile.runtime } else { "llama.cpp" }
        $reason = "Curated runtime fit ($script:CATALOG_SELECTOR_POLICY): $($selected.name) is the highest-priority installable model for $memoryClass memory; it uses $($top.RuntimeProfile.label) via $runtimeName, needs about $($top.RequiredGB) GiB ($breakdown)$ramNote, fits $([Math]::Round($capacityGb, 1)) GiB $($memory.Label) on $($GpuInfo.Backend) ($marginText), and gives ${contextK}K context. Throughput still requires a local benchmark after first launch."
    } else {
        $reason = "Curated fit ($script:CATALOG_SELECTOR_POLICY): $($selected.name) is the highest-priority installable model for $memoryClass memory that fits $([Math]::Round($capacityGb, 1)) GiB $($memory.Label) on $($GpuInfo.Backend) ($marginText) at ${contextK}K context; it needs about $($top.RequiredGB) GiB ($breakdown). Throughput requires a local benchmark after first launch."
    }
    if ($MinContext -gt 0 -and -not $top.MeetsMinContext) {
        $reason += " No installable model fits this hardware at the $MinContext context floor; this is the largest context that fits."
    }

    Set-CatalogTierConfigFromCandidate -TierConfig $TierConfig -Candidate $top
    $TierConfig["RecommendationSource"] = if ($top.RuntimeProfile) { "catalog_runtime_profile_pre_download" } else { "catalog_fit_pre_download" }
    $TierConfig["RecommendationPolicy"] = $script:CATALOG_SELECTOR_POLICY
    $TierConfig["RecommendationConfidence"] = $confidence
    $TierConfig["RecommendationReason"] = $reason
    $TierConfig["RecommendationAlternatives"] = $alternatives
    return $TierConfig
}

# Hermes needs 64K. Check whether the resolved model still fits at a larger
# context before raising it (installers/windows/phases/03-features.ps1).
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
            "^CLOUD$"                { return "anthropic/claude-sonnet-4-6" }
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
        "^CLOUD$"                { return "anthropic/claude-sonnet-4-6" }
        "^NV_ULTRA$"             { return "qwen3-coder-next" }
        "^SH_LARGE$"             { return "qwen3.6-35b-a3b" }
        "^(SH_COMPACT|SH)$"      { return "qwen3.6-35b-a3b" }
        "^(0|T0)$"               { return "qwen3.5-2b" }
        "^(1|T1)$"               { return "qwen3.5-9b" }
        "^(2|T2)$"               { return "qwen3.5-9b" }
        "^(3|T3)$"               { return "qwen3.5-27b" }
        "^(4|T4)$"               { return "qwen3.6-35b-a3b" }
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
