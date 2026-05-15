# ============================================================================
# Dream Server Windows Installer -- Main Orchestrator
# ============================================================================
# Standalone Windows installer. Does not modify any Linux installer files.
#
# NVIDIA:           Docker Desktop handles GPU passthrough via WSL2.
#                   docker-compose.base.yml + docker-compose.nvidia.yml used unchanged.
#
# AMD Strix Halo:   llama-server runs natively with Vulkan on Windows.
#                   Everything else runs in Docker. Containers reach the host
#                   via host.docker.internal.
#
# Architecture:
#   This file is the orchestrator only. It sources lib/ helpers, sets phase
#   context variables, then dot-sources each numbered phase from phases/:
#
#     phases/01-preflight.ps1    -- admin, PS version, Docker, disk, Ollama
#     phases/02-detection.ps1    -- GPU, RAM, tier selection, driver check
#     phases/03-features.ps1     -- interactive feature selection menu
#     phases/04-requirements.ps1 -- tier RAM/disk minimums, port conflicts
#     phases/05-docker.ps1       -- Docker daemon health, Compose detection
#     phases/06-directories.ps1  -- dirs, robocopy, .env, SearXNG, OpenClaw
#     phases/07-devtools.ps1     -- OpenCode, Claude Code, Codex CLI
#
#   Phases 08 (LAUNCH) and 09 (VERIFY) remain inline here pending extraction.
#
# Usage:
#   .\install-windows.ps1                  # Interactive install
#   .\install-windows.ps1 --Tier 3         # Force tier 3
#   .\install-windows.ps1 --Cloud          # Cloud-only (no local GPU)
#   .\install-windows.ps1 --DryRun         # Validate without installing
#   .\install-windows.ps1 --All            # Enable all optional services
#   .\install-windows.ps1 --Hermes         # Enable Hermes Agent
#   .\install-windows.ps1 --NonInteractive # Headless install (defaults)
#
# ============================================================================

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Force,
    [switch]$NonInteractive,
    [string]$Tier = "",
    [switch]$Voice,
    [switch]$Workflows,
    [switch]$Rag,
    [switch]$Recommended,
    [switch]$NoRecommended,
    [switch]$Hermes,
    [switch]$NoHermes,
    [switch]$OpenClaw,
    [switch]$All,
    [switch]$Cloud,
    [switch]$Comfyui,
    [switch]$NoComfyui,
    [switch]$Lan,
    [switch]$Langfuse,
    [switch]$NoLangfuse,
    [string]$SummaryJsonPath = ""
)

$ErrorActionPreference = "Stop"

# ── Locate directories ────────────────────────────────────────────────────────
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
# NOTE: Nested Join-Path required -- PS 5.1 only accepts 2 arguments
$SourceRoot = (Resolve-Path (Join-Path (Join-Path $ScriptDir "..") "..")).Path

# ── Source libraries ──────────────────────────────────────────────────────────
$LibDir = Join-Path $ScriptDir "lib"
. (Join-Path $LibDir "constants.ps1")
. (Join-Path $LibDir "ui.ps1")
. (Join-Path $LibDir "compose-diagnostics.ps1")
. (Join-Path $LibDir "tier-map.ps1")
. (Join-Path $LibDir "detection.ps1")
. (Join-Path $LibDir "env-generator.ps1")
. (Join-Path $LibDir "llm-endpoint.ps1")
. (Join-Path $LibDir "opencode-config.ps1")
. (Join-Path $LibDir "readiness-summary.ps1")
. (Join-Path $LibDir "service-plan.ps1")

# ── Phase context variables ───────────────────────────────────────────────────
# These are plain (non-$script:) variables set in the orchestrator scope.
# Because phases are dot-sourced, they run in this same scope and can both
# read these inputs and write back their own output variables.
$dryRun         = $DryRun.IsPresent
$force          = $Force.IsPresent
$nonInteractive = $NonInteractive.IsPresent
$cloudMode      = $Cloud.IsPresent
$tierOverride   = $Tier
$voiceFlag      = $Voice.IsPresent
$workflowsFlag  = $Workflows.IsPresent
$ragFlag        = $Rag.IsPresent
$recommendedFlag = $Recommended.IsPresent
$noRecommendedFlag = $NoRecommended.IsPresent
$hermesFlag     = $Hermes.IsPresent
$noHermesFlag   = $NoHermes.IsPresent
$openClawFlag   = $OpenClaw.IsPresent
$allFlag        = $All.IsPresent
$comfyuiFlag    = $Comfyui.IsPresent
$noComfyuiFlag  = $NoComfyui.IsPresent
$lanFlag        = $Lan.IsPresent
$langfuseFlag   = $Langfuse.IsPresent
$noLangfuseFlag = $NoLangfuse.IsPresent
$installDir     = $script:DS_INSTALL_DIR
$sourceRoot     = $SourceRoot

# ── Phase dispatcher ──────────────────────────────────────────────────────────
function Get-UsableWindowsBash {
    <#
    .SYNOPSIS
        Prefer a Git Bash-style shell for bootstrap-upgrade.sh on Windows.
    #>
    param(
        [string]$InstallPath = $installDir
    )

    $probeCommand = "command -v bash >/dev/null 2>&1"
    if ($InstallPath -match "^([A-Za-z]):") {
        $probeCommand += " && test -d /$($Matches[1].ToLower())"
    }

    $candidates = New-Object 'System.Collections.Generic.List[string]'

    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd -and $gitCmd.Source) {
        $gitRoot = Split-Path (Split-Path $gitCmd.Source -Parent) -Parent
        $gitBash = Join-Path $gitRoot "bin\bash.exe"
        if (Test-Path $gitBash) {
            [void]$candidates.Add($gitBash)
        }
    }

    $programFilesBash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
    if (Test-Path $programFilesBash) {
        [void]$candidates.Add($programFilesBash)
    }

    if (${env:ProgramFiles(x86)} -and $env:ProgramFiles -ne ${env:ProgramFiles(x86)}) {
        $programFilesX86Bash = Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe"
        if (Test-Path $programFilesX86Bash) {
            [void]$candidates.Add($programFilesX86Bash)
        }
    }

    $bashCmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($bashCmd -and $bashCmd.Source) {
        [void]$candidates.Add($bashCmd.Source)
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        if ($seen.ContainsKey($candidate)) { continue }
        $seen[$candidate] = $true

        try {
            & $candidate -lc $probeCommand *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch { }
    }

    return $null
}

$PhasesDir = Join-Path $ScriptDir "phases"

Write-DreamBanner

# Variables produced by each phase and consumed by downstream phases:
#
#  Phase 01 → $preflight_docker (hashtable)
#  Phase 02 → $gpuInfo, $systemRamGB, $selectedTier, $tierConfig, $llamaServerImage
#  Phase 03 → $enableVoice, $enableWorkflows, $enableRag, $enableOpenClaw, $openClawConfig
#  Phase 04 → $requirementsMet
#  Phase 05 → $dockerComposeCmd
#  Phase 06 → $envResult (SearxngSecret, OpenclawToken)
#  Phase 07 → (no output -- tools installed to $env:USERPROFILE)

. (Join-Path $PhasesDir "01-preflight.ps1")
. (Join-Path $PhasesDir "02-detection.ps1")
. (Join-Path $PhasesDir "03-features.ps1")
. (Join-Path $PhasesDir "04-requirements.ps1")
. (Join-Path $PhasesDir "05-docker.ps1")
. (Join-Path $PhasesDir "06-directories.ps1")
. (Join-Path $PhasesDir "07-devtools.ps1")

