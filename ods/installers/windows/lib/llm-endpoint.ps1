# ============================================================================
# ODS Windows -- local LLM endpoint helpers
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Parse .env safely and resolve the active local LLM endpoint across
#          Docker-backed NVIDIA/CPU installs and native AMD backends.
# ============================================================================

function Get-WindowsODSEnvMap {
    <#
    .SYNOPSIS
        Parse the generated .env file without executing it.
    #>
    param(
        [string]$InstallDir = $script:ODS_INSTALL_DIR,
        [string]$Path = ""
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        if ([string]::IsNullOrWhiteSpace($InstallDir)) {
            return @{}
        }
        $Path = Join-Path $InstallDir ".env"
    }

    $result = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $result }

    try {
        Get-Content -LiteralPath $Path -ErrorAction Stop | ForEach-Object {
            $line = $_.Trim()
            if ($line -match "^#" -or $line -eq "") { return }
            if ($line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
                $key = $Matches[1]
                $val = $Matches[2]
                # Strip exactly one matching pair of surrounding quotes.
                # Trimming each quote character independently corrupts values
                # that legitimately start or end with the other quote: a
                # double-quoted "'literal'" loses its inner single quotes and
                # KEY="'" collapses to empty. Mismatched quotes are kept
                # verbatim, matching lib/safe-env.sh on Linux.
                if ($val.Length -ge 2 -and (
                        ($val.StartsWith('"') -and $val.EndsWith('"')) -or
                        ($val.StartsWith("'") -and $val.EndsWith("'")))) {
                    $val = $val.Substring(1, $val.Length - 2)
                }
                $result[$key] = $val
            }
        }
    } catch {
        return @{}
    }

    return $result
}

function Get-WindowsODSEnvValue {
    <#
    .SYNOPSIS
        Read the first populated value from a parsed .env hashtable.
    #>
    param(
        [hashtable]$EnvMap,
        [string[]]$Keys,
        [string]$Default = ""
    )

    foreach ($key in $Keys) {
        if ($EnvMap.ContainsKey($key)) {
            $value = [string]$EnvMap[$key]
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value
            }
        }
    }

    return $Default
}

function Get-WindowsLocalLlmEndpoint {
    <#
    .SYNOPSIS
        Resolve the active local LLM endpoint for native AMD and Docker installs.
    .OUTPUTS
        @{ Name; Backend; Port; ApiBasePath; HealthUrl; BaseUrl; ChatCompletionsUrl }
    #>
    param(
        [string]$InstallDir = $script:ODS_INSTALL_DIR,
        [hashtable]$EnvMap = $null,
        [string]$GpuBackend = "",
        [string]$NativeBackend = "",
        [switch]$UseLemonade,
        [switch]$CloudMode
    )

    if ($null -eq $EnvMap) {
        $EnvMap = Get-WindowsODSEnvMap -InstallDir $InstallDir
    }

    $resolvedNativeBackend = $NativeBackend
    if ([string]::IsNullOrWhiteSpace($resolvedNativeBackend)) {
        $resolvedNativeBackend = ""
    } else {
        $resolvedNativeBackend = $resolvedNativeBackend.ToLowerInvariant()
    }

    $resolvedGpuBackend = $GpuBackend
    if ([string]::IsNullOrWhiteSpace($resolvedGpuBackend)) {
        $resolvedGpuBackend = Get-WindowsODSEnvValue -EnvMap $EnvMap -Keys @("GPU_BACKEND") -Default ""
    }
    if (-not [string]::IsNullOrWhiteSpace($resolvedGpuBackend)) {
        $resolvedGpuBackend = $resolvedGpuBackend.ToLowerInvariant()
    }

    $llmBackend = Get-WindowsODSEnvValue -EnvMap $EnvMap -Keys @("LLM_BACKEND") -Default "llama-server"
    if (-not [string]::IsNullOrWhiteSpace($llmBackend)) {
        $llmBackend = $llmBackend.ToLowerInvariant()
    }

    $amdInferenceRuntime = Get-WindowsODSEnvValue -EnvMap $EnvMap -Keys @("AMD_INFERENCE_RUNTIME") -Default ""
    if (-not [string]::IsNullOrWhiteSpace($amdInferenceRuntime)) {
        $amdInferenceRuntime = $amdInferenceRuntime.ToLowerInvariant()
    }

    $amdInferenceLocation = Get-WindowsODSEnvValue -EnvMap $EnvMap -Keys @("AMD_INFERENCE_LOCATION") -Default ""
    if (-not [string]::IsNullOrWhiteSpace($amdInferenceLocation)) {
        $amdInferenceLocation = $amdInferenceLocation.ToLowerInvariant()
    }

    $amdInferenceRuntimeMode = Get-WindowsODSEnvValue -EnvMap $EnvMap -Keys @("AMD_INFERENCE_RUNTIME_MODE") -Default ""
    if (-not [string]::IsNullOrWhiteSpace($amdInferenceRuntimeMode)) {
        $amdInferenceRuntimeMode = $amdInferenceRuntimeMode.ToLowerInvariant()
    }

    if ($UseLemonade -or $resolvedNativeBackend -eq "lemonade" -or $llmBackend -eq "lemonade") {
        return @{
            Name = "LLM (Lemonade)"
            Backend = "lemonade"
            Port = "$($script:LEMONADE_PORT)"
            ApiBasePath = "/api/v1"
            HealthUrl = $script:LEMONADE_HEALTH_URL
            BaseUrl = "http://localhost:$($script:LEMONADE_PORT)/api/v1"
            ChatCompletionsUrl = "http://localhost:$($script:LEMONADE_PORT)/api/v1/chat/completions"
        }
    }

    $usesNativeHostLlamaServer = (-not $CloudMode -and (
        $resolvedGpuBackend -eq "amd" -or
        $amdInferenceRuntimeMode -eq "windows-llama-server-fallback" -or
        ($resolvedNativeBackend -eq "llama-server" -and
            $amdInferenceRuntime -eq "llama-server" -and
            $amdInferenceLocation -eq "host")
    ))

    if ($usesNativeHostLlamaServer) {
        return @{
            Name = "LLM (llama-server)"
            Backend = "native-llama-server"
            Port = "8080"
            ApiBasePath = "/v1"
            HealthUrl = "http://localhost:8080/health"
            BaseUrl = "http://localhost:8080/v1"
            ChatCompletionsUrl = "http://localhost:8080/v1/chat/completions"
        }
    }

    $port = Get-WindowsODSEnvValue -EnvMap $EnvMap -Keys @("OLLAMA_PORT", "LLAMA_SERVER_PORT") -Default "11434"
    $apiBasePath = Get-WindowsODSEnvValue -EnvMap $EnvMap -Keys @("LLM_API_BASE_PATH") -Default "/v1"
    if ($apiBasePath -notmatch "^/") {
        $apiBasePath = "/$apiBasePath"
    }

    return @{
        Name = "LLM (llama-server)"
        Backend = "docker-llama-server"
        Port = $port
        ApiBasePath = $apiBasePath
        HealthUrl = "http://localhost:${port}/health"
        BaseUrl = "http://localhost:${port}${apiBasePath}"
        ChatCompletionsUrl = "http://localhost:${port}${apiBasePath}/chat/completions"
    }
}

