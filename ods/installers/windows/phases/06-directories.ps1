# ============================================================================
# ODS Windows Installer -- Phase 06: Directories & Configuration
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Create install directory tree, copy source files via robocopy,
#          generate .env with secure secrets, generate SearXNG settings.yml,
#          validate .env schema.
#
# Reads:
#   $installDir, $sourceRoot   -- from orchestrator context
#   $dryRun, $cloudMode        -- from orchestrator context
#   $selectedTier, $tierConfig -- from phase 02
#   $gpuInfo                   -- from phase 02
#   $llamaServerImage          -- from phase 02
#   $whisperCudaSupported      -- from phase 02
#   $enableRecommended, $enableDeepResearch, $enableHermes -- from phase 03
#
# Writes:
#   $envResult  -- hashtable: SearxngSecret
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
    # Signal to later phases: no envResult in dry-run mode
    $envResult = @{
        SearxngSecret = "(dry-run-placeholder)"
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
    (Join-Path (Join-Path $_dataDir "remote-provider") "secrets"),
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
    "config\litellm\switchboard.yaml",
    "data\.extensions-lock",
    "extensions\services\litellm\select-config.sh",
    "extensions\services\litellm\ods_token_spy_callback.py",
    "extensions\services\hermes\cli-config.yaml.template",
    "extensions\services\hermes\SOUL.md.template",
    "extensions\services\hermes-proxy\Caddyfile",
    "extensions\services\ods-proxy\Caddyfile",
    "extensions\services\whisper\docker-entrypoint.sh",
    "extensions\services\perplexica\docker-entrypoint.sh",
    "extensions\services\perplexica\patch-client-citations.js",
    "extensions\services\perplexica\citation-renderer.js",
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

    $pruneStaleDevPaths = (
        (Test-Path -LiteralPath (Join-Path $installDir ".env") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $installDir "manifest.json") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $installDir "docker-compose.base.yml") -PathType Leaf)
    )
    $devOnlyDirectories = @("tests", "docs", "examples", ".github")
    $devOnlyFiles = @(
        "CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md",
        "EDGE-QUICKSTART.md", "FAQ.md", "QUICKSTART.md",
        "SECURITY.md", "README.md",
        ".shellcheckrc", "PSScriptAnalyzerSettings.psd1",
        "test-stack.sh", ".gitignore"
    )

    # robocopy exit codes 0-7 are success (bits for files copied, extras, etc.)
    $robocopyArgs = @(
        $sourceRoot, $installDir,
        "/E",                                  # Copy subdirectories including empty ones
        "/NFL", "/NDL", "/NJH", "/NJS",        # Suppress file/dir/job headers (clean output)
        "/XD", ".git", "data", "logs", "models", "node_modules", "dist"
    )
    $robocopyArgs += @($devOnlyDirectories | ForEach-Object {
        Join-Path $sourceRoot $_
    })
    $robocopyArgs += @(
        "/XF", ".env", "*.log", ".current-mode", ".profiles",
               ".target-model", ".target-quantization", ".offline-mode"
    )
    $robocopyArgs += @($devOnlyFiles | ForEach-Object {
        Join-Path $sourceRoot $_
    })
    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        Write-AIError "File copy failed (robocopy exit code: $LASTEXITCODE)."
        Write-AI "  Try re-running with --Force or check that $installDir is writable."
        throw "ODS_INSTALL_ABORTED"
    }

    # Robocopy exclusions leave files copied by older installers in place.
    # Move managed-upgrade leftovers to a recoverable backup instead of
    # deleting possible user modifications. Unmanaged targets remain untouched.
    if ($pruneStaleDevPaths) {
        $devBackup = Move-ODSDevelopmentPathsToBackup `
            -InstallDir $installDir `
            -RelativePaths @($devOnlyDirectories + $devOnlyFiles)
        if (-not [string]::IsNullOrWhiteSpace($devBackup)) {
            Write-AIWarn "Older development files were moved to $devBackup"
        }
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
# The legacy OpenClaw extension (the ods-openclaw container) was removed;
# Portal (Pixel) and Hermes are the supported agents. Remove its stale service
# files the same way so `up --remove-orphans` drops the old container.
$_retiredOpenClaw = Join-Path $installDir "extensions\services\openclaw"
if (Test-Path -LiteralPath $_retiredOpenClaw) {
    Remove-Item -LiteralPath $_retiredOpenClaw -Recurse -Force
    Write-AI "Removed retired OpenClaw service files from extensions/services"
}
# Every release copied the OpenClaw templates into config\openclaw, used or
# not. A template that is still byte-identical to a shipped version is not
# owner data; anything else, and data\openclaw, stays.
$_openClawConfig = Join-Path $_configDir "openclaw"
$_openClawData = Join-Path $_dataDir "openclaw"
$_openClawManifest = Join-Path $sourceRoot "installers\lib\retired-openclaw-config.sha256"
if ((Test-Path -LiteralPath $_openClawConfig -PathType Container) -and
    (Test-Path -LiteralPath $_openClawManifest -PathType Leaf) -and
    -not ((Get-Item -LiteralPath $_openClawConfig -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    foreach ($_line in Get-Content -LiteralPath $_openClawManifest) {
        if ($_line -notmatch '^([0-9a-f]{64})\s+(\S+)$') { continue }
        $_digest = $Matches[1]
        $_relative = $Matches[2]
        if ($_relative.Contains('..')) { continue }
        $_template = Join-Path $_openClawConfig ($_relative -replace '/', '\')
        # Never reach a template through a linked folder.
        $_parent = Get-Item -LiteralPath (Split-Path -Parent $_template) -Force -ErrorAction SilentlyContinue
        if ($null -ne $_parent -and ($_parent.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
        $_item = Get-Item -LiteralPath $_template -Force -ErrorAction SilentlyContinue
        if ($null -eq $_item -or $_item.PSIsContainer -or
            ($_item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
        # An unreadable file has no digest, so it is kept.
        $_hash = Get-FileHash -LiteralPath $_template -Algorithm SHA256 -ErrorAction SilentlyContinue
        if ($null -eq $_hash -or $_hash.Hash.ToLowerInvariant() -ne $_digest) { continue }
        Remove-Item -LiteralPath $_template -Force -ErrorAction SilentlyContinue -ErrorVariable _removeErrors
        if ($_removeErrors.Count -gt 0) {
            Write-AIWarn "Could not remove the unchanged OpenClaw template $_template"
        }
    }
    foreach ($_folder in @((Join-Path $_openClawConfig "workspace"), $_openClawConfig)) {
        if (-not (Test-Path -LiteralPath $_folder -PathType Container)) { continue }
        # A folder that cannot be listed may hold owner data, so it stays.
        $_entries = @(Get-ChildItem -LiteralPath $_folder -Force -ErrorAction SilentlyContinue -ErrorVariable _listErrors)
        if ($_listErrors.Count -eq 0 -and $_entries.Count -eq 0) {
            Remove-Item -LiteralPath $_folder -Force -ErrorAction SilentlyContinue -ErrorVariable _removeErrors
            if ($_removeErrors.Count -gt 0) {
                Write-AIWarn "Could not remove the empty folder $_folder"
            }
        }
    }
}
$_openClawKept = @()
if (Test-Path -LiteralPath $_openClawConfig) { $_openClawKept += "config\openclaw" }
if (Test-Path -LiteralPath $_openClawData -PathType Container) {
    # A data folder that cannot be listed may still hold the agent's state.
    $_entries = @(Get-ChildItem -LiteralPath $_openClawData -Force -ErrorAction SilentlyContinue -ErrorVariable _listErrors)
    if ($_listErrors.Count -gt 0 -or $_entries.Count -gt 0) { $_openClawKept += "data\openclaw" }
}
if ($_openClawKept.Count -gt 0) {
    Write-AI "The legacy OpenClaw extension was removed. Its remaining files in $($_openClawKept -join ' and ') were kept; delete them by hand when you no longer need them (docs/MIGRATION-OPENCLAW-TO-HERMES.md explains how)."
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
if ($gpuInfo.Backend -eq "amd" -and -not $cloudMode) {
    # AMD runs ggml-org llama-server.exe (Vulkan) natively on this PC; the
    # values are written directly (no post-launch .env patching).
    $_amdInferenceRuntime = "llama-server"
    $_amdInferenceBackend = "vulkan"
    $_amdInferenceLocation = "host"
    $_amdInferencePort = [string]$script:NATIVE_LLM_PORT
    $_amdInferenceSupportedBackends = "vulkan"
    $_amdInferenceRuntimeMode = "windows-native-llama-server"
    $_amdInferenceManaged = "true"
}
$_enableWebSearch = Test-ODSWindowsSearxngNeeded `
    -EnableRecommended $enableRecommended `
    -EnableDeepResearch $enableDeepResearch `
    -EnableHermes $enableHermes
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
    -SystemRamGB    $systemRamGB `
    -WhisperCudaEnabled $whisperCudaSupported `
    -EnableLangfuse $enableLangfuse `
    -SwitchboardMode $env:ODS_MODEL_SWITCHBOARD `
    -EnableLan      $lanFlag `
    -EnableODSProxy $enableODSProxy `
    -EnableWebSearch $_enableWebSearch
Write-AISuccess "Generated .env with secure secrets"

# ── Post-generation validation: verify all required keys are present with values ──
# Defense-in-depth: catches silent failures in env generation before docker compose
# hits the ${VAR:?} hard-fail syntax and produces a confusing error.
# NOTE: Only checks keys that use :? (required non-empty) in compose files.
# Keys like ANTHROPIC_API_KEY= are intentionally empty and not checked here.
$_envPath = Join-Path $installDir ".env"
$_requiredKeys = @("WEBUI_SECRET", "N8N_PASS", "LITELLM_KEY", "DASHBOARD_API_KEY")
$_envLines = @{}
if (Test-Path $_envPath) {
    Get-Content $_envPath | ForEach-Object {
        if ($_ -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
            $_envLines[$Matches[1]] = $Matches[2]
        }
    }
}
if ($_amdInferenceRuntimeMode -eq "windows-native-llama-server") {
    $_requiredKeys += "LLAMA_SERVER_API_KEY"
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
        [string]$ApiKey = "",
        [int]$ContextLength,
        [int]$RequestTimeoutSeconds = 180,
        [int]$MaxTokens = 1024,
        [switch]$CompactToolset
    )

    if (-not (Test-Path $Path)) { return $false }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $content = [System.IO.File]::ReadAllText($Path, $utf8NoBom)
    # .NET reads '$' in a -replace *replacement* as a group-substitution token
    # ($1, ${name}, $$ ...), so a model id or URL carrying '$' is rewritten on
    # its way into the file. Doubling is the documented escape. The verification
    # near the end of this function still compares against the raw values, which
    # is what should actually land on disk.
    $modelReplacement = $Model.Replace('$', '$$')
    $baseUrlReplacement = $BaseUrl.Replace('$', '$$')
    $apiKeyReplacement = $ApiKey.Replace('$', '$$')
    # The source template quotes these values, while Hermes serializes its live
    # data/config.yaml without quotes. Match either form so post-start model
    # persistence does not silently no-op against the live file.
    $content = $content -replace '(?m)^  default:\s*(?:"[^"]*"|[^\r\n#]+)\s*(?:#.*)?\r?$', "  default: `"$modelReplacement`""
    $content = $content -replace '(?m)^  base_url:\s*(?:"[^"]*"|[^\r\n#]+)\s*(?:#.*)?\r?$', "  base_url: `"$baseUrlReplacement`""
    if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
        if ($content -match '(?m)^  api_key:') {
            $content = $content -replace '(?m)^  api_key:\s*(?:"[^"]*"|[^\r\n#]+)\s*(?:#.*)?\r?$', "  api_key: `"$apiKeyReplacement`""
        } else {
            $baseUrlLine = "  base_url: `"$BaseUrl`""
            $content = $content.Replace($baseUrlLine, "$baseUrlLine`n  api_key: `"$ApiKey`"")
        }
    }
    $content = $content -replace '(?m)^  context_length: .+\r?$', "  context_length: $ContextLength"
    $content = $content -replace '(?m)^    context_length: .+\r?$', "    context_length: $ContextLength"
    if ($MaxTokens -lt 1) { $MaxTokens = 1024 }
    if ($content -notmatch '(?m)^  max_tokens:\s*\d+\s*$') {
        $content = $content -replace '(?m)^model:\s*$', "model:`n  max_tokens: $MaxTokens"
    }
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
        $content += "`ncompression:`n  enabled: true`n  threshold: 0.75`n  target_ratio: 0.50`n  protect_last_n: 40`n"
    } else {
        if ($content -notmatch '(?m)^  enabled:') {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  enabled: true"
        }
        if ($content -match '(?m)^  threshold:') {
            $content = $content -replace '(?m)^  threshold: .+$', "  threshold: 0.75"
        } else {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  threshold: 0.75"
        }
        if ($content -match '(?m)^  target_ratio:') {
            $content = $content -replace '(?m)^  target_ratio: .+$', "  target_ratio: 0.50"
        } else {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  target_ratio: 0.50"
        }
        if ($content -match '(?m)^  protect_last_n:') {
            $content = $content -replace '(?m)^  protect_last_n: .+$', "  protect_last_n: 40"
        } else {
            $content = $content -replace '(?m)^compression:\s*$', "compression:`n  protect_last_n: 40"
        }
    }

    if ($CompactToolset) {
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
    if (-not [string]::IsNullOrWhiteSpace($ApiKey) -and
        [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
        try {
            # data\ is container-writable and receives an inheritable Everyone
            # grant earlier in this phase. A Hermes config containing the
            # LiteLLM master key must not retain that broad DACL. Match the
            # current-user-only protection used for .env, including on
            # reinstalls where icacls may have made the grant explicit.
            $secretItem = Get-Item -LiteralPath $Path
            if ($PSVersionTable.PSEdition -eq 'Core') {
                $secretAcl = [System.IO.FileSystemAclExtensions]::GetAccessControl($secretItem, [System.Security.AccessControl.AccessControlSections]::Access)
            } else {
                $secretAcl = $secretItem.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
            }
            $secretAcl.SetAccessRuleProtection($true, $false)
            foreach ($existingRule in @($secretAcl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
                $secretAcl.RemoveAccessRuleSpecific($existingRule)
            }
            $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            $currentUserRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $currentUser,
                "FullControl",
                "Allow"
            )
            $secretAcl.SetAccessRule($currentUserRule)
            if ($PSVersionTable.PSEdition -eq 'Core') {
                [System.IO.FileSystemAclExtensions]::SetAccessControl($secretItem, $secretAcl)
            } else {
                $secretItem.SetAccessControl($secretAcl)
            }
        } catch {
            Write-AIWarn "Could not restrict Hermes credential file permissions: $Path"
            return $false
        }
    }
    $verified = [System.IO.File]::ReadAllText($Path, $utf8NoBom)
    if (-not $verified.Contains("  default: `"$Model`"")) { return $false }
    if (-not $verified.Contains("  base_url: `"$BaseUrl`"")) { return $false }
    if (-not [string]::IsNullOrWhiteSpace($ApiKey) -and -not $verified.Contains("  api_key: `"$ApiKey`"")) { return $false }
    if (-not [string]::IsNullOrWhiteSpace($ApiKey) -and
        [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
        $verifiedAcl = Get-Acl -LiteralPath $Path
        if (-not $verifiedAcl.AreAccessRulesProtected) { return $false }
        $everyoneSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-1-0")
        foreach ($verifiedRule in $verifiedAcl.GetAccessRules(
            $true,
            $true,
            [System.Security.Principal.SecurityIdentifier]
        )) {
            if ($verifiedRule.IdentityReference -eq $everyoneSid -and
                $verifiedRule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow) {
                return $false
            }
        }
    }
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
        # Windows AMD keeps the compact prompt profile. Its name is the
        # build-installation-context.py interface, not a runtime choice.
        if ($_envText -match '(?m)^AMD_INFERENCE_RUNTIME_MODE=windows-native-llama-server\s*$') {
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
        if (Test-Path -LiteralPath $_output) {
            Remove-Item -LiteralPath $_output -Recurse -Force
        }
        $_content = Get-Content -LiteralPath $_template -Raw
        $_content = $_content -replace "(?m)^\s*<!-- INSTALLATION_CONTEXT -->\s*\r?\n?", ""
        Write-Utf8NoBom -Path $_output -Content $_content
        Write-AIWarn "Generated fallback Hermes SOUL.md without dynamic installation context"
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
    # llama-server serves the GGUF file name as the model id (--alias).
    $_hermesModel = $(if ($tierConfig.GgufFile) {
        $tierConfig.GgufFile
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
        $_hermesBaseUrl = $(if ($cloudMode) {
            "http://litellm:4000/v1"
        } elseif ($_switchboardMode -eq "enabled") {
            "http://model-router:9099/v1"
        } elseif ($gpuInfo.Backend -eq "amd") {
            "http://litellm:4000/v1"
        } else {
            "http://llama-server:8080/v1"
        })
    }
    $_hermesApiKey = ""
    if ($_envLines.ContainsKey("HERMES_LLM_API_KEY")) {
        $_hermesApiKey = $_envLines["HERMES_LLM_API_KEY"].Trim().Trim('"').Trim("'")
    }
    if ([string]::IsNullOrWhiteSpace($_hermesApiKey) -and $_hermesBaseUrl -match 'litellm:4000') {
        if ($_envLines.ContainsKey("LITELLM_KEY")) {
            $_hermesApiKey = $_envLines["LITELLM_KEY"].Trim().Trim('"').Trim("'")
        }
    } elseif ([string]::IsNullOrWhiteSpace($_hermesApiKey) -and $_hermesBaseUrl -match 'model-router:9099') {
        $_hermesApiKey = "no-key"
    }
    if ([string]::IsNullOrWhiteSpace($_hermesApiKey)) {
        $_hermesApiKey = "sk-ods-hermes-local"
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
    $_patchedHermesTemplate = Update-HermesConfigFile -Path $_hermesTemplate -Model $_hermesModel -BaseUrl $_hermesBaseUrl -ApiKey $_hermesApiKey -ContextLength ([int]$tierConfig.MaxContext) -RequestTimeoutSeconds $_hermesRequestTimeout -CompactToolset:($gpuInfo.Backend -eq "amd")
    $_patchedHermesLive = Update-HermesConfigFile -Path $_hermesLive -Model $_hermesModel -BaseUrl $_hermesBaseUrl -ApiKey $_hermesApiKey -ContextLength ([int]$tierConfig.MaxContext) -RequestTimeoutSeconds $_hermesRequestTimeout -CompactToolset:($gpuInfo.Backend -eq "amd")
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