# ============================================================================
# PHASE 8 -- LAUNCH (download model, start Docker services)
# ============================================================================
Write-Phase -Phase 8 -Total 13 -Name "LAUNCH" -Estimate "2-30 minutes (model download)"

if ($dryRun) {
    if ($tierConfig.GgufUrl) {
        Write-AI "[DRY RUN] Would download: $($tierConfig.GgufFile)"
    }
    if ($gpuInfo.Backend -eq "amd") {
        Write-AI "[DRY RUN] Would install AMD Lemonade Server (or fallback to llama-server Vulkan)"
        Write-AI "[DRY RUN] Would start native inference server on port 8080"
    }
    Write-AI "[DRY RUN] Would run: docker compose up -d"
} else {
    Push-Location $installDir
    # Sync .NET CWD so in-process .NET API calls using relative paths (e.g., Test-Path
    # internals, [IO.File] methods) resolve against $installDir, not the launch directory.
    # PowerShell's Push-Location does not update [Environment]::CurrentDirectory.
    $_previousCwd = [Environment]::CurrentDirectory
    [Environment]::CurrentDirectory = $installDir

    try {
        # ── Bootstrap fast-start ──────────────────────────────────────────────
        $bootstrapActive = $false
        $fullTierConfig = $null

        if (Should-UseBootstrap -Tier $selectedTier -InstallDir $installDir `
                -GgufFile $tierConfig.GgufFile -CloudMode $cloudMode) {
            $bootstrapActive = $true
            $fullTierConfig = @{}
            foreach ($k in $tierConfig.Keys) { $fullTierConfig[$k] = $tierConfig[$k] }
            $tierConfig.GgufFile   = $script:BOOTSTRAP_GGUF_FILE
            $tierConfig.GgufUrl    = $script:BOOTSTRAP_GGUF_URL
            $tierConfig.GgufSha256 = ""
            $tierConfig.LlmModel   = $script:BOOTSTRAP_LLM_MODEL
            $tierConfig.MaxContext  = $script:BOOTSTRAP_MAX_CONTEXT
            Write-AI "Fast-start mode: downloading bootstrap model (~1.5GB) for instant chat."
            Write-AI "Your full model ($($fullTierConfig.LlmModel)) will download in the background."
        }

        # ── Download GGUF model ───────────────────────────────────────────────
        if ($tierConfig.GgufUrl -and -not $cloudMode) {
            $modelPath    = Join-Path (Join-Path $installDir "data\models") $tierConfig.GgufFile
            $needsDownload = -not (Test-Path $modelPath)

            if ((Test-Path $modelPath) -and $tierConfig.GgufSha256) {
                Write-AI "Verifying model integrity (SHA256)..."
                $integrity = Test-ModelIntegrity -Path $modelPath -ExpectedHash $tierConfig.GgufSha256
                if ($integrity.Valid) {
                    Write-AISuccess "Model verified: $($tierConfig.GgufFile)"
                } else {
                    Write-AIWarn "Model file is corrupt (hash mismatch). Removing and re-downloading..."
                    Remove-Item $modelPath -Force
                    $needsDownload = $true
                }
            } elseif (Test-Path $modelPath) {
                Write-AISuccess "Model already present: $($tierConfig.GgufFile)"
            }

            if ($needsDownload) {
                $dlOk = Show-ProgressDownload -Url $tierConfig.GgufUrl `
                    -Destination $modelPath -Label "Downloading $($tierConfig.GgufFile)"
                if (-not $dlOk) {
                    Write-AIError "Model download failed. Re-run the installer to resume."
                    exit 1
                }
                if ($tierConfig.GgufSha256) {
                    Write-AI "Verifying download integrity (SHA256)..."
                    $integrity = Test-ModelIntegrity -Path $modelPath -ExpectedHash $tierConfig.GgufSha256
                    if ($integrity.Valid) {
                        Write-AISuccess "Download verified OK"
                    } else {
                        Write-AIError "Downloaded file is corrupt (SHA256 mismatch)."
                        Write-AI "  Expected: $($integrity.ExpectedHash)"
                        Write-AI "  Got:      $($integrity.ActualHash)"
                        Remove-Item $modelPath -Force
                        Write-AIError "Re-run the installer to download again."
                        exit 1
                    }
                }
            }
        }

        # ── Patch .env for bootstrap model ────────────────────────────────────
        if ($bootstrapActive) {
            $envPath = Join-Path $installDir ".env"
            if (Test-Path $envPath) {
                $envContent = Get-Content $envPath -Raw
                $envContent = $envContent -replace "(?m)^GGUF_FILE=.*$", "GGUF_FILE=$($tierConfig.GgufFile)"
                $envContent = $envContent -replace "(?m)^LLM_MODEL=.*$", "LLM_MODEL=$($tierConfig.LlmModel)"
                $envContent = $envContent -replace "(?m)^MAX_CONTEXT=.*$", "MAX_CONTEXT=$($tierConfig.MaxContext)"
                $envContent = $envContent -replace "(?m)^CTX_SIZE=.*$", "CTX_SIZE=$($tierConfig.MaxContext)"
                [System.IO.File]::WriteAllText($envPath, $envContent, (New-Object System.Text.UTF8Encoding($false)))
                Write-AISuccess "Patched .env for bootstrap model ($($tierConfig.GgufFile))"
            }
        }

        # ── AMD: native inference server (Lemonade preferred, llama-server fallback) ──
        $useLemonade = $false
        if ($gpuInfo.Backend -eq "amd" -and -not $cloudMode) {
            Write-Chapter "AMD INFERENCE BACKEND"

            # Offer Lemonade if not already installed
            if (Test-Path $script:LEMONADE_EXE) {
                Write-AISuccess "AMD Lemonade Server already installed"
                $useLemonade = $true
            } else {
                # Prompt user before installing third-party software
                $npuNote = $(if ($gpuInfo.HasNpu) { " (NPU + GPU hybrid acceleration detected)" } else { " (Vulkan GPU acceleration)" })
                Write-Host ""
                Write-AI "AMD Lemonade Server provides optimized local AI inference$npuNote."
                Write-AI "It replaces the default llama-server with native AMD acceleration."
                Write-Host ""
                $lemonadeChoice = "Y"
                if (-not $nonInteractive) {
                    Write-Host "  Install AMD Lemonade for optimized inference? [Y/n] " -ForegroundColor Cyan -NoNewline
                    $lemonadeChoice = Read-Host
                    if (-not $lemonadeChoice) { $lemonadeChoice = "Y" }
                }

                if ($lemonadeChoice -match "^[Yy]") {
                    Write-AI "Installing AMD Lemonade Server..."
                    $msiPath = Join-Path $env:TEMP $script:LEMONADE_MSI_FILE
                    $dlOk = Invoke-DownloadWithRetry -Url $script:LEMONADE_MSI_URL `
                        -Destination $msiPath -Label "Downloading Lemonade Server (~3MB)"
                    if ($dlOk) {
                        $msiArgs = "/i `"$msiPath`" /quiet /norestart ALLUSERS=1"
                        Start-Process msiexec.exe -ArgumentList $msiArgs -Wait -NoNewWindow
                        if (Test-Path $script:LEMONADE_EXE) {
                            Write-AISuccess "AMD Lemonade Server installed"
                            $useLemonade = $true
                        } else {
                            Write-AIWarn "Lemonade MSI installed but executable not found at expected path."
                            Write-AI "  Falling back to llama-server (Vulkan)."
                        }
                    } else {
                        Write-AIWarn "Lemonade download failed. Falling back to llama-server (Vulkan)."
                    }
                } else {
                    Write-AI "Skipped Lemonade. Using llama-server (Vulkan) instead."
                }
            }

            # Honour the unified BIND_ADDRESS knob (PR #964) for native servers.
            # Phase 06 has already written BIND_ADDRESS to .env (0.0.0.0 with -Lan,
            # 127.0.0.1 otherwise). Read once and reuse for both Lemonade and
            # llama.cpp launches below. Empty/missing → loopback.
            $_envPath = Join-Path $installDir ".env"
            $bindAddr = "127.0.0.1"
            if (Test-Path $_envPath) {
                $_envText = Get-Content $_envPath -Raw
                if ($_envText -match "(?m)^BIND_ADDRESS=(.*)$") {
                    $_match = $Matches[1].Trim().Trim('"').Trim("'")
                    if (-not [string]::IsNullOrWhiteSpace($_match)) { $bindAddr = $_match }
                }
            }

            if ($useLemonade) {
                # ── Start Lemonade server ──
                # --extra-models-dir: Lemonade auto-discovers GGUF files in this directory
                # --no-tray: headless mode (no GUI system tray icon)
                # --llamacpp vulkan: AMD Vulkan GPU acceleration
                # Model loads automatically on first chat request -- no /api/v1/load needed
                Write-AI "Starting Lemonade server..."
                $modelsDir = Join-Path (Join-Path $installDir "data") "models"
                $lemonadeArgs = @(
                    "serve",
                    "--port", "$($script:LEMONADE_PORT)",
                    "--host", $bindAddr,
                    "--no-tray",
                    "--llamacpp", "vulkan",
                    "--extra-models-dir", $modelsDir
                )

                $pidDir = Split-Path $script:INFERENCE_PID_FILE
                New-Item -ItemType Directory -Path $pidDir -Force | Out-Null

                $proc = Start-Process -FilePath $script:LEMONADE_EXE `
                    -ArgumentList $lemonadeArgs -WindowStyle Hidden -PassThru
                Set-Content -Path $script:INFERENCE_PID_FILE -Value $proc.Id

                Write-AI "Waiting for Lemonade server to start..."
                $maxWait = 60; $waited = 0; $healthy = $false
                while ($waited -lt $maxWait) {
                    Start-Sleep -Seconds 2; $waited += 2
                    try {
                        $req = [System.Net.HttpWebRequest]::Create($script:LEMONADE_HEALTH_URL)
                        $req.Timeout = 3000; $req.Method = "GET"
                        $resp = $req.GetResponse(); $code = [int]$resp.StatusCode; $resp.Close()
                        if ($code -eq 200) { $healthy = $true; break }
                    } catch { }
                    if ($waited % 10 -eq 0) { Write-AI "  Still starting... ($waited s)" }
                }
                if ($healthy) {
                    Write-AISuccess "Lemonade server healthy (PID $($proc.Id))"
                    if ($gpuInfo.HasNpu) {
                        Write-AISuccess "NPU hybrid mode available (NPU prefill + GPU decode)"
                    }
                    Write-AI "Model ($($tierConfig.GgufFile)) will load on first request."
                } else {
                    Write-AIWarn "Lemonade server did not respond within ${maxWait}s. It may still be starting."
                }
            } else {
                # ── Fallback: llama-server.exe (Vulkan) ──
                $llamaZip = Join-Path $env:TEMP $script:LLAMA_CPP_VULKAN_ASSET
                if (-not (Test-Path $script:LLAMA_SERVER_EXE)) {
                    if (-not (Test-Path $llamaZip)) {
                        $dlOk = Invoke-DownloadWithRetry -Url $script:LLAMA_CPP_VULKAN_URL `
                            -Destination $llamaZip -Label "Downloading llama-server (Vulkan)"
                        if (-not $dlOk) {
                            Write-AIError "Failed to download llama-server after retries."
                            exit 1
                        }
                    }

                    Write-AI "Validating llama-server archive..."
                    $zipValid = Test-ZipIntegrity -Path $llamaZip
                    if (-not $zipValid.Valid) {
                        Write-AIWarn "Archive is corrupt: $($zipValid.ErrorMessage)"
                        Remove-Item $llamaZip -Force -ErrorAction SilentlyContinue
                        Write-AIError "Corrupted download. Re-run the installer."
                        exit 1
                    }

                    Write-AI "Extracting llama-server..."
                    New-Item -ItemType Directory -Path $script:LLAMA_SERVER_DIR -Force | Out-Null
                    if (-not (Invoke-ExtractionWithRetry -ZipPath $llamaZip -DestinationPath $script:LLAMA_SERVER_DIR)) {
                        Write-AIError "Failed to extract llama-server after retries."
                        exit 1
                    }

                    $exeFound = Get-ChildItem -Path $script:LLAMA_SERVER_DIR -Recurse -Filter "llama-server.exe" |
                        Select-Object -First 1
                    if ($exeFound -and $exeFound.DirectoryName -ne $script:LLAMA_SERVER_DIR) {
                        Get-ChildItem -Path $exeFound.DirectoryName -Force |
                            Move-Item -Destination $script:LLAMA_SERVER_DIR -Force
                    }
                    if (-not (Test-Path $script:LLAMA_SERVER_EXE)) {
                        Write-AIError "llama-server.exe not found after extraction."
                        exit 1
                    }
                    Write-AISuccess "llama-server (Vulkan) extracted"
                } else {
                    Write-AISuccess "llama-server.exe already present"
                }

                # Start native llama-server
                Write-AI "Starting native llama-server (Vulkan)..."
                $modelFullPath = Join-Path (Join-Path $installDir "data\models") $tierConfig.GgufFile
                $llamaArgs = @(
                    "--model", $modelFullPath,
                    "--host", $bindAddr,
                    "--port", "8080",
                    "--n-gpu-layers", "999",
                    "--ctx-size", "$($tierConfig.MaxContext)"
                )
                $_llamaEnv = @{}
                Get-Content -LiteralPath (Join-Path $installDir ".env") -ErrorAction SilentlyContinue | ForEach-Object {
                    if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
                    $parts = $_ -split '=', 2
                    $_llamaEnv[$parts[0].Trim()] = $parts[1].Trim().Trim('"')
                }
                if ($_llamaEnv["LLAMA_ARG_FLASH_ATTN"]) { $llamaArgs += @("--flash-attn", $_llamaEnv["LLAMA_ARG_FLASH_ATTN"]) }
                if ($_llamaEnv["LLAMA_ARG_CACHE_TYPE_K"]) { $llamaArgs += @("--cache-type-k", $_llamaEnv["LLAMA_ARG_CACHE_TYPE_K"]) }
                if ($_llamaEnv["LLAMA_ARG_CACHE_TYPE_V"]) { $llamaArgs += @("--cache-type-v", $_llamaEnv["LLAMA_ARG_CACHE_TYPE_V"]) }
                if ($_llamaEnv["LLAMA_ARG_N_CPU_MOE"]) { $llamaArgs += @("--n-cpu-moe", $_llamaEnv["LLAMA_ARG_N_CPU_MOE"]) }
                $pidDir = Split-Path $script:INFERENCE_PID_FILE
                New-Item -ItemType Directory -Path $pidDir -Force | Out-Null

                $proc = Start-Process -FilePath $script:LLAMA_SERVER_EXE `
                    -ArgumentList $llamaArgs -WindowStyle Hidden -PassThru
                Set-Content -Path $script:INFERENCE_PID_FILE -Value $proc.Id

                Write-AI "Waiting for llama-server to load model..."
                $maxWait = 120; $waited = 0; $healthy = $false
                while ($waited -lt $maxWait) {
                    Start-Sleep -Seconds 2; $waited += 2
                    try {
                        $req = [System.Net.HttpWebRequest]::Create("http://localhost:8080/health")
                        $req.Timeout = 3000; $req.Method = "GET"
                        $resp = $req.GetResponse(); $code = [int]$resp.StatusCode; $resp.Close()
                        if ($code -eq 200) { $healthy = $true; break }
                    } catch { }
                    if ($waited % 10 -eq 0) { Write-AI "  Still loading... ($waited s)" }
                }
                if ($healthy) {
                    Write-AISuccess "Native llama-server healthy (PID $($proc.Id))"
                } else {
                    Write-AIWarn "llama-server did not respond within ${maxWait}s. It may still be loading."
                }

                # Patch .env: user declined Lemonade, correct backend and API path
                $envPath = Join-Path $installDir ".env"
                if (Test-Path $envPath) {
                    $envContent = Get-Content $envPath -Raw
                    $envContent = $envContent -replace "(?m)^LLM_BACKEND=.*$", "LLM_BACKEND=llama-server"
                    $envContent = $envContent -replace "(?m)^LLM_API_BASE_PATH=.*$", "LLM_API_BASE_PATH=/v1"
                    [System.IO.File]::WriteAllText($envPath, $envContent, (New-Object System.Text.UTF8Encoding($false)))
                    Write-AISuccess "Patched .env for llama-server backend"
                }
            }
        }

        # ── Assemble Docker Compose flags ─────────────────────────────────────
        # NOTE: Blackwell GPUs (sm_120) work with the standard server-cuda image
        # via PTX JIT compilation. No special image override is needed.
        #
        # --env-file is explicit: Docker Compose V2 on Windows may not auto-discover
        # .env from the project directory when multiple -f flags are used. Explicitly
        # passing --env-file removes ambiguity in .env resolution.
        $composeFlags = @("--env-file", ".env", "-f", "docker-compose.base.yml")

        if ($cloudMode) {
            $composeFlags += @("-f", "installers/windows/docker-compose.windows-amd.yml")
        } elseif ($gpuInfo.Backend -eq "nvidia") {
            if ($script:gpuPassthroughFailed) {
                Write-AIWarn "NVIDIA GPU passthrough unavailable -- falling back to CPU-only inference."
                Write-AI "  Inference will be slower but functional. To fix GPU passthrough:"
                Write-AI "  1. Restart Docker Desktop and WSL: wsl --shutdown"
                Write-AI "  2. Verify: docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi"
                $composeFlags += @("-f", "docker-compose.cpu.yml")
            } else {
                $composeFlags += @("-f", "docker-compose.nvidia.yml")
            }
        } elseif ($gpuInfo.Backend -eq "amd") {
            $composeFlags += @("-f", "installers/windows/docker-compose.windows-amd.yml")
            # Local-LLM readiness sidecar: gates open-webui on the native inference
            # server (Lemonade or llama-server.exe) becoming healthy. Only added
            # when a native server actually runs (AMD non-cloud); cloud mode loads
            # the windows-amd.yml overlay too but starts no native server, so the
            # sidecar would block open-webui forever there.
            $composeFlags += @("-f", "installers/windows/docker-compose.windows-amd.local.yml")
        } else {
            # No supported GPU detected (Intel integrated, etc.) -- use CPU-only overlay
            Write-AIWarn "No supported GPU detected. Using CPU-only inference (slower)."
            $composeFlags += @("-f", "docker-compose.cpu.yml")
        }

        # Discover enabled extension compose fragments via manifests
        # Mirrors resolve-compose-stack.sh: reads manifest.yaml, checks schema_version
        # and gpu_backends before including a service's compose file.
        $extDir        = Join-Path (Join-Path $installDir "extensions") "services"
        $currentBackend = $(if ($cloudMode) { "none" } else { $gpuInfo.Backend })
        $servicePlan = New-DreamWindowsServicePlan `
            -EnableRecommended $enableRecommended `
            -EnableVoice $enableVoice `
            -EnableWorkflows $enableWorkflows `
            -EnableRag $enableRag `
            -EnableHermes $enableHermes `
            -EnableOpenClaw $enableOpenClaw `
            -EnableComfyui $enableComfyui `
            -EnableDeepResearch $enableDeepResearch `
            -EnablePrivacyShield $enablePrivacyShield `
            -EnableBraveSearch $enableBraveSearch `
            -EnableDreamProxy $enableDreamProxy `
            -EnableRemoteAccess $enableRemoteAccess
        $enabledExtensionServices = @()
        $skippedExtensionServices = @()

        if (Test-Path $extDir) {
            $extServices = Get-ChildItem -Path $extDir -Directory | Sort-Object Name
            foreach ($svcDir in $extServices) {
                $manifestPath = Join-Path $svcDir.FullName "manifest.yaml"
                if (-not (Test-Path $manifestPath)) {
                    $manifestPath = Join-Path $svcDir.FullName "manifest.yml"
                }
                if (-not (Test-Path $manifestPath)) { continue }

                $manifestLines = Get-Content $manifestPath -ErrorAction SilentlyContinue
                if (-not $manifestLines) { continue }

                $hasSchema = $manifestLines | Where-Object { $_ -match "schema_version:\s*dream\.services\.v1" }
                if (-not $hasSchema) { continue }

                $category = ""
                $categoryLine = $manifestLines | Where-Object { $_ -match "^\s*category:" } | Select-Object -First 1
                if ($categoryLine) {
                    $category = (($categoryLine -split "category:")[1]).Trim().Trim('"').Trim("'")
                }

                $backendsLine = $manifestLines | Where-Object { $_ -match "gpu_backends:" }
                if ($backendsLine -and $currentBackend -ne "none") {
                    $backendsStr = ($backendsLine -split "gpu_backends:")[1]
                    if ($backendsStr -notmatch $currentBackend -and $backendsStr -notmatch "all") {
                        continue
                    }
                }

                $composeFile    = "compose.yaml"
                $composeRefLine = $manifestLines | Where-Object { $_ -match "compose_file:" }
                if ($composeRefLine) {
                    $composeFile = (($composeRefLine -split "compose_file:")[1]).Trim().Trim('"').Trim("'")
                }

                $composePath = Join-Path $svcDir.FullName $composeFile
                if (-not (Test-Path $composePath)) { continue }

                $svcName = $svcDir.Name
                $decision = Get-DreamWindowsServicePlanDecision `
                    -ServiceId $svcName `
                    -Category $category `
                    -Plan $servicePlan `
                    -EnableRecommended $enableRecommended
                if (-not $decision.Enabled) {
                    $skippedExtensionServices += "$svcName ($($decision.DisabledReason))"
                    continue
                }

                $relPath = $composePath.Substring($installDir.Length + 1) -replace "\\", "/"
                $composeFlags += @("-f", $relPath)
                $enabledExtensionServices += $svcName

                if ($currentBackend -eq "nvidia" -and -not $script:gpuPassthroughFailed) {
                    $gpuOverlay = Join-Path $svcDir.FullName "compose.nvidia.yaml"
                    if (Test-Path $gpuOverlay) {
                        $useGpuOverlay = $true
                        if ($svcName -eq "whisper" -and $gpuInfo.DriverMajor -lt 575) {
                            $useGpuOverlay = $false
                            Write-AIWarn "Whisper CUDA image requires a newer NVIDIA driver than $($gpuInfo.DriverVersion); using CPU Whisper."
                        }
                        if ($useGpuOverlay) {
                            $relOverlay = $gpuOverlay.Substring($installDir.Length + 1) -replace "\\", "/"
                            $composeFlags += @("-f", $relOverlay)
                        }
                    }
                }
            }
        }

        if ($enabledExtensionServices.Count -gt 0) {
            Write-AI "Extension service plan: $($enabledExtensionServices -join ', ')"
        } else {
            Write-AI "Extension service plan: core services only"
        }
        if ($skippedExtensionServices.Count -gt 0) {
            Write-AI "Skipped extension services: $($skippedExtensionServices.Count)"
        }

        # Tier 0 memory overlay
        if ($selectedTier -eq "0" -and (Test-Path (Join-Path $installDir "docker-compose.tier0.yml"))) {
            $composeFlags += @("-f", "docker-compose.tier0.yml")
            Write-AI "Applying lightweight memory limits for Tier 0"
        }

        # User override
        if (Test-Path (Join-Path $installDir "docker-compose.override.yml")) {
            $composeFlags += @("-f", "docker-compose.override.yml")
        }

        # Validate compose files exist before launching
        for ($fi = 0; $fi -lt $composeFlags.Count; $fi++) {
            if ($composeFlags[$fi] -eq "-f" -and ($fi + 1) -lt $composeFlags.Count) {
                $cf = $composeFlags[$fi + 1]
                if (-not (Test-Path $cf)) {
                    Write-AIError "Compose file not found: $cf"
                    Write-AI "  Re-run with --Force or check that $installDir is intact."
                    exit 1
                }
            }
        }

        # ── Start Docker services ─────────────────────────────────────────────
        Write-Chapter "STARTING SERVICES"

        # Pre-flight: verify .env is readable from CWD before compose up
        $_envCheck = Join-Path $installDir ".env"
        if (-not (Test-Path $_envCheck)) {
            Write-AIError ".env file not found at $_envCheck -- cannot start services."
            Write-AI "  Re-run the installer to regenerate the .env file."
            exit 1
        }

        function Test-DreamDockerImageAvailable {
            param([string]$Image)
            if ([string]::IsNullOrWhiteSpace($Image)) { return $false }

            & docker image inspect $Image *> $null
            if ($LASTEXITCODE -eq 0) { return $true }

            & docker manifest inspect $Image *> $null
            return ($LASTEXITCODE -eq 0)
        }

        function Resolve-DreamDockerImageOrFallback {
            param(
                [string]$Image,
                [string]$Label,
                [string]$FallbackImage = ""
            )

            if (Test-DreamDockerImageAvailable -Image $Image) {
                Write-AISuccess "$Label image available: $Image"
                return $Image
            }

            $fallback = $FallbackImage
            if ([string]::IsNullOrWhiteSpace($fallback)) {
                $fallback = [Environment]::GetEnvironmentVariable("LLAMA_SERVER_IMAGE_FALLBACK")
            }
            if (-not [string]::IsNullOrWhiteSpace($fallback)) {
                Write-AIWarn "$Label image unavailable: $Image"
                Write-AIWarn "Trying explicit fallback from LLAMA_SERVER_IMAGE_FALLBACK: $fallback"
                if (Test-DreamDockerImageAvailable -Image $fallback) {
                    Write-AISuccess "$Label fallback image available: $fallback"
                    return $fallback
                }
                Write-AIError "$Label fallback image is also unavailable: $fallback"
            } else {
                Write-AIError "$Label image is unavailable: $Image"
            }

            Write-AI "  Docker cannot resolve this image tag before service startup."
            Write-AI "  Check the tag, registry access, and Docker Desktop network."
            Write-AI "  To override intentionally, set LLAMA_SERVER_IMAGE to a valid image."
            Write-AI "  To permit an explicit fallback, set LLAMA_SERVER_IMAGE_FALLBACK to a valid image."
            return $null
        }

        if (-not $cloudMode -and $currentBackend -ne "amd") {
            $envLlamaImage = ""
            $envFallbackImage = ""
            foreach ($line in Get-Content $_envCheck) {
                if ($line -match '^LLAMA_SERVER_IMAGE=(.+)$') {
                    $envLlamaImage = $Matches[1].Trim().Trim('"').Trim("'")
                } elseif ($line -match '^LLAMA_SERVER_IMAGE_FALLBACK=(.+)$') {
                    $envFallbackImage = $Matches[1].Trim().Trim('"').Trim("'")
                }
            }

            if (-not [string]::IsNullOrWhiteSpace($envLlamaImage)) {
                Write-AI "Validating llama-server image tag before startup..."
                $validatedImage = Resolve-DreamDockerImageOrFallback -Image $envLlamaImage -Label "llama-server" -FallbackImage $envFallbackImage
                if ([string]::IsNullOrWhiteSpace($validatedImage)) { exit 1 }

                if ($validatedImage -ne $envLlamaImage) {
                    $envLines = Get-Content $_envCheck
                    $envLines = $envLines | ForEach-Object {
                        if ($_ -match '^LLAMA_SERVER_IMAGE=') { "LLAMA_SERVER_IMAGE=$validatedImage" } else { $_ }
                    }
                    Write-Utf8NoBom -Path $_envCheck -Content ($envLines -join "`n")
                }
            }
        }

        Write-AI "Running: docker compose $($composeFlags -join ' ') up -d --remove-orphans --no-build"
        # PS 5.1 treats ANY stderr output from native commands as NativeCommandError.
        # Silence stderr-as-error so $LASTEXITCODE reflects the real compose exit code.
        # Write output to log file to avoid ForEach-Object pipeline hang on failure.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $_composeLogDir = Join-Path $installDir "logs"
        if (-not (Test-Path $_composeLogDir)) { New-Item -ItemType Directory -Path $_composeLogDir -Force | Out-Null }
        $_composeLog = Join-Path $_composeLogDir "compose-up.log"

        # ── Rebuild local-built images ─────────────────────────────────────
        # Mirrors phases/11-services.sh on Linux: local Dockerfiles can drift
        # from the baked images, so we always rebuild without cache before
        # `up -d`. llama-server runs natively on Windows (Lemonade or Vulkan
        # binary) so it is not built here. ComfyUI is included only if
        # explicitly enabled.
        $_buildServices = @("dashboard", "dashboard-api")
        if (Test-DreamWindowsServiceEnabled -ServiceId "ape" -Plan $servicePlan) {
            $_buildServices += "ape"
        }
        if (Test-DreamWindowsServiceEnabled -ServiceId "token-spy" -Plan $servicePlan) {
            $_buildServices += "token-spy"
        }
        if (Test-DreamWindowsServiceEnabled -ServiceId "privacy-shield" -Plan $servicePlan) {
            $_buildServices += "privacy-shield"
        }
        if ($enableComfyui) { $_buildServices += "comfyui" }
        Write-AI "Rebuilding local-built images (no-cache)..."
        $_buildLog = Join-Path $_composeLogDir "compose-build.log"
        "" | Out-File -FilePath $_buildLog -Encoding ascii
        foreach ($_svc in $_buildServices) {
            Write-AI "  building $_svc ..."
            & docker compose @composeFlags build --no-cache $_svc *>> $_buildLog
            if ($LASTEXITCODE -ne 0) {
                Write-AIWarn "$_svc build failed (non-fatal - see $_buildLog)"
            }
        }
        Write-AISuccess "Local images rebuilt"

        Write-AI "Starting services... this may take several minutes."
        & docker compose @composeFlags up -d --remove-orphans --no-build *> $_composeLog
        $composeExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        # Show tail of compose output for immediate feedback
        if (Test-Path $_composeLog) {
            Get-Content $_composeLog -Tail 20 | ForEach-Object { Write-Host "  $_" }
        }
        if ($composeExit -ne 0) {
            Write-AIError "docker compose up failed (exit code: $composeExit)"
            Write-DreamComposeDiagnostics -InstallDir $installDir -ComposeFlags $composeFlags `
                -ComposeArgs @("up", "-d", "--remove-orphans", "--no-build") `
                -ComposeLogPath $_composeLog `
                -Phase "install-windows.ps1 docker compose up -d" `
                -SaveReport
            exit 1
        }
        Write-AISuccess "Docker services started"

        # Save compose flags for dream.ps1 (BOM-free for reliable parsing)
        $flagsFile = Join-Path $installDir ".compose-flags"
        Write-Utf8NoBom -Path $flagsFile -Content ($composeFlags -join " ")

        # ── Launch background model upgrade ──────────────────────────────────
        if ($bootstrapActive -and $fullTierConfig) {
            Write-AI "Launching background download for $($fullTierConfig.LlmModel)..."
            $logDir = Join-Path $installDir "logs"
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
            $upgradeLog = Join-Path $logDir "model-upgrade.log"
            $upgradeErrLog = Join-Path $logDir "model-upgrade-err.log"
            $upgradeScript = Join-Path $installDir "scripts\bootstrap-upgrade.sh"

            if (Test-Path $upgradeScript) {
                # Convert Windows path to Git Bash Unix-style
                $bashInstallDir = ($installDir -replace "\\", "/" -replace "^([A-Za-z]):", '/$1').ToLower()
                $bashScript = ($upgradeScript -replace "\\", "/" -replace "^([A-Za-z]):", '/$1').ToLower()

                # Write a temp wrapper script to avoid Windows/PowerShell quoting
                # issues. Empty arguments (e.g., SHA256 for some tiers) get lost
                # during command-line parsing -- embedding them in a script file
                # with bash double-quotes preserves them correctly.
                $wrapperScript = Join-Path $logDir "bootstrap-run.sh"
                $wrapperContent = @"
#!/bin/bash
exec bash "$bashScript" "$bashInstallDir" "$($fullTierConfig.GgufFile)" "$($fullTierConfig.GgufUrl)" "$($fullTierConfig.GgufSha256)" "$($fullTierConfig.LlmModel)" "$($fullTierConfig.MaxContext)"
"@
                [System.IO.File]::WriteAllText($wrapperScript, $wrapperContent.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))

                $bashPath = Get-UsableWindowsBash -InstallPath $installDir
                if ($bashPath) {
                    $upgradeProc = Start-Process -FilePath $bashPath -ArgumentList $wrapperScript `
                        -WindowStyle Hidden `
                        -RedirectStandardOutput $upgradeLog `
                        -RedirectStandardError $upgradeErrLog `
                        -PassThru

                    Start-Sleep -Seconds 2
                    $upgradeProc.Refresh()

                    if ($upgradeProc.HasExited) {
                        Write-AIWarn "Background full-model download exited immediately (exit code: $($upgradeProc.ExitCode))."
                        Write-AI "  Retry manually with: & '$bashPath' '$wrapperScript'"
                        Write-AI "  Error log: $upgradeErrLog"
                    } else {
                        Write-AI "Full model ($($fullTierConfig.LlmModel)) downloading in background."
                        Write-AI "Check progress: Get-Content '$upgradeLog' -Tail 10"
                    }
                } else {
                    Write-AIWarn "No Git Bash-compatible shell was found for bootstrap-upgrade.sh."
                    Write-AI "  Install Git for Windows or run the upgrade script manually after adding bash.exe to PATH."
                }
            } else {
                Write-AIWarn "bootstrap-upgrade.sh not found at $upgradeScript"
                Write-AIWarn "Download the full model manually or re-run the installer."
            }
        }

    } finally {
        Pop-Location
        [Environment]::CurrentDirectory = $_previousCwd
    }
}

