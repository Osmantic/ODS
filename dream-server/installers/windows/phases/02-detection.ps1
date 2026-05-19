# ============================================================================
# Dream Server Windows Installer -- Phase 02: Hardware Detection
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: GPU detection, system RAM, tier auto-selection, NVIDIA driver check,
#          cloud mode handling, tier-based disk re-check.
#
# Reads:
#   $cloudMode      -- skip GPU detection, force CLOUD tier
#   $tierOverride   -- user-supplied tier string (empty = auto-detect)
#   $force          -- skip non-fatal failures
#   $installDir     -- used for tier-based disk check
#   $preflight_docker -- from phase 01, for GPU passthrough status message
#   $script:MIN_NVIDIA_DRIVER -- minimum NVIDIA driver version
#
# Writes:
#   $gpuInfo         -- hashtable: Backend, Name, VramMB, Count, MemoryType,
#                       DriverVersion, DriverMajor, ComputeCap, IsBlackwell
#   $systemRamGB     -- int: total physical RAM in GB
#   $selectedTier    -- string: resolved tier (0-4, CLOUD, NV_ULTRA, SH_*)
#   $tierConfig      -- hashtable: TierName, LlmModel, GgufFile, GgufUrl,
#                       GgufSha256, MaxContext
#   $llamaServerImage -- string: custom llama-server image override (usually "")
#
# Modder notes:
#   Add new GPU vendors (e.g., Intel Arc) in detection.ps1, not here.
#   Change tier thresholds in tier-map.ps1 ConvertTo-TierFromGpu, not here.
# ============================================================================

Write-Phase -Phase 2 -Total 13 -Name "HARDWARE DETECTION" -Estimate "~10 seconds"
Write-AI "Reading hardware telemetry..."

# ── GPU and RAM detection ─────────────────────────────────────────────────────
$gpuInfo     = Get-GpuInfo
$systemRamGB = Get-SystemRamGB

Write-InfoBox "GPU:"        "$($gpuInfo.Name)"
Write-InfoBox "VRAM:"       "$($gpuInfo.VramMB) MB ($($gpuInfo.MemoryType))"
Write-InfoBox "System RAM:" "$systemRamGB GB"
Write-InfoBox "Backend:"    "$($gpuInfo.Backend)"

# ── NVIDIA-specific checks ────────────────────────────────────────────────────
if ($gpuInfo.Backend -eq "nvidia") {
    Write-InfoBox "Driver:"  "$($gpuInfo.DriverVersion)"
    Write-InfoBox "Compute:" "sm_$($gpuInfo.ComputeCap -replace '\.', '')"

    # Driver minimum: CUDA in Docker Desktop (WSL2) requires >= MIN_NVIDIA_DRIVER
    if ($gpuInfo.DriverMajor -lt $script:MIN_NVIDIA_DRIVER) {
        Write-AIError "NVIDIA driver $($gpuInfo.DriverVersion) is below the required minimum ($($script:MIN_NVIDIA_DRIVER))."
        Write-AI "  Update your driver: https://www.nvidia.com/Download/index.aspx"
        Write-AI "  After updating, re-run this installer."
        if (-not $force) { exit 1 }
        Write-AIWarn "--Force specified, continuing with outdated driver (may fail at inference)."
    } else {
        Write-AISuccess "NVIDIA driver $($gpuInfo.DriverVersion) OK (>= $($script:MIN_NVIDIA_DRIVER) required)"
    }

    # GPU passthrough status (preflight_docker set by phase 01)
    if ($preflight_docker -and $preflight_docker.GpuSupport) {
        Write-AISuccess "Docker GPU passthrough confirmed (WSL2 + NVIDIA driver present)"
    } else {
        Write-AIWarn "Docker GPU passthrough not confirmed. Verify WSL2 backend is enabled."
        Write-AI "  Docker Desktop > Settings > General > Use WSL 2 based engine"
    }

    # Blackwell (sm_120+): works via PTX JIT with the standard CUDA image
    if ($gpuInfo.IsBlackwell) {
        Write-AISuccess "Blackwell GPU (sm_120) detected -- supported via PTX JIT in standard CUDA image"
    }
}

# ── AMD-specific info ─────────────────────────────────────────────────────────
if ($gpuInfo.Backend -eq "amd") {
    if ($gpuInfo.MemoryType -eq "unified") {
        Write-AISuccess "AMD unified memory APU detected (Strix Halo / RDNA)"
        Write-AI "  llama-server will run natively with Vulkan (not in Docker)"
    } else {
        Write-AISuccess "AMD discrete GPU detected"
        Write-AI "  llama-server will use the Docker Vulkan overlay"
    }
}