function Test-WindowsLlmModelReadiness {
    <#
    .SYNOPSIS
        Prove the local LLM can actually serve, not just that its process is alive.
    .DESCRIPTION
        A healthy Lemonade/llama-server process is NOT proof the model works: if the
        GGUF backing file was never placed on disk, /v1/models still lists the model
        but every chat/completions returns 500. This gate proves two things before an
        install may report healthy:
          1. the GGUF backing file exists at the path the backend loads from, and
          2. a minimal completion actually succeeds (the real user path).
        Returns a result hashtable; the caller decides fatality.
    .OUTPUTS
        @{ Ok; FileExists; ModelFile; ModelId; CompletionOk; Detail }
    #>
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Endpoint,
        [Parameter(Mandatory = $true)] [string]$InstallDir,
        [string]$GgufFile = "",
        [int]$TimeoutSec = 120
    )

    $result = @{ Ok = $false; FileExists = $false; ModelFile = ""; ModelId = ""; CompletionOk = $false; Detail = "" }

    # 1. The backing GGUF must exist on disk where the backend loads it from.
    if (-not [string]::IsNullOrWhiteSpace($GgufFile)) {
        $modelPath = Join-Path (Join-Path (Join-Path $InstallDir "data") "models") $GgufFile
        $result.ModelFile = $modelPath
        $result.FileExists = Test-Path $modelPath
    } else {
        # No local GGUF configured (e.g. cloud/managed backend) -> file gate N/A.
        $result.FileExists = $true
    }

    # 2. Resolve the served model id. Lemonade prefixes discovered GGUFs with
    #    'extra.', while the native llama-server fallback serves the GGUF name
    #    directly. Key this off the resolved endpoint, not the broader AMD GPU
    #    family, so a valid Vulkan fallback install does not false-fail.
    $modelId = $GgufFile
    $isLemonadeEndpoint = $false
    if ($Endpoint.ContainsKey("Backend")) {
        $isLemonadeEndpoint = ([string]$Endpoint.Backend).ToLowerInvariant() -eq "lemonade"
    } elseif ($Endpoint.ContainsKey("ApiBasePath")) {
        $isLemonadeEndpoint = ([string]$Endpoint.ApiBasePath) -eq "/api/v1"
    }
    if (-not [string]::IsNullOrWhiteSpace($GgufFile) -and $isLemonadeEndpoint) {
        $modelId = "extra.$GgufFile"
    }
    if ([string]::IsNullOrWhiteSpace($modelId)) { $modelId = "default" }
    $result.ModelId = $modelId

    # 3. A minimal completion must actually succeed -- this is the real user path that
    #    a "registered but missing file" install silently fails.
    $body = @{
        model       = $modelId
        messages    = @(@{ role = "user"; content = "hi" })
        max_tokens  = 1
        temperature = 0
        stream      = $false
    } | ConvertTo-Json -Compress -Depth 5

    try {
        $resp = Invoke-WebRequest -Method POST -Uri $Endpoint.ChatCompletionsUrl `
            -ContentType "application/json" -Body $body -TimeoutSec $TimeoutSec `
            -UseBasicParsing -ErrorAction Stop
        if ([int]$resp.StatusCode -ge 200 -and [int]$resp.StatusCode -lt 300) {
            $result.CompletionOk = $true
        }
    } catch [System.Net.WebException] {
        # Narrow I/O-boundary catch: map the failed completion to a meaningful status.
        $code = -1
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        $result.Detail = "completion request failed (status=$code)"
    }

    if ($result.FileExists -and $result.CompletionOk) {
        $result.Ok = $true
        $result.Detail = "model file present and completion succeeded"
    } elseif (-not $result.FileExists) {
        $result.Detail = "model '$modelId' is registered but its backing file is missing: $($result.ModelFile)"
    } elseif ([string]::IsNullOrWhiteSpace($result.Detail)) {
        $result.Detail = "completion did not succeed"
    }

    return $result
}
