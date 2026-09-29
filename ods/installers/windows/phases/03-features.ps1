# ============================================================================
# ODS Windows Installer -- Phase 03: Feature Selection
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Interactive feature selection menu; respects CLI flags for
#          non-interactive / headless installs.
#
# Reads:
#   $voiceFlag, $workflowsFlag, $ragFlag, $recommendedFlag, $hermesFlag,
#   $openClawFlag, $allFlag
#   $noRecommendedFlag, $comfyuiFlag, $noHermesFlag, $noComfyuiFlag
#   $nonInteractive  -- suppress menus (use flag defaults)
#   $dryRun          -- skip prompts, log only
#   $selectedTier    -- from phase 02, for tier-appropriate OpenClaw config
#   $gpuInfo         -- from phase 02, used for backend-specific safety gates
#   $cloudMode       -- true when external/cloud LLM mode is selected
#
# Writes:
#   $enableVoice      -- bool: enable Whisper + Kokoro TTS
#   $enableWorkflows  -- bool: enable n8n workflow automation
#   $enableRag        -- bool: enable Qdrant + embeddings (RAG)
#   $enableRecommended -- bool: enable recommended web/API support services
#   $enableHermes     -- bool: enable Hermes agent framework
#   $enableOpenClaw   -- bool: enable deprecated OpenClaw agent framework
#   $enableComfyui    -- bool: enable ComfyUI image generation
#   $openClawConfig   -- string: tier-appropriate OpenClaw config filename
#
# Modder notes:
#   Add new optional features to the Custom menu here.
#   For a new feature, add a flag parameter in install-windows.ps1 and a
#   $enable<Feature> variable here.
# ============================================================================

Write-Phase -Phase 3 -Total 13 -Name "FEATURE SELECTION" -Estimate "interactive"

# ── Defaults from CLI flags ────────────────────────────────────────────────────
$installedSelection = Get-ODSWindowsInstalledFeatureSelection -InstallDir $installDir
$priorFeatures = if ($installedSelection.Kind -eq "preserved") { $installedSelection.Features } else { @{} }
if ($installedSelection.Kind -eq "unknown" -and ($nonInteractive -or $dryRun) -and -not $allFlag) {
    throw "Existing ODS feature selection is unknown ($($installedSelection.Reason)). Choose Full Stack or Core Only interactively, or pass -All."
}
$enableVoice         = $voiceFlag -or $allFlag -or [bool]$priorFeatures.Voice
$enableWorkflows     = $workflowsFlag -or $allFlag -or [bool]$priorFeatures.Workflows
$enableRag           = $ragFlag -or $allFlag -or [bool]$priorFeatures.Rag
$enableRecommended   = (-not $noRecommendedFlag) -and ($recommendedFlag -or $allFlag -or [bool]$priorFeatures.Recommended)
$enableHermes        = (-not $noHermesFlag) -and ($hermesFlag -or $allFlag -or [bool]$priorFeatures.Hermes)
$enableOpenClaw      = $openClawFlag -or [bool]$priorFeatures.OpenClaw
$enableComfyui       = (-not $noComfyuiFlag) -and ($comfyuiFlag -or $allFlag -or [bool]$priorFeatures.Comfyui)
$enableDeepResearch  = $allFlag -or [bool]$priorFeatures.DeepResearch
$enablePrivacyShield = $allFlag -or [bool]$priorFeatures.PrivacyShield
$enableBraveSearch   = $false
$enableODSProxy    = $false
$enableRemoteAccess  = $false
# Langfuse defaults OFF on all tiers because its clickhouse + postgres + minio
# stack adds ~500MB baseline memory. Opt in via -Langfuse, -All, the Custom
# menu, or post-install `ods enable langfuse`. -NoLangfuse is honored as an
# explicit override so a -All run can still suppress Langfuse.
$enableLangfuse   = ($langfuseFlag -or $allFlag -or [bool]$priorFeatures.Langfuse) -and (-not $noLangfuseFlag)

