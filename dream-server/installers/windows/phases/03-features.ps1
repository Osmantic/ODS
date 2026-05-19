# ============================================================================
# Dream Server Windows Installer -- Phase 03: Feature Selection
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Interactive feature selection menu; respects CLI flags for
#          non-interactive / headless installs.
#
# Reads:
#   $voiceFlag, $workflowsFlag, $ragFlag, $openClawFlag, $allFlag
#   $hermesFlag, $noHermesFlag, $comfyuiFlag, $noComfyuiFlag
#   $nonInteractive  -- suppress menus (use flag defaults)
#   $dryRun          -- skip prompts, log only
#   $selectedTier    -- from phase 02, for tier-appropriate OpenClaw config
#
# Writes:
#   $enableVoice      -- bool: enable Whisper + Kokoro TTS
#   $enableWorkflows  -- bool: enable n8n workflow automation
#   $enableRag        -- bool: enable Qdrant + embeddings (RAG)
#   $enableHermes     -- bool: enable Hermes Agent
#   $enableOpenClaw   -- bool: enable OpenClaw agent framework
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
$enableVoice      = $voiceFlag -or $allFlag
$enableWorkflows  = $workflowsFlag -or $allFlag
$enableRag        = $ragFlag -or $allFlag
$enableHermes     = (-not $noHermesFlag) -and ($hermesFlag -or $allFlag -or (-not $nonInteractive))
if ($nonInteractive -and -not $noHermesFlag) { $enableHermes = $true }
$enableOpenClaw   = $openClawFlag
$enableComfyui    = -not $noComfyuiFlag
# Langfuse defaults OFF on all tiers because its clickhouse + postgres + minio
# stack adds ~500MB baseline memory. Opt in via -Langfuse, -All, the Custom
# menu, or post-install `dream enable langfuse`. -NoLangfuse is honored as an
# explicit override so a -All run can still suppress Langfuse.
$enableLangfuse   = ($langfuseFlag -or $allFlag) -and (-not $noLangfuseFlag)

# ── Interactive menu (skipped in non-interactive / dry-run / --All mode) ──────
if (-not $nonInteractive -and -not $allFlag -and -not $dryRun) {
    Write-Host ""
    Write-Host "  Choose your Dream Server configuration:" -ForegroundColor White
    Write-Host ""
    Write-Host "  [1] Full Stack   -- Hermes + Voice + Workflows + RAG + image generation" -ForegroundColor Green
    Write-Host "  [2] Core Only    -- Chat + LLM inference (lean, fastest startup)" -ForegroundColor White
    Write-Host "  [3] Custom       -- Choose each feature individually" -ForegroundColor White
    Write-Host ""

    $choice = Read-Host "  Selection [1/2/3] (default: 1)"
    switch ($choice) {
        "2" {
            $enableVoice     = $false
            $enableWorkflows = $false
            $enableRag       = $false
            $enableHermes    = $false
            $enableOpenClaw  = $false
            $enableComfyui   = $false
            $enableLangfuse  = $false
        }
        "3" {
            Write-Host ""
            $enableVoice     = (Read-Host "  Enable Voice (Whisper STT + Kokoro TTS)?  [y/N]") -match "^[yY]"
            $enableWorkflows = (Read-Host "  Enable Workflows (n8n, 400+ integrations)? [y/N]") -match "^[yY]"
            $enableRag       = (Read-Host "  Enable RAG (Qdrant vector DB + embeddings)? [y/N]") -match "^[yY]"
            $enableHermes    = (Read-Host "  Enable Hermes Agent (default AI agent)? [Y/n]") -notmatch "^[nN]"
            $enableOpenClaw  = (Read-Host "  Enable OpenClaw (DEPRECATED; Hermes replaces it)? [y/N]") -match "^[yY]"
            $enableComfyui   = (Read-Host "  Enable image generation (ComfyUI + SDXL Lightning, ~6.5GB)? [y/N]") -match "^[yY]"
            $enableLangfuse  = (Read-Host "  Enable Langfuse (LLM observability, ~500MB)? [y/N]") -match "^[yY]"

            # Warn on low-tier
            if ($enableComfyui -and ($selectedTier -eq "0" -or $selectedTier -eq "1")) {
                Write-AIWarn "ComfyUI requires 8GB+ RAM and a dedicated GPU. Your Tier $selectedTier system may not support it."
                $enableComfyui = (Read-Host "  Continue with image generation enabled? [y/N]") -match "^[yY]"
            }
        }
        default {
            # "" (Enter) and "1" both select Full Stack
            $enableVoice     = $true
            $enableWorkflows = $true
            $enableRag       = $true
            $enableHermes    = $true
            $enableOpenClaw  = $false
            $enableComfyui   = $true
            $enableLangfuse  = $true

            # Disable image generation on low-tier systems (insufficient RAM/VRAM)
            if ($selectedTier -eq "0" -or $selectedTier -eq "1") {
                $enableComfyui = $false
                Write-AIWarn "Image generation (ComfyUI) disabled -- your hardware doesn't have enough RAM."
                Write-AI "  You can enable it later with: dream enable comfyui"
            }
        }
    }
}

if ($noHermesFlag) {
    $enableHermes = $false
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

if ($enableHermes -and -not $cloudMode) {
    $hermesContextSize = 131072
    if ([int]$tierConfig.MaxContext -lt $hermesContextSize) {
        Write-AIWarn "Hermes enabled: increasing llama context from $($tierConfig.MaxContext) to $hermesContextSize (128K)."
        $tierConfig.MaxContext = $hermesContextSize
    }
}

# ── Feature summary ───────────────────────────────────────────────────────────
Write-Host ""
Write-AI "Feature configuration:"
Write-InfoBox "  Voice (Whisper + Kokoro):" $(if ($enableVoice)     { "enabled" } else { "disabled" })
Write-InfoBox "  Workflows (n8n):"          $(if ($enableWorkflows) { "enabled" } else { "disabled" })
Write-InfoBox "  RAG (Qdrant + embeddings):" $(if ($enableRag)      { "enabled" } else { "disabled" })
Write-InfoBox "  Hermes Agent:"              $(if ($enableHermes)   { "enabled" } else { "disabled" })
Write-InfoBox "  Agents (OpenClaw):"         $(if ($enableOpenClaw) { "enabled" } else { "disabled" })
Write-InfoBox "  Image gen (ComfyUI):"        $(if ($enableComfyui)  { "enabled" } else { "disabled" })
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
