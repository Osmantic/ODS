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
