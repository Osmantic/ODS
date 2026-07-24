# ============================================================================
# ODS Windows Installer -- Phase 06: Directories & Configuration
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Create install directory tree, copy source files via robocopy,
#          generate .env with secure secrets, generate SearXNG settings.yml,
#          generate OpenClaw configs (if enabled), validate .env schema.
#
# Reads:
#   $installDir, $sourceRoot   -- from orchestrator context
#   $dryRun, $cloudMode        -- from orchestrator context
#   $selectedTier, $tierConfig -- from phase 02
#   $gpuInfo                   -- from phase 02
#   $llamaServerImage          -- from phase 02
#   $whisperCudaSupported      -- from phase 02
#   $enableOpenClaw            -- from phase 03
#   $openClawConfig            -- from phase 03
#
# Writes:
#   $envResult  -- hashtable: SearxngSecret, OpenclawToken
#
# Modder notes:
#   Add new directories to $_dirs array below.
#   Add new .env variables in lib/env-generator.ps1 New-ODSEnv function.
#   Add new config files (e.g., Perplexica config) as a New-XyzConfig function
#   in env-generator.ps1 and call it here.
# ============================================================================

Write-Phase -Phase 6 -Total 13 -Name "SETUP" -Estimate "~1-2 minutes"

if ($dryRun) {
    Write-AI "[DRY RUN] Would create: $installDir"
    Write-AI "[DRY RUN] Would copy source files via robocopy (excluding .git, data/, .env, models/)"
    Write-AI "[DRY RUN] Would generate .env with secure secrets (WEBUI_SECRET, N8N_PASS, LITELLM_KEY, ...)"
    Write-AI "[DRY RUN] Would generate SearXNG config with randomized secret key"
    Write-AI "[DRY RUN] Would copy ods.ps1 CLI + lib/ to install root"
    if ($enableOpenClaw) {
        Write-AI "[DRY RUN] Would generate OpenClaw configs (model: $($tierConfig.LlmModel))"
    }
    # Signal to later phases: no envResult in dry-run mode
    $envResult = @{
        SearxngSecret = "(dry-run-placeholder)"
        OpenclawToken = "(dry-run-placeholder)"
    }
    return
}

# ── Directory structure ───────────────────────────────────────────────────────
# NOTE: Nested Join-Path required for PS 5.1 (only accepts 2 path arguments).
$_configDir = Join-Path $installDir "config"
$_dataDir   = Join-Path $installDir "data"

$_dirs = @(
    (Join-Path $_configDir "searxng"),
    (Join-Path $_configDir "n8n"),
    (Join-Path $_configDir "litellm"),
    (Join-Path $_configDir "openclaw"),
    (Join-Path $_configDir "llama-server"),
    (Join-Path $_dataDir "auth"),
    (Join-Path $_dataDir "config"),
    (Join-Path $_dataDir "config-backups"),
    (Join-Path $_dataDir "extension-progress"),
    (Join-Path $_dataDir "open-webui"),
    (Join-Path $_dataDir "whisper"),
    (Join-Path $_dataDir "tts"),
    (Join-Path $_dataDir "n8n"),
    (Join-Path $_dataDir "qdrant"),
    (Join-Path $_dataDir "models"),
    (Join-Path $_dataDir "user-extensions"),
    (Join-Path $_dataDir "extensions-library"),
    (Join-Path $_dataDir "comfyui"),
    (Join-Path $_dataDir "perplexica"),
    (Join-Path $_dataDir "ape"),
    (Join-Path $_dataDir "token-spy"),
    (Join-Path $_dataDir "privacy-shield"),
    (Join-Path $_dataDir "hermes"),
    (Join-Path $_dataDir "persona"),
    (Join-Path (Join-Path $_dataDir "hermes-proxy") "caddy-data"),
    (Join-Path (Join-Path $_dataDir "hermes-proxy") "caddy-config")
)
foreach ($_d in $_dirs) {
    New-Item -ItemType Directory -Path $_d -Force | Out-Null
}
Write-AISuccess "Created directory structure under $installDir"