function Read-ODSWindowsFeatureChoice {
    param([string]$Prompt, [bool]$Default)
    $suffix = if ($Default) { '[Y/n]' } else { '[y/N]' }
    $answer = Read-Host "  $Prompt $suffix"
    if ($answer -match '^[yY]') { return $true }
    if ($answer -match '^[nN]') { return $false }
    return $Default
}

# ── Interactive menu (skipped in non-interactive / dry-run / --All mode) ──────
if (-not $nonInteractive -and -not $allFlag -and -not $dryRun) {
    Write-Host ""
    Write-Host "  Choose your ODS configuration:" -ForegroundColor White
    Write-Host ""
    Write-Host "  [1] Full Stack   -- Voice + Workflows + RAG + Hermes + research tools" -ForegroundColor Green
    Write-Host "  [2] Core Only    -- Chat + LLM inference (lean, fastest startup)" -ForegroundColor White
    Write-Host "  [3] Custom       -- Choose each feature individually" -ForegroundColor White
    if ($installedSelection.Kind -eq "preserved") {
        Write-Host "  [4] Keep current -- Preserve installed service choices" -ForegroundColor White
    }
    Write-Host ""

    $defaultChoice = switch ($installedSelection.Kind) {
        "preserved" { "4" }
        "fresh" { "2" }
        default { "" }
    }
    if ($installedSelection.Kind -eq "unknown") {
        Write-AIWarn "Existing selection is unknown: $($installedSelection.Reason). Choose Full Stack, Core Only, or Custom explicitly."
    }
    $choice = Read-Host "  Selection [1/2/3/4] (default: $defaultChoice)"
    if ([string]::IsNullOrWhiteSpace($choice)) { $choice = $defaultChoice }
    switch ($choice) {
        "4" {
            if ($installedSelection.Kind -ne "preserved") { throw "No installed feature selection is available to keep." }
        }
        "2" {
            $enableVoice     = $false
            $enableWorkflows = $false
            $enableRag       = $false
            $enableRecommended = $false
            $enableHermes    = $false
            $enableOpenClaw  = $false
            $enableComfyui   = $false
            $enableDeepResearch = $false
            $enablePrivacyShield = $false
            $enableLangfuse  = $false
        }
        "3" {
            Write-Host ""
            $enableVoice = Read-ODSWindowsFeatureChoice 'Enable Voice (Whisper STT + Kokoro TTS)?' $enableVoice
            $enableWorkflows = Read-ODSWindowsFeatureChoice 'Enable Workflows (n8n, 400+ integrations)?' $enableWorkflows
            $enableRag = Read-ODSWindowsFeatureChoice 'Enable RAG (Qdrant + embeddings)?' $enableRag
            $enableRecommended = Read-ODSWindowsFeatureChoice 'Enable recommended web/API support?' $enableRecommended
            $enableHermes = Read-ODSWindowsFeatureChoice 'Enable Hermes Agent?' $enableHermes
            $enableOpenClaw = Read-ODSWindowsFeatureChoice 'Enable deprecated OpenClaw?' $enableOpenClaw
            $enableComfyui = Read-ODSWindowsFeatureChoice 'Enable image generation (ComfyUI, ~6.5GB)?' $enableComfyui
            $enableDeepResearch = Read-ODSWindowsFeatureChoice 'Enable Perplexica deep research?' $enableDeepResearch
            $enablePrivacyShield = Read-ODSWindowsFeatureChoice 'Enable Privacy Shield?' $enablePrivacyShield
            $enableLangfuse = Read-ODSWindowsFeatureChoice 'Enable Langfuse observability?' $enableLangfuse

            # Warn on low-tier
            if ($enableComfyui -and ($selectedTier -eq "0" -or $selectedTier -eq "1")) {
                Write-AIWarn "ComfyUI requires 8GB+ RAM and a dedicated GPU. Your Tier $selectedTier system may not support it."
                $enableComfyui = (Read-Host "  Continue with image generation enabled? [y/N]") -match "^[yY]"
            }
        }
        "1" {
            $enableVoice     = $true
            $enableWorkflows = $true
            $enableRag       = $true
            $enableRecommended = $true
            $enableHermes    = $true
            $enableOpenClaw  = $false
            $enableComfyui   = $true
            $enableDeepResearch = $true
            $enablePrivacyShield = $true
            $enableLangfuse  = $true

            # Disable image generation on low-tier systems (insufficient RAM/VRAM)
            if ($selectedTier -eq "0" -or $selectedTier -eq "1") {
                $enableComfyui = $false
                Write-AIWarn "Image generation (ComfyUI) disabled -- your hardware doesn't have enough RAM."
                Write-AI "  You can enable it later with: ods enable comfyui"
            }
        }
        default { throw "Choose a valid feature selection (1, 2, 3, or 4 when available)." }
    }
}

