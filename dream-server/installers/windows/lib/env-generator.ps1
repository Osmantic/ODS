# ============================================================================
# Dream Server Windows Installer -- Environment Generator
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Generate .env file, SearXNG config, OpenClaw configs
#          Uses .NET crypto for secrets (no openssl dependency)
#
# Canonical source: installers/phases/06-directories.sh (keep .env format in sync)
#
# Modder notes:
#   Modify New-DreamEnv to add new environment variables.
#   All secrets use cryptographic RNG -- never use Get-Random for secrets.
# ============================================================================

function Write-Utf8NoBom {
    <#
    .SYNOPSIS
        Write text to file as UTF-8 WITHOUT BOM. PS 5.1's Set-Content -Encoding UTF8
        writes a BOM which corrupts Docker Compose .env parsing and YAML files.
    #>
    param(
        [string]$Path,
        [string]$Content
    )
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function New-SecureHex {
    <#
    .SYNOPSIS
        Generate a cryptographically secure hex string.
    .PARAMETER Bytes
        Number of random bytes (output is 2x chars). Default 32.
    #>
    param([int]$Bytes = 32)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] $Bytes
    $rng.GetBytes($buf)
    return ($buf | ForEach-Object { $_.ToString("x2") }) -join ""
}

function New-SecureBase64 {
    <#
    .SYNOPSIS
        Generate a cryptographically secure Base64 string.
    .PARAMETER Bytes
        Number of random bytes. Default 32.
    #>
    param([int]$Bytes = 32)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] $Bytes
    $rng.GetBytes($buf)
    return [Convert]::ToBase64String($buf)
}

