# ============================================================================
# ODS Windows Installer -- Environment Generator
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Generate .env file, SearXNG config, OpenClaw configs
#          Uses .NET crypto for secrets (no openssl dependency)
#
# Canonical source: installers/phases/06-directories.sh (keep .env format in sync)
#
# Modder notes:
#   Modify New-ODSEnv to add new environment variables.
#   All secrets use cryptographic RNG -- never use Get-Random for secrets.
# ============================================================================

function Resolve-WindowsODSPort {
    <#
    .SYNOPSIS
        Resolve a Windows installer port from an explicit process override,
        persisted .env state, or the platform default.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [int]$DefaultPort,

        [hashtable]$ExistingEnv,
        [string]$InstallDir = ""
    )

    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $processValue = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        [void]$candidates.Add($processValue)
    }

    if ($ExistingEnv -and $ExistingEnv.ContainsKey($Name)) {
        [void]$candidates.Add([string]$ExistingEnv[$Name])
    } elseif (-not [string]::IsNullOrWhiteSpace($InstallDir)) {
        $envPath = Join-Path $InstallDir ".env"
        if (Test-Path -LiteralPath $envPath -PathType Leaf) {
            $assignment = Get-Content -LiteralPath $envPath -ErrorAction SilentlyContinue |
                Where-Object { $_ -match "^$([regex]::Escape($Name))=(.*)$" } |
                Select-Object -First 1
            if ($assignment -and $assignment -match "^[^=]+=(.*)$") {
                [void]$candidates.Add([string]$Matches[1])
            }
        }
    }

    [void]$candidates.Add([string]$DefaultPort)
    foreach ($candidate in $candidates) {
        $parsedPort = 0
        if ([int]::TryParse(([string]$candidate).Trim(), [ref]$parsedPort) -and
            $parsedPort -ge 1 -and $parsedPort -le 65535) {
            return $parsedPort
        }
    }

    return $DefaultPort
}

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
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $Path -PathType Container) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-AIWarn "Removed malformed $Path directory from a previous partial install."
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Write-WindowsODSLemonadeLiteLlmConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,

        [Parameter(Mandatory = $true)]
        [string]$ModelId,

        [string]$Port = "8080",
        [string]$ApiKey = "not-needed"
    )

    if ([string]::IsNullOrWhiteSpace($ModelId)) {
        throw "Lemonade model ID is required."
    }
    if ($ModelId -match '[\r\n]') {
        throw "Lemonade model ID cannot contain line breaks."
    }

    $litellmDir = Join-Path (Join-Path $InstallDir "config") "litellm"
    New-Item -ItemType Directory -Path $litellmDir -Force | Out-Null
    $lemonadeApiBase = "http://host.docker.internal:$Port/api/v1"
    $lemonadeConfig = @"
model_list:
  - model_name: default
    litellm_params:
      model: openai/$ModelId
      api_base: $lemonadeApiBase
      api_key: $ApiKey
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/$ModelId
      api_base: $lemonadeApiBase
      api_key: $ApiKey
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"@
    $configPath = Join-Path $litellmDir "lemonade.yaml"
    Write-Utf8NoBom -Path $configPath -Content $lemonadeConfig
    return $configPath
}

