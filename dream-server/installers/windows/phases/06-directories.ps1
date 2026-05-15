# ============================================================================
# Dream Server Windows Installer -- Phase 06: Directories & Configuration
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
#   $enableOpenClaw            -- from phase 03
#   $openClawConfig            -- from phase 03
#
# Writes:
#   $envResult  -- hashtable: SearxngSecret, OpenclawToken
#
# Modder notes:
#   Add new directories to $_dirs array below.
#   Add new .env variables in lib/env-generator.ps1 New-DreamEnv function.
#   Add new config files (e.g., Perplexica config) as a New-XyzConfig function
#   in env-generator.ps1 and call it here.
# ============================================================================

Write-Phase -Phase 6 -Total 13 -Name "SETUP" -Estimate "~1-2 minutes"

if ($dryRun) {
    Write-AI "[DRY RUN] Would create: $installDir"
    Write-AI "[DRY RUN] Would copy source files via robocopy (excluding .git, data/, .env, models/)"
    Write-AI "[DRY RUN] Would generate .env with secure secrets (WEBUI_SECRET, N8N_PASS, LITELLM_KEY, ...)"
    Write-AI "[DRY RUN] Would generate SearXNG config with randomized secret key"
    Write-AI "[DRY RUN] Would copy dream.ps1 CLI + lib/ to install root"
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
    (Join-Path $_dataDir "open-webui"),
    (Join-Path $_dataDir "whisper"),
    (Join-Path $_dataDir "tts"),
    (Join-Path $_dataDir "n8n"),
    (Join-Path $_dataDir "qdrant"),
    (Join-Path $_dataDir "models"),
    (Join-Path $_dataDir "comfyui"),
    (Join-Path $_dataDir "perplexica"),
    (Join-Path $_dataDir "ape"),
    (Join-Path $_dataDir "token-spy"),
    (Join-Path $_dataDir "privacy-shield"),
    (Join-Path $_dataDir "hermes"),
    (Join-Path (Join-Path $_dataDir "hermes-proxy") "caddy-data"),
    (Join-Path (Join-Path $_dataDir "hermes-proxy") "caddy-config")
)
foreach ($_d in $_dirs) {
    New-Item -ItemType Directory -Path $_d -Force | Out-Null
}
Write-AISuccess "Created directory structure under $installDir"

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
        exit 1
    }
    Write-AISuccess "Source files installed to $installDir"
} else {
    Write-AI "Running in-place (source == install directory) -- skipping file copy"
}

# ── Copy dream.ps1 CLI + lib/ ─────────────────────────────────────────────────
# Retired from the shipped stack after Hermes became the default agent surface.
# Remove stale service files left behind by non-pruning upgrades, while
# preserving data/dreamforge for user-controlled archival.
$_retiredDreamForge = Join-Path $installDir "extensions\services\dreamforge"
if (Test-Path $_retiredDreamForge) {
    Remove-Item -LiteralPath $_retiredDreamForge -Recurse -Force
    Write-AI "Removed retired DreamForge service files from extensions/services"
}

# Copies from the Windows installer directory to the install root so users
# can manage Dream Server with: .\dream.ps1 status
# $ScriptDir is set by install-windows.ps1 (installers/windows/) and is
# visible here because phases are dot-sourced in the orchestrator's scope.
$_scriptDir = $ScriptDir   # installers/windows/
$_dreamSrc  = Join-Path $_scriptDir "dream.ps1"
$_dreamDst  = Join-Path $installDir "dream.ps1"
if (Test-Path $_dreamSrc) {
    Copy-Item -Path $_dreamSrc -Destination $_dreamDst -Force
    # Also copy lib/ so dream.ps1 can find its helper functions
    $_libSrc = Join-Path $_scriptDir "lib"
    $_libDst = Join-Path $installDir "lib"
    New-Item -ItemType Directory -Path $_libDst -Force | Out-Null
    Copy-Item -Path (Join-Path $_libSrc "*") -Destination $_libDst -Recurse -Force
    Write-AISuccess "Installed dream.ps1 CLI"
} else {
    Write-AIWarn "dream.ps1 not found at $_dreamSrc -- CLI management unavailable"
}

# ── Generate .env with secure secrets ────────────────────────────────────────
$_dreamMode = $(if ($cloudMode) { "cloud" } else { "local" })
$envResult = New-DreamEnv `
    -InstallDir     $installDir `
    -TierConfig     $tierConfig `
    -Tier           $selectedTier `
    -GpuBackend     $gpuInfo.Backend `
    -DreamMode      $_dreamMode `
    -LlamaServerImage $llamaServerImage `
    -EnableLangfuse $enableLangfuse `
    -EnableLan      $lanFlag
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
    exit 1
}
Write-AISuccess "Verified .env contains all required secrets"

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
        "http://host.docker.internal:8080/api"
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
            exit 1
        }
    }
}

# ── Create llama-server models.ini stub ──────────────────────────────────────
$_modelsIni = Join-Path (Join-Path $installDir "config\llama-server") "models.ini"
if (-not (Test-Path $_modelsIni)) {
    Write-Utf8NoBom -Path $_modelsIni -Content "# Dream Server model registry`n"
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