function New-DreamEnv {
    <#
    .SYNOPSIS
        Generate the .env file matching Phase 06 output format.
    .PARAMETER InstallDir
        Target installation directory.
    .PARAMETER TierConfig
        Hashtable from Resolve-TierConfig (TierName, LlmModel, GgufFile, MaxContext).
    .PARAMETER Tier
        Tier identifier string (1-4, SH_COMPACT, SH_LARGE, etc.).
    .PARAMETER GpuBackend
        GPU backend: "nvidia", "amd", or "none".
    .PARAMETER DreamMode
        LLM backend mode: "local", "cloud", or "hybrid".
    #>
    param(
        [string]$InstallDir,
        [hashtable]$TierConfig,
        [string]$Tier,
        [string]$GpuBackend = "nvidia",
        [string]$DreamMode = "local",
        [string]$LlamaServerImage = "",
        # Mirror the install-time ENABLE_LANGFUSE toggle from phase 03 into
        # .env's LANGFUSE_ENABLED default. Re-install preserves whatever the
        # user already had in .env (via Get-EnvOrNew), so manual
        # `dream enable langfuse` edits survive.
        [bool]$EnableLangfuse = $false,
        [bool]$EnableLan = $false
    )

    # Preserve existing secrets on re-install (mirrors Linux _env_get logic)
    $existingEnv = @{}
    $envPath = Join-Path $InstallDir ".env"
    if (Test-Path $envPath) {
        Get-Content $envPath | ForEach-Object {
            if ($_ -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
                $existingEnv[$Matches[1]] = $Matches[2]
            }
        }
    }

    # Helper: reuse existing value or generate new
    function Get-EnvOrNew { param([string]$Key, [string]$Default)
        if ($existingEnv.ContainsKey($Key) -and $existingEnv[$Key]) {
            return $existingEnv[$Key]
        }
        return $Default
    }

    function Select-AutoCpuValue {
        param(
            [string]$Key,
            [string]$Detected
        )

        $existing = ""
        if ($existingEnv.ContainsKey($Key)) {
            $existing = $existingEnv[$Key]
        }

        $existingNumber = 0.0
        $detectedNumber = 0.0
        $style = [System.Globalization.NumberStyles]::Float
        $culture = [System.Globalization.CultureInfo]::InvariantCulture
        $existingValid = [double]::TryParse($existing, $style, $culture, [ref]$existingNumber)
        $detectedValid = [double]::TryParse($Detected, $style, $culture, [ref]$detectedNumber)

        if ($existingValid -and $detectedValid -and $existingNumber -gt 0 -and $existingNumber -le $detectedNumber) {
            return $existing
        }
        return $Detected
    }

    # Generate secrets (reuse existing on re-install)
    $webuiSecret     = Get-EnvOrNew "WEBUI_SECRET"       (New-SecureHex -Bytes 32)
    $n8nPass         = Get-EnvOrNew "N8N_PASS"           (New-SecureBase64 -Bytes 16)
    $litellmKey      = Get-EnvOrNew "LITELLM_KEY"        "sk-dream-$(New-SecureHex -Bytes 16)"
    $litellmLemonadeApiKey = Get-EnvOrNew "LITELLM_LEMONADE_API_KEY" "sk-dream-lemonade-$(New-SecureHex -Bytes 16)"
    $llamaServerImageFallback = Get-EnvOrNew "LLAMA_SERVER_IMAGE_FALLBACK" ([Environment]::GetEnvironmentVariable("LLAMA_SERVER_IMAGE_FALLBACK"))
    if ([string]::IsNullOrWhiteSpace($llamaServerImageFallback)) { $llamaServerImageFallback = "" }
    $livekitSecret   = Get-EnvOrNew "LIVEKIT_API_SECRET" (New-SecureBase64 -Bytes 32)
    $livekitApiKey   = Get-EnvOrNew "LIVEKIT_API_KEY"    (New-SecureHex -Bytes 16)
    $dashboardApiKey = Get-EnvOrNew "DASHBOARD_API_KEY"  (New-SecureHex -Bytes 32)
    $dreamAgentKey   = Get-EnvOrNew "DREAM_AGENT_KEY"    (New-SecureHex -Bytes 32)
    # HMAC key for signing dream-session cookies. Without it the dashboard-api
    # session_signer raises on issue() and verify-session returns no-secret —
    # the magic-link gate effectively breaks.
    $dreamSessionSecret = Get-EnvOrNew "DREAM_SESSION_SECRET" (New-SecureHex -Bytes 32)
    $shieldApiKey    = Get-EnvOrNew "SHIELD_API_KEY"     (New-SecureHex -Bytes 32)
    $openclawToken   = Get-EnvOrNew "OPENCLAW_TOKEN"     (New-SecureHex -Bytes 24)
    $searxngSecret   = Get-EnvOrNew "SEARXNG_SECRET"     (New-SecureHex -Bytes 32)
    $difySecretKey    = Get-EnvOrNew "DIFY_SECRET_KEY"           (New-SecureHex -Bytes 32)
    $qdrantApiKey     = Get-EnvOrNew "QDRANT_API_KEY"            (New-SecureHex -Bytes 32)
    $opencodePassword = Get-EnvOrNew "OPENCODE_SERVER_PASSWORD"  (New-SecureBase64 -Bytes 16)
    $cpuBudget = Get-LlamaCpuBudget -GpuBackend $(if ($GpuBackend -eq "none") { "cpu" } else { $GpuBackend })
    $llamaCpuLimit = Select-AutoCpuValue -Key "LLAMA_CPU_LIMIT" -Detected $cpuBudget.Limit
    $llamaCpuReservation = Select-AutoCpuValue -Key "LLAMA_CPU_RESERVATION" -Detected $cpuBudget.Reservation
    $limitNumber = 0.0
    $reservationNumber = 0.0
    $style = [System.Globalization.NumberStyles]::Float
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    if ([double]::TryParse($llamaCpuLimit, $style, $culture, [ref]$limitNumber) -and [double]::TryParse($llamaCpuReservation, $style, $culture, [ref]$reservationNumber)) {
        if ($reservationNumber -gt $limitNumber) {
            $llamaCpuReservation = $llamaCpuLimit
        }
    }

    # Langfuse observability secrets
    $langfusePort              = Get-EnvOrNew "LANGFUSE_PORT"              "3006"
    $langfuseDefault           = if ($EnableLangfuse) { "true" } else { "false" }
    $langfuseEnabled           = Get-EnvOrNew "LANGFUSE_ENABLED"           $langfuseDefault
    $langfuseNextauthSecret    = Get-EnvOrNew "LANGFUSE_NEXTAUTH_SECRET"   (New-SecureHex -Bytes 32)
    $langfuseSalt              = Get-EnvOrNew "LANGFUSE_SALT"              (New-SecureHex -Bytes 32)
    $langfuseEncryptionKey     = Get-EnvOrNew "LANGFUSE_ENCRYPTION_KEY"    (New-SecureHex -Bytes 32)
    $langfuseDbPassword        = Get-EnvOrNew "LANGFUSE_DB_PASSWORD"       (New-SecureHex -Bytes 16)
    $langfuseClickhousePassword = Get-EnvOrNew "LANGFUSE_CLICKHOUSE_PASSWORD" (New-SecureHex -Bytes 16)
    $langfuseRedisPassword     = Get-EnvOrNew "LANGFUSE_REDIS_PASSWORD"    (New-SecureHex -Bytes 16)
    $langfuseMinioAccessKey    = Get-EnvOrNew "LANGFUSE_MINIO_ACCESS_KEY"  (New-SecureHex -Bytes 16)
    $langfuseMinioSecretKey    = Get-EnvOrNew "LANGFUSE_MINIO_SECRET_KEY"  (New-SecureHex -Bytes 32)
    $langfuseProjectPublicKey  = Get-EnvOrNew "LANGFUSE_PROJECT_PUBLIC_KEY" "pk-lf-dream-$(New-SecureHex -Bytes 16)"
    $langfuseProjectSecretKey  = Get-EnvOrNew "LANGFUSE_PROJECT_SECRET_KEY" "sk-lf-dream-$(New-SecureHex -Bytes 16)"
    $langfuseInitProjectId     = Get-EnvOrNew "LANGFUSE_INIT_PROJECT_ID"   (New-SecureHex -Bytes 16)
    $langfuseInitUserEmail     = Get-EnvOrNew "LANGFUSE_INIT_USER_EMAIL"   "admin@dreamserver.local"
    $langfuseInitUserPassword  = Get-EnvOrNew "LANGFUSE_INIT_USER_PASSWORD" (New-SecureHex -Bytes 16)

    # Determine LLM backend engine and API URL
    # AMD on Windows: inference server runs natively, containers reach it via host.docker.internal
    # NVIDIA: llama-server runs in Docker, containers reach it via service name
    # NOTE: $(if ...) syntax required for PS 5.1 compatibility
    $llmBackend = $(if ($GpuBackend -eq "amd") {
        "lemonade"
    } elseif ($DreamMode -eq "cloud") {
        "litellm"
    } else {
        "llama-server"
    })

    # Lemonade serves OpenAI-compatible API at /api/v1; llama-server at /v1
    $llmApiBasePath = $(if ($GpuBackend -eq "amd") { "/api/v1" } else { "/v1" })

    $llmApiUrl = $(if ($GpuBackend -eq "amd") {
        "http://host.docker.internal:8080"
    } elseif ($DreamMode -eq "cloud") {
        "http://litellm:4000"
    } else {
        "http://llama-server:8080"
    })

    # Timezone -- convert Windows timezone ID to IANA for Docker containers
    $tz = $(try {
        $tzInfo = [System.TimeZoneInfo]::Local
        # .NET 6+ has TimeZoneInfo.TryConvertWindowsIdToIanaId; fall back to common mappings
        $ianaId = $null
        try {
            # Works on .NET 6+ / PS 7+
            # TryConvert returns bool; the IANA ID is written to the [ref] out-param
            $outIana = $null
            $ok = [System.TimeZoneInfo]::TryConvertWindowsIdToIanaId($tzInfo.Id, [ref]$outIana)
            if ($ok -and $outIana) { $ianaId = $outIana }
        } catch { }
        if ($ianaId) { $ianaId } else {
            switch -Wildcard ($tzInfo.Id) {
                "*Eastern*"    { "America/New_York" }
                "*Central*"    { "America/Chicago" }
                "*Mountain*"   { "America/Denver" }
                "*Pacific*"    { "America/Los_Angeles" }
                "*Alaska*"     { "America/Anchorage" }
                "*Hawaii*"     { "Pacific/Honolulu" }
                "*UTC*"        { "UTC" }
                "*GMT*"        { "Europe/London" }
                "*W. Europe*"  { "Europe/Berlin" }
                "*Romance*"    { "Europe/Paris" }
                "*India*"      { "Asia/Kolkata" }
                "*China*"      { "Asia/Shanghai" }
                "*Tokyo*"      { "Asia/Tokyo" }
                "*Korea*"      { "Asia/Seoul" }
                "*AUS Eastern*"  { "Australia/Sydney" }
                "*E. South America*" { "America/Sao_Paulo" }
                "*SE Asia*"    { "Asia/Bangkok" }
                "*Arab*"       { "Asia/Riyadh" }
                "*Egypt*"      { "Africa/Cairo" }
                "*South Africa*" { "Africa/Johannesburg" }
                "*E. Europe*"  { "Europe/Bucharest" }
                "*FLE*"        { "Europe/Kiev" }
                default        { "UTC" }
            }
        }
    } catch { "UTC" })

    $timestamp = Get-Date -Format "o"

    # Build .env content (matches Phase 06 format)
    $envContent = @"
# Dream Server Configuration -- $($TierConfig.TierName) Edition
# Generated by Windows installer v$($script:DS_VERSION) on $timestamp
# Tier: $Tier ($($TierConfig.TierName))

#=== Network Binding ===
# 127.0.0.1 = localhost only (secure default)
# 0.0.0.0   = accessible from LAN (install with -Lan or set manually)
BIND_ADDRESS=$(Get-EnvOrNew "BIND_ADDRESS" "$(if ($EnableLan) { "0.0.0.0" } else { "127.0.0.1" })")
# Docker Desktop containers reach loopback-only host services through this name.
DREAM_AGENT_HOST=$(Get-EnvOrNew "DREAM_AGENT_HOST" "host.docker.internal")

#=== LLM Backend Mode ===
DREAM_MODE=$DreamMode
LLM_BACKEND=$llmBackend
LLM_API_URL=$llmApiUrl
LLM_API_BASE_PATH=$llmApiBasePath

#=== Cloud API Keys ===
ANTHROPIC_API_KEY=$(Get-EnvOrNew "ANTHROPIC_API_KEY" "")
OPENAI_API_KEY=$(Get-EnvOrNew "OPENAI_API_KEY" "")
TOGETHER_API_KEY=$(Get-EnvOrNew "TOGETHER_API_KEY" "")
MINIMAX_API_KEY=$(Get-EnvOrNew "MINIMAX_API_KEY" "")

#=== LLM Settings (llama-server) ===
MODEL_PROFILE=$(Get-EnvOrNew "MODEL_PROFILE" "$(if ($TierConfig.ModelProfileRequested) { $TierConfig.ModelProfileRequested } else { "qwen" })")
LLM_MODEL=$($TierConfig.LlmModel)
GGUF_FILE=$($TierConfig.GgufFile)
MAX_CONTEXT=$($TierConfig.MaxContext)
CTX_SIZE=$($TierConfig.MaxContext)
GPU_BACKEND=$GpuBackend
$(if ($LlamaServerImage) { "LLAMA_SERVER_IMAGE=$LlamaServerImage" } else { "#LLAMA_SERVER_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda" })
$(if ($llamaServerImageFallback) { "LLAMA_SERVER_IMAGE_FALLBACK=$llamaServerImageFallback" } else { "#LLAMA_SERVER_IMAGE_FALLBACK=ghcr.io/ggml-org/llama.cpp:server-cuda-b9014" })
#=== llama.cpp Runtime Tuning ===
LLAMA_ARG_FLASH_ATTN=$(Get-EnvOrNew "LLAMA_ARG_FLASH_ATTN" "auto")
LLAMA_ARG_CACHE_TYPE_K=$(Get-EnvOrNew "LLAMA_ARG_CACHE_TYPE_K" "f16")
LLAMA_ARG_CACHE_TYPE_V=$(Get-EnvOrNew "LLAMA_ARG_CACHE_TYPE_V" "f16")
# Optional MoE only. Example for 8-12GB VRAM: LLAMA_ARG_N_CPU_MOE=25
LLAMA_CPU_LIMIT=$llamaCpuLimit
LLAMA_CPU_RESERVATION=$llamaCpuReservation

#=== Ports ===
OLLAMA_PORT=11434
WEBUI_PORT=3000
WHISPER_PORT=9000
TTS_PORT=8880
N8N_PORT=5678
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_API_KEY=$qdrantApiKey
LITELLM_PORT=4000
OPENCLAW_PORT=7860
SEARXNG_PORT=8888

#=== Hermes Agent ===
HERMES_LLM_BASE_URL=$llmApiUrl$llmApiBasePath
HERMES_LLM_API_KEY=sk-dream-hermes-local
HERMES_LANGUAGE=en
HERMES_PROXY_PORT=9120
HERMES_PROXY_UPSTREAM=dream-hermes:9119
DREAM_AUTH_UPSTREAM=dream-dashboard-api:3002

#=== Security (auto-generated, keep secret!) ===
WEBUI_SECRET=$webuiSecret
DASHBOARD_API_KEY=$dashboardApiKey
DREAM_AGENT_KEY=$dreamAgentKey
DREAM_SESSION_SECRET=$dreamSessionSecret
SHIELD_API_KEY=$shieldApiKey
N8N_USER=admin@dreamserver.local
N8N_PASS=$n8nPass
LITELLM_KEY=$litellmKey
$(if ($GpuBackend -eq "amd") { "LITELLM_LEMONADE_API_KEY=$litellmLemonadeApiKey" })
LIVEKIT_API_KEY=$livekitApiKey
LIVEKIT_API_SECRET=$livekitSecret
OPENCLAW_TOKEN=$openclawToken
OPENCODE_SERVER_PASSWORD=$opencodePassword
OPENCODE_PORT=3003
SEARXNG_SECRET=$searxngSecret
DIFY_SECRET_KEY=$difySecretKey

#=== Voice Settings ===
WHISPER_MODEL=base
# Whisper STT model — NVIDIA uses the larger turbo model, others use base.
# Open WebUI reads this to request transcription; installer pre-downloads
# the same model so the first transcription works.
AUDIO_STT_MODEL=$(Get-EnvOrNew "AUDIO_STT_MODEL" $(if ($GpuBackend -eq "nvidia") { "deepdml/faster-whisper-large-v3-turbo-ct2" } else { "Systran/faster-whisper-base" }))
TTS_VOICE=en_US-lessac-medium

#=== Web UI Settings ===
WEBUI_AUTH=true
ENABLE_WEB_SEARCH=true
WEB_SEARCH_ENGINE=searxng

#=== n8n Settings ===
N8N_HOST=localhost
N8N_WEBHOOK_URL=http://localhost:5678
TIMEZONE=$tz

#=== Langfuse Observability ===
LANGFUSE_PORT=$langfusePort
LANGFUSE_ENABLED=$langfuseEnabled
LANGFUSE_NEXTAUTH_SECRET=$langfuseNextauthSecret
LANGFUSE_SALT=$langfuseSalt
LANGFUSE_ENCRYPTION_KEY=$langfuseEncryptionKey
LANGFUSE_DB_PASSWORD=$langfuseDbPassword
LANGFUSE_CLICKHOUSE_PASSWORD=$langfuseClickhousePassword
LANGFUSE_REDIS_PASSWORD=$langfuseRedisPassword
LANGFUSE_MINIO_ACCESS_KEY=$langfuseMinioAccessKey
LANGFUSE_MINIO_SECRET_KEY=$langfuseMinioSecretKey
LANGFUSE_PROJECT_PUBLIC_KEY=$langfuseProjectPublicKey
LANGFUSE_PROJECT_SECRET_KEY=$langfuseProjectSecretKey
LANGFUSE_INIT_PROJECT_ID=$langfuseInitProjectId
LANGFUSE_INIT_USER_EMAIL=$langfuseInitUserEmail
LANGFUSE_INIT_USER_PASSWORD=$langfuseInitUserPassword
"@

    # NOTE: No VIDEO_GID, RENDER_GID, HSA_OVERRIDE_GFX_VERSION on Windows
    # Those are Linux-only for AMD ROCm container device access

    $envPath = Join-Path $InstallDir ".env"
    Write-Utf8NoBom -Path $envPath -Content $envContent

    # Restrict .env to current user only (Windows ACL equivalent of chmod 600)
    try {
        $acl = Get-Acl $envPath
        $acl.SetAccessRuleProtection($true, $false)  # Disable inheritance
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $currentUser, "FullControl", "Allow"
        )
        $acl.SetAccessRule($rule)
        Set-Acl -Path $envPath -AclObject $acl
    } catch {
        # ACL restriction failed -- not fatal, just warn
        Write-AIWarn "Could not restrict .env permissions: $_"
    }

    return @{
        SearxngSecret  = $searxngSecret
        OpenclawToken  = $openclawToken
    }
}