# ============================================================================
# PHASE 9 -- VERIFY (health checks, Perplexica config, shortcuts, summary)
# ============================================================================
Write-Phase -Phase 9 -Total 13 -Name "VERIFICATION" -Estimate "~30 seconds"

if ($dryRun) {
    $_dryRunServicePlan = New-DreamWindowsServicePlan `
        -EnableRecommended $enableRecommended `
        -EnableVoice $enableVoice `
        -EnableWorkflows $enableWorkflows `
        -EnableRag $enableRag `
        -EnableHermes $enableHermes `
        -EnableOpenClaw $enableOpenClaw `
        -EnableComfyui $enableComfyui `
        -EnableDeepResearch $enableDeepResearch `
        -EnablePrivacyShield $enablePrivacyShield `
        -EnableBraveSearch $enableBraveSearch `
        -EnableDreamProxy $enableDreamProxy `
        -EnableRemoteAccess $enableRemoteAccess
    Write-AI "[DRY RUN] Would health-check selected services"
    if (Test-DreamWindowsServiceEnabled -ServiceId "perplexica" -Plan $_dryRunServicePlan) {
        Write-AI "[DRY RUN] Would auto-configure Perplexica for $($tierConfig.LlmModel)"
    }
    Write-AI "[DRY RUN] Install validation complete"
    Write-AISuccess "Dry run finished -- no changes made"
    exit 0
}

