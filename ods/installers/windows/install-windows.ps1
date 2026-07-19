# ============================================================================
# ODS Windows Installer -- Main Orchestrator
# ============================================================================
# Standalone Windows installer. Does not modify any Linux installer files.
#
# NVIDIA:           Docker Desktop handles GPU passthrough via WSL2.
#                   docker-compose.base.yml + docker-compose.nvidia.yml used unchanged.
#
# AMD Strix Halo:   Lemonade runs natively with Vulkan on Windows, with
#                   llama-server.exe Vulkan fallback if Lemonade is unavailable.
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
#   .\install-windows.ps1 -NoHermes        # Disable Hermes Agent
#   .\install-windows.ps1 -NoBootstrap     # Wait for full model before launch
#   .\install-windows.ps1 -InstallDir <path>
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
    [switch]$NoBootstrap,
    [string]$InstallDir = "",
    [string]$SummaryJsonPath = ""
)

$ErrorActionPreference = "Stop"

# ── Locate directories ────────────────────────────────────────────────────────
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
# NOTE: Nested Join-Path required -- PS 5.1 only accepts 2 arguments
$SourceRoot = (Resolve-Path (Join-Path (Join-Path $ScriptDir "..") "..")).Path

if (-not [string]::IsNullOrWhiteSpace($InstallDir)) {
    $env:ODS_HOME = [System.IO.Path]::GetFullPath($InstallDir)
}

# ── Source libraries ──────────────────────────────────────────────────────────
$LibDir = Join-Path $ScriptDir "lib"
. (Join-Path $LibDir "constants.ps1")
. (Join-Path $LibDir "ui.ps1")
. (Join-Path $LibDir "compose-diagnostics.ps1")
. (Join-Path $LibDir "backend-contract.ps1")
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
$noBootstrapFlag = $NoBootstrap.IsPresent
$installDir     = $script:ODS_INSTALL_DIR
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

Write-ODSBanner

# Variables produced by each phase and consumed by downstream phases:
#
#  Phase 01 → $preflight_docker (hashtable)
#  Phase 02 → $gpuInfo, $systemRamGB, $selectedTier, $tierConfig, $llamaServerImage
#  Phase 03 → $enableVoice, $enableWorkflows, $enableRag, $enableOpenClaw, $openClawConfig
#  Phase 04 → $requirementsMet
#  Phase 05 → $dockerComposeCmd
#  Phase 06 → $envResult (SearxngSecret, OpenclawToken)
#  Phase 07 → (no output -- tools installed to $env:USERPROFILE)