function Set-WindowsODSLemonadeModelConfiguration {
    <#
    .SYNOPSIS
        Persist the active Lemonade model ID and regenerate its LiteLLM route.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,

        [Parameter(Mandatory = $true)]
        [string]$ModelId,

        [string]$Port = "",
        [string]$ApiKey = ""
    )

    if ([string]::IsNullOrWhiteSpace($ModelId)) {
        throw "Lemonade model ID is required."
    }
    if ($ModelId -match '[\r\n]') {
        throw "Lemonade model ID cannot contain line breaks."
    }

    $envPath = Join-Path $InstallDir ".env"
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw "Cannot persist Lemonade model ID because .env is missing: $envPath"
    }

    $envContent = [System.IO.File]::ReadAllText($envPath)
    $assignment = "LEMONADE_MODEL=$ModelId"
    if ($envContent -match '(?m)^LEMONADE_MODEL=') {
        $replacement = $assignment.Replace('$', '$$')
        $envContent = [regex]::Replace($envContent, '(?m)^LEMONADE_MODEL=[^\r\n]*', $replacement)
    } else {
        $newline = $(if ($envContent.Contains("`r`n")) { "`r`n" } else { "`n" })
        if ($envContent.Length -gt 0 -and -not $envContent.EndsWith("`n")) {
            $envContent += $newline
        }
        $envContent += "$assignment$newline"
    }
    Write-Utf8NoBom -Path $envPath -Content $envContent

    if ([string]::IsNullOrWhiteSpace($Port)) {
        $portMatch = [regex]::Match($envContent, '(?m)^AMD_INFERENCE_PORT=([^\r\n]*)')
        if ($portMatch.Success) { $Port = $portMatch.Groups[1].Value.Trim().Trim('"').Trim("'") }
    }
    $parsedPort = 0
    if (-not [int]::TryParse($Port, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        $Port = "8080"
    }

    if ([string]::IsNullOrWhiteSpace($ApiKey)) {
        $keyMatch = [regex]::Match($envContent, '(?m)^LITELLM_LEMONADE_API_KEY=([^\r\n]*)')
        if ($keyMatch.Success) { $ApiKey = $keyMatch.Groups[1].Value.Trim().Trim('"').Trim("'") }
    }
    if ([string]::IsNullOrWhiteSpace($ApiKey)) { $ApiKey = "not-needed" }

    $configPath = Write-WindowsODSLemonadeLiteLlmConfig `
        -InstallDir $InstallDir -ModelId $ModelId -Port $Port -ApiKey $ApiKey
    return @{
        ModelId = $ModelId
        EnvPath = $envPath
        LiteLlmConfigPath = $configPath
    }
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

function New-ODSEnv {
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
    .PARAMETER ODSMode
        LLM backend mode: "local", "cloud", or "hybrid".
    #>
    param(
        [string]$InstallDir,
        [hashtable]$TierConfig,
        [string]$Tier,
        [string]$GpuBackend = "nvidia",
        [string]$ODSMode = "local",
        [string]$LlamaServerImage = "",
        [string]$AmdInferenceRuntime = "",
        [string]$AmdInferenceBackend = "",
        [string]$AmdInferenceLocation = "",
        [string]$AmdInferencePort = "",
        [string]$AmdInferenceSupportedBackends = "",
        [string]$AmdInferenceRuntimeMode = "",
        [string]$AmdInferenceManaged = "",
        [string]$LemonadeServerImage = "",
        [string]$LemonadeModel = "",
        [int]$SystemRamGB = 0,
        [bool]$WhisperCudaEnabled = $true,
        [string]$SwitchboardMode = "",
        # Mirror the install-time ENABLE_LANGFUSE toggle from phase 03 into
        # .env's LANGFUSE_ENABLED default. Re-install preserves whatever the
        # user already had in .env (via Get-EnvOrNew), so manual
        # `ods enable langfuse` edits survive.
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

    # Empty is meaningful for optional provider overrides: it selects the
    # bundled service. Distinguish it from a key missing in an older .env.
    function Get-EnvOrNewAllowEmpty { param([string]$Key, [string]$Default)
        if ($existingEnv.ContainsKey($Key)) {
            return $existingEnv[$Key]
        }
        return $Default
    }

    $webuiPort = Resolve-WindowsODSPort `
        -Name "WEBUI_PORT" -DefaultPort 3000 `
        -ExistingEnv $existingEnv -InstallDir $InstallDir

    # Lemonade's native Windows router reserves host port 9000 for websockets.
    # Keep Whisper's container port unchanged, but move its host port out of the
    # way on managed AMD/Lemonade installs. Existing .env choices still win.
    $whisperPortDefault = "9000"
    if ($GpuBackend -eq "amd" -and $AmdInferenceRuntime -eq "lemonade" -and $AmdInferenceLocation -eq "host") {
        $whisperPortDefault = "9100"
    }
    $whisperPort = Get-EnvOrNew "WHISPER_PORT" $whisperPortDefault
    if ($whisperPortDefault -eq "9100" -and $whisperPort -eq "9000") {
        $whisperPort = "9100"
    }

    function Get-ExistingTokenSpyApiKey {
        $tokenSpyKeyFile = Join-Path $InstallDir "data\token-spy\token-spy-api-key.txt"
        if (Test-Path $tokenSpyKeyFile) {
            $value = (Get-Content -Raw -Path $tokenSpyKeyFile -ErrorAction SilentlyContinue)
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value.Trim()
            }
        }
        return ""
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

    function Select-CappedCpuValue {
        param(
            [string]$Desired,
            [string]$Ceiling
        )

        $desiredNumber = 0.0
        $ceilingNumber = 0.0
        $style = [System.Globalization.NumberStyles]::Float
        $culture = [System.Globalization.CultureInfo]::InvariantCulture
        if (-not [double]::TryParse($Desired, $style, $culture, [ref]$desiredNumber)) {
            $desiredNumber = 1.0
        }
        if (-not [double]::TryParse($Ceiling, $style, $culture, [ref]$ceilingNumber) -or $ceilingNumber -le 0) {
            $ceilingNumber = 1.0
        }

        $value = [Math]::Min($desiredNumber, $ceilingNumber)
        if ($value -lt 0.01) { $value = 0.01 }
        return $value.ToString("0.0", $culture)
    }

    function Select-ServiceCpuLimit {
        param(
            [string]$Key,
            [string]$Desired,
            [string]$Available
        )
        return Select-AutoCpuValue -Key $Key -Detected (Select-CappedCpuValue -Desired $Desired -Ceiling $Available)
    }

    function Select-ServiceCpuReservation {
        param(
            [string]$Key,
            [string]$Desired,
            [string]$Limit
        )
        return Select-AutoCpuValue -Key $Key -Detected (Select-CappedCpuValue -Desired $Desired -Ceiling $Limit)
    }

    # Generate secrets (reuse existing on re-install)
    $webuiSecret     = Get-EnvOrNew "WEBUI_SECRET"       (New-SecureHex -Bytes 32)
    $n8nPass         = Get-EnvOrNew "N8N_PASS"           (New-SecureBase64 -Bytes 16)
    $litellmKey      = Get-EnvOrNew "LITELLM_KEY"        "sk-ods-$(New-SecureHex -Bytes 16)"
    $litellmLemonadeApiKey = Get-EnvOrNew "LITELLM_LEMONADE_API_KEY" "sk-ods-lemonade-$(New-SecureHex -Bytes 16)"
    $llamaServerImageFallback = Get-EnvOrNew "LLAMA_SERVER_IMAGE_FALLBACK" ([Environment]::GetEnvironmentVariable("LLAMA_SERVER_IMAGE_FALLBACK"))
    if ([string]::IsNullOrWhiteSpace($llamaServerImageFallback)) { $llamaServerImageFallback = "" }
    $livekitSecret   = Get-EnvOrNew "LIVEKIT_API_SECRET" (New-SecureBase64 -Bytes 32)
    $livekitApiKey   = Get-EnvOrNew "LIVEKIT_API_KEY"    (New-SecureHex -Bytes 16)
    $dashboardApiKey = Get-EnvOrNew "DASHBOARD_API_KEY"  (New-SecureHex -Bytes 32)
    $odsAgentKey   = Get-EnvOrNew "ODS_AGENT_KEY"    (New-SecureHex -Bytes 32)
    # HMAC key for signing ods-session cookies. Without it the dashboard-api
    # session_signer raises on issue() and verify-session returns no-secret —
    # the magic-link gate effectively breaks.
    $odsSessionSecret = Get-EnvOrNew "ODS_SESSION_SECRET" (New-SecureHex -Bytes 32)
    $shieldApiKey    = Get-EnvOrNew "SHIELD_API_KEY"     (New-SecureHex -Bytes 32)
    $tokenSpyApiKeyDefault = Get-ExistingTokenSpyApiKey
    if ([string]::IsNullOrWhiteSpace($tokenSpyApiKeyDefault)) {
        $tokenSpyApiKeyDefault = New-SecureHex -Bytes 32
    }
    $tokenSpyApiKey = Get-EnvOrNew "TOKEN_SPY_API_KEY" $tokenSpyApiKeyDefault
    $openclawToken   = Get-EnvOrNew "OPENCLAW_TOKEN"     (New-SecureHex -Bytes 24)
    $searxngSecret   = Get-EnvOrNew "SEARXNG_SECRET"     (New-SecureHex -Bytes 32)
    $difySecretKey    = Get-EnvOrNew "DIFY_SECRET_KEY"           (New-SecureHex -Bytes 32)
    $qdrantApiKey     = Get-EnvOrNew "QDRANT_API_KEY"            (New-SecureHex -Bytes 32)
    $opencodePassword = Get-EnvOrNew "OPENCODE_SERVER_PASSWORD"  (New-SecureBase64 -Bytes 16)
    $switchboardModeDefault = if ([string]::IsNullOrWhiteSpace($SwitchboardMode)) { "observe" } else { $SwitchboardMode.Trim().ToLowerInvariant() }
    if ($switchboardModeDefault -notin @("legacy", "observe", "enabled")) {
        $switchboardModeDefault = "observe"
    }
    $switchboardMode = Get-EnvOrNew "ODS_MODEL_SWITCHBOARD" $switchboardModeDefault
    $switchboardMode = $switchboardMode.Trim().ToLowerInvariant()
    if ($switchboardMode -notin @("legacy", "observe", "enabled")) {
        $switchboardMode = "observe"
    }
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
    $ttsCpuLimit = Select-ServiceCpuLimit -Key "TTS_CPU_LIMIT" -Desired "8.0" -Available $cpuBudget.Available
    $ttsCpuReservation = Select-ServiceCpuReservation -Key "TTS_CPU_RESERVATION" -Desired "2.0" -Limit $ttsCpuLimit
    $whisperCpuLimit = Select-ServiceCpuLimit -Key "WHISPER_CPU_LIMIT" -Desired "4.0" -Available $cpuBudget.Available
    $whisperCpuReservation = Select-ServiceCpuReservation -Key "WHISPER_CPU_RESERVATION" -Desired "1.0" -Limit $whisperCpuLimit
    $hermesCpuLimit = Select-ServiceCpuLimit -Key "HERMES_CPU_LIMIT" -Desired "4.0" -Available $cpuBudget.Available
    $hermesCpuReservation = Select-ServiceCpuReservation -Key "HERMES_CPU_RESERVATION" -Desired "0.5" -Limit $hermesCpuLimit
    $comfyuiCpuLimit = Select-ServiceCpuLimit -Key "COMFYUI_CPU_LIMIT" -Desired "16.0" -Available $cpuBudget.Available
    $comfyuiCpuReservation = Select-ServiceCpuReservation -Key "COMFYUI_CPU_RESERVATION" -Desired "2.0" -Limit $comfyuiCpuLimit

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
    $langfuseProjectPublicKey  = Get-EnvOrNew "LANGFUSE_PROJECT_PUBLIC_KEY" "pk-lf-ods-$(New-SecureHex -Bytes 16)"
    $langfuseProjectSecretKey  = Get-EnvOrNew "LANGFUSE_PROJECT_SECRET_KEY" "sk-lf-ods-$(New-SecureHex -Bytes 16)"
    $langfuseInitProjectId     = Get-EnvOrNew "LANGFUSE_INIT_PROJECT_ID"   (New-SecureHex -Bytes 16)
    $langfuseInitUserEmail     = Get-EnvOrNew "LANGFUSE_INIT_USER_EMAIL"   "admin@ods.local"
    $langfuseInitUserPassword  = Get-EnvOrNew "LANGFUSE_INIT_USER_PASSWORD" (New-SecureHex -Bytes 16)

    # Determine LLM backend engine and API URL.
    # AMD on Windows runs inference natively on the host. When that runtime is
    # Lemonade, ODS_MODE must also be lemonade so the LiteLLM container mounts
    # config/litellm/lemonade.yaml instead of local.yaml's in-container
    # llama-server route.
    $windowsAmdHostInference = ($GpuBackend -eq "amd" -and $ODSMode -ne "cloud")
    $windowsAmdLemonade = ($windowsAmdHostInference -and $AmdInferenceRuntime -eq "lemonade")
    $nativeInferencePort = "8080"
    $parsedNativeInferencePort = 0
    if ([int]::TryParse([string]$AmdInferencePort, [ref]$parsedNativeInferencePort) -and
        $parsedNativeInferencePort -ge 1 -and $parsedNativeInferencePort -le 65535) {
        $nativeInferencePort = [string]$parsedNativeInferencePort
    }
    $effectiveODSMode = $(if ($windowsAmdLemonade) { "lemonade" } else { $ODSMode })
    $existingLemonadeModel = Get-EnvOrNew "LEMONADE_MODEL" ""
    $existingGgufFile = Get-EnvOrNew "GGUF_FILE" ""
    $effectiveLemonadeModel = $existingLemonadeModel
    if ($windowsAmdLemonade) {
        $effectiveLemonadeModel = $(if (-not [string]::IsNullOrWhiteSpace($LemonadeModel)) {
            $LemonadeModel
        } elseif (-not [string]::IsNullOrWhiteSpace($existingLemonadeModel) -and
            -not [string]::IsNullOrWhiteSpace($existingGgufFile) -and
            $existingGgufFile.Equals([string]$TierConfig.GgufFile, [StringComparison]::OrdinalIgnoreCase)) {
            $existingLemonadeModel
        } elseif (-not [string]::IsNullOrWhiteSpace([string]$TierConfig.GgufFile)) {
            "extra.$($TierConfig.GgufFile)"
        } else {
            $TierConfig.LlmModel
        })
    }

    # NOTE: $(if ...) syntax required for PS 5.1 compatibility
    $llmBackend = $(if ($windowsAmdLemonade) {
        "lemonade"
    } elseif ($ODSMode -eq "cloud") {
        "litellm"
    } elseif ($windowsAmdHostInference) {
        "llama-server"
    } else {
        "llama-server"
    })

    # Lemonade serves OpenAI-compatible API at /api/v1; llama-server at /v1
    $llmApiBasePath = $(if ($windowsAmdLemonade) { "/api/v1" } else { "/v1" })

    $llmApiUrl = $(if ($windowsAmdHostInference) {
        "http://host.docker.internal:$nativeInferencePort"
    } elseif ($ODSMode -eq "cloud") {
        "http://litellm:4000"
    } else {
        "http://llama-server:8080"
    })

    # Hermes streams through the OpenAI-compatible provider. On Windows AMD
    # Lemonade, direct streaming against Lemonade can close chunked responses
    # early; LiteLLM normalizes that path and already fronts the same runtime
    # for Open WebUI. Match the Linux AMD behavior and authenticate with the
    # LiteLLM master key whenever Hermes targets LiteLLM.
    $hermesUsesLiteLlm = ($windowsAmdLemonade -or $ODSMode -eq "cloud")
    if ($switchboardMode -eq "enabled") {
        $hermesUsesLiteLlm = $true
    }
    $hermesLlmBaseUrl = $(if ($hermesUsesLiteLlm) { "http://litellm:4000/v1" } else { "$llmApiUrl$llmApiBasePath" })
    $hermesLlmApiKey = $(if ($hermesUsesLiteLlm) { $litellmKey } else { "sk-ods-hermes-local" })
    $openWebuiLlmBaseUrl = Get-EnvOrNew "OPEN_WEBUI_LLM_BASE_URL" $(if ($switchboardMode -eq "enabled") { "http://litellm:4000" } else { "" })
    $openWebuiLlmApiKey = Get-EnvOrNew "OPEN_WEBUI_LLM_API_KEY" $(if ($switchboardMode -eq "enabled") { $litellmKey } else { "" })

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
            # PowerShell `switch` runs *every* matching arm, and as a
            # subexpression it collects all emitted values into an array, so
            # multi-matching IDs (e.g. "AUS Eastern Standard Time" hits both
            # "*AUS Eastern*" and "*Eastern*") would write an invalid
            # "TIMEZONE=America/New_York Australia/Sydney". `break` on every arm
            # makes the first match win; the more specific "*AUS Eastern*" is
            # ordered ahead of "*Eastern*" so it takes precedence.
            switch -Wildcard ($tzInfo.Id) {
                "*AUS Eastern*"  { "Australia/Sydney"; break }
                "*Eastern*"    { "America/New_York"; break }
                "*Central*"    { "America/Chicago"; break }
                "*Mountain*"   { "America/Denver"; break }
                "*Pacific*"    { "America/Los_Angeles"; break }
                "*Alaska*"     { "America/Anchorage"; break }
                "*Hawaii*"     { "Pacific/Honolulu"; break }
                "*UTC*"        { "UTC"; break }
                "*GMT*"        { "Europe/London"; break }
                "*W. Europe*"  { "Europe/Berlin"; break }
                "*Romance*"    { "Europe/Paris"; break }
                "*India*"      { "Asia/Kolkata"; break }
                "*China*"      { "Asia/Shanghai"; break }
                "*Tokyo*"      { "Asia/Tokyo"; break }
                "*Korea*"      { "Asia/Seoul"; break }
                "*E. South America*" { "America/Sao_Paulo"; break }
                "*SE Asia*"    { "Asia/Bangkok"; break }
                "*Arab*"       { "Asia/Riyadh"; break }
                "*Egypt*"      { "Africa/Cairo"; break }
                "*South Africa*" { "Africa/Johannesburg"; break }
                "*E. Europe*"  { "Europe/Bucharest"; break }
                "*FLE*"        { "Europe/Kiev"; break }
                default        { "UTC"; break }
            }
        }
    } catch { "UTC" })

    $timestamp = Get-Date -Format "o"
    $whisperAccelerationDefault = $(if ($GpuBackend -eq "nvidia" -and $WhisperCudaEnabled) { "cuda" } else { "cpu" })
    $whisperAcceleration = Get-EnvOrNew "WHISPER_ACCELERATION" $whisperAccelerationDefault
    if ($GpuBackend -eq "nvidia" -and -not $WhisperCudaEnabled) {
        $whisperAcceleration = "cpu"
    }
    if ($whisperAcceleration -notin @("cpu", "cuda")) {
        $whisperAcceleration = $whisperAccelerationDefault
    }

    $whisperImageDefault = $(if ($whisperAcceleration -eq "cuda") { "" } else { "ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu" })
    $whisperImage = Get-EnvOrNew "WHISPER_IMAGE" $whisperImageDefault
    if ($whisperAcceleration -eq "cpu" -and
        ([string]::IsNullOrWhiteSpace($whisperImage) -or $whisperImage -match "(?i)cuda")) {
        $whisperImage = "ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu"
    }

    $audioSttModelDefault = $(if ($whisperAcceleration -eq "cuda") { "deepdml/faster-whisper-large-v3-turbo-ct2" } else { "Systran/faster-whisper-base" })
    $audioSttModel = Get-EnvOrNew "AUDIO_STT_MODEL" $audioSttModelDefault
    if ($whisperAcceleration -eq "cpu" -and $audioSttModel -match "(?i)large-v3|turbo") {
        $audioSttModel = $audioSttModelDefault
    }
    $embeddingModelDefault = [Environment]::GetEnvironmentVariable("EMBEDDING_MODEL")
    if ([string]::IsNullOrWhiteSpace($embeddingModelDefault)) { $embeddingModelDefault = "BAAI/bge-base-en-v1.5" }
    $embeddingModel = Get-EnvOrNew "EMBEDDING_MODEL" $embeddingModelDefault
    $ragEmbeddingModel = Get-EnvOrNewAllowEmpty "RAG_EMBEDDING_MODEL" ([Environment]::GetEnvironmentVariable("RAG_EMBEDDING_MODEL"))
    $ragOpenAiApiBaseUrl = Get-EnvOrNewAllowEmpty "RAG_OPENAI_API_BASE_URL" ([Environment]::GetEnvironmentVariable("RAG_OPENAI_API_BASE_URL"))
    $ragOpenAiApiKey = Get-EnvOrNewAllowEmpty "RAG_OPENAI_API_KEY" ([Environment]::GetEnvironmentVariable("RAG_OPENAI_API_KEY"))
    $embeddingsMemoryLimitDefault = [Environment]::GetEnvironmentVariable("EMBEDDINGS_MEMORY_LIMIT")
    if ([string]::IsNullOrWhiteSpace($embeddingsMemoryLimitDefault)) { $embeddingsMemoryLimitDefault = "4G" }
    $embeddingsMemoryLimit = Get-EnvOrNew "EMBEDDINGS_MEMORY_LIMIT" $embeddingsMemoryLimitDefault

    # Build .env content (matches Phase 06 format)
    $envContent = @"
# ODS Configuration -- $($TierConfig.TierName) Edition
# Generated by Windows installer v$($script:ODS_VERSION) on $timestamp
# Tier: $Tier ($($TierConfig.TierName))

#=== Network Binding ===
# 127.0.0.1 = localhost only (secure default)
# 0.0.0.0   = accessible from LAN (install with -Lan or set manually)
BIND_ADDRESS=$(Get-EnvOrNew "BIND_ADDRESS" "$(if ($EnableLan) { "0.0.0.0" } else { "127.0.0.1" })")
# Docker Desktop containers reach loopback-only host services through this name.
ODS_AGENT_HOST=$(Get-EnvOrNew "ODS_AGENT_HOST" "host.docker.internal")
# The dashboard-api container must call the host agent over Docker Desktop's
# host gateway. Bearer auth still protects every host-agent endpoint.
ODS_AGENT_BIND=$(Get-EnvOrNew "ODS_AGENT_BIND" "0.0.0.0")

#=== LLM Backend Mode ===
ODS_MODE=$effectiveODSMode
ODS_MODEL_SWITCHBOARD=$switchboardMode
LLM_BACKEND=$llmBackend
LLM_API_URL=$llmApiUrl
OPEN_WEBUI_LLM_BASE_URL=$openWebuiLlmBaseUrl
OPEN_WEBUI_LLM_API_KEY=$openWebuiLlmApiKey
LLM_API_BASE_PATH=$llmApiBasePath
AMD_INFERENCE_RUNTIME=$AmdInferenceRuntime
AMD_INFERENCE_BACKEND=$AmdInferenceBackend
AMD_INFERENCE_LOCATION=$AmdInferenceLocation
AMD_INFERENCE_PORT=$(if ($windowsAmdHostInference) { $nativeInferencePort } else { $AmdInferencePort })
AMD_INFERENCE_SUPPORTED_BACKENDS=$AmdInferenceSupportedBackends
AMD_INFERENCE_RUNTIME_MODE=$AmdInferenceRuntimeMode
AMD_INFERENCE_MANAGED=$AmdInferenceManaged

#=== Cloud API Keys ===
ANTHROPIC_API_KEY=$(Get-EnvOrNew "ANTHROPIC_API_KEY" "")
OPENAI_API_KEY=$(Get-EnvOrNew "OPENAI_API_KEY" "")
TOGETHER_API_KEY=$(Get-EnvOrNew "TOGETHER_API_KEY" "")
MINIMAX_API_KEY=$(Get-EnvOrNew "MINIMAX_API_KEY" "")

#=== LLM Settings (llama-server) ===
MODEL_PROFILE=$(Get-EnvOrNew "MODEL_PROFILE" "$(if ($TierConfig.ModelProfileRequested) { $TierConfig.ModelProfileRequested } else { "qwen" })")
LLM_MODEL=$($TierConfig.LlmModel)
GGUF_FILE=$($TierConfig.GgufFile)
LEMONADE_MODEL=$effectiveLemonadeModel
MAX_CONTEXT=$($TierConfig.MaxContext)
CTX_SIZE=$($TierConfig.MaxContext)
MODEL_RECOMMENDED_MODEL=$($TierConfig.LlmModel)
MODEL_RECOMMENDED_GGUF=$($TierConfig.GgufFile)
MODEL_RECOMMENDED_CONTEXT=$($TierConfig.MaxContext)
MODEL_RECOMMENDATION_SOURCE=$(if ($TierConfig.RecommendationSource) { $TierConfig.RecommendationSource } else { "installer_tier_map" })
MODEL_RECOMMENDATION_POLICY=$(if ($TierConfig.RecommendationPolicy) { $TierConfig.RecommendationPolicy } else { "tier-map" })
MODEL_RECOMMENDATION_CONFIDENCE=$(if ($TierConfig.RecommendationConfidence) { $TierConfig.RecommendationConfidence } else { "medium" })
MODEL_RECOMMENDATION_REASON=$(if ($TierConfig.RecommendationReason) { $TierConfig.RecommendationReason } else { "Selected by installer tier $Tier ($($TierConfig.TierName)) for $GpuBackend backend; benchmark locally after first launch." })
MODEL_RECOMMENDED_ALTERNATIVES=$(if ($TierConfig.RecommendationAlternatives) { $TierConfig.RecommendationAlternatives } else { "" })
MODEL_RUNTIME_PROFILE=$(if ($TierConfig.RuntimeProfile) { $TierConfig.RuntimeProfile } else { "" })
MODEL_RUNTIME_PROFILE_LABEL=$(if ($TierConfig.RuntimeProfileLabel) { $TierConfig.RuntimeProfileLabel } else { "" })
MODEL_RUNTIME_PROFILE_SOURCE=$(if ($TierConfig.RuntimeProfileSource) { $TierConfig.RuntimeProfileSource } else { "" })
MODEL_PERFORMANCE_SOURCE=benchmark_required
MODEL_PERFORMANCE_LABEL=Benchmark after first launch
GPU_BACKEND=$GpuBackend
SYSTEM_RAM_GB=$SystemRamGB
$(if ($LlamaServerImage) { "LLAMA_SERVER_IMAGE=$LlamaServerImage" } else { "#LLAMA_SERVER_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda" })
$(if ($llamaServerImageFallback) { "LLAMA_SERVER_IMAGE_FALLBACK=$llamaServerImageFallback" } else { "#LLAMA_SERVER_IMAGE_FALLBACK=ghcr.io/ggml-org/llama.cpp:server-cuda-b9014" })
$(if ($LemonadeServerImage) { "LEMONADE_SERVER_IMAGE=$LemonadeServerImage" } else { "#LEMONADE_SERVER_IMAGE=ghcr.io/lemonade-sdk/lemonade-server:v10.2.0" })
#=== llama.cpp Runtime Tuning ===
LLAMA_PARALLEL=$(Get-EnvOrNew "LLAMA_PARALLEL" "$(if ($TierConfig.LLAMA_PARALLEL) { $TierConfig.LLAMA_PARALLEL } else { "1" })")
LLAMA_ARG_FLASH_ATTN=$(Get-EnvOrNew "LLAMA_ARG_FLASH_ATTN" "$(if ($TierConfig.LLAMA_ARG_FLASH_ATTN) { $TierConfig.LLAMA_ARG_FLASH_ATTN } else { "auto" })")
LLAMA_ARG_CACHE_TYPE_K=$(Get-EnvOrNew "LLAMA_ARG_CACHE_TYPE_K" "$(if ($TierConfig.LLAMA_ARG_CACHE_TYPE_K) { $TierConfig.LLAMA_ARG_CACHE_TYPE_K } else { "f16" })")
LLAMA_ARG_CACHE_TYPE_V=$(Get-EnvOrNew "LLAMA_ARG_CACHE_TYPE_V" "$(if ($TierConfig.LLAMA_ARG_CACHE_TYPE_V) { $TierConfig.LLAMA_ARG_CACHE_TYPE_V } else { "f16" })")
# Optional MoE only. Example for 8-12GB VRAM: LLAMA_ARG_N_CPU_MOE=25
$(if ($TierConfig.LLAMA_ARG_N_CPU_MOE) { "LLAMA_ARG_N_CPU_MOE=$($TierConfig.LLAMA_ARG_N_CPU_MOE)" })
$(if ($TierConfig.LLAMA_ARG_NO_CACHE_PROMPT) { "LLAMA_ARG_NO_CACHE_PROMPT=$($TierConfig.LLAMA_ARG_NO_CACHE_PROMPT)" })
$(if ($TierConfig.LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS) { "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS=$($TierConfig.LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS)" })
# Optional MTP speculative decoding only. Requires an MTP-capable GGUF and llama.cpp build.
# LLAMA_ARG_SPEC_TYPE=draft-mtp
# LLAMA_ARG_SPEC_DRAFT_N_MAX=3
LLAMA_CPU_LIMIT=$llamaCpuLimit
LLAMA_CPU_RESERVATION=$llamaCpuReservation

#=== Bundled Service CPU Budgets ===
TTS_CPU_LIMIT=$ttsCpuLimit
TTS_CPU_RESERVATION=$ttsCpuReservation
WHISPER_CPU_LIMIT=$whisperCpuLimit
WHISPER_CPU_RESERVATION=$whisperCpuReservation
HERMES_CPU_LIMIT=$hermesCpuLimit
HERMES_CPU_RESERVATION=$hermesCpuReservation
COMFYUI_CPU_LIMIT=$comfyuiCpuLimit
COMFYUI_CPU_RESERVATION=$comfyuiCpuReservation

#=== Ports ===
OLLAMA_PORT=11434
WEBUI_PORT=$webuiPort
WHISPER_PORT=$whisperPort
TTS_PORT=8880
N8N_PORT=5678
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_API_KEY=$qdrantApiKey
LITELLM_PORT=4000
OPENCLAW_PORT=7860
SEARXNG_PORT=8888

#=== Hermes Agent ===
HERMES_LLM_BASE_URL=$hermesLlmBaseUrl
HERMES_LLM_API_KEY=$hermesLlmApiKey
HERMES_LANGUAGE=en
HERMES_PROXY_PORT=9120
HERMES_PROXY_UPSTREAM=ods-hermes:9119
ODS_AUTH_UPSTREAM=ods-dashboard-api:3002

#=== Security (auto-generated, keep secret!) ===
WEBUI_SECRET=$webuiSecret
DASHBOARD_API_KEY=$dashboardApiKey
ODS_AGENT_KEY=$odsAgentKey
ODS_SESSION_SECRET=$odsSessionSecret
SHIELD_API_KEY=$shieldApiKey
N8N_USER=admin@ods.local
N8N_PASS=$n8nPass
LITELLM_KEY=$litellmKey
$(if ($GpuBackend -eq "amd") { "LITELLM_LEMONADE_API_KEY=$litellmLemonadeApiKey" })
LIVEKIT_API_KEY=$livekitApiKey
LIVEKIT_API_SECRET=$livekitSecret
OPENCLAW_TOKEN=$openclawToken
OPENCODE_SERVER_PASSWORD=$opencodePassword
OPENCODE_PORT=3003
TOKEN_SPY_API_KEY=$tokenSpyApiKey
SEARXNG_SECRET=$searxngSecret
DIFY_SECRET_KEY=$difySecretKey

#=== Voice Settings ===
WHISPER_MODEL=base
# Whisper STT runtime. Windows NVIDIA uses CUDA only when the driver supports
# the bundled Speaches CUDA image; otherwise Whisper stays on the CPU image.
WHISPER_ACCELERATION=$whisperAcceleration
$(if ($whisperImage) { "WHISPER_IMAGE=$whisperImage" } else { "#WHISPER_IMAGE=ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu" })
# Whisper STT model — CUDA uses the larger turbo model, CPU uses base.
# Open WebUI reads this to request transcription; installer pre-downloads
# the same model so the first transcription works.
AUDIO_STT_MODEL=$audioSttModel
TTS_VOICE=en_US-lessac-medium

#=== Embeddings / RAG ===
# Open WebUI uses this canonical model at first boot unless an explicit
# external-provider override is configured.
EMBEDDING_MODEL=$embeddingModel
RAG_EMBEDDING_MODEL=$ragEmbeddingModel
RAG_OPENAI_API_BASE_URL=$ragOpenAiApiBaseUrl
RAG_OPENAI_API_KEY=$ragOpenAiApiKey
EMBEDDINGS_MEMORY_LIMIT=$embeddingsMemoryLimit

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
    if (Test-Path -LiteralPath $envPath -PathType Container) {
        Remove-Item -LiteralPath $envPath -Recurse -Force
        Write-AIWarn "Removed malformed .env directory from a previous partial install."
    }
    Write-Utf8NoBom -Path $envPath -Content $envContent

    if ($effectiveODSMode -eq "local") {
        $litellmDir = Join-Path (Join-Path $InstallDir "config") "litellm"
        $localModel = $(if ($TierConfig.GgufFile) { $TierConfig.GgufFile } else { $TierConfig.LlmModel })
        $localApiBase = "$llmApiUrl$llmApiBasePath"
        $localConfig = @"
model_list:
  - model_name: default
    litellm_params:
      model: openai/$localModel
      api_base: $localApiBase
      api_key: sk-ods-hermes-local
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/$localModel
      api_base: $localApiBase
      api_key: sk-ods-hermes-local
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
        Write-Utf8NoBom -Path (Join-Path $litellmDir "local.yaml") -Content $localConfig
    }

    if ($windowsAmdLemonade) {
        $lemonadePort = $nativeInferencePort
        $null = Write-WindowsODSLemonadeLiteLlmConfig `
            -InstallDir $InstallDir -ModelId $effectiveLemonadeModel `
            -Port $lemonadePort -ApiKey $litellmLemonadeApiKey
    }

    $modelRouterDir = Join-Path (Join-Path $InstallDir "config") "model-router"
    New-Item -ItemType Directory -Path $modelRouterDir -Force | Out-Null
    function ConvertTo-RouterOrigin {
        param([string]$Url, [string]$Fallback)
        $value = if ([string]::IsNullOrWhiteSpace($Url)) { $Fallback } else { $Url.TrimEnd("/") }
        if ($value.EndsWith("/api/v1", [StringComparison]::OrdinalIgnoreCase)) {
            return $value.Substring(0, $value.Length - "/v1".Length)
        }
        if ($value.EndsWith("/v1", [StringComparison]::OrdinalIgnoreCase)) {
            return $value.Substring(0, $value.Length - "/v1".Length)
        }
        return $value
    }
    $routerLlamaBase = ConvertTo-RouterOrigin -Url "$llmApiUrl$llmApiBasePath" -Fallback "http://llama-server:8080"
    $routerEndpoints = @(
        [ordered]@{ id = "llama-server-default"; baseUrl = $routerLlamaBase }
    )
    if ($windowsAmdLemonade) {
        $lemonadePort = $nativeInferencePort
        $routerEndpoints += [ordered]@{
            id = "lemonade-default"
            baseUrl = "http://host.docker.internal:$lemonadePort/api"
        }
    }
    $routerPayload = [ordered]@{ endpoints = $routerEndpoints }
    Write-Utf8NoBom -Path (Join-Path $modelRouterDir "endpoints.json") -Content (($routerPayload | ConvertTo-Json -Depth 6) + "`n")

    # Restrict .env to current user only (Windows ACL equivalent of chmod 600)
    try {
        # Retrieve only the DACL (Access) to avoid requiring SeSecurityPrivilege
        $acl = [System.IO.File]::GetAccessControl($envPath, [System.Security.AccessControl.AccessControlSections]::Access)
        $acl.SetAccessRuleProtection($true, $false)  # Disable inheritance
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $currentUser, "FullControl", "Allow"
        )
        $acl.SetAccessRule($rule)
        [System.IO.File]::SetAccessControl($envPath, $acl)
    } catch {
        # ACL restriction failed -- not fatal, just warn
        Write-AIWarn "Could not restrict .env permissions: $_"
    }

    return @{
        SearxngSecret  = $searxngSecret
        OpenclawToken  = $openclawToken
        LemonadeModel  = $effectiveLemonadeModel
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
            "name": "ODS LLM (Local)",
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
          "name": "ODS LLM (Local)",
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
        Served model id to configure as the default chat model.
    .PARAMETER LlmBaseUrl
        OpenAI-compatible base URL as seen from the Perplexica container.
    .PARAMETER ApiKey
        API key for the configured provider.
    #>
    param(
        [int]$PerplexicaPort = 3004,
        [string]$LlmModel,
        [string]$LlmBaseUrl = "http://llama-server:8080/v1",
        [string]$ApiKey = "no-key"
    )

    $baseUrl = "http://localhost:$PerplexicaPort"
    if ([string]::IsNullOrWhiteSpace($LlmModel)) { $LlmModel = "default" }
    if ([string]::IsNullOrWhiteSpace($LlmBaseUrl)) { $LlmBaseUrl = "http://llama-server:8080/v1" }
    $LlmBaseUrl = $LlmBaseUrl.TrimEnd("/")
    if (-not ($LlmBaseUrl.EndsWith("/v1") -or $LlmBaseUrl.EndsWith("/api/v1"))) {
        $LlmBaseUrl = "$LlmBaseUrl/v1"
    }
    if ([string]::IsNullOrWhiteSpace($ApiKey)) { $ApiKey = "no-key" }

    function Set-PerplexicaObjectProperty {
        param($Target, [string]$Name, $Value)
        $property = $Target.PSObject.Properties[$Name]
        if ($property) {
            $property.Value = $Value
        } else {
            $Target | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
        }
    }

    function Post-Json {
        param([string]$Uri, $Value)
        $body = $Value | ConvertTo-Json -Depth 10 -Compress
        $utf8Bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $req = [System.Net.HttpWebRequest]::Create($Uri)
        $req.Method = "POST"
        $req.ContentType = "application/json"
        $req.Timeout = 5000
        $stream = $req.GetRequestStream()
        $stream.Write($utf8Bytes, 0, $utf8Bytes.Length)
        $stream.Close()
        $resp = $req.GetResponse()
        $resp.Close()
    }

    # Helper: POST a key/value pair to the config API.
    function Post-ConfigValue {
        param([string]$Key, $Value)
        Post-Json -Uri "$baseUrl/api/config" -Value @{ key = $Key; value = $Value }
    }

    function Mark-SetupComplete {
        try {
            Post-Json -Uri "$baseUrl/api/config/setup-complete" -Value @{}
        } catch {
            Post-ConfigValue -Key "setupComplete" -Value $true
        }
    }

    function Test-PerplexicaConfigReady {
        param($Config)
        if (-not $Config.setupComplete) { return $false }
        $providers = @($Config.modelProviders)
        $openaiProv = $providers | Where-Object { $_.type -eq "openai" } | Select-Object -First 1
        if (-not $openaiProv) { return $false }
        $chatModels = @($openaiProv.chatModels)
        $hasModel = $false
        foreach ($model in $chatModels) {
            if ($model.key -eq $LlmModel -or $model.name -eq $LlmModel) {
                $hasModel = $true
                break
            }
        }
        if (-not $hasModel) { return $false }
        if (-not $Config.preferences) { return $false }
        return ($Config.preferences.defaultChatModel -eq $LlmModel)
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

        if (Test-PerplexicaConfigReady -Config $config) { return $true }

        $providers = @($config.modelProviders)
        $openaiProv = $providers | Where-Object { $_.type -eq "openai" } | Select-Object -First 1
        $transformersProv = $providers | Where-Object { $_.type -eq "transformers" } | Select-Object -First 1

        if (-not $openaiProv) { return $false }

        # Seed the chat model into the OpenAI provider and set provider auth/config.
        Set-PerplexicaObjectProperty -Target $openaiProv -Name "chatModels" -Value @(@{ key = $LlmModel; name = $LlmModel })
        if (-not $openaiProv.PSObject.Properties["config"] -or $null -eq $openaiProv.config) {
            Set-PerplexicaObjectProperty -Target $openaiProv -Name "config" -Value ([pscustomobject]@{})
        }
        Set-PerplexicaObjectProperty -Target $openaiProv.config -Name "apiKey" -Value $ApiKey
        Set-PerplexicaObjectProperty -Target $openaiProv.config -Name "baseURL" -Value $LlmBaseUrl
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
        Mark-SetupComplete

        return $true
    } catch {
        return $false
    }
}
