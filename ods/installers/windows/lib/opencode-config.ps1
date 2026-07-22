# ============================================================================
# ODS Windows -- OpenCode config helpers
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Create or update OpenCode config from the active local LLM settings.
# Requires: constants.ps1 and llm-endpoint.ps1 sourced first.
# ============================================================================

function Set-OpenCodeObjectProperty {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Target,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        $Value
    )

    $property = $Target.PSObject.Properties[$Name]
    if ($property) {
        $property.Value = $Value
    } else {
        $Target | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function New-WindowsOpenCodeConfigObject {
    param(
        [hashtable]$LlmEndpoint,
        [string]$ModelId,
        [string]$ModelName,
        [int]$ContextLimit,
        [string]$ApiKey = "no-key",
        [string]$ProviderName = "llama-server (local)"
    )

    return [pscustomobject]@{
        '$schema' = "https://opencode.ai/config.json"
        model = "llama-server/$ModelId"
        small_model = "llama-server/$ModelId"
        provider = [pscustomobject]@{
            'llama-server' = [pscustomobject]@{
                npm = "@ai-sdk/openai-compatible"
                name = $ProviderName
                options = [pscustomobject]@{
                    baseURL = $LlmEndpoint.BaseUrl
                    apiKey = $ApiKey
                }
                models = [pscustomobject]@{
                    $ModelId = [pscustomobject]@{
                        name = $ModelName
                        limit = [pscustomobject]@{
                            context = $ContextLimit
                            output = 32768
                        }
                    }
                }
            }
        }
    }
}

function Update-WindowsOpenCodeConfigObject {
    param(
        [object]$Config,
        [hashtable]$LlmEndpoint,
        [string]$ModelId,
        [string]$ModelName,
        [int]$ContextLimit,
        [string]$ApiKey = "no-key",
        [string]$ProviderName = "llama-server (local)"
    )

    if ($null -eq $Config) {
        return New-WindowsOpenCodeConfigObject `
            -LlmEndpoint $LlmEndpoint `
            -ModelId $ModelId `
            -ModelName $ModelName `
            -ContextLimit $ContextLimit `
            -ApiKey $ApiKey `
            -ProviderName $ProviderName
    }

    Set-OpenCodeObjectProperty -Target $Config -Name '$schema' -Value "https://opencode.ai/config.json"
    Set-OpenCodeObjectProperty -Target $Config -Name 'model' -Value "llama-server/$ModelId"
    Set-OpenCodeObjectProperty -Target $Config -Name 'small_model' -Value "llama-server/$ModelId"

    if (-not $Config.PSObject.Properties['provider'] -or $null -eq $Config.provider) {
        Set-OpenCodeObjectProperty -Target $Config -Name 'provider' -Value ([pscustomobject]@{})
    }
    $provider = $Config.provider

    if (-not $provider.PSObject.Properties['llama-server'] -or $null -eq $provider.'llama-server') {
        Set-OpenCodeObjectProperty -Target $provider -Name 'llama-server' -Value ([pscustomobject]@{})
    }
    $llamaProvider = $provider.'llama-server'

    Set-OpenCodeObjectProperty -Target $llamaProvider -Name 'npm' -Value "@ai-sdk/openai-compatible"
    Set-OpenCodeObjectProperty -Target $llamaProvider -Name 'name' -Value $ProviderName

    if (-not $llamaProvider.PSObject.Properties['options'] -or $null -eq $llamaProvider.options) {
        Set-OpenCodeObjectProperty -Target $llamaProvider -Name 'options' -Value ([pscustomobject]@{})
    }
    Set-OpenCodeObjectProperty -Target $llamaProvider.options -Name 'baseURL' -Value $LlmEndpoint.BaseUrl
    Set-OpenCodeObjectProperty -Target $llamaProvider.options -Name 'apiKey' -Value $ApiKey

    if (-not $llamaProvider.PSObject.Properties['models'] -or $null -eq $llamaProvider.models) {
        Set-OpenCodeObjectProperty -Target $llamaProvider -Name 'models' -Value ([pscustomobject]@{})
    }
    $models = $llamaProvider.models

    if (-not $models.PSObject.Properties[$ModelId] -or $null -eq $models.PSObject.Properties[$ModelId].Value) {
        Set-OpenCodeObjectProperty -Target $models -Name $ModelId -Value ([pscustomobject]@{})
    }
    $modelEntry = $models.PSObject.Properties[$ModelId].Value
    Set-OpenCodeObjectProperty -Target $modelEntry -Name 'name' -Value $ModelName

    if (-not $modelEntry.PSObject.Properties['limit'] -or $null -eq $modelEntry.limit) {
        Set-OpenCodeObjectProperty -Target $modelEntry -Name 'limit' -Value ([pscustomobject]@{})
    }
    Set-OpenCodeObjectProperty -Target $modelEntry.limit -Name 'context' -Value $ContextLimit
    Set-OpenCodeObjectProperty -Target $modelEntry.limit -Name 'output' -Value 32768

    return $Config
}

function Sync-WindowsOpenCodeConfig {
    param(
        [hashtable]$LlmEndpoint,
        [string]$ModelId,
        [string]$ModelName,
        [int]$ContextLimit,
        [string]$ConfigDir = $script:OPENCODE_CONFIG_DIR,
        [string]$ApiKey = "no-key",
        [string]$ProviderName = "llama-server (local)",
        [switch]$SkipIfUnavailable
    )

    $_ocConfigFile = Join-Path $ConfigDir "opencode.json"
    $_ocCompatConfigFile = Join-Path $ConfigDir "config.json"

    if ($SkipIfUnavailable -and `
        -not (Test-Path $script:OPENCODE_EXE) -and `
        -not (Test-Path $_ocConfigFile) -and `
        -not (Test-Path $_ocCompatConfigFile)) {
        return @{
            Status = "skipped"
            ConfigPath = $_ocConfigFile
            CompatConfigPath = $_ocCompatConfigFile
            ModelId = $ModelId
            ModelName = $ModelName
            BaseUrl = $LlmEndpoint.BaseUrl
        }
    }

    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null

    $_existingConfigFile = @($_ocConfigFile, $_ocCompatConfigFile) |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1

    $_configObject = $null
    $_configStatus = "created"

    if ($_existingConfigFile) {
        try {
            $_configObject = Get-Content $_existingConfigFile -Raw | ConvertFrom-Json -ErrorAction Stop
            $_configStatus = "updated"
        } catch {
            $_configStatus = "regenerated"
        }
    }

    $_configObject = Update-WindowsOpenCodeConfigObject `
        -Config $_configObject `
        -LlmEndpoint $LlmEndpoint `
        -ModelId $ModelId `
        -ModelName $ModelName `
        -ContextLimit $ContextLimit `
        -ApiKey $ApiKey `
        -ProviderName $ProviderName

    $_configJson = $_configObject | ConvertTo-Json -Depth 12
    $_utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($_ocConfigFile, $_configJson, $_utf8NoBom)
    [System.IO.File]::WriteAllText($_ocCompatConfigFile, $_configJson, $_utf8NoBom)

    return @{
        Status = $_configStatus
        ConfigPath = $_ocConfigFile
        CompatConfigPath = $_ocCompatConfigFile
        ModelId = $ModelId
        ModelName = $ModelName
        BaseUrl = $LlmEndpoint.BaseUrl
    }
}

function Sync-WindowsOpenCodeConfigFromEnv {
    param(
        [string]$InstallDir = $script:ODS_INSTALL_DIR,
        [string]$ConfigDir = $script:OPENCODE_CONFIG_DIR,
        [string]$GpuBackend = "",
        [string]$NativeBackend = "",
        [switch]$UseLemonade,
        [switch]$CloudMode,
        [string]$DefaultModelId = "",
        [string]$DefaultModelName = "",
        [int]$DefaultContextLimit = 16384,
        [switch]$SkipIfUnavailable
    )

    $_envMap = Get-WindowsODSEnvMap -InstallDir $InstallDir
    $_llmEndpoint = Get-WindowsLocalLlmEndpoint -InstallDir $InstallDir -EnvMap $_envMap `
        -GpuBackend $GpuBackend -NativeBackend $NativeBackend `
        -UseLemonade:$UseLemonade -CloudMode:$CloudMode
    $_modelId = Get-WindowsODSEnvValue -EnvMap $_envMap -Keys @("GGUF_FILE") -Default $DefaultModelId
    $_modelName = Get-WindowsODSEnvValue -EnvMap $_envMap -Keys @("LLM_MODEL") -Default $DefaultModelName
    $_contextRaw = Get-WindowsODSEnvValue -EnvMap $_envMap -Keys @("MAX_CONTEXT", "CTX_SIZE") -Default "$DefaultContextLimit"
    $_apiKey = "no-key"
    $_providerName = "llama-server (local)"
    $_switchboardMode = (Get-WindowsODSEnvValue -EnvMap $_envMap -Keys @("ODS_MODEL_SWITCHBOARD") -Default "observe").ToLowerInvariant()
    if ($_switchboardMode -eq "enabled") {
        $_litellmPort = Get-WindowsODSEnvValue -EnvMap $_envMap -Keys @("LITELLM_PORT") -Default "4000"
        $_litellmKey = Get-WindowsODSEnvValue -EnvMap $_envMap -Keys @("LITELLM_KEY") -Default ""
        if ([string]::IsNullOrWhiteSpace($_litellmKey)) {
            throw "OpenCode switchboard config requires LITELLM_KEY, but it is empty."
        }
        $_llmEndpoint = @{
            Name = "LLM (ODS switchboard)"
            Backend = "litellm-switchboard"
            Port = $_litellmPort
            ApiBasePath = "/v1"
            HealthUrl = "http://127.0.0.1:${_litellmPort}/health"
            BaseUrl = "http://127.0.0.1:${_litellmPort}/v1"
            ChatCompletionsUrl = "http://127.0.0.1:${_litellmPort}/v1/chat/completions"
        }
        $_modelId = "ods/current"
        $_modelName = "ods/current"
        $_apiKey = $_litellmKey
        $_providerName = "ODS switchboard"
    }
    $_contextLimit = 0
    if (-not [int]::TryParse($_contextRaw, [ref]$_contextLimit)) {
        $_contextLimit = $DefaultContextLimit
    }

    return Sync-WindowsOpenCodeConfig `
        -LlmEndpoint $_llmEndpoint `
        -ModelId $_modelId `
        -ModelName $_modelName `
        -ContextLimit $_contextLimit `
        -ConfigDir $ConfigDir `
        -ApiKey $_apiKey `
        -ProviderName $_providerName `
        -SkipIfUnavailable:$SkipIfUnavailable
}