# Docker/PowerShell partial installs and stale Docker bind mounts can leave
# directories where install-owned regular files must exist. Remove those
# malformed paths before robocopy and config generation, otherwise later reads
# fail with "access denied".
$_expectedRegularFiles = @(
    ".env",
    ".env.example",
    ".env.schema.json",
    "config\llama-server\models.ini",
    "config\litellm\local.yaml",
    "config\litellm\lemonade.yaml",
    "config\litellm\switchboard.yaml",
    "data\.extensions-lock",
    "extensions\services\hermes\cli-config.yaml.template",
    "extensions\services\hermes\SOUL.md.template",
    "extensions\services\hermes-proxy\Caddyfile",
    "extensions\services\ods-proxy\Caddyfile",
    "extensions\services\whisper\docker-entrypoint.sh",
    "extensions\services\perplexica\docker-entrypoint.sh",
    "data\persona\SOUL.md"
)
foreach ($_expectedFileName in $_expectedRegularFiles) {
    $_expectedFilePath = Join-Path $installDir $_expectedFileName
    if (Test-Path -LiteralPath $_expectedFilePath -PathType Container) {
        Remove-Item -LiteralPath $_expectedFilePath -Recurse -Force
        Write-AIWarn "Removed malformed $_expectedFileName directory from a previous partial install."
    }
}

$_containerWritableDirs = @(
    $_dataDir,
    (Join-Path $_dataDir "auth"),
    (Join-Path $_dataDir "config"),
    (Join-Path $_dataDir "config-backups"),
    (Join-Path $_dataDir "extension-progress"),
    (Join-Path $_dataDir "n8n"),
    (Join-Path $_dataDir "user-extensions")
)
foreach ($_writableDir in $_containerWritableDirs) {
    if (Test-Path -LiteralPath $_writableDir -PathType Container) {
        & icacls $_writableDir /grant "*S-1-1-0:(OI)(CI)M" /T /C /Q | Out-Null
    }
}
$_extensionsLock = Join-Path $_dataDir ".extensions-lock"
if (-not (Test-Path -LiteralPath $_extensionsLock -PathType Leaf)) {
    New-Item -ItemType File -Path $_extensionsLock -Force | Out-Null
}
& icacls $_extensionsLock /grant "*S-1-1-0:M" /C /Q | Out-Null

# ── Copy source tree (skip if running in-place) ───────────────────────────────
if ($sourceRoot -ne $installDir) {
    Write-AI "Copying source files to $installDir..."

    # robocopy exit codes 0-7 are success (bits for files copied, extras, etc.)
    $robocopyArgs = @(
        $sourceRoot, $installDir,
        "/E",                                  # Copy subdirectories including empty ones
        "/NFL", "/NDL", "/NJH", "/NJS",        # Suppress file/dir/job headers (clean output)
        "/XD", ".git", "data", "logs", "models", "node_modules", "dist",
        "/XF", ".env", "*.log", ".current-mode", ".profiles",
               ".target-model", ".target-quantization", ".offline-mode"
    )
    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        Write-AIError "File copy failed (robocopy exit code: $LASTEXITCODE)."
        Write-AI "  Try re-running with --Force or check that $installDir is writable."
        throw "ODS_INSTALL_ABORTED"
    }
    Write-AISuccess "Source files installed to $installDir"
} else {
    Write-AI "Running in-place (source == install directory) -- skipping file copy"
}

# Copy extensions library to data dir for dashboard portal installs.
# Linux/macOS do this in Phase 06 as well; Windows needs the same deployed
# data/extensions-library tree or dashboard-api refuses /api/extensions/*/install.
$_extLibDst = Join-Path $_dataDir "extensions-library"
$_extLibSrc = $null
$_extLibCandidates = @(
    (Join-Path $sourceRoot "extensions\library\services"),
    (Join-Path $installDir "extensions\library\services"),
    (Join-Path $installDir "extensions-library-bundle\services")
)
foreach ($_candidate in $_extLibCandidates) {
    if (Test-Path -LiteralPath $_candidate) {
        $_extLibSrc = $_candidate
        break
    }
}
if ($_extLibSrc) {
    New-Item -ItemType Directory -Path $_extLibDst -Force | Out-Null
    Copy-Item -Path (Join-Path $_extLibSrc "*") -Destination $_extLibDst -Recurse -Force
    Write-AISuccess "Extensions library copied to data/extensions-library (from $_extLibSrc)"
} else {
    Write-AIWarn "Extensions library not found; dashboard Extensions page will return 503 until populated"
}