function New-SearxngConfig {
    <#
    .SYNOPSIS
        Generate SearXNG settings.yml with randomized secret key.
    #>
    param(
        [string]$InstallDir,
        [string]$SecretKey
    )

    $configDir = Join-Path (Join-Path $InstallDir "config") "searxng"
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null

    $config = @"
use_default_settings: true
server:
  secret_key: "$SecretKey"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
search:
  safe_search: 0
  formats:
    - html
    - json
engines:
  - name: duckduckgo
    disabled: false
  - name: google
    disabled: false
  - name: brave
    disabled: false
  - name: wikipedia
    disabled: false
  - name: github
    disabled: false
  - name: stackoverflow
    disabled: false
"@

    $settingsPath = Join-Path $configDir "settings.yml"
    Write-Utf8NoBom -Path $settingsPath -Content $config
    return $settingsPath
}

function New-OpenClawConfig {
    <#
    .SYNOPSIS
        Generate OpenClaw home config and auth profiles for local llama-server.
    #>
    param(
        [string]$InstallDir,
        [string]$LlmModel,
        [int]$MaxContext,
        [string]$Token,
        [string]$ProviderName = "local-llama",
        [string]$ProviderUrl  = "http://host.docker.internal:8080"
    )

    # Create directories
    # NOTE: Nested Join-Path required -- PS 5.1 only accepts 2 arguments
    $homeDir  = Join-Path (Join-Path (Join-Path $InstallDir "data") "openclaw") "home"
    $agentDir = Join-Path (Join-Path (Join-Path $homeDir "agents") "main") "agent"
    $sessDir  = Join-Path (Join-Path (Join-Path $homeDir "agents") "main") "sessions"
    New-Item -ItemType Directory -Path $agentDir -Force | Out-Null
    New-Item -ItemType Directory -Path $sessDir -Force | Out-Null

    # Home config
    $homeConfig = @"
{
  "models": {
    "providers": {
      "$ProviderName": {
        "baseUrl": "$ProviderUrl",
        "apiKey": "none",
        "api": "openai-completions",
        "models": [
          {
            "id": "$LlmModel",
            "name": "Dream Server LLM (Local)",
            "reasoning": false,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": $MaxContext,
            "maxTokens": 8192,
            "compat": {
              "supportsStore": false,
              "supportsDeveloperRole": false,
              "supportsReasoningEffort": false,
              "maxTokensField": "max_tokens"
            }
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {"primary": "$ProviderName/$LlmModel"},
      "models": {"$ProviderName/$LlmModel": {}},
      "compaction": {"mode": "safeguard"},
      "subagents": {"maxConcurrent": 20, "model": "$ProviderName/$LlmModel"}
    }
  },
  "commands": {"native": "auto", "nativeSkills": "auto"},
  "gateway": {
    "mode": "local",
    "bind": "lan",
    "controlUi": {"allowInsecureAuth": true},
    "auth": {"mode": "token", "token": "$Token"}
  }
}
"@
    Write-Utf8NoBom -Path (Join-Path $homeDir "openclaw.json") -Content $homeConfig

    # Auth profiles
    $authProfiles = @"
{
  "version": 1,
  "profiles": {
    "${ProviderName}:default": {
      "type": "api_key",
      "provider": "$ProviderName",
      "key": "none"
    }
  },
  "lastGood": {"$ProviderName": "${ProviderName}:default"},
  "usageStats": {}
}
"@
    Write-Utf8NoBom -Path (Join-Path $agentDir "auth-profiles.json") -Content $authProfiles

    # Models config
    $modelsConfig = @"
{
  "providers": {
    "$ProviderName": {
      "baseUrl": "$ProviderUrl",
      "apiKey": "none",
      "api": "openai-completions",
      "models": [
        {
          "id": "$LlmModel",
          "name": "Dream Server LLM (Local)",
          "reasoning": false,
          "input": ["text"],
          "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
          "contextWindow": $MaxContext,
          "maxTokens": 8192,
          "compat": {
            "supportsStore": false,
            "supportsDeveloperRole": false,
            "supportsReasoningEffort": false,
            "maxTokensField": "max_tokens"
          }
        }
      ]
    }
  }
}
"@
    Write-Utf8NoBom -Path (Join-Path $agentDir "models.json") -Content $modelsConfig

    # Workspace directory (must exist before Docker Compose)
    $workspaceDir = Join-Path (Join-Path (Join-Path (Join-Path $InstallDir "config") "openclaw") "workspace") "memory"
    New-Item -ItemType Directory -Path $workspaceDir -Force | Out-Null
}

function Set-PerplexicaConfig {
    <#
    .SYNOPSIS
        Auto-configure Perplexica to use the local llama-server on first boot.
        Seeds the chat model and embedding model, then marks setup complete
        so the wizard is bypassed. Mirrors installers/phases/12-health.sh logic.
    .PARAMETER PerplexicaPort
        Port where Perplexica is running (default 3004).
    .PARAMETER LlmModel
        Model name to configure as the default chat model.
    #>
    param(
        [int]$PerplexicaPort = 3004,
        [string]$LlmModel
    )

    $baseUrl = "http://localhost:$PerplexicaPort"

    # Helper: POST a key/value pair to the config API
    # Uses HttpWebRequest instead of Invoke-WebRequest to avoid PS 5.1
    # credential dialog on non-200 responses.
    function Post-ConfigValue {
        param([string]$Key, $Value)
        $body = @{ key = $Key; value = $Value } | ConvertTo-Json -Depth 10 -Compress
        $utf8Bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $req = [System.Net.HttpWebRequest]::Create("$baseUrl/api/config")
        $req.Method = "POST"
        $req.ContentType = "application/json"
        $req.Timeout = 5000
        $stream = $req.GetRequestStream()
        $stream.Write($utf8Bytes, 0, $utf8Bytes.Length)
        $stream.Close()
        $resp = $req.GetResponse()
        $resp.Close()
    }

    try {
        # GET current config using HttpWebRequest (avoids PS 5.1 credential dialog)
        $req = [System.Net.HttpWebRequest]::Create("$baseUrl/api/config")
        $req.Method = "GET"
        $req.Timeout = 5000
        $httpResp = $req.GetResponse()
        $reader = New-Object System.IO.StreamReader($httpResp.GetResponseStream())
        $respBody = $reader.ReadToEnd()
        $reader.Close()
        $httpResp.Close()
        $config = ($respBody | ConvertFrom-Json).values

        # Already configured -- skip
        if ($config.setupComplete) { return $true }

        $providers = @($config.modelProviders)
        $openaiProv = $providers | Where-Object { $_.type -eq "openai" } | Select-Object -First 1
        $transformersProv = $providers | Where-Object { $_.type -eq "transformers" } | Select-Object -First 1

        if (-not $openaiProv) { return $false }

        # Seed the chat model into the OpenAI provider
        $openaiProv.chatModels = @(@{ key = $LlmModel; name = $LlmModel })
        Post-ConfigValue -Key "modelProviders" -Value $providers

        # Set default providers and models
        $embeddingId = $(if ($transformersProv) { $transformersProv.id } else { $openaiProv.id })
        Post-ConfigValue -Key "preferences" -Value @{
            defaultChatProvider      = $openaiProv.id
            defaultChatModel         = $LlmModel
            defaultEmbeddingProvider = $embeddingId
            defaultEmbeddingModel    = "Xenova/all-MiniLM-L6-v2"
        }

        # Mark setup complete to bypass wizard
        Post-ConfigValue -Key "setupComplete" -Value $true

        return $true
    } catch {
        return $false
    }
}