# ── Service health checks ─────────────────────────────────────────────────────
$opencodeSync = Sync-WindowsOpenCodeConfigFromEnv -InstallDir $installDir `
    -GpuBackend $gpuInfo.Backend -UseLemonade:$useLemonade -CloudMode:$cloudMode `
    -DefaultModelId $tierConfig.GgufFile -DefaultModelName $tierConfig.LlmModel `
    -DefaultContextLimit ([int]$tierConfig.MaxContext) -SkipIfUnavailable
switch ($opencodeSync.Status) {
    "created" {
        Write-AISuccess "OpenCode config synced to active model (model: $($opencodeSync.ModelName))"
    }
    "updated" {
        Write-AISuccess "OpenCode config synced to active model (model: $($opencodeSync.ModelName))"
    }
    "regenerated" {
        Write-AISuccess "OpenCode config regenerated for active model (model: $($opencodeSync.ModelName))"
    }
}

$llmEndpoint = Get-WindowsLocalLlmEndpoint -InstallDir $installDir `
    -EnvMap (Get-WindowsDreamEnvMap -InstallDir $installDir) `
    -UseLemonade:$useLemonade -GpuBackend $gpuInfo.Backend -CloudMode:$cloudMode
$healthChecks = @(
    @{ Name = $llmEndpoint.Name; Url = $llmEndpoint.HealthUrl }
    @{ Name = "Chat UI (Open WebUI)"; Url = "http://localhost:3000" }
)
if ($enableVoice)     { $healthChecks += @{ Name = "Whisper (STT)";    Url = "http://localhost:9000/health" } }
if ($enableWorkflows) { $healthChecks += @{ Name = "n8n (Workflows)";   Url = "http://localhost:5678/healthz" } }

Write-AI "Running health checks..."
$maxAttempts = 60; $allHealthy = $true

foreach ($check in $healthChecks) {
    $healthy = $false
    for ($i = 1; $i -le $maxAttempts; $i++) {
        try {
            $req = [System.Net.HttpWebRequest]::Create($check.Url)
            $req.Timeout = 3000; $req.Method = "GET"
            $resp = $req.GetResponse(); $code = [int]$resp.StatusCode; $resp.Close()
            if ($code -ge 200 -and $code -lt 400) { $healthy = $true; break }
        } catch [System.Net.WebException] {
            # 401/403 means the service IS up (auth-protected) -- treat as healthy
            $webResp = $_.Exception.Response
            if ($webResp) {
                $code = [int]$webResp.StatusCode
                if ($code -eq 401 -or $code -eq 403) { $healthy = $true; break }
            }
        } catch { }
        if ($i -le 3 -or $i % 5 -eq 0) {
            Write-AI "  Waiting for $($check.Name)... ($i/$maxAttempts)"
        }
        Start-Sleep -Seconds 2
    }
    if ($healthy) {
        Write-AISuccess "$($check.Name): healthy"
    } else {
        Write-AIWarn "$($check.Name): not responding after $maxAttempts attempts"
        $allHealthy = $false
    }
}

# ── Pre-download the Whisper STT model ───────────────────────────────────────
# Speaches does NOT auto-download on transcription requests — it returns 404.
# Trigger the download explicitly, verify it completed, surface recovery
# instructions on failure. Mirrors Linux Phase 12 and macOS install-macos.sh.
if ($enableVoice) {
    # Read AUDIO_STT_MODEL and WHISPER_PORT from .env (written by env-generator.ps1).
    # Use ReadAllText with explicit UTF8NoBom encoding so legacy BOM-prefixed
    # .env files (written by old Set-Content -Encoding UTF8) don't break the
    # regex on the first line.
    $sttModel = "Systran/faster-whisper-base"  # safe fallback
    $whisperPort = "9000"  # safe fallback
    $envPath = Join-Path $installDir ".env"
    if (Test-Path $envPath) {
        try {
            $envText = [System.IO.File]::ReadAllText($envPath, (New-Object System.Text.UTF8Encoding($false)))
            # Strip any leading BOM defensively in case the file was written
            # with a different encoding.
            if ($envText.Length -gt 0 -and [int]$envText[0] -eq 0xFEFF) {
                $envText = $envText.Substring(1)
            }
            foreach ($line in ($envText -split "`r?`n")) {
                if ($line -match "^AUDIO_STT_MODEL=(.*)$") {
                    $val = $Matches[1].Trim('"').Trim()
                    if ($val) { $sttModel = $val }
                } elseif ($line -match "^WHISPER_PORT=(.*)$") {
                    $val = $Matches[1].Trim('"').Trim()
                    if ($val) { $whisperPort = $val }
                }
            }
        } catch {
            # Fall through to defaults on any read failure.
        }
    }
    $sttModelEncoded = $sttModel -replace "/", "%2F"
    $whisperUrl = "http://localhost:$whisperPort"
    $sttRecoveryCmd = "Invoke-WebRequest -Method POST -Uri '$whisperUrl/v1/models/$sttModelEncoded' -TimeoutSec 3600"

    # Step 1: wait briefly for the models API to be ready (max 15s).
    $sttApiReady = $false
    for ($i = 1; $i -le 15; $i++) {
        try {
            $probe = Invoke-WebRequest -Uri "$whisperUrl/v1/models" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            if ($probe.StatusCode -eq 200) { $sttApiReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }

    if (-not $sttApiReady) {
        Write-AIWarn "STT models API not ready -- download manually:"
        Write-Host "    $sttRecoveryCmd" -ForegroundColor DarkGray
    } else {
        # Step 2: skip if already cached.
        $alreadyCached = $false
        try {
            $check = Invoke-WebRequest -Uri "$whisperUrl/v1/models/$sttModelEncoded" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
            if ($check.StatusCode -eq 200) { $alreadyCached = $true }
        } catch { }

        if ($alreadyCached) {
            Write-AISuccess "STT model already cached ($sttModel)"
        } else {
            # Step 3: POST to trigger download.
            Write-AI "Downloading STT model ($sttModel)..."
            try {
                Invoke-WebRequest -Method POST -Uri "$whisperUrl/v1/models/$sttModelEncoded" -TimeoutSec 3600 -UseBasicParsing -ErrorAction Stop | Out-Null
            } catch {
                # Fall through to verification step regardless — POST can succeed or partial-fail.
            }

            # Step 4: verify the model is actually cached.
            $verified = $false
            try {
                $verify = Invoke-WebRequest -Uri "$whisperUrl/v1/models/$sttModelEncoded" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
                if ($verify.StatusCode -eq 200) { $verified = $true }
            } catch { }

            if ($verified) {
                Write-AISuccess "STT model cached ($sttModel)"
            } else {
                Write-AIWarn "STT model download failed -- run manually:"
                Write-Host "    $sttRecoveryCmd" -ForegroundColor DarkGray
            }
        }
    }
}

# ── Auto-configure Perplexica ─────────────────────────────────────────────────
if (Test-DreamWindowsServiceEnabled -ServiceId "perplexica" -Plan $servicePlan) {
    Write-AI "Configuring Perplexica..."
    $perplexicaOk = Set-PerplexicaConfig -PerplexicaPort 3004 -LlmModel $tierConfig.LlmModel
    if ($perplexicaOk) {
        Write-AISuccess "Perplexica configured (model: $($tierConfig.LlmModel))"
    } else {
        Write-AIWarn "Perplexica auto-config skipped -- complete setup at http://localhost:3004"
    }
}

$readinessEnv = Get-WindowsDreamEnvMap -InstallDir $installDir
function Get-ReadinessPort {
    param([string]$Name, [string]$Default)
    if ($readinessEnv.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace($readinessEnv[$Name])) {
        return $readinessEnv[$Name]
    }
    return $Default
}

$dashboardPort = Get-ReadinessPort -Name "DASHBOARD_PORT" -Default "3001"
$webuiPort = Get-ReadinessPort -Name "WEBUI_PORT" -Default "3000"
$dashboardApiPort = Get-ReadinessPort -Name "DASHBOARD_API_PORT" -Default "3002"
$llmContainer = if ($useLemonade -or $cloudMode -or $gpuInfo.Backend -eq "amd") { "" } else { "dream-llama-server" }
$readinessChecks = @(
    @{ Name = "Dashboard"; Url = "http://localhost:$dashboardPort"; Container = "dream-dashboard"; OpenUrl = "http://localhost:$dashboardPort" }
    @{ Name = "Chat UI (Open WebUI)"; Url = "http://localhost:$webuiPort"; Container = "dream-webui"; OpenUrl = "http://localhost:$webuiPort" }
    @{ Name = $llmEndpoint.Name; Url = $llmEndpoint.HealthUrl; Container = $llmContainer; OpenUrl = $llmEndpoint.BaseUrl }
    @{ Name = "Dashboard API"; Url = "http://localhost:$dashboardApiPort/health"; Container = "dream-dashboard-api"; OpenUrl = "http://localhost:$dashboardApiPort" }
)
if (Test-DreamWindowsServiceEnabled -ServiceId "litellm" -Plan $servicePlan) {
    $litellmPort = Get-ReadinessPort -Name "LITELLM_PORT" -Default "4000"
    $readinessChecks += @{ Name = "LiteLLM"; Url = "http://localhost:$litellmPort/health/readiness"; Container = "dream-litellm"; OpenUrl = "http://localhost:$litellmPort" }
}
if (Test-DreamWindowsServiceEnabled -ServiceId "searxng" -Plan $servicePlan) {
    $searxngPort = Get-ReadinessPort -Name "SEARXNG_PORT" -Default "8888"
    $readinessChecks += @{ Name = "SearXNG"; Url = "http://localhost:$searxngPort/healthz"; Container = "dream-searxng"; OpenUrl = "http://localhost:$searxngPort" }
}
if (Test-DreamWindowsServiceEnabled -ServiceId "token-spy" -Plan $servicePlan) {
    $tokenSpyPort = Get-ReadinessPort -Name "TOKEN_SPY_PORT" -Default "3005"
    $readinessChecks += @{ Name = "Token Spy"; Url = "http://localhost:$tokenSpyPort/health"; Container = "dream-token-spy"; OpenUrl = "http://localhost:$tokenSpyPort" }
}
if ($enableVoice) {
    $whisperPort = Get-ReadinessPort -Name "WHISPER_PORT" -Default "9000"
    $ttsPort = Get-ReadinessPort -Name "TTS_PORT" -Default "8880"
    $readinessChecks += @{ Name = "Whisper (STT)"; Url = "http://localhost:$whisperPort/health"; Container = "dream-whisper"; OpenUrl = "http://localhost:$whisperPort" }
    $readinessChecks += @{ Name = "Kokoro (TTS)"; Url = "http://localhost:$ttsPort/health"; Container = "dream-tts"; OpenUrl = "http://localhost:$ttsPort" }
}
if ($enableWorkflows) {
    $n8nPort = Get-ReadinessPort -Name "N8N_PORT" -Default "5678"
    $readinessChecks += @{ Name = "n8n"; Url = "http://localhost:$n8nPort/healthz"; Container = "dream-n8n"; OpenUrl = "http://localhost:$n8nPort" }
}
if ($enableRag) {
    $qdrantPort = Get-ReadinessPort -Name "QDRANT_PORT" -Default "6333"
    $readinessChecks += @{ Name = "Qdrant"; Url = "http://localhost:$qdrantPort"; Container = "dream-qdrant"; OpenUrl = "http://localhost:$qdrantPort" }
}
if ($enableOpenClaw) {
    $openClawPort = Get-ReadinessPort -Name "OPENCLAW_PORT" -Default "7860"
    $readinessChecks += @{ Name = "OpenClaw"; Url = "http://localhost:$openClawPort"; Container = "dream-openclaw"; OpenUrl = "http://localhost:$openClawPort" }
}
if (Test-DreamWindowsServiceEnabled -ServiceId "hermes-proxy" -Plan $servicePlan) {
    $hermesProxyPort = Get-ReadinessPort -Name "HERMES_PROXY_PORT" -Default "9120"
    $readinessChecks += @{ Name = "Hermes Proxy"; Url = "http://localhost:$hermesProxyPort/health"; Container = "dream-hermes-proxy"; OpenUrl = "http://localhost:$hermesProxyPort" }
}
if ($enableComfyui) {
    $comfyPort = Get-ReadinessPort -Name "COMFYUI_PORT" -Default "8188"
    $readinessChecks += @{ Name = "ComfyUI"; Url = "http://localhost:$comfyPort"; Container = "dream-comfyui"; OpenUrl = "http://localhost:$comfyPort" }
}
if (Test-DreamWindowsServiceEnabled -ServiceId "perplexica" -Plan $servicePlan) {
    $perplexicaPort = Get-ReadinessPort -Name "PERPLEXICA_PORT" -Default "3004"
    $readinessChecks += @{ Name = "Perplexica"; Url = "http://localhost:$perplexicaPort"; Container = "dream-perplexica"; OpenUrl = "http://localhost:$perplexicaPort" }
}
if (Test-DreamWindowsServiceEnabled -ServiceId "privacy-shield" -Plan $servicePlan) {
    $privacyPort = Get-ReadinessPort -Name "SHIELD_PORT" -Default "8085"
    $readinessChecks += @{ Name = "Privacy Shield"; Url = "http://localhost:$privacyPort/health"; Container = "dream-privacy-shield"; OpenUrl = "http://localhost:$privacyPort" }
}
Write-DreamInstallReadinessSummary -Checks $readinessChecks `
    -StatusCommand ".\dream.ps1 status" `
    -LogPath (Join-Path $installDir "logs\install.log") `
    -DashboardUrl "http://localhost:$dashboardPort"