# Explicit CLI selections win over menu choices and preserved state.
if ($voiceFlag) { $enableVoice = $true }
if ($workflowsFlag) { $enableWorkflows = $true }
if ($ragFlag) { $enableRag = $true }
if ($recommendedFlag) { $enableRecommended = $true }
if ($hermesFlag) { $enableHermes = $true }
if ($openClawFlag) { $enableOpenClaw = $true }
if ($comfyuiFlag) { $enableComfyui = $true }
if ($langfuseFlag) { $enableLangfuse = $true }
if ($noHermesFlag) {
    $enableHermes = $false
}

if ($noRecommendedFlag) {
    $enableRecommended = $false
}
if ($noComfyuiFlag) {
    $enableComfyui = $false
}
if ($noLangfuseFlag) {
    $enableLangfuse = $false
}

# Tier safety net: disable ComfyUI on Tier 0/1 or CLOUD in non-interactive mode.
# Interactive mode has its own tier checks in the menu -- this catches -NonInteractive.
if ($nonInteractive -and $enableComfyui -and ($selectedTier -eq "0" -or $selectedTier -eq "1")) {
    $enableComfyui = $false
    Write-AI "ComfyUI auto-disabled for Tier $selectedTier (insufficient RAM for shm_size 8GB)"
}

# CLOUD tier cannot use ComfyUI (no local GPU for image generation)
if ($enableComfyui -and $selectedTier -eq "CLOUD") {
    $enableComfyui = $false
    Write-AIWarn "ComfyUI disabled for CLOUD tier (requires local GPU for image generation)"
}

# Docker Desktop on Windows AMD does not expose Linux ROCm device nodes used by
# the AMD ComfyUI compose overlay, so launching it leaves the stack half-created.
if ($enableComfyui -and $gpuInfo.Backend -eq "amd" -and -not $cloudMode) {
    $enableComfyui = $false
    Write-AIWarn "ComfyUI disabled on Windows AMD: Docker Desktop does not expose /dev/dri and /dev/kfd to Linux containers."
    Write-AI "  Image generation can be enabled later when a Windows-native ComfyUI backend is available."
}