# ── Copy ods.ps1 CLI + lib/ ─────────────────────────────────────────────────
# Retired from the shipped stack after Hermes became the default agent surface.
# Remove stale service files left behind by non-pruning upgrades, while
# preserving data/odsforge for user-controlled archival.
$_retiredODSForge = Join-Path $installDir "extensions\services\odsforge"
if (Test-Path $_retiredODSForge) {
    Remove-Item -LiteralPath $_retiredODSForge -Recurse -Force
    Write-AI "Removed retired ODSForge service files from extensions/services"
}

# Copy extensions library to data dir for dashboard portal. Keep this in
# parity with Linux phase 06 and macOS install: dashboard-api installs
# optional extensions from data/extensions-library, not from the source tree.
$_extLibSrc = $null
foreach ($_candidate in @(
    (Join-Path $sourceRoot "extensions\library\services"),
    (Join-Path $installDir "extensions\library\services"),
    (Join-Path $installDir "extensions-library-bundle\services")
)) {
    if (Test-Path $_candidate) {
        $_extLibSrc = $_candidate
        break
    }
}
if ($null -ne $_extLibSrc) {
    $_extLibDst = Join-Path $installDir "data\extensions-library"
    New-Item -ItemType Directory -Path $_extLibDst -Force | Out-Null
    Copy-Item -Path (Join-Path $_extLibSrc "*") -Destination $_extLibDst -Recurse -Force
    Write-AISuccess "Extensions library copied to data/extensions-library (from $_extLibSrc)"
} else {
    Write-AIWarn "Extensions library not found; dashboard Extensions page will return 503 until populated"
}