# ── Desktop & Start Menu shortcuts ───────────────────────────────────────────
try {
    $dashboardUrl  = "http://localhost:3001"
    $shortcutName  = "Dream Server"
    $urlContent    = "[InternetShortcut]`nURL=$dashboardUrl`nIconIndex=0`n"

    $desktopDir    = [Environment]::GetFolderPath("Desktop")
    Write-Utf8NoBom -Path (Join-Path $desktopDir   "$shortcutName.url") -Content $urlContent

    $startMenuDir  = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    Write-Utf8NoBom -Path (Join-Path $startMenuDir "$shortcutName.url") -Content $urlContent

    # Attempt taskbar pin via Shell COM verb (silent no-op on builds that block it)
    try {
        $shell = New-Object -ComObject Shell.Application
        $folder = $shell.Namespace($desktopDir)
        $item = $folder.ParseName("$shortcutName.url")
        if ($item) {
            $item.Verbs() | Where-Object { $_.Name -match "pin.*taskbar|Taskbar" } |
                ForEach-Object { $_.DoIt() }
        }
    } catch { }

    Write-AISuccess "Added Dream Server shortcut to Desktop and Start Menu"
} catch {
    Write-AIWarn "Could not create shortcuts: $_"
}

# ── Success card ──────────────────────────────────────────────────────────────
if ($allHealthy) {
    Write-SuccessCard
} else {
    Write-Host ""
    Write-AIWarn "Some services may still be starting. Check status with:"
    Write-Host "  .\dream.ps1 status" -ForegroundColor Cyan
    Write-Host ""
    Write-SuccessCard
}