# Phases signal a fatal, already-explained failure by throwing the
# ODS_INSTALL_ABORTED sentinel. `exit 1` inside a dot-sourced file does NOT
# stop this orchestrator (PowerShell resumes at the next statement), which
# previously let later phases run against half-initialized state.
try {
. (Join-Path $PhasesDir "01-preflight.ps1")
. (Join-Path $PhasesDir "02-detection.ps1")
. (Join-Path $PhasesDir "03-features.ps1")
. (Join-Path $PhasesDir "04-requirements.ps1")
. (Join-Path $PhasesDir "05-docker.ps1")
$amdLemonadeRuntime = $null
if ($gpuInfo.Backend -eq "amd") {
    $amdLemonadeRuntime = Get-ODSAmdLemonadeRuntime -RootPath $SourceRoot
    $script:LEMONADE_VERSION = [string]$amdLemonadeRuntime.windows_version
    $script:LEMONADE_MSI_FILE = [string]$amdLemonadeRuntime.windows_msi_file
    $script:LEMONADE_MSI_URL = "https://github.com/lemonade-sdk/lemonade/releases/download/v$($script:LEMONADE_VERSION)/$($script:LEMONADE_MSI_FILE)"
    $script:LEMONADE_EXE = Join-Path (Join-Path $script:LEMONADE_INSTALL_DIR "bin") ([string]$amdLemonadeRuntime.windows_executable)
    $_resolvedLemonadeExe = Resolve-ODSLemonadeExe -ExecutableName ([string]$amdLemonadeRuntime.windows_executable)
    if ($_resolvedLemonadeExe) { $script:LEMONADE_EXE = $_resolvedLemonadeExe }
    $script:LEMONADE_PORT = [int]$amdLemonadeRuntime.api_port
    $script:LEMONADE_HEALTH_URL = "http://localhost:$($script:LEMONADE_PORT)$($amdLemonadeRuntime.health_path)"
}
. (Join-Path $PhasesDir "06-directories.ps1")
. (Join-Path $PhasesDir "07-devtools.ps1")
} catch {
    if ($_.FullyQualifiedErrorId -eq "ODS_INSTALL_ABORTED") { exit 1 }
    throw
}

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
    Write-AI "[DRY RUN] Would run: docker compose up -d --remove-orphans --no-build --pull never"
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
                -GgufFile $tierConfig.GgufFile -CloudMode $cloudMode `
                -NoBootstrap $noBootstrapFlag) {
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
                $dlOk = Invoke-DownloadWithRetry -Url $tierConfig.GgufUrl `
                    -Destination $modelPath -Label "Downloading $($tierConfig.GgufFile)" -MaxRetries 4
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

            if ($enableHermes) {
                $hermesModel = $(if ($tierConfig.GgufFile) {
                    if ($gpuInfo.Backend -eq "amd") { "extra.$($tierConfig.GgufFile)" } else { $tierConfig.GgufFile }
                } else {
                    $tierConfig.LlmModel
                })
                $hermesBaseUrl = ""
                if (Test-Path $envPath) {
                    foreach ($line in Get-Content $envPath) {
                        if ($line -match "^HERMES_LLM_BASE_URL=(.+)$") {
                            $hermesBaseUrl = $Matches[1].Trim().Trim('"').Trim("'")
                            break
                        }
                    }
                }
                if ([string]::IsNullOrWhiteSpace($hermesBaseUrl)) {
                    $hermesBaseUrl = $(if ($cloudMode -or $gpuInfo.Backend -eq "amd") {
                        "http://litellm:4000/v1"
                    } else {
                        "http://llama-server:8080/v1"
                    })
                }
                $hermesTemplate = Join-Path (Join-Path (Join-Path $installDir "extensions") "services\hermes") "cli-config.yaml.template"
                $hermesLive = Join-Path (Join-Path $installDir "data\hermes") "config.yaml"
                if (-not (Test-Path $hermesTemplate)) {
                    Write-AIError "Missing Hermes config template at $hermesTemplate"
                    exit 1
                }
                if (-not (Test-Path $hermesLive)) {
                    Copy-Item -Path $hermesTemplate -Destination $hermesLive -Force
                }
                $hermesRequestTimeout = $(if ($cloudMode) { 180 } else { 900 })
                $patchedHermesTemplate = Update-HermesConfigFile -Path $hermesTemplate -Model $hermesModel -BaseUrl $hermesBaseUrl -ContextLength ([int]$tierConfig.MaxContext) -RequestTimeoutSeconds $hermesRequestTimeout -LemonadeCompact:($gpuInfo.Backend -eq "amd")
                $patchedHermesLive = Update-HermesConfigFile -Path $hermesLive -Model $hermesModel -BaseUrl $hermesBaseUrl -ContextLength ([int]$tierConfig.MaxContext) -RequestTimeoutSeconds $hermesRequestTimeout -LemonadeCompact:($gpuInfo.Backend -eq "amd")
                if (-not ($patchedHermesTemplate -and $patchedHermesLive)) {
                    Write-AIError "Failed to patch Hermes config for Windows runtime (model=$hermesModel, base_url=$hermesBaseUrl)"
                    exit 1
                }
                Write-AISuccess "Patched Hermes config for bootstrap model (model=$hermesModel, context=$($tierConfig.MaxContext), request_timeout=${hermesRequestTimeout}s)"
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
                    $lemonadeInstallDir = Get-ODSLemonadeUserInstallDir
                    $lemonadeMsiLog = Join-Path (Join-Path $installDir "logs") "lemonade-msi-install.log"
                    $dlOk = Invoke-DownloadWithRetry -Url $script:LEMONADE_MSI_URL `
                        -Destination $msiPath -Label "Downloading Lemonade Server (~3MB)"
                    if ($dlOk) {
                        if ([string]::IsNullOrWhiteSpace($lemonadeInstallDir)) {
                            Write-AIWarn "Could not determine the current user's Lemonade install directory."
                            Write-AI "  Falling back to llama-server (Vulkan)."
                        } else {
                            $lemonadeMsiLogDir = Split-Path -Parent $lemonadeMsiLog
                            New-Item -ItemType Directory -Path $lemonadeMsiLogDir -Force | Out-Null
                            Remove-Item -LiteralPath $lemonadeMsiLog -Force -ErrorAction SilentlyContinue
                            # Keep Lemonade in its supported per-user location. ODS installs from a
                            # normal PowerShell and must not require an all-users MSI elevation.
                            $msiArgs = "/i `"$msiPath`" /quiet /norestart INSTALLDIR=`"$lemonadeInstallDir`" /L*V `"$lemonadeMsiLog`""
                            $msiProc = Start-Process msiexec.exe -ArgumentList $msiArgs -Wait -NoNewWindow -PassThru
                            $_msiExit = $(if ($msiProc) { [int]$msiProc.ExitCode } else { 0 })
                            if ($_msiExit -eq 0) {
                                $_resolvedLemonadeExe = Resolve-ODSLemonadeExe -ExecutableName ([string]$amdLemonadeRuntime.windows_executable)
                                if ($_resolvedLemonadeExe) { $script:LEMONADE_EXE = $_resolvedLemonadeExe }
                                if (Test-Path $script:LEMONADE_EXE) {
                                    Write-AISuccess "AMD Lemonade Server installed"
                                    $useLemonade = $true
                                } else {
                                    Write-AIWarn "Lemonade MSI completed, but no Lemonade executable was found in the known install roots."
                                    Write-AI "  Expected per-user location: $lemonadeInstallDir"
                                    $_candidateSample = @(Get-ODSLemonadeExeCandidatePaths -ExecutableName ([string]$amdLemonadeRuntime.windows_executable) | Select-Object -First 6)
                                    if ($_candidateSample.Count -gt 0) {
                                        Write-AI "  Checked paths include: $($_candidateSample -join '; ')"
                                    }
                                    Write-AI "  Falling back to llama-server (Vulkan)."
                                }
                            } else {
                                Write-AIWarn "Lemonade MSI exited with code $_msiExit."
                                Write-AI "  Verbose MSI log: $lemonadeMsiLog"
                                Write-AI "  Falling back to llama-server (Vulkan)."
                            }
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

            function Get-ODSPriorLemonadeTaskName {
                return (-join ([char[]](
                    68, 114, 101, 97, 109, 83, 101, 114, 118, 101, 114, 76, 101,
                    109, 111, 110, 97, 100, 101, 82, 117, 110, 116, 105, 109, 101
                )))
            }

            function Stop-ODSWindowsLemonadeProcesses {
                param(
                    [string]$ExePath,
                    [string[]]$TaskNames = @("ODSLemonadeRuntime")
                )

                $_resolvedExe = $null
                try {
                    if (-not [string]::IsNullOrWhiteSpace($ExePath)) {
                        $_resolvedExe = [System.IO.Path]::GetFullPath(
                            [Environment]::ExpandEnvironmentVariables($ExePath.Trim('"'))
                        )
                    }
                } catch { }
                $_managedBin = if ($_resolvedExe) { Split-Path -Parent $_resolvedExe } else { $null }
                $_managedBinPrefix = if ($_managedBin) {
                    $_managedBin.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
                } else {
                    $null
                }

                foreach ($_taskName in @($TaskNames | Where-Object { $_ } | Select-Object -Unique)) {
                    try { Stop-ScheduledTask -TaskName $_taskName -ErrorAction SilentlyContinue } catch { }
                    try { Unregister-ScheduledTask -TaskName $_taskName -Confirm:$false -ErrorAction SilentlyContinue } catch { }
                }

                try {
                    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                        Where-Object {
                            $_path = [string]$_.ExecutablePath
                            ($_resolvedExe -and $_path -and $_path.Equals($_resolvedExe, [StringComparison]::OrdinalIgnoreCase)) -or
                            ($_managedBinPrefix -and $_path -and $_path.StartsWith($_managedBinPrefix, [StringComparison]::OrdinalIgnoreCase))
                        } |
                        ForEach-Object {
                            try { Stop-Process -Id ([int]$_.ProcessId) -Force -ErrorAction SilentlyContinue } catch { }
                        }
                } catch {
                    Write-AIWarn "Could not stop stale Lemonade processes: $_"
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
                $taskName = "ODSLemonadeRuntime"
                $taskNames = @($taskName, (Get-ODSPriorLemonadeTaskName))
                Stop-ODSWindowsLemonadeProcesses -ExePath $script:LEMONADE_EXE -TaskNames $taskNames
                foreach ($listener in @(Get-NetTCPConnection -LocalPort $script:LEMONADE_PORT -State Listen -ErrorAction SilentlyContinue)) {
                    if ($listener.OwningProcess -gt 0) {
                        Stop-Process -Id ([int]$listener.OwningProcess) -Force -ErrorAction SilentlyContinue
                    }
                }
                $pidDir = Split-Path $script:INFERENCE_PID_FILE
                New-Item -ItemType Directory -Path $pidDir -Force | Out-Null

                $argString = "serve --port $($script:LEMONADE_PORT) --host $bindAddr --no-tray --llamacpp vulkan --extra-models-dir `"$modelsDir`""
                $launchMethod = "scheduled task"
                try {
                    $action = New-ScheduledTaskAction -Execute $script:LEMONADE_EXE -Argument $argString -WorkingDirectory (Split-Path -Parent $script:LEMONADE_EXE)
                    $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddYears(1))
                    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
                    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force -ErrorAction Stop | Out-Null
                    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
                } catch {
                    $launchMethod = "direct process"
                    Write-AIWarn "Could not start Lemonade through Task Scheduler: $_"
                    Write-AI "Starting Lemonade directly for this Windows session..."
                    Start-Process -FilePath $script:LEMONADE_EXE -ArgumentList $argString -WindowStyle Hidden -WorkingDirectory (Split-Path -Parent $script:LEMONADE_EXE) | Out-Null
                }
                Start-Sleep -Seconds 5
                $proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($script:LEMONADE_EXE, [StringComparison]::OrdinalIgnoreCase) } |
                    Sort-Object ProcessId -Descending |
                    Select-Object -First 1
                if (-not $proc -and $launchMethod -eq "scheduled task") {
                    $launchMethod = "direct process"
                    Write-AIWarn "Lemonade scheduled task did not start a server process."
                    Write-AI "Starting Lemonade directly for this Windows session..."
                    Start-Process -FilePath $script:LEMONADE_EXE -ArgumentList $argString -WindowStyle Hidden -WorkingDirectory (Split-Path -Parent $script:LEMONADE_EXE) | Out-Null
                    Start-Sleep -Seconds 3
                    $proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($script:LEMONADE_EXE, [StringComparison]::OrdinalIgnoreCase) } |
                        Sort-Object ProcessId -Descending |
                        Select-Object -First 1
                }
                if (-not $proc) {
                    Write-AIWarn "Lemonade $launchMethod started but no Lemonade process was found. Falling back to native llama-server (Vulkan)."
                    Stop-ODSWindowsLemonadeProcesses -ExePath $script:LEMONADE_EXE -TaskNames $taskNames
                    Remove-Item -LiteralPath $script:INFERENCE_PID_FILE -Force -ErrorAction SilentlyContinue
                    $useLemonade = $false
                }

                if ($useLemonade) {
                    Set-Content -Path $script:INFERENCE_PID_FILE -Value $proc.ProcessId

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
                        Write-AISuccess "Lemonade server healthy (PID $($proc.ProcessId))"
                        if ($gpuInfo.HasNpu) {
                            Write-AISuccess "NPU hybrid mode available (NPU prefill + GPU decode)"
                        }
                        Write-AI "Model ($($tierConfig.GgufFile)) will load on first request."
                    } else {
                        Write-AIWarn "Lemonade server did not respond within ${maxWait}s. Falling back to native llama-server (Vulkan)."
                        Stop-ODSWindowsLemonadeProcesses -ExePath $script:LEMONADE_EXE -TaskNames $taskNames
                        Remove-Item -LiteralPath $script:INFERENCE_PID_FILE -Force -ErrorAction SilentlyContinue
                        $useLemonade = $false
                    }
                }
            }

            if (-not $useLemonade) {
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
                if ($_llamaEnv["LLAMA_PARALLEL"]) { $llamaArgs += @("--parallel", $_llamaEnv["LLAMA_PARALLEL"]) }
                if ($_llamaEnv["LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"]) { $llamaArgs += @("--checkpoint-every-n-tokens", $_llamaEnv["LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"]) }
                if ($_llamaEnv["LLAMA_ARG_NO_CACHE_PROMPT"] -and $_llamaEnv["LLAMA_ARG_NO_CACHE_PROMPT"] -notin @("0", "false", "off", "no")) { $llamaArgs += @("--no-cache-prompt") }
                if ($_llamaEnv["LLAMA_ARG_SPEC_TYPE"]) { $llamaArgs += @("--spec-type", $_llamaEnv["LLAMA_ARG_SPEC_TYPE"]) }
                if ($_llamaEnv["LLAMA_ARG_SPEC_DRAFT_N_MAX"]) { $llamaArgs += @("--spec-draft-n-max", $_llamaEnv["LLAMA_ARG_SPEC_DRAFT_N_MAX"]) }
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
                    $envContent = $envContent -replace "(?m)^ODS_MODE=.*$", "ODS_MODE=local"
                    $envContent = $envContent -replace "(?m)^LLM_BACKEND=.*$", "LLM_BACKEND=llama-server"
                    $envContent = $envContent -replace "(?m)^LLM_API_BASE_PATH=.*$", "LLM_API_BASE_PATH=/v1"
                    $envContent = $envContent -replace "(?m)^AMD_INFERENCE_RUNTIME=.*$", "AMD_INFERENCE_RUNTIME=llama-server"
                    $envContent = $envContent -replace "(?m)^AMD_INFERENCE_BACKEND=.*$", "AMD_INFERENCE_BACKEND=vulkan"
                    $envContent = $envContent -replace "(?m)^AMD_INFERENCE_LOCATION=.*$", "AMD_INFERENCE_LOCATION=host"
                    $envContent = $envContent -replace "(?m)^AMD_INFERENCE_PORT=.*$", "AMD_INFERENCE_PORT=8080"
                    $envContent = $envContent -replace "(?m)^AMD_INFERENCE_SUPPORTED_BACKENDS=.*$", "AMD_INFERENCE_SUPPORTED_BACKENDS=vulkan"
                    $envContent = $envContent -replace "(?m)^AMD_INFERENCE_RUNTIME_MODE=.*$", "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback"
                    $envContent = $envContent -replace "(?m)^AMD_INFERENCE_MANAGED=.*$", "AMD_INFERENCE_MANAGED=true"
                    [System.IO.File]::WriteAllText($envPath, $envContent, (New-Object System.Text.UTF8Encoding($false)))
                    Write-AISuccess "Patched .env for llama-server backend"

                    $nativeModel = ([regex]::Match($envContent, "(?m)^GGUF_FILE=([^\r\n]+)\r?$")).Groups[1].Value.Trim().Trim('"').Trim("'")
                    if ([string]::IsNullOrWhiteSpace($nativeModel)) { $nativeModel = $tierConfig.GgufFile }
                    $nativePort = ([regex]::Match($envContent, "(?m)^AMD_INFERENCE_PORT=([^\r\n]+)\r?$")).Groups[1].Value.Trim().Trim('"').Trim("'")
                    if ([string]::IsNullOrWhiteSpace($nativePort)) { $nativePort = "8080" }
                    $nativeApiBase = "http://host.docker.internal:$nativePort/v1"
                    $litellmDir = Join-Path (Join-Path $installDir "config") "litellm"
                    New-Item -ItemType Directory -Path $litellmDir -Force | Out-Null
                    $litellmLocal = @"
model_list:
  - model_name: default
    litellm_params:
      model: openai/$nativeModel
      api_base: $nativeApiBase
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/*
      api_base: $nativeApiBase
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"@
                    [System.IO.File]::WriteAllText((Join-Path $litellmDir "local.yaml"), $litellmLocal, (New-Object System.Text.UTF8Encoding($false)))
                    Write-AISuccess "Patched LiteLLM local config for native llama-server"
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
        $servicePlan = New-ODSWindowsServicePlan `
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
            -EnableODSProxy $enableODSProxy `
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

                $hasSchema = $manifestLines | Where-Object { $_ -match "schema_version:\s*ods\.services\.v1" }
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
                $decision = Get-ODSWindowsServicePlanDecision `
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
                } elseif ($currentBackend -eq "amd") {
                    $gpuOverlay = Join-Path $svcDir.FullName "compose.amd.yaml"
                    if (Test-Path $gpuOverlay) {
                        $relOverlay = $gpuOverlay.Substring($installDir.Length + 1) -replace "\\", "/"
                        $composeFlags += @("-f", $relOverlay)
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
                $cfPath = $cf
                if (-not [System.IO.Path]::IsPathRooted($cfPath)) {
                    $cfPath = Join-Path $installDir $cfPath
                }
                if (-not (Test-Path $cfPath)) {
                    Write-AIError "Compose file not found: $cf"
                    Write-AI "  Expected path: $cfPath"
                    Write-AI "  Re-run with --Force or check that $installDir is intact."
                    exit 1
                }
            }
        }

        # Save compose flags before build/up so ods.ps1 and diagnostics have
        # the exact selected stack even after a partial install failure.
        $flagsFile = Join-Path $installDir ".compose-flags"
        Write-Utf8NoBom -Path $flagsFile -Content ($composeFlags -join " ")

        function Assert-ODSWindowsComposeCwd {
            param([string]$InstallDir)

            $expected = (Resolve-Path -LiteralPath $InstallDir).Path
            $locationPath = (Get-Location).ProviderPath
            $dotnetPath = [Environment]::CurrentDirectory

            if ($locationPath -ne $expected -or $dotnetPath -ne $expected) {
                Write-AIError "Internal installer error: Docker Compose is not running from the install directory."
                Write-AI "  Install dir: $expected"
                Write-AI "  PowerShell location: $locationPath"
                Write-AI "  .NET current directory: $dotnetPath"
                Write-AI "  This prevents Compose from accidentally reading .env or compose files from the source checkout."
                exit 1
            }
        }

        function Initialize-ODSWindowsDockerClientConfig {
            param([string]$InstallDir)

            $dockerConfigDir = Join-Path (Join-Path $InstallDir "data") "docker-client-public"
            if (-not (Test-Path $dockerConfigDir)) {
                New-Item -ItemType Directory -Path $dockerConfigDir -Force | Out-Null
            }

            $dockerConfigPath = Join-Path $dockerConfigDir "config.json"
            if (-not (Test-Path $dockerConfigPath)) {
                Write-Utf8NoBom -Path $dockerConfigPath -Content "{`n  `"auths`": {}`n}`n"
            }

            $env:DOCKER_CONFIG = $dockerConfigDir
            Write-AI "Using install-scoped Docker client config: $dockerConfigDir"
        }

        function Get-ODSWindowsUserDockerClientArgs {
            <#
            .SYNOPSIS
                Return the Docker client configuration that existed before ODS
                created its installer-scoped config.

            .DESCRIPTION
                A user may intentionally set DOCKER_CONFIG for a private registry
                or an enterprise credential helper. Keep that exact setting for
                recovery instead of silently replacing it with an empty/default
                profile after the installer-scoped config fails.
            #>
            if ($script:ODSWindowsOriginalDockerConfigDefined -and
                -not [string]::IsNullOrWhiteSpace($script:ODSWindowsOriginalDockerConfig)) {
                return @("--config", $script:ODSWindowsOriginalDockerConfig)
            }

            $userProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
            if ([string]::IsNullOrWhiteSpace($userProfile)) { return @() }
            return @("--config", (Join-Path $userProfile ".docker"))
        }

        function Use-ODSWindowsUserDockerConfig {
            param([string]$Reason = "Docker recovery")

            if ($script:ODSWindowsDockerConfigMode -eq "user") { return }

            $script:ODSWindowsDockerClientArgs = @($script:ODSWindowsUserDockerClientArgs)
            $script:ODSWindowsDockerConfigMode = "user"
            if ($script:ODSWindowsOriginalDockerConfigDefined) {
                $env:DOCKER_CONFIG = $script:ODSWindowsOriginalDockerConfig
            } else {
                Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
            }

            $source = if ($script:ODSWindowsOriginalDockerConfigDefined -and
                -not [string]::IsNullOrWhiteSpace($script:ODSWindowsOriginalDockerConfig)) {
                "the user's original DOCKER_CONFIG"
            } else {
                "the user's default Docker config"
            }
            Write-AIWarn "Using $source for $Reason."
        }

        function Write-ODSWindowsComposeLaunchRecord {
            param(
                [string]$InstallDir,
                [string[]]$ComposeFlags,
                [string[]]$ComposeArgs,
                [string[]]$DockerClientArgs
            )

            $logDir = Join-Path $InstallDir "logs"
            if (-not (Test-Path $logDir)) {
                New-Item -ItemType Directory -Path $logDir -Force | Out-Null
            }
            $recordPath = Join-Path $logDir "compose-launch.txt"
            $dockerConfig = "default"
            $dockerPrefix = "docker"
            if ($DockerClientArgs.Count -ge 2 -and $DockerClientArgs[0] -eq "--config") {
                $dockerConfig = $DockerClientArgs[1]
                $dockerPrefix = "docker --config `"$dockerConfig`""
            }
            $composeCommand = ("$dockerPrefix compose " + (($ComposeFlags + $ComposeArgs) -join " ")).Trim()
            $composeFiles = New-Object System.Collections.Generic.List[string]
            for ($i = 0; $i -lt $ComposeFlags.Count; $i++) {
                if ($ComposeFlags[$i] -eq "-f" -and ($i + 1) -lt $ComposeFlags.Count) {
                    [void]$composeFiles.Add($ComposeFlags[$i + 1])
                }
            }

            $lines = @(
                "timestamp=$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))",
                "cwd=$((Get-Location).ProviderPath)",
                "dotnet_cwd=$([Environment]::CurrentDirectory)",
                "install_dir=$InstallDir",
                "docker_config=$dockerConfig",
                "compose_command=$composeCommand",
                "compose_flags=$($ComposeFlags -join ' ')",
                "compose_flags_file=$InstallDir\.compose-flags",
                "compose_ps_command=cd '$InstallDir'; $dockerPrefix compose $($ComposeFlags -join ' ') ps -a",
                "compose_logs_command=cd '$InstallDir'; $dockerPrefix compose $($ComposeFlags -join ' ') logs --tail 200",
                "compose_files="
            )
            foreach ($file in $composeFiles) {
                $lines += "  - $file"
            }
            [System.IO.File]::WriteAllText($recordPath, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
            Write-AI "Saved compose launch record to $recordPath"
        }

        function Assert-ODSWindowsManagedContainers {
            param(
                [string]$InstallDir,
                [string[]]$ComposeFlags,
                [string[]]$RequiredServices,
                [switch]$RetriedWithUserDockerConfig
            )

            Push-Location $InstallDir
            try {
                $managedIds = @(& docker @script:ODSWindowsDockerClientArgs compose @ComposeFlags ps -q 2>$null |
                    Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
                if ($managedIds.Count -eq 0) {
                    if (-not $RetriedWithUserDockerConfig -and $script:ODSWindowsDockerConfigMode -eq "install-scoped") {
                        Write-AIWarn "Managed-container inspection failed with the install-scoped Docker config; retrying with the user's Docker config."
                        Use-ODSWindowsUserDockerConfig -Reason "managed-container inspection"
                        return Assert-ODSWindowsManagedContainers -InstallDir $InstallDir -ComposeFlags $ComposeFlags `
                            -RequiredServices $RequiredServices -RetriedWithUserDockerConfig
                    }
                    Write-AIError "Docker Compose did not create any managed Windows containers."
                    Write-AI "  Native inference may be healthy, but ODS also needs the dashboard/chat container stack."
                    Write-AI "  Inspect with: docker compose $($ComposeFlags -join ' ') ps -a"
                    return $false
                }

                $missingServices = New-Object System.Collections.Generic.List[string]
                foreach ($service in $RequiredServices) {
                    $serviceIds = @(& docker @script:ODSWindowsDockerClientArgs compose @ComposeFlags ps -q $service 2>$null |
                        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
                    if ($serviceIds.Count -eq 0) {
                        [void]$missingServices.Add($service)
                    }
                }

                if ($missingServices.Count -gt 0) {
                    if (-not $RetriedWithUserDockerConfig -and $script:ODSWindowsDockerConfigMode -eq "install-scoped") {
                        Write-AIWarn "Managed-container inspection failed with the install-scoped Docker config; retrying with the user's Docker config."
                        Use-ODSWindowsUserDockerConfig -Reason "managed-container inspection"
                        return Assert-ODSWindowsManagedContainers -InstallDir $InstallDir -ComposeFlags $ComposeFlags `
                            -RequiredServices $RequiredServices -RetriedWithUserDockerConfig
                    }
                    Write-AIError "Docker Compose did not start required Windows service(s): $($missingServices -join ', ')"
                    Write-AI "  Native inference may be healthy, but ODS is not ready without these containers."
                    Write-AI "  Inspect with: docker compose $($ComposeFlags -join ' ') ps -a"
                    return $false
                }

                Write-AISuccess "Compose-managed Windows stack running ($($managedIds.Count) container(s))"
                return $true
            }
            finally {
                Pop-Location
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

        $script:ODSWindowsOriginalDockerConfigDefined = Test-Path Env:DOCKER_CONFIG
        $script:ODSWindowsOriginalDockerConfig = if ($script:ODSWindowsOriginalDockerConfigDefined) { $env:DOCKER_CONFIG } else { "" }
        Initialize-ODSWindowsDockerClientConfig -InstallDir $installDir
        $script:ODSWindowsDockerClientArgs = @("--config", $env:DOCKER_CONFIG)
        $script:ODSWindowsUserDockerClientArgs = @(Get-ODSWindowsUserDockerClientArgs)
        $script:ODSWindowsDockerConfigMode = "install-scoped"

        function Test-ODSDockerImageAvailable {
            param(
                [string]$Image,
                [string[]]$DockerClientArgs = $script:ODSWindowsDockerClientArgs
            )
            if ([string]::IsNullOrWhiteSpace($Image)) { return $false }

            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            try {
                & docker @DockerClientArgs image inspect $Image *> $null
                if ($LASTEXITCODE -eq 0) { return $true }

                & docker @DockerClientArgs manifest inspect $Image *> $null
                return ($LASTEXITCODE -eq 0)
            } finally {
                $ErrorActionPreference = $prevEAP
            }
        }

        function Test-ODSWindowsDockerCredentialHelperFailure {
            param([string]$LogPath)

            if (-not (Test-Path -LiteralPath $LogPath)) { return $false }

            try {
                $text = Get-Content -LiteralPath $LogPath -Raw -ErrorAction Stop
            } catch {
                try {
                    $text = (Get-Content -LiteralPath $LogPath -Tail 200 -ErrorAction Stop) -join "`n"
                } catch {
                    return $false
                }
            }
            $text = $text.Replace([string][char]0, "")

            return (
                $text -match "error getting credentials" -or
                $text -match "specified logon session does not exist"
            )
        }

        function Invoke-ODSWindowsComposeBuildService {
            param(
                [Parameter(Mandatory = $true)][string]$Service,
                [Parameter(Mandatory = $true)][string[]]$DockerClientArgs,
                [Parameter(Mandatory = $true)][string[]]$ComposeFlags,
                [Parameter(Mandatory = $true)][string]$BuildLog,
                [switch]$UseLegacyBuilder
            )

            $hadBuildKit = Test-Path Env:DOCKER_BUILDKIT
            $previousBuildKit = $env:DOCKER_BUILDKIT
            try {
                if ($UseLegacyBuilder) {
                    $env:DOCKER_BUILDKIT = "0"
                }

                & docker @DockerClientArgs compose @ComposeFlags build --no-cache $Service *>> $BuildLog
                return $LASTEXITCODE
            } finally {
                if ($UseLegacyBuilder) {
                    if ($hadBuildKit) {
                        $env:DOCKER_BUILDKIT = $previousBuildKit
                    } else {
                        Remove-Item Env:DOCKER_BUILDKIT -ErrorAction SilentlyContinue
                    }
                }
            }
        }

        function Invoke-ODSWindowsPlainDockerBuildService {
            param(
                [Parameter(Mandatory = $true)][string]$Service,
                [Parameter(Mandatory = $true)][string[]]$DockerClientArgs,
                [Parameter(Mandatory = $true)][string[]]$ComposeFlags,
                [Parameter(Mandatory = $true)][string]$BuildLog
            )

            $configJson = & docker @DockerClientArgs compose @ComposeFlags config --format json 2>$null
            if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($configJson)) {
                Add-Content -LiteralPath $BuildLog -Value "plain docker fallback could not read compose config for $Service"
                return 1
            }

            try {
                $composeConfig = $configJson | ConvertFrom-Json
            } catch {
                Add-Content -LiteralPath $BuildLog -Value "plain docker fallback could not parse compose config for ${Service}: $($_.Exception.Message)"
                return 1
            }

            $serviceProperty = $composeConfig.services.PSObject.Properties[$Service]
            if (-not $serviceProperty) {
                Add-Content -LiteralPath $BuildLog -Value "plain docker fallback found no compose service named $Service"
                return 1
            }

            $serviceConfig = $serviceProperty.Value
            if (-not $serviceConfig.build) {
                Add-Content -LiteralPath $BuildLog -Value "plain docker fallback found no build config for $Service"
                return 1
            }

            $contextPath = [string]$serviceConfig.build.context
            $dockerfileName = [string]$serviceConfig.build.dockerfile
            if ([string]::IsNullOrWhiteSpace($dockerfileName)) {
                $dockerfileName = "Dockerfile"
            }
            if ([string]::IsNullOrWhiteSpace($contextPath)) {
                Add-Content -LiteralPath $BuildLog -Value "plain docker fallback found no build context for $Service"
                return 1
            }

            $dockerfilePath = $dockerfileName
            if (-not [System.IO.Path]::IsPathRooted($dockerfilePath)) {
                $dockerfilePath = Join-Path $contextPath $dockerfileName
            }

            $imageTag = [string]$serviceConfig.image
            if ([string]::IsNullOrWhiteSpace($imageTag)) {
                $imageTag = "ods-${Service}:latest"
            }

            $buildArgs = @()
            if ($serviceConfig.build.args) {
                foreach ($arg in $serviceConfig.build.args.PSObject.Properties) {
                    $buildArgs += @("--build-arg", "$($arg.Name)=$($arg.Value)")
                }
            }

            $hadBuildKit = Test-Path Env:DOCKER_BUILDKIT
            $previousBuildKit = $env:DOCKER_BUILDKIT

            try {
                $env:DOCKER_BUILDKIT = "0"
                Add-Content -LiteralPath $BuildLog -Value "plain docker fallback building $Service as $imageTag from $contextPath"
                & docker @DockerClientArgs build --no-cache -t $imageTag -f $dockerfilePath @buildArgs $contextPath *>> $BuildLog
                return $LASTEXITCODE
            } finally {
                if ($hadBuildKit) {
                    $env:DOCKER_BUILDKIT = $previousBuildKit
                } else {
                    Remove-Item Env:DOCKER_BUILDKIT -ErrorAction SilentlyContinue
                }
            }
        }

        function Resolve-ODSDockerImageOrFallback {
            param(
                [string]$Image,
                [string]$Label,
                [string]$FallbackImage = "",
                [string]$ImageEnvName = "LLAMA_SERVER_IMAGE",
                [string]$FallbackEnvName = "LLAMA_SERVER_IMAGE_FALLBACK"
            )

            if (Test-ODSDockerImageAvailable -Image $Image) {
                Write-AISuccess "$Label image available: $Image"
                return $Image
            }

            if ($script:ODSWindowsDockerConfigMode -eq "install-scoped") {
                Write-AIWarn "$Label image validation failed with the install-scoped Docker config; retrying with the user's Docker config."
                if (Test-ODSDockerImageAvailable -Image $Image -DockerClientArgs $script:ODSWindowsUserDockerClientArgs) {
                    Use-ODSWindowsUserDockerConfig -Reason "$Label image validation"
                    Write-AISuccess "$Label image available: $Image"
                    return $Image
                }
            }

            $fallback = $FallbackImage
            if ([string]::IsNullOrWhiteSpace($fallback)) {
                $fallback = [Environment]::GetEnvironmentVariable($FallbackEnvName)
            }
            if (-not [string]::IsNullOrWhiteSpace($fallback)) {
                Write-AIWarn "$Label image unavailable: $Image"
                Write-AIWarn "Trying explicit fallback from ${FallbackEnvName}: $fallback"
                if (Test-ODSDockerImageAvailable -Image $fallback) {
                    Write-AISuccess "$Label fallback image available: $fallback"
                    return $fallback
                }
                if ($script:ODSWindowsDockerConfigMode -eq "install-scoped" -and
                    (Test-ODSDockerImageAvailable -Image $fallback -DockerClientArgs $script:ODSWindowsUserDockerClientArgs)) {
                    Use-ODSWindowsUserDockerConfig -Reason "$Label fallback image validation"
                    Write-AISuccess "$Label fallback image available: $fallback"
                    return $fallback
                }
                Write-AIError "$Label fallback image is also unavailable: $fallback"
            } else {
                Write-AIError "$Label image is unavailable: $Image"
            }

            Write-AI "  Docker cannot resolve this image tag before service startup."
            Write-AI "  Check the tag, registry access, and Docker Desktop network."
            Write-AI "  To override intentionally, set $ImageEnvName to a valid image."
            Write-AI "  To permit an explicit fallback, set $FallbackEnvName to a valid image."
            return $null
        }

        function Get-ODSEnvValueFromFile {
            param(
                [Parameter(Mandatory = $true)][string]$Path,
                [Parameter(Mandatory = $true)][string]$Key
            )
            if (-not (Test-Path $Path)) { return "" }
            foreach ($line in Get-Content $Path) {
                if ($line -match "^$([regex]::Escape($Key))=(.+)$") {
                    return $Matches[1].Trim().Trim('"').Trim("'")
                }
            }
            return ""
        }

        function Test-ODSWindowsLocalImageTag {
            param([string]$Image)

            if ([string]::IsNullOrWhiteSpace($Image)) { return $true }
            return (
                $Image -match '^ods-' -or
                $Image -match '^docker\.io/library/ods-' -or
                $Image -match '^localhost[/:]' -or
                $Image -match '^127\.0\.0\.1:'
            )
        }

        function Get-ODSWindowsComposeExternalImages {
            param(
                [Parameter(Mandatory = $true)][string[]]$DockerClientArgs,
                [Parameter(Mandatory = $true)][string[]]$ComposeFlags
            )

            $images = New-Object System.Collections.Generic.List[string]
            $seen = @{}
            $configJson = & docker @DockerClientArgs compose @ComposeFlags config --format json 2>$null
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($configJson)) {
                try {
                    $composeConfig = ($configJson -join "`n") | ConvertFrom-Json
                    foreach ($serviceProperty in $composeConfig.services.PSObject.Properties) {
                        $service = $serviceProperty.Value
                        $hasBuild = $false
                        if ($service.PSObject.Properties["build"] -and $null -ne $service.build) {
                            $hasBuild = $true
                        }
                        if ($hasBuild) { continue }

                        $image = [string]$service.image
                        if ([string]::IsNullOrWhiteSpace($image)) { continue }
                        if (Test-ODSWindowsLocalImageTag -Image $image) { continue }
                        if (-not $seen.ContainsKey($image)) {
                            $seen[$image] = $true
                            [void]$images.Add($image)
                        }
                    }
                    return @($images)
                } catch {
                    Write-AIWarn "Could not parse Docker Compose JSON image list: $($_.Exception.Message)"
                }
            }

            $imageLines = @(& docker @DockerClientArgs compose @ComposeFlags config --images 2>$null |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
            if ($LASTEXITCODE -ne 0) {
                throw "docker compose config --images failed"
            }

            foreach ($image in $imageLines) {
                $image = [string]$image
                if (Test-ODSWindowsLocalImageTag -Image $image) { continue }
                if (-not $seen.ContainsKey($image)) {
                    $seen[$image] = $true
                    [void]$images.Add($image)
                }
            }
            return @($images)
        }

        function Invoke-ODSWindowsDockerPullWithRetry {
            param(
                [Parameter(Mandatory = $true)][string]$Image,
                [Parameter(Mandatory = $true)][string[]]$DockerClientArgs,
                [Parameter(Mandatory = $true)][string]$LogPath,
                [int]$MaxAttempts = 4
            )

            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            try {
                & docker @DockerClientArgs image inspect $Image *> $null
                if ($LASTEXITCODE -eq 0) {
                    Add-Content -LiteralPath $LogPath -Value "Compose image already cached: $Image"
                    return $true
                }
            } finally {
                $ErrorActionPreference = $prevEAP
            }

            $delays = @(5, 15, 30)
            for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
                Write-AI "Pulling Compose image ($attempt/$MaxAttempts): $Image"
                & docker @DockerClientArgs pull $Image *>> $LogPath
                if ($LASTEXITCODE -eq 0) {
                    Write-AISuccess "Pulled $Image"
                    return $true
                }
                if ($attempt -lt $MaxAttempts) {
                    $delay = $delays[[Math]::Min($attempt - 1, $delays.Count - 1)]
                    Write-AIWarn "Pull failed for $Image; retrying in ${delay}s"
                    Start-Sleep -Seconds $delay
                }
            }

            Write-AIError "Failed to pull Compose image after retries: $Image"
            return $false
        }

        function Invoke-ODSWindowsComposeImagePreflight {
            param(
                [Parameter(Mandatory = $true)][string[]]$DockerClientArgs,
                [Parameter(Mandatory = $true)][string[]]$ComposeFlags,
                [Parameter(Mandatory = $true)][string]$LogPath
            )

            try {
                $images = @(Get-ODSWindowsComposeExternalImages -DockerClientArgs $DockerClientArgs -ComposeFlags $ComposeFlags)
            } catch {
                Write-AIError "Could not resolve Windows Docker Compose images before service launch."
                Write-AI "  Inspect compose config with: docker compose $($ComposeFlags -join ' ') config --images"
                Add-Content -LiteralPath $LogPath -Value "compose image preflight failed: $($_.Exception.Message)"
                return $false
            }

            if ($images.Count -eq 0) { return $true }

            Write-AI "Verifying Compose image cache before launch..."
            $failed = New-Object System.Collections.Generic.List[string]
            foreach ($image in $images) {
                if (-not (Invoke-ODSWindowsDockerPullWithRetry -Image $image -DockerClientArgs $DockerClientArgs -LogPath $LogPath)) {
                    [void]$failed.Add($image)
                }
            }

            if ($failed.Count -eq 0) {
                Write-AISuccess "Compose image cache ready"
                return $true
            }

            Write-AIError "$($failed.Count) Compose image(s) could not be pulled before launch."
            Write-AI "Windows installer will not allow Docker Compose to pull images implicitly."
            Write-AI "Fix Docker registry/network/disk access, then re-run .\install-windows.ps1."
            return $false
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
                $validatedImage = Resolve-ODSDockerImageOrFallback `
                    -Image $envLlamaImage `
                    -Label "llama-server" `
                    -FallbackImage $envFallbackImage `
                    -ImageEnvName "LLAMA_SERVER_IMAGE" `
                    -FallbackEnvName "LLAMA_SERVER_IMAGE_FALLBACK"
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

        if ($enableHermes) {
            $envHermesImage = Get-ODSEnvValueFromFile -Path $_envCheck -Key "HERMES_AGENT_IMAGE"
            $hasHermesImageOverride = -not [string]::IsNullOrWhiteSpace($envHermesImage)
            $envHermesFallbackImage = Get-ODSEnvValueFromFile -Path $_envCheck -Key "HERMES_AGENT_IMAGE_FALLBACK"
            if ([string]::IsNullOrWhiteSpace($envHermesImage)) {
                $envHermesImage = "nousresearch/hermes-agent:v2026.5.16"
            }

            Write-AI "Validating Hermes Agent image tag before startup..."
            $validatedHermesImage = Resolve-ODSDockerImageOrFallback `
                -Image $envHermesImage `
                -Label "Hermes Agent" `
                -FallbackImage $envHermesFallbackImage `
                -ImageEnvName "HERMES_AGENT_IMAGE" `
                -FallbackEnvName "HERMES_AGENT_IMAGE_FALLBACK"
            if ([string]::IsNullOrWhiteSpace($validatedHermesImage)) { exit 1 }

            if ($validatedHermesImage -ne $envHermesImage) {
                $envLines = Get-Content $_envCheck
                $updatedHermesImage = $false
                $envLines = $envLines | ForEach-Object {
                    if ($_ -match '^HERMES_AGENT_IMAGE=') {
                        $updatedHermesImage = $true
                        "HERMES_AGENT_IMAGE=$validatedHermesImage"
                    } else {
                        $_
                    }
                }
                if (-not $updatedHermesImage -and -not $hasHermesImageOverride) {
                    $envLines += "HERMES_AGENT_IMAGE=$validatedHermesImage"
                }
                Write-Utf8NoBom -Path $_envCheck -Content ($envLines -join "`n")
            }
        }

        Assert-ODSWindowsComposeCwd -InstallDir $installDir
        $composeUpArgs = @("up", "-d", "--remove-orphans", "--no-build", "--pull", "never")
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
        # binary) so it is not built here. ComfyUI is only locally built on
        # NVIDIA; the Windows AMD stack uses a prebuilt image overlay.
        $_buildServices = @("dashboard", "dashboard-api")
        if (Test-ODSWindowsServiceEnabled -ServiceId "ape" -Plan $servicePlan) {
            $_buildServices += "ape"
        }
        if (Test-ODSWindowsServiceEnabled -ServiceId "token-spy" -Plan $servicePlan) {
            $_buildServices += "token-spy"
        }
        if (Test-ODSWindowsServiceEnabled -ServiceId "privacy-shield" -Plan $servicePlan) {
            $_buildServices += "privacy-shield"
        }
        if ($enableComfyui -and $currentBackend -eq "nvidia" -and -not $script:gpuPassthroughFailed) {
            $_buildServices += "comfyui"
        }
        $_buildLog = Join-Path $_composeLogDir "compose-build.log"
        "" | Out-File -FilePath $_buildLog -Encoding ascii

        Push-Location $installDir
        try {
            Write-AI "Rebuilding local-built images (no-cache)..."
            $_failedBuildServices = @()
            $_legacyBuilderServices = @()
            $_defaultDockerConfigServices = @()
            foreach ($_svc in $_buildServices) {
                Write-AI "  building $_svc ..."
                $_buildExit = Invoke-ODSWindowsComposeBuildService `
                    -Service $_svc `
                    -DockerClientArgs $script:ODSWindowsDockerClientArgs `
                    -ComposeFlags $composeFlags `
                    -BuildLog $_buildLog

                if ($_buildExit -ne 0 -and (Test-ODSWindowsDockerCredentialHelperFailure -LogPath $_buildLog)) {
                    Write-AIWarn "  Docker Desktop credential helper failed for $_svc; retrying with plain docker build and DOCKER_BUILDKIT=0."
                    Add-Content -LiteralPath $_buildLog -Value "`n--- retrying $_svc with plain docker build and DOCKER_BUILDKIT=0 after Docker credential-helper failure ---"
                    $_buildExit = Invoke-ODSWindowsPlainDockerBuildService `
                        -Service $_svc `
                        -DockerClientArgs $script:ODSWindowsDockerClientArgs `
                        -ComposeFlags $composeFlags `
                        -BuildLog $_buildLog
                    if ($_buildExit -eq 0) {
                        $_legacyBuilderServices += $_svc
                    }
                }

                if ($_buildExit -ne 0) {
                    Write-AIWarn "  $_svc build failed with the install-scoped Docker config; retrying with the user's Docker config."
                    Add-Content -LiteralPath $_buildLog -Value "`n--- retrying $_svc with user's Docker config after install-scoped Docker config failure ---"
                    $_buildExit = Invoke-ODSWindowsComposeBuildService `
                        -Service $_svc `
                        -DockerClientArgs $script:ODSWindowsUserDockerClientArgs `
                        -ComposeFlags $composeFlags `
                        -BuildLog $_buildLog
                    if ($_buildExit -eq 0) {
                        $_defaultDockerConfigServices += $_svc
                    }
                }

                if ($_buildExit -ne 0) {
                    $_failedBuildServices += $_svc
                    Write-AIError "$_svc build failed (see $_buildLog)"
                }
            }
            if ($_legacyBuilderServices.Count -gt 0) {
                Write-AIWarn "Used Docker legacy builder fallback for: $($_legacyBuilderServices -join ', ')"
            }
            if ($_defaultDockerConfigServices.Count -gt 0) {
                Use-ODSWindowsUserDockerConfig -Reason "local image builds: $($_defaultDockerConfigServices -join ', ')"
                Write-AIWarn "Continuing Compose preflight and service launch with the user's Docker config."
            }
            if ($_failedBuildServices.Count -gt 0) {
                Write-Host ""
                Write-AIError "Local image build failed: $($_failedBuildServices -join ', ')"
                Write-AI "Compose up will not be attempted because --no-build would otherwise use stale or missing images."
                if (Test-Path $_buildLog) {
                    Write-Host "  --- docker compose build log tail ---" -ForegroundColor DarkGray
                    Get-Content $_buildLog -Tail 60 | ForEach-Object { Write-Host "  $_" }
                }
                Write-ODSComposeDiagnostics -InstallDir $installDir -ComposeFlags $composeFlags `
                    -ComposeArgs (@("build", "--no-cache") + $_failedBuildServices) `
                    -ComposeLogPath $_buildLog `
                    -Phase "install-windows.ps1 local image build" `
                    -NextStep "Fix the local Dockerfile/build error shown above, then re-run .\install-windows.ps1." `
                    -SaveReport
                exit 1
            }
            Write-AISuccess "Local images rebuilt"

            $composePreflightOk = Invoke-ODSWindowsComposeImagePreflight -DockerClientArgs $script:ODSWindowsDockerClientArgs -ComposeFlags $composeFlags -LogPath $_composeLog
            if (-not $composePreflightOk -and $script:ODSWindowsDockerConfigMode -eq "install-scoped") {
                Write-AIWarn "Compose image preflight failed with the install-scoped Docker config; retrying with the user's Docker config."
                Add-Content -LiteralPath $_composeLog -Value "`n--- retrying Compose image preflight with user's Docker config after install-scoped config failure ---"
                Use-ODSWindowsUserDockerConfig -Reason "Compose image preflight"
                $composePreflightOk = Invoke-ODSWindowsComposeImagePreflight -DockerClientArgs $script:ODSWindowsDockerClientArgs -ComposeFlags $composeFlags -LogPath $_composeLog
                if ($composePreflightOk) {
                    Write-AIWarn "Continuing Compose service launch with the user's Docker config."
                }
            }

            Write-ODSWindowsComposeLaunchRecord -InstallDir $installDir -ComposeFlags $composeFlags `
                -ComposeArgs $composeUpArgs -DockerClientArgs $script:ODSWindowsDockerClientArgs
            $dockerLaunchLabel = if ($script:ODSWindowsDockerClientArgs.Count -eq 0) { "docker" } else { "docker $($script:ODSWindowsDockerClientArgs -join ' ')" }
            Write-AI "Running: $dockerLaunchLabel compose $($composeFlags -join ' ') $($composeUpArgs -join ' ')"
            Write-AI "Compose working directory: $installDir"

            if (-not $composePreflightOk) {
                Write-ODSComposeDiagnostics -InstallDir $installDir -ComposeFlags $composeFlags `
                    -ComposeArgs $composeUpArgs `
                    -ComposeLogPath $_composeLog `
                    -Phase "install-windows.ps1 compose image preflight" `
                    -NextStep "A required Compose image did not download during the retry-protected preflight. Fix Docker registry/network/disk access, then re-run .\install-windows.ps1." `
                    -SaveReport
                exit 1
            }

            Write-AI "Starting services... this may take several minutes."
            & docker @script:ODSWindowsDockerClientArgs compose @composeFlags @composeUpArgs *> $_composeLog
            $composeExit = $LASTEXITCODE
            if ($composeExit -ne 0 -and $script:ODSWindowsDockerConfigMode -eq "install-scoped") {
                Write-AIWarn "Compose service launch failed with the install-scoped Docker config; retrying with the user's Docker config."
                Add-Content -LiteralPath $_composeLog -Value "`n--- retrying Compose service launch with user's Docker config after install-scoped config failure ---"
                Use-ODSWindowsUserDockerConfig -Reason "Compose service launch"
                Write-ODSWindowsComposeLaunchRecord -InstallDir $installDir -ComposeFlags $composeFlags `
                    -ComposeArgs $composeUpArgs -DockerClientArgs $script:ODSWindowsDockerClientArgs
                & docker @script:ODSWindowsDockerClientArgs compose @composeFlags @composeUpArgs *>> $_composeLog
                $composeExit = $LASTEXITCODE
            }
        }
        finally {
            Pop-Location
            $ErrorActionPreference = $prevEAP
        }
        # Show tail of compose output for immediate feedback
        if (Test-Path $_composeLog) {
            Get-Content $_composeLog -Tail 20 | ForEach-Object { Write-Host "  $_" }
        }
        if ($composeExit -ne 0) {
            Write-AIError "docker compose up failed (exit code: $composeExit)"
            Write-ODSComposeDiagnostics -InstallDir $installDir -ComposeFlags $composeFlags `
                -ComposeArgs $composeUpArgs `
                -ComposeLogPath $_composeLog `
                -Phase "install-windows.ps1 docker compose up -d" `
                -SaveReport
            exit 1
        }
        Write-AISuccess "Docker services started"
        if (-not (Assert-ODSWindowsManagedContainers -InstallDir $installDir -ComposeFlags $composeFlags `
                    -RequiredServices @("dashboard", "dashboard-api", "open-webui"))) {
            Write-ODSComposeDiagnostics -InstallDir $installDir -ComposeFlags $composeFlags `
                -ComposeArgs @("ps", "-a") `
                -ComposeLogPath $_composeLog `
                -Phase "install-windows.ps1 managed container assertion" `
                -NextStep "Fix the missing Windows container stack shown above, then re-run .\install-windows.ps1." `
                -SaveReport
            exit 1
        }

        if ($enableHermes -and (Get-Command Invoke-HermesSoulRefresh -ErrorAction SilentlyContinue)) {
            Invoke-HermesSoulRefresh -InstallRoot $installDir -SyncContainer
        }

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
                $bashUpgradeLog = ($upgradeLog -replace "\\", "/" -replace "^([A-Za-z]):", '/$1').ToLower()
                $bashUpgradeErrLog = ($upgradeErrLog -replace "\\", "/" -replace "^([A-Za-z]):", '/$1').ToLower()
                $upgradePidFile = Join-Path $logDir "model-upgrade.pid"
                $upgradeLaunchLog = Join-Path $logDir "model-upgrade-launch.log"
                $upgradeLaunchErrLog = Join-Path $logDir "model-upgrade-launch-err.log"
                $upgradeTaskName = "ODSModelUpgrade"
                $bashUpgradePidFile = ($upgradePidFile -replace "\\", "/" -replace "^([A-Za-z]):", '/$1').ToLower()

                # Write a temp wrapper script to avoid Windows/PowerShell quoting
                # issues. Empty arguments (e.g., SHA256 for some tiers) get lost
                # during command-line parsing. The Scheduled Task owns the long
                # upgrade process directly; Start-ScheduledTask returns immediately,
                # while the task state/result keep reflecting the real download.
                $wrapperScript = Join-Path $logDir "bootstrap-run.sh"
                $wrapperContent = @"
#!/bin/bash
set -uo pipefail
mkdir -p "`$(dirname "$bashUpgradeLog")"
echo "`$`$" > "$bashUpgradePidFile"
exec bash "$bashScript" "$bashInstallDir" "$($fullTierConfig.GgufFile)" "$($fullTierConfig.GgufUrl)" "$($fullTierConfig.GgufSha256)" "$($fullTierConfig.LlmModel)" "$($fullTierConfig.MaxContext)" > "$bashUpgradeLog" 2> "$bashUpgradeErrLog" < /dev/null
"@
                [System.IO.File]::WriteAllText($wrapperScript, $wrapperContent.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))

                $bashPath = Get-UsableWindowsBash -InstallPath $installDir
                if ($bashPath) {
                    Remove-Item -LiteralPath $upgradePidFile, $upgradeLaunchLog, $upgradeLaunchErrLog -ErrorAction SilentlyContinue

                    $scheduled = $false
                    try {
                        try { Stop-ScheduledTask -TaskName $upgradeTaskName -ErrorAction SilentlyContinue } catch { }
                        try { Unregister-ScheduledTask -TaskName $upgradeTaskName -Confirm:$false -ErrorAction SilentlyContinue } catch { }

                        $upgradeAction = New-ScheduledTaskAction -Execute $bashPath -Argument ('"{0}"' -f $wrapperScript)
                        $upgradeTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
                        $upgradeSettings = New-ScheduledTaskSettingsSet `
                            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                            -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
                        $upgradePrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
                        Register-ScheduledTask -TaskName $upgradeTaskName `
                            -Action $upgradeAction `
                            -Trigger $upgradeTrigger `
                            -Settings $upgradeSettings `
                            -Principal $upgradePrincipal `
                            -Description "ODS background model upgrade" `
                            -Force | Out-Null
                        Start-ScheduledTask -TaskName $upgradeTaskName -ErrorAction Stop
                        $scheduled = $true
                    } catch {
                        Write-AIWarn "Could not register background upgrade task through Task Scheduler: $($_.Exception.Message)"
                        Write-AI "Starting background upgrade task directly..."
                        try {
                            Start-Process -FilePath $bashPath -ArgumentList ('"{0}"' -f $wrapperScript) -WindowStyle Hidden | Out-Null
                            $scheduled = $true
                        } catch {
                            Write-AIError "Failed to start background upgrade task directly: $_"
                        }
                    }

                    $pidDeadline = (Get-Date).AddSeconds(10)
                    while ($scheduled -and -not (Test-Path -LiteralPath $upgradePidFile) -and (Get-Date) -lt $pidDeadline) {
                        Start-Sleep -Milliseconds 250
                    }

                    if (Test-Path -LiteralPath $upgradePidFile) {
                        Write-AI "Full model ($($fullTierConfig.LlmModel)) downloading in background."
                        Write-AI "Check progress: Get-Content '$upgradeLog' -Tail 10"
                    } else {
                        if ($scheduled) {
                            try {
                                $taskState = (Get-ScheduledTask -TaskName $upgradeTaskName -ErrorAction Stop).State
                                Write-AIWarn "Background full-model download task did not write a PID file before continuing (task state: $taskState)."
                            } catch {
                                Write-AIWarn "Background full-model download task did not write a PID file before continuing."
                            }
                        }
                        Write-AI "  Retry manually with: & '$bashPath' '$wrapperScript'"
                        Write-AI "  Launcher log: $upgradeLaunchLog"
                        Write-AI "  Launcher error log: $upgradeLaunchErrLog"
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
    $_dryRunServicePlan = New-ODSWindowsServicePlan `
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
        -EnableODSProxy $enableODSProxy `
        -EnableRemoteAccess $enableRemoteAccess
    Write-AI "[DRY RUN] Would health-check selected services"
    if (Test-ODSWindowsServiceEnabled -ServiceId "perplexica" -Plan $_dryRunServicePlan) {
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

$windowsEnvMap = Get-WindowsODSEnvMap -InstallDir $installDir
$llmEndpoint = Get-WindowsLocalLlmEndpoint -InstallDir $installDir `
    -EnvMap $windowsEnvMap `
    -UseLemonade:$useLemonade -GpuBackend $gpuInfo.Backend -CloudMode:$cloudMode
$healthChecks = @(
    @{ Name = $llmEndpoint.Name; Url = $llmEndpoint.HealthUrl }
    @{ Name = "Chat UI (Open WebUI)"; Url = "http://localhost:3000" }
)
if ($enableVoice)     {
    $healthWhisperPort = if ($windowsEnvMap.ContainsKey("WHISPER_PORT") -and -not [string]::IsNullOrWhiteSpace($windowsEnvMap["WHISPER_PORT"])) { $windowsEnvMap["WHISPER_PORT"] } else { "9000" }
    $healthChecks += @{ Name = "Whisper (STT)"; Url = "http://localhost:$healthWhisperPort/health" }
}
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

# ── LLM model-serving gate ───────────────────────────────────────────────────
# A healthy LLM process is NOT proof the model can serve: if the GGUF backing file
# was never placed on disk, /v1/models still lists it but every completion 500s.
# Prove the file exists AND a minimal completion succeeds before this install may
# report healthy — otherwise fail loud instead of a "health says yes, chat says no"
# green install.
$llmModelReady = $true
if (-not $cloudMode) {
    Write-AI "Verifying the LLM can actually serve a completion..."
    $llmReady = Test-WindowsLlmModelReadiness -Endpoint $llmEndpoint -InstallDir $installDir `
        -GgufFile $tierConfig.GgufFile -TimeoutSec 120
    if ($llmReady.Ok) {
        Write-AISuccess "LLM serving verified (model: $($llmReady.ModelId))"
    } else {
        $allHealthy = $false
        $llmModelReady = $false
        Write-AIError "LLM not serving: $($llmReady.Detail)"
        if (-not $llmReady.FileExists) {
            Write-Host "    Model file missing: $($llmReady.ModelFile)" -ForegroundColor DarkGray
            Write-Host "    Re-run the installer to (re)download it: .\install.ps1" -ForegroundColor DarkGray
        }
    }
}

# ── Pre-download the Whisper STT model ───────────────────────────────────────
# Speaches does NOT auto-download on transcription requests — it returns 404.
# Trigger the download explicitly, verify it completed, surface recovery
# instructions on failure. Mirrors Linux Phase 12 and macOS install-macos.sh.
function Test-WindowsSttModelCached {
    param([Parameter(Mandatory=$true)][string]$ModelUrl)

    try {
        $check = Invoke-WebRequest -Uri $ModelUrl -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        return ($check.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Invoke-WindowsSttModelDownloadTrigger {
    param([Parameter(Mandatory=$true)][string]$ModelUrl)

    # Speaches keeps downloading after the request is accepted. Use a bounded
    # client so a slow Hugging Face transfer cannot take down the installer host.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source --fail --silent --show-error --max-time 30 -X POST $ModelUrl | Out-Null
        if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 28) { return $true }
        Write-AIWarn "STT model download trigger returned curl exit $LASTEXITCODE; verifying cache before failing."
        return $false
    }

    try {
        Invoke-WebRequest -Method POST -Uri $ModelUrl -TimeoutSec 30 -UseBasicParsing -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-AIWarn "STT model download trigger failed; verifying cache before failing."
        return $false
    }
}

function Wait-WindowsSttModelCached {
    param(
        [Parameter(Mandatory=$true)][string]$ModelUrl,
        [int]$TimeoutSeconds = 900
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-WindowsSttModelCached -ModelUrl $ModelUrl) { return $true }
        Start-Sleep -Seconds 5
    }
    return (Test-WindowsSttModelCached -ModelUrl $ModelUrl)
}

$sttModelReady = (-not $enableVoice)
$sttModelNameForReadiness = ""
$sttModelCacheUrl = ""
$sttRecoveryCmd = ""
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
    $sttRecoveryCmd = "curl.exe --max-time 30 -X POST '$whisperUrl/v1/models/$sttModelEncoded'"
    $sttModelNameForReadiness = $sttModel
    $sttModelCacheUrl = "$whisperUrl/v1/models/$sttModelEncoded"

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
        $sttModelReady = $false
        $allHealthy = $false
        Write-AIWarn "STT models API not ready -- download manually:"
        Write-Host "    $sttRecoveryCmd" -ForegroundColor DarkGray
    } else {
        # Step 2: skip if already cached.
        $alreadyCached = Test-WindowsSttModelCached -ModelUrl $sttModelCacheUrl

        if ($alreadyCached) {
            $sttModelReady = $true
            Write-AISuccess "STT model already cached ($sttModel)"
        } else {
            # Step 3: POST to trigger download.
            Write-AI "Downloading STT model ($sttModel)..."
            Invoke-WindowsSttModelDownloadTrigger -ModelUrl $sttModelCacheUrl | Out-Null

            # Step 4: verify the model is actually cached.
            $verified = Wait-WindowsSttModelCached -ModelUrl $sttModelCacheUrl -TimeoutSeconds 900

            if ($verified) {
                $sttModelReady = $true
                Write-AISuccess "STT model cached ($sttModel)"
            } else {
                $sttModelReady = $false
                $allHealthy = $false
                Write-AIWarn "STT model download failed -- run manually:"
                Write-Host "    $sttRecoveryCmd" -ForegroundColor DarkGray
            }
        }
    }
}

# ── Auto-configure Perplexica ─────────────────────────────────────────────────
if (Test-ODSWindowsServiceEnabled -ServiceId "perplexica" -Plan $servicePlan) {
    Write-AI "Configuring Perplexica..."
    $perplexicaModel = $(if ($tierConfig.GgufFile) {
        if ($useLemonade) { "extra.$($tierConfig.GgufFile)" } else { $tierConfig.GgufFile }
    } else {
        $tierConfig.LlmModel
    })
    $perplexicaBaseUrl = $(if ($useLemonade -or $cloudMode) {
        "http://litellm:4000/v1"
    } elseif ([string]$llmEndpoint["Backend"] -eq "native-llama-server") {
        "http://host.docker.internal:8080/v1"
    } else {
        "http://llama-server:8080/v1"
    })
    $perplexicaApiKey = "no-key"
    if (($useLemonade -or $cloudMode) -and $windowsEnvMap.ContainsKey("LITELLM_KEY") -and -not [string]::IsNullOrWhiteSpace($windowsEnvMap["LITELLM_KEY"])) {
        $perplexicaApiKey = $windowsEnvMap["LITELLM_KEY"]
    }
    $perplexicaOk = Set-PerplexicaConfig -PerplexicaPort 3004 -LlmModel $perplexicaModel -LlmBaseUrl $perplexicaBaseUrl -ApiKey $perplexicaApiKey
    if ($perplexicaOk) {
        Write-AISuccess "Perplexica configured (model: $perplexicaModel)"
    } else {
        Write-AIWarn "Perplexica auto-config skipped -- complete setup at http://localhost:3004"
    }
}

$readinessEnv = Get-WindowsODSEnvMap -InstallDir $installDir
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
$llmContainer = if ($useLemonade -or $cloudMode -or $gpuInfo.Backend -eq "amd") { "" } else { "ods-llama-server" }
$readinessChecks = @(
    @{ Name = "Dashboard"; Url = "http://localhost:$dashboardPort"; Container = "ods-dashboard"; OpenUrl = "http://localhost:$dashboardPort" }
    @{ Name = "Chat UI (Open WebUI)"; Url = "http://localhost:$webuiPort"; Container = "ods-webui"; OpenUrl = "http://localhost:$webuiPort" }
    @{ Name = $llmEndpoint.Name; Url = $llmEndpoint.HealthUrl; Container = $llmContainer; OpenUrl = $llmEndpoint.BaseUrl }
    @{ Name = "Dashboard API"; Url = "http://localhost:$dashboardApiPort/health"; Container = "ods-dashboard-api"; OpenUrl = "http://localhost:$dashboardApiPort" }
)
if (Test-ODSWindowsServiceEnabled -ServiceId "litellm" -Plan $servicePlan) {
    $litellmPort = Get-ReadinessPort -Name "LITELLM_PORT" -Default "4000"
    $readinessChecks += @{ Name = "LiteLLM"; Url = "http://localhost:$litellmPort/health/readiness"; Container = "ods-litellm"; OpenUrl = "http://localhost:$litellmPort" }
}
if (Test-ODSWindowsServiceEnabled -ServiceId "searxng" -Plan $servicePlan) {
    $searxngPort = Get-ReadinessPort -Name "SEARXNG_PORT" -Default "8888"
    $readinessChecks += @{ Name = "SearXNG"; Url = "http://localhost:$searxngPort/healthz"; Container = "ods-searxng"; OpenUrl = "http://localhost:$searxngPort" }
}
if (Test-ODSWindowsServiceEnabled -ServiceId "token-spy" -Plan $servicePlan) {
    $tokenSpyPort = Get-ReadinessPort -Name "TOKEN_SPY_PORT" -Default "3005"
    $readinessChecks += @{ Name = "Token Spy"; Url = "http://localhost:$tokenSpyPort/health"; Container = "ods-token-spy"; OpenUrl = "http://localhost:$tokenSpyPort" }
}
if ($enableVoice) {
    $whisperPort = Get-ReadinessPort -Name "WHISPER_PORT" -Default "9000"
    $ttsPort = Get-ReadinessPort -Name "TTS_PORT" -Default "8880"
    $readinessChecks += @{ Name = "Whisper (STT)"; Url = "http://localhost:$whisperPort/health"; Container = "ods-whisper"; OpenUrl = "http://localhost:$whisperPort" }
    if ($sttModelCacheUrl) {
        $readinessChecks += @{ Name = "Whisper STT model cache"; Url = $sttModelCacheUrl; Container = "ods-whisper"; OpenUrl = $sttModelNameForReadiness; Hint = "Run: $sttRecoveryCmd" }
    }
    $readinessChecks += @{ Name = "Kokoro (TTS)"; Url = "http://localhost:$ttsPort/health"; Container = "ods-tts"; OpenUrl = "http://localhost:$ttsPort" }
}
if ($enableWorkflows) {
    $n8nPort = Get-ReadinessPort -Name "N8N_PORT" -Default "5678"
    $readinessChecks += @{ Name = "n8n"; Url = "http://localhost:$n8nPort/healthz"; Container = "ods-n8n"; OpenUrl = "http://localhost:$n8nPort" }
}
if ($enableRag) {
    $qdrantPort = Get-ReadinessPort -Name "QDRANT_PORT" -Default "6333"
    $readinessChecks += @{ Name = "Qdrant"; Url = "http://localhost:$qdrantPort"; Container = "ods-qdrant"; OpenUrl = "http://localhost:$qdrantPort" }
}
if ($enableOpenClaw) {
    $openClawPort = Get-ReadinessPort -Name "OPENCLAW_PORT" -Default "7860"
    $readinessChecks += @{ Name = "OpenClaw"; Url = "http://localhost:$openClawPort"; Container = "ods-openclaw"; OpenUrl = "http://localhost:$openClawPort" }
}
if (Test-ODSWindowsServiceEnabled -ServiceId "hermes-proxy" -Plan $servicePlan) {
    $hermesProxyPort = Get-ReadinessPort -Name "HERMES_PROXY_PORT" -Default "9120"
    $readinessChecks += @{ Name = "Hermes Proxy"; Url = "http://localhost:$hermesProxyPort/health"; Container = "ods-hermes-proxy"; OpenUrl = "http://localhost:$hermesProxyPort" }
}
if ($enableComfyui) {
    $comfyPort = Get-ReadinessPort -Name "COMFYUI_PORT" -Default "8188"
    $readinessChecks += @{ Name = "ComfyUI"; Url = "http://localhost:$comfyPort"; Container = "ods-comfyui"; OpenUrl = "http://localhost:$comfyPort" }
}
if (Test-ODSWindowsServiceEnabled -ServiceId "perplexica" -Plan $servicePlan) {
    $perplexicaPort = Get-ReadinessPort -Name "PERPLEXICA_PORT" -Default "3004"
    $readinessChecks += @{ Name = "Perplexica"; Url = "http://localhost:$perplexicaPort"; Container = "ods-perplexica"; OpenUrl = "http://localhost:$perplexicaPort" }
}
if (Test-ODSWindowsServiceEnabled -ServiceId "privacy-shield" -Plan $servicePlan) {
    $privacyPort = Get-ReadinessPort -Name "SHIELD_PORT" -Default "8085"
    $readinessChecks += @{ Name = "Privacy Shield"; Url = "http://localhost:$privacyPort/health"; Container = "ods-privacy-shield"; OpenUrl = "http://localhost:$privacyPort" }
}
$installReadiness = Write-ODSInstallReadinessSummary -Checks $readinessChecks `
    -StatusCommand ".\ods.ps1 status" `
    -LogPath (Join-Path $installDir "logs\install.log") `
    -DashboardUrl "http://localhost:$dashboardPort" `
    -PassThru
if ($installReadiness -and $installReadiness.AllReady -and $llmModelReady -and $sttModelReady) {
    $allHealthy = $true
}

# ── Desktop & Start Menu shortcuts ───────────────────────────────────────────
try {
    $dashboardUrl  = "http://localhost:3001"
    $shortcutName  = "ODS"
    $iconPath      = Join-Path $installDir "extensions\services\dashboard\public\osmantic-os.ico"
    $iconContent   = if (Test-Path -LiteralPath $iconPath) { "IconFile=$iconPath`nIconIndex=0" } else { "IconIndex=0" }
    $urlContent    = "[InternetShortcut]`nURL=$dashboardUrl`n$iconContent`n"

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

    Write-AISuccess "Added ODS shortcut to Desktop and Start Menu"
} catch {
    Write-AIWarn "Could not create shortcuts: $_"
}

# ── Success card ──────────────────────────────────────────────────────────────
if ($allHealthy) {
    Write-SuccessCard
} else {
    Write-Host ""
    Write-AIWarn "Install finished, but one or more services are not ready yet. Check status with:"
    Write-Host "  .\ods.ps1 status" -ForegroundColor Cyan
    Write-AIWarn "ODS is not being marked fully healthy until readiness recovers."
    Write-Host ""
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
        version    = $script:ODS_VERSION
        tier       = $selectedTier
        tierName   = $tierConfig.TierName
        model      = $tierConfig.LlmModel
        gpuBackend = $gpuInfo.Backend
        gpuName    = $gpuInfo.Name
        installDir = $installDir
        sttModelCached = $sttModelReady
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

$global:LASTEXITCODE = 0
exit 0