# Copies from the Windows installer directory to the install root so users
# can manage ODS with: .\ods.ps1 status
# $ScriptDir is set by install-windows.ps1 (installers/windows/) and is
# visible here because phases are dot-sourced in the orchestrator's scope.
$_scriptDir = $ScriptDir   # installers/windows/
$_odsSrc  = Join-Path $_scriptDir "ods.ps1"
$_odsDst  = Join-Path $installDir "ods.ps1"
if (Test-Path $_odsSrc) {
    Copy-Item -Path $_odsSrc -Destination $_odsDst -Force
    # Also copy lib/ so ods.ps1 can find its helper functions
    $_libSrc = Join-Path $_scriptDir "lib"
    $_libDst = Join-Path $installDir "lib"
    New-Item -ItemType Directory -Path $_libDst -Force | Out-Null
    Copy-Item -Path (Join-Path $_libSrc "*") -Destination $_libDst -Recurse -Force
    $_cmdShim = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%~dp0ods.ps1"" %*`r`n"
    Write-Utf8NoBom -Path (Join-Path $installDir "ods.cmd") -Content $_cmdShim
    Write-Utf8NoBom -Path (Join-Path $installDir "ods-cli.cmd") -Content $_cmdShim

    try {
        $_userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $_pathParts = @()
        if (-not [string]::IsNullOrWhiteSpace($_userPath)) {
            $_pathParts = @($_userPath -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        }
        $_installPathPresent = $false
        foreach ($_pathPart in $_pathParts) {
            if ($_pathPart.TrimEnd("\") -ieq $installDir.TrimEnd("\")) {
                $_installPathPresent = $true
                break
            }
        }
        if (-not $_installPathPresent) {
            $_newUserPath = (@($_pathParts) + $installDir) -join ";"
            [Environment]::SetEnvironmentVariable("Path", $_newUserPath, "User")
            if (($env:Path -split ";" | Where-Object { $_.TrimEnd("\") -ieq $installDir.TrimEnd("\") }).Count -eq 0) {
                $env:Path = "$env:Path;$installDir"
            }
            Write-AISuccess "Added ODS CLI directory to user PATH"
        }
    } catch {
        Write-AIWarn "Could not add ODS CLI directory to user PATH: $_"
        Write-AI "  You can still run: $installDir\ods.cmd"
    }
    Write-AISuccess "Installed ODS CLI shims (ods, ods-cli)"
} else {
    Write-AIWarn "ods.ps1 not found at $_odsSrc -- CLI management unavailable"
}

# ── Generate .env with secure secrets ────────────────────────────────────────
$_odsMode = $(if ($cloudMode) { "cloud" } else { "local" })
$_amdInferenceRuntime = ""
$_amdInferenceBackend = ""
$_amdInferenceLocation = ""
$_amdInferencePort = ""
$_amdInferenceSupportedBackends = ""
$_amdInferenceRuntimeMode = ""
$_amdInferenceManaged = ""
$_lemonadeServerImage = ""
if ($gpuInfo.Backend -eq "amd" -and -not $cloudMode) {
    $_amdInferenceRuntime = "lemonade"
    $_amdInferenceBackend = $(if ($amdLemonadeRuntime -and $amdLemonadeRuntime.windows_backend) { $amdLemonadeRuntime.windows_backend } else { "vulkan" })
    $_amdInferenceLocation = "host"
    $_amdInferencePort = [string]$script:LEMONADE_PORT
    $_amdInferenceSupportedBackends = $_amdInferenceBackend
    $_amdInferenceRuntimeMode = "windows-legacy-lemonade"
    $_amdInferenceManaged = "true"
}
if ($amdLemonadeRuntime -and $amdLemonadeRuntime.container_image) {
    $_lemonadeServerImage = $amdLemonadeRuntime.container_image
}
$envResult = New-ODSEnv `
    -InstallDir     $installDir `
    -TierConfig     $tierConfig `
    -Tier           $selectedTier `
    -GpuBackend     $gpuInfo.Backend `
    -ODSMode      $_odsMode `
    -LlamaServerImage $llamaServerImage `
    -AmdInferenceRuntime $_amdInferenceRuntime `
    -AmdInferenceBackend $_amdInferenceBackend `
    -AmdInferenceLocation $_amdInferenceLocation `
    -AmdInferencePort $_amdInferencePort `
    -AmdInferenceSupportedBackends $_amdInferenceSupportedBackends `
    -AmdInferenceRuntimeMode $_amdInferenceRuntimeMode `
    -AmdInferenceManaged $_amdInferenceManaged `
    -LemonadeServerImage $_lemonadeServerImage `
    -SystemRamGB    $systemRamGB `
    -WhisperCudaEnabled $whisperCudaSupported `
    -EnableLangfuse $enableLangfuse `
    -SwitchboardMode $env:ODS_MODEL_SWITCHBOARD `
    -EnableLan      $lanFlag `
    -ModelsDir      $modelsDir
$_resolvedModelsDir = Get-ODSModelsDir -InstallDir $installDir -ModelsDirOverride $modelsDir
if (-not (Test-Path $_resolvedModelsDir)) {
    New-Item -ItemType Directory -Path $_resolvedModelsDir -Force | Out-Null
}
Write-AISuccess "Generated .env with secure secrets"

# ── Post-generation validation: verify all required keys are present with values ──
# Defense-in-depth: catches silent failures in env generation before docker compose
# hits the ${VAR:?} hard-fail syntax and produces a confusing error.
# NOTE: Only checks keys that use :? (required non-empty) in compose files.
# Keys like ANTHROPIC_API_KEY= are intentionally empty and not checked here.
$_envPath = Join-Path $installDir ".env"
$_requiredKeys = @("WEBUI_SECRET", "N8N_PASS", "LITELLM_KEY", "OPENCLAW_TOKEN", "DASHBOARD_API_KEY")
$_envLines = @{}
if (Test-Path $_envPath) {
    Get-Content $_envPath | ForEach-Object {
        if ($_ -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
            $_envLines[$Matches[1]] = $Matches[2]
        }
    }
}
if ($_amdInferenceRuntime -eq "lemonade") {
    $_requiredKeys += "LEMONADE_MODEL"
}
$_missingKeys = @()
foreach ($_k in $_requiredKeys) {
    if (-not $_envLines.ContainsKey($_k) -or -not $_envLines[$_k]) {
        $_missingKeys += $_k
    }
}
if ($_missingKeys.Count -gt 0) {
    Write-AIError ".env is missing required keys: $($_missingKeys -join ', ')"
    Write-AI "  This will cause docker compose to fail. The .env file may be corrupted."
    Write-AI "  Try deleting $(Join-Path $installDir '.env') and re-running the installer."
    throw "ODS_INSTALL_ABORTED"
}
Write-AISuccess "Verified .env contains all required secrets"

function Update-HermesConfigFile {
    param(
        [string]$Path,
        [string]$Model,
        [string]$BaseUrl,
        [int]$ContextLength,
        [int]$RequestTimeoutSeconds = 180,
        [switch]$LemonadeCompact
    )

    if (-not (Test-Path $Path)) { return $false }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $content = [System.IO.File]::ReadAllText($Path, $utf8NoBom)
    $content = $content -replace '(?m)^  default: ".*"\r?$', "  default: `"$Model`""
    $content = $content -replace '(?m)^  base_url: ".*"\r?$', "  base_url: `"$BaseUrl`""
    $content = $content -replace '(?m)^  context_length: .+\r?$', "  context_length: $ContextLength"
    $content = $content -replace '(?m)^    context_length: .+\r?$', "    context_length: $ContextLength"
    if ($RequestTimeoutSeconds -lt 1) { $RequestTimeoutSeconds = 180 }

    $timeoutMatch = [regex]::Match($content, '(?m)^    request_timeout_seconds:\s*(\d+)\s*$')
    if ($timeoutMatch.Success) {
        if ($timeoutMatch.Groups[1].Value -eq "180" -and $RequestTimeoutSeconds -ne 180) {
            $content = [regex]::Replace(
                $content,
                '(?m)^    request_timeout_seconds:\s*180\s*$',
                "    request_timeout_seconds: $RequestTimeoutSeconds"
            )
        }
    } elseif ($content -match '(?m)^  custom:\s*$') {
        $content = $content -replace '(?m)^  custom:\s*$', "  custom:`n    request_timeout_seconds: $RequestTimeoutSeconds"
    } elseif ($content -match '(?m)^providers:\s*$') {
        $content = $content -replace '(?m)^providers:\s*$', "providers:`n  custom:`n    request_timeout_seconds: $RequestTimeoutSeconds"
    } elseif ($content -match '(?m)^auxiliary:\s*$') {
        $content = $content -replace '(?m)^auxiliary:\s*$', "providers:`n  custom:`n    request_timeout_seconds: $RequestTimeoutSeconds`n`nauxiliary:"
    } else {
        $content += "`nproviders:`n  custom:`n    request_timeout_seconds: $RequestTimeoutSeconds`n"
    }

    if ($content -notmatch '(?m)^auxiliary:\s*$') {
        if ($content -match '(?m)^terminal:\s*$') {
            $content = $content -replace '(?m)^terminal:\s*$', "auxiliary:`n  compression:`n    context_length: $ContextLength`n`nterminal:"
        } else {
            $content += "`nauxiliary:`n  compression:`n    context_length: $ContextLength`n"
        }
    } elseif ($content -notmatch '(?m)^  compression:\s*$') {
        $content = $content -replace '(?m)^auxiliary:\s*$', "auxiliary:`n  compression:`n    context_length: $ContextLength"
    }

    if ($content -notmatch '(?m)^compression:\s*$') {
        $content += "`ncompression:`n  enabled: true`n  threshold: 0.50`n  target_ratio: 0.20`n  protect_last_n: 20`n"
    } else {
        if ($content -notmatch '(?m)^  enabled:') {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  enabled: true"
        }
        if ($content -match '(?m)^  threshold:') {
            $content = $content -replace '(?m)^  threshold: .+$', "  threshold: 0.50"
        } else {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  threshold: 0.50"
        }
        if ($content -match '(?m)^  target_ratio:') {
            $content = $content -replace '(?m)^  target_ratio: .+$', "  target_ratio: 0.20"
        } else {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  target_ratio: 0.20"
        }
        if ($content -notmatch '(?m)^  protect_last_n:') {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  protect_last_n: 20"
        }
    }

    if ($LemonadeCompact) {
        $compactAgent = @"
agent:
  disabled_toolsets:
    - terminal
    - browser
    - vision
    - video
    - image_gen
    - video_gen
    - x_search
    - moa
    - tts
    - skills
    - todo
    - memory
    - session_search
    - clarify
    - delegation
    - cronjob
    - messaging
    - homeassistant
    - spotify
    - yuanbao
    - computer_use
"@
        if ($content -match '(?ms)^agent:\r?\n.*?(?=^terminal:|^platforms:|^compression:|\z)') {
            $content = [regex]::Replace($content, '(?ms)^agent:\r?\n.*?(?=^terminal:|^platforms:|^compression:|\z)', "$compactAgent`n")
        } elseif ($content -match '(?m)^terminal:\s*$') {
            $content = $content -replace '(?m)^terminal:\s*$', "$compactAgent`nterminal:"
        } else {
            $content += "`n$compactAgent`n"
        }
    }

    [System.IO.File]::WriteAllText($Path, $content, $utf8NoBom)
    $verified = [System.IO.File]::ReadAllText($Path, $utf8NoBom)
    if (-not $verified.Contains("  default: `"$Model`"")) { return $false }
    if (-not $verified.Contains("  base_url: `"$BaseUrl`"")) { return $false }
    return $true
}

function Invoke-HermesSoulRefresh {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [switch]$SyncContainer
    )

    $_builder = Join-Path (Join-Path $InstallRoot "scripts") "build-installation-context.py"
    $_template = Join-Path (Join-Path (Join-Path $InstallRoot "extensions") "services\hermes") "SOUL.md.template"
    $_envPath = Join-Path $InstallRoot ".env"
    $_output = Join-Path (Join-Path (Join-Path $InstallRoot "data") "persona") "SOUL.md"
    $_outputDir = Split-Path -Parent $_output

    if (-not (Test-Path $_template)) {
        Write-AIWarn "Hermes SOUL.md template not found at $_template"
        return
    }

    New-Item -ItemType Directory -Path $_outputDir -Force | Out-Null
    $_rendered = $false
    $_profileArgs = @()
    try {
        $_envText = Get-Content -LiteralPath $_envPath -Raw -ErrorAction Stop
        if ($_envText -match '(?m)^LLM_BACKEND=lemonade\s*$' -and
            $_envText -match '(?m)^AMD_INFERENCE_RUNTIME=lemonade\s*$') {
            $_profileArgs = @("--profile", "local-lemonade")
        }
    } catch { }

    if (Test-Path $_builder) {
        $_pythonCandidates = @(
            @{ Command = "python"; Args = @() },
            @{ Command = "python3"; Args = @() },
            @{ Command = "py"; Args = @("-3") }
        )

        foreach ($_candidate in $_pythonCandidates) {
            $_cmd = Get-Command $_candidate.Command -ErrorAction SilentlyContinue
            if (-not $_cmd -or -not $_cmd.Source) { continue }

            try {
                & $_cmd.Source @($_candidate.Args) $_builder "--template" $_template "--env" $_envPath "--output" $_output @_profileArgs *>> $script:ODS_LOG_FILE
                if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $_output -PathType Leaf)) {
                    $_rendered = $true
                    break
                }
            } catch {
                $_msg = $_.Exception.Message
                Add-Content -Path $script:ODS_LOG_FILE -Value "Hermes SOUL.md render failed with $($_candidate.Command): $_msg"
            }
        }
    }

    if (-not $_rendered) {
        if (Test-Path -LiteralPath $_output -PathType Container) {
            Remove-Item -LiteralPath $_output -Recurse -Force
        }
        if (-not (Test-Path -LiteralPath $_output -PathType Leaf)) {
            $_content = Get-Content -LiteralPath $_template -Raw
            $_content = $_content -replace "(?m)^\s*<!-- INSTALLATION_CONTEXT -->\s*\r?\n?", ""
            Write-Utf8NoBom -Path $_output -Content $_content
            Write-AIWarn "Generated fallback Hermes SOUL.md without dynamic installation context"
        }
    }

    if ($SyncContainer) {
        $_names = & docker ps --format "{{.Names}}" 2>$null
        if ($_names -contains "ods-hermes") {
            & docker exec ods-hermes cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md *>> $script:ODS_LOG_FILE
            if ($LASTEXITCODE -eq 0) {
                Write-AISuccess "Synced Hermes SOUL.md into running container"
            } else {
                Write-AIWarn "Could not sync Hermes SOUL.md into running container"
            }
        }
    }
}

if ($enableHermes) {
    $_switchboardMode = $(if ($_envLines.ContainsKey("ODS_MODEL_SWITCHBOARD")) {
        ([string]$_envLines["ODS_MODEL_SWITCHBOARD"]).Trim().ToLowerInvariant()
    } else {
        "observe"
    })
    $_hermesModel = $(if ($tierConfig.GgufFile) {
        if ($gpuInfo.Backend -eq "amd" -and
            $_envLines.ContainsKey("LEMONADE_MODEL") -and
            -not [string]::IsNullOrWhiteSpace([string]$_envLines["LEMONADE_MODEL"])) {
            $_envLines["LEMONADE_MODEL"].Trim().Trim('"').Trim("'")
        } else {
            $tierConfig.GgufFile
        }
    } else {
        $tierConfig.LlmModel
    })
    if ($_switchboardMode -eq "enabled") {
        $_hermesModel = "ods/current"
    }
    $_hermesBaseUrl = ""
    if ($_envLines.ContainsKey("HERMES_LLM_BASE_URL")) {
        $_hermesBaseUrl = $_envLines["HERMES_LLM_BASE_URL"].Trim().Trim('"').Trim("'")
    }
    if ([string]::IsNullOrWhiteSpace($_hermesBaseUrl)) {
        $_hermesBaseUrl = $(if ($cloudMode -or $gpuInfo.Backend -eq "amd" -or $_switchboardMode -eq "enabled") {
            "http://litellm:4000/v1"
        } else {
            "http://llama-server:8080/v1"
        })
    }
    $_hermesTemplate = Join-Path (Join-Path (Join-Path $installDir "extensions") "services\hermes") "cli-config.yaml.template"
    $_hermesLive = Join-Path (Join-Path $installDir "data\hermes") "config.yaml"
    if (-not (Test-Path $_hermesTemplate)) {
        Write-AIError "Missing Hermes config template at $_hermesTemplate"
        throw "ODS_INSTALL_ABORTED"
    }
    if (-not (Test-Path $_hermesLive)) {
        Copy-Item -Path $_hermesTemplate -Destination $_hermesLive -Force
    }
    $_hermesRequestTimeout = $(if ($cloudMode -and $_switchboardMode -ne "enabled") { 180 } else { 900 })
    $_patchedHermesTemplate = Update-HermesConfigFile -Path $_hermesTemplate -Model $_hermesModel -BaseUrl $_hermesBaseUrl -ContextLength ([int]$tierConfig.MaxContext) -RequestTimeoutSeconds $_hermesRequestTimeout -LemonadeCompact:($gpuInfo.Backend -eq "amd")
    $_patchedHermesLive = Update-HermesConfigFile -Path $_hermesLive -Model $_hermesModel -BaseUrl $_hermesBaseUrl -ContextLength ([int]$tierConfig.MaxContext) -RequestTimeoutSeconds $_hermesRequestTimeout -LemonadeCompact:($gpuInfo.Backend -eq "amd")
    if (-not ($_patchedHermesTemplate -and $_patchedHermesLive)) {
        Write-AIError "Failed to patch Hermes config for Windows runtime (model=$_hermesModel, base_url=$_hermesBaseUrl)"
        throw "ODS_INSTALL_ABORTED"
    }
    Invoke-HermesSoulRefresh -InstallRoot $installDir
    Write-AISuccess "Patched Hermes config (model=$_hermesModel, context=$($tierConfig.MaxContext), request_timeout=${_hermesRequestTimeout}s)"
}

# ── Generate SearXNG config ───────────────────────────────────────────────────
$_searxngPath = New-SearxngConfig -InstallDir $installDir -SecretKey $envResult.SearxngSecret
Write-AISuccess "Generated SearXNG config ($_searxngPath)"

# ── Generate OpenClaw configs ─────────────────────────────────────────────────
if ($enableOpenClaw) {
    # On Windows, AMD native inference server is reachable from Docker containers
    # via host.docker.internal; NVIDIA runs in Docker as llama-server service name.
    # Lemonade serves at /api/v1, so OpenClaw base URL needs /api prefix
    # (OpenClaw appends /v1/chat/completions to the base URL)
    $_providerUrl = $(if ($gpuInfo.Backend -eq "amd") {
        "http://host.docker.internal:$($script:LEMONADE_PORT)/api"
    } else {
        "http://llama-server:8080"
    })

    New-OpenClawConfig `
        -InstallDir   $installDir `
        -LlmModel     $tierConfig.LlmModel `
        -MaxContext   $tierConfig.MaxContext `
        -Token        $envResult.OpenclawToken `
        -ProviderUrl  $_providerUrl
    Write-AISuccess "Generated OpenClaw configs (model: $($tierConfig.LlmModel))"

    # Select and copy the tier-appropriate OpenClaw agent profile
    if ($openClawConfig) {
        $_ocSrcProfile = Join-Path (Join-Path $installDir "config\openclaw") $openClawConfig
        $_ocDstProfile = Join-Path (Join-Path $installDir "config\openclaw") "openclaw.json"
        if (Test-Path $_ocSrcProfile) {
            $_ocSrcResolved = (Resolve-Path $_ocSrcProfile).Path
            $_ocDstResolved = [System.IO.Path]::GetFullPath($_ocDstProfile)
            if ($_ocSrcResolved -ieq $_ocDstResolved) {
                Write-AISuccess "OpenClaw profile already installed: $openClawConfig"
            } else {
                Copy-Item -Path $_ocSrcProfile -Destination $_ocDstProfile -Force
                Write-AISuccess "Installed OpenClaw profile: $openClawConfig -> openclaw.json"
            }
        } else {
            Write-AIError "Missing OpenClaw config $openClawConfig and no fallback present in repo. This is a packaging bug; please re-clone or report."
            throw "ODS_INSTALL_ABORTED"
        }
    }
}

# ── Create llama-server models.ini stub ──────────────────────────────────────
$_modelsIni = Join-Path (Join-Path $installDir "config\llama-server") "models.ini"
if (-not (Test-Path $_modelsIni)) {
    Write-Utf8NoBom -Path $_modelsIni -Content "# ODS model registry`n"
}

# ── .env schema validation ────────────────────────────────────────────────────
# Validates the generated .env against .env.schema.json using Python if available.
# Non-fatal on Windows: Python may not be present, and the schema validator is
# primarily a CI gate. A warning is shown but installation continues.
$_schemaJson = Join-Path $installDir ".env.schema.json"
if (Test-Path $_schemaJson) {
    # Locate Python (python3 preferred, python fallback)
    $_pyCmd = $null
    foreach ($_pyTry in @("python3", "python")) {
        $_pyFound = Get-Command $_pyTry -ErrorAction SilentlyContinue
        if ($_pyFound) { $_pyCmd = $_pyTry; break }
    }

    if ($_pyCmd) {
        $_validateScript = Join-Path $installDir "scripts\validate-env.sh"
        if (-not (Test-Path $_validateScript)) {
            # Use inline Python for schema validation (no bash dependency)
            $_envPath = Join-Path $installDir ".env"
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            $_pyOut = & $_pyCmd -c @"
import json, sys, re
env_path = r'$($_envPath -replace "\\", "\\")'
schema_path = r'$($_schemaJson -replace "\\", "\\")'
try:
    schema = json.load(open(schema_path, encoding='utf-8'))
    required = schema.get('required', [])
    props = schema.get('properties', {})
    env = {}
    for line in open(env_path, encoding='utf-8'):
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)', line.strip())
        if m: env[m.group(1)] = m.group(2)
    missing = [k for k in required if k not in env]
    if missing:
        print('MISSING: ' + ', '.join(missing))
        sys.exit(1)
    print('OK')
except Exception as e:
    print(f'SKIP: {e}')
"@ 2>&1
            $ErrorActionPreference = $prevEAP
            if ($_pyOut -match "^OK") {
                Write-AISuccess "Validated .env against .env.schema.json"
            } elseif ($_pyOut -match "^MISSING") {
                Write-AIWarn ".env schema validation warning: $_pyOut"
            } else {
                Write-AIWarn ".env schema validation skipped: $_pyOut"
            }
        }
    } else {
        Write-AIWarn ".env schema validation skipped (Python not found -- install Python 3 for validation)"
    }
} else {
    Write-AIWarn ".env.schema.json not found -- skipping schema validation"
}

Write-AISuccess "Setup complete"