# ── Pre-mark setup wizard complete ────────────────────────────────────────────
# The dashboard-api reads ${INSTALL_DIR}/data/config/setup-complete.json
# (mounted at /data/config/setup-complete.json inside the container) to decide
# first_run state. Writing this here prevents the wizard from reappearing on
# every visit after a fresh install. Non-fatal.
try {
    $setupConfigDir = Join-Path $installDir "data\config"
    $setupCompleteFile = Join-Path $setupConfigDir "setup-complete.json"
    New-Item -Path $setupConfigDir -ItemType Directory -Force | Out-Null
    $completedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $payload = @{ completed_at = $completedAt; version = "1.0.0" } | ConvertTo-Json -Compress
    Set-Content -Path $setupCompleteFile -Value $payload -Encoding UTF8
    Write-AISuccess "Setup wizard pre-marked complete"
} catch {
    Write-AIWarn "Could not write setup-complete.json (non-fatal): $_"
}

# ── Summary JSON (for CI / automation) ───────────────────────────────────────
if ($SummaryJsonPath) {
    $summary = @{
        version    = $script:DS_VERSION
        tier       = $selectedTier
        tierName   = $tierConfig.TierName
        model      = $tierConfig.LlmModel
        gpuBackend = $gpuInfo.Backend
        gpuName    = $gpuInfo.Name
        installDir = $installDir
        features   = @{
            voice        = $enableVoice
            workflows    = $enableWorkflows
            rag          = $enableRag
            recommended  = $enableRecommended
            hermes       = $enableHermes
            openclaw     = $enableOpenClaw
            comfyui      = $enableComfyui
            deepResearch = $enableDeepResearch
            privacyShield = $enablePrivacyShield
        }
        healthy    = $allHealthy
        timestamp  = (Get-Date -Format "o")
    }
    Write-Utf8NoBom -Path $SummaryJsonPath -Content ($summary | ConvertTo-Json -Depth 3)
    Write-AI "Summary written to $SummaryJsonPath"
}