# ── Tier selection ────────────────────────────────────────────────────────────
if ($cloudMode) {
    $selectedTier = "CLOUD"
} elseif ($tierOverride) {
    $selectedTier = $tierOverride.ToUpper()
    # Normalize T-prefix aliases: T1 -> 1, T2 -> 2, etc.
    if ($selectedTier -match "^T(\d)$") { $selectedTier = $Matches[1] }
} else {
    $selectedTier = ConvertTo-TierFromGpu -GpuInfo $gpuInfo -SystemRamGB $systemRamGB
}

if (-not $env:MODEL_PROFILE) {
    $existingEnvPath = Join-Path $installDir ".env"
    if (Test-Path $existingEnvPath) {
        $existingProfile = Get-Content $existingEnvPath |
            Where-Object { $_ -match '^MODEL_PROFILE=' } |
            Select-Object -First 1
        if ($existingProfile) {
            $env:MODEL_PROFILE = ($existingProfile -split '=', 2)[1].Trim()
        } else {
            $env:MODEL_PROFILE = "qwen"
        }
    } else {
        $env:MODEL_PROFILE = "qwen"
    }
}

$tierConfig = Resolve-TierConfig -Tier $selectedTier
$tierConfig = Resolve-CatalogModelRecommendation `
    -TierConfig $tierConfig `
    -Tier $selectedTier `
    -GpuInfo $gpuInfo `
    -SystemRamGB $systemRamGB `
    -SourceRoot $sourceRoot
$llamaServerImage = if ($tierConfig.LlamaServerImage) { $tierConfig.LlamaServerImage } else { "" }
if ($tierConfig.LlamaCppReleaseTag) {
    $script:LLAMA_CPP_RELEASE_TAG = $tierConfig.LlamaCppReleaseTag
    $script:LLAMA_CPP_VULKAN_ASSET = "llama-$($script:LLAMA_CPP_RELEASE_TAG)-bin-win-vulkan-x64.zip"
    $script:LLAMA_CPP_VULKAN_URL = "https://github.com/ggml-org/llama.cpp/releases/download/$($script:LLAMA_CPP_RELEASE_TAG)/$($script:LLAMA_CPP_VULKAN_ASSET)"
}

Write-Host ""
Write-AISuccess "Selected tier: $selectedTier -- $($tierConfig.TierName)"
Write-InfoBox "  Model:"   "$($tierConfig.LlmModel)"
Write-InfoBox "  GGUF:"    "$(if ($tierConfig.GgufFile) { $tierConfig.GgufFile } else { '(cloud API -- no local model)' })"
Write-InfoBox "  Context:" "$($tierConfig.MaxContext) tokens"

# ── Performance disclosure (informational) ────────────────────────────────────
$_speedEst = if ($selectedTier -eq "CLOUD") { "cloud API" } else { "benchmark after first launch" }
$_usersEst = if ($selectedTier -eq "CLOUD") { "depends on API tier" } else { "measured after local benchmark" }
Write-InfoBox "  Speed:"   "$_speedEst"
Write-InfoBox "  Capacity:" "$_usersEst"

# ── Tier-aware disk re-check ──────────────────────────────────────────────────
# Now that tier is known we can calculate the actual storage needed:
# model file + Docker image layers (~15 GB headroom).
$_modelGB = $(
    if ($tierConfig.ModelSizeMB) { [Math]::Ceiling([double]$tierConfig.ModelSizeMB / 1024.0) }
    elseif ($tierConfig.GgufFile -match "80B|Coder-Next") { 50 }
    elseif ($tierConfig.GgufFile -match "31B") { 22 }
    elseif ($tierConfig.GgufFile -match "30B|26B") { 20 }
    elseif ($tierConfig.GgufFile -match "20b") { 12 }
    elseif ($tierConfig.GgufFile -match "14B") { 12 }
    elseif ($tierConfig.GgufFile -match "E4B")  { 8 }
    elseif ($tierConfig.GgufFile -match "E2B")  { 5 }
    elseif ($tierConfig.GgufFile -match "9B|8B")  {  8 }
    else                                        {  5 }
)
$_neededGB = $_modelGB + 15   # model + Docker images
$_tierDisk = Test-DiskSpace -Path $installDir -RequiredGB $_neededGB
if (-not $_tierDisk.Sufficient) {
    Write-AIWarn "Tier $selectedTier needs ~${_neededGB} GB (${_modelGB} GB model + 15 GB Docker images)."
    Write-AIWarn "Only $($_tierDisk.FreeGB) GB free on $($_tierDisk.Drive)."
    if (-not $force) {
        Write-AIError "Insufficient disk space. Free up space and re-run, or use --Force to override."
        exit 1
    }
    Write-AIWarn "--Force specified, continuing with limited disk space."
}