# Hermes needs 64K context. The raise grows the KV cache, so it is re-checked
# against the same hardware envelope phase 02 selected with (mirrors
# installers/phases/03-features.sh): raise when the pick fits at 64K,
# otherwise re-select a model that does, otherwise keep the largest context
# that fits and say ODS Talk is unavailable (the Dashboard shows why).
$hermesContextBelowFloor = $false
if ($enableHermes -and -not $cloudMode) {
    $hermesContextSize = 65536
    if ([int]$tierConfig.MaxContext -lt $hermesContextSize) {
        $hermesFloorAction = "raise"
        $hermesFit = Test-CatalogModelContextFit -TierConfig $tierConfig -GpuInfo $gpuInfo `
            -SystemRamGB $systemRamGB -SourceRoot $sourceRoot -ContextLength $hermesContextSize
        if ($hermesFit -eq $false) {
            $hermesPrevious = "$($tierConfig.LlmModel) at $($tierConfig.MaxContext)"
            try {
                $hermesReselected = Resolve-CatalogModelRecommendation `
                    -TierConfig (Resolve-TierConfig -Tier $selectedTier) `
                    -Tier $selectedTier `
                    -GpuInfo $gpuInfo `
                    -SystemRamGB $systemRamGB `
                    -SourceRoot $sourceRoot `
                    -MinContext $hermesContextSize `
                    -RequireMinContext
                $tierConfig = $hermesReselected
                $hermesFloorAction = "reselected"
                Write-AIWarn "Hermes needs 64K context: $hermesPrevious cannot serve 64K here, so $($tierConfig.LlmModel) was selected at $($tierConfig.MaxContext)."
            } catch {
                $hermesFloorAction = "cap"
            }
        }
        if ($hermesFloorAction -eq "raise") {
            Write-AIWarn "Hermes enabled: increasing llama context from $($tierConfig.MaxContext) to $hermesContextSize (64K floor)."
            if ($tierConfig.ContainsKey("RecommendationReason") -and $tierConfig.RecommendationReason) {
                $tierConfig.RecommendationReason = "$($tierConfig.RecommendationReason) Hermes requires at least 64K context, so runtime context was raised to $hermesContextSize."
            }
            $tierConfig.MaxContext = $hermesContextSize
        } elseif ($hermesFloorAction -eq "cap") {
            $hermesContextBelowFloor = $true
            Write-AIWarn "Hermes needs at least 64K context, but $($tierConfig.LlmModel) runs at $($tierConfig.MaxContext) here (64K does not fit or exceeds its native context)."
            Write-AIWarn "ODS Talk stays unavailable (the Dashboard says why) until you choose a model that fits 64K in Models."
            if ($tierConfig.ContainsKey("RecommendationReason") -and $tierConfig.RecommendationReason) {
                $tierConfig.RecommendationReason = "$($tierConfig.RecommendationReason) Hermes requires 64K context, which does not fit here; ODS Talk is unavailable with this model."
            }
        }
    }
}

# ── Feature summary ───────────────────────────────────────────────────────────
Write-Host ""
Write-AI "Feature configuration:"
Write-InfoBox "  Voice (Whisper + Kokoro):" $(if ($enableVoice)     { "enabled" } else { "disabled" })
Write-InfoBox "  Workflows (n8n):"          $(if ($enableWorkflows) { "enabled" } else { "disabled" })
Write-InfoBox "  RAG (Qdrant + embeddings):" $(if ($enableRag)      { "enabled" } else { "disabled" })
Write-InfoBox "  Recommended web/API:"       $(if ($enableRecommended) { "enabled" } else { "disabled" })
Write-InfoBox "  Agents (Hermes):"           $(if ($enableHermes)   { "enabled" } else { "disabled" })
Write-InfoBox "  Legacy OpenClaw:"           $(if ($enableOpenClaw) { "enabled (DEPRECATED)" } else { "disabled" })
Write-InfoBox "  Image gen (ComfyUI):"        $(if ($enableComfyui)  { "enabled" } else { "disabled" })
Write-InfoBox "  Deep research:"              $(if ($enableDeepResearch) { "enabled" } else { "disabled" })
Write-InfoBox "  Privacy Shield:"             $(if ($enablePrivacyShield) { "enabled" } else { "disabled" })
Write-InfoBox "  Langfuse (observability):"   $(if ($enableLangfuse) { "enabled" } else { "disabled" })

# ── Tier-appropriate OpenClaw config selection ────────────────────────────────
# Mirrors bash phase 03 logic (config/openclaw/<profile>.json).
$openClawConfig = ""
if ($enableOpenClaw) {
    $openClawConfig = switch ($selectedTier) {
        "NV_ULTRA"   { "pro.json" }
        "SH_LARGE"   { "openclaw-strix-halo.json" }
        "SH_COMPACT" { "openclaw-strix-halo.json" }
        "4"          { "pro.json" }
        "3"          { "openclaw.json" }
        "2"          { "openclaw.json" }
        "1"          { "openclaw.json" }
        "CLOUD"      { "openclaw.json" }
        default      { "openclaw.json" }
    }
    Write-InfoBox "  OpenClaw config:" "$openClawConfig (matched to Tier $selectedTier)"
}
