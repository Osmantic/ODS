$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "installers\windows\lib\constants.ps1")
. (Join-Path $root "installers\windows\lib\model-activation.ps1")
. (Join-Path $root "installers\windows\lib\tier-map.ps1")

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Pattern, [string]$Label)
    try {
        & $Action
    } catch {
        if ($_.Exception.Message -notmatch $Pattern) {
            throw "$Label threw the wrong error: $($_.Exception.Message)"
        }
        return
    }
    throw "$Label did not throw"
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "ods-windows-model-activation-contract"
Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Join-Path $tempRoot "config") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "data\models") -Force | Out-Null
Set-Content -LiteralPath (Join-Path $tempRoot "data\models\model.gguf") -Value "fixture"
@{
    models = @(@{ id = "catalog-model"; gguf_file = "model.gguf" })
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $tempRoot "config\model-library.json")

# Create custom models directory for testing ODS_MODELS_DIR override
$customModelsDir = Join-Path ([IO.Path]::GetTempPath()) "ods-windows-custom-models"
Remove-Item -LiteralPath $customModelsDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $customModelsDir -Force | Out-Null
Set-Content -LiteralPath (Join-Path $customModelsDir "model.gguf") -Value "custom-fixture"

try {
    $previousModelProfile = $env:MODEL_PROFILE
    $previousOdsModelsDir = $env:ODS_MODELS_DIR
    $previousModelsDir    = $env:MODELS_DIR
    try {
        $env:MODEL_PROFILE = "qwen"
        Assert-Equal `
            (ConvertTo-ModelFromTier -Tier "T1" -ModelProfile "gemma4") `
            "gemma-4-e2b-it" "Explicit model profile selection"
        $gemmaTier = Resolve-TierConfig -Tier "1" -ModelProfile "gemma4"
        Assert-Equal $gemmaTier.LlmModel "gemma-4-e2b-it" "Explicit tier profile model"
        Assert-Equal $gemmaTier.GgufFile `
            "gemma-4-E2B-it-Q4_K_M.gguf" "Explicit tier profile GGUF"

        # Test Get-ODSModelsDir precedence
        Assert-Equal (Get-ODSModelsDir -InstallDir $tempRoot) (Join-Path $tempRoot "data\models") "Default models dir"
        $env:ODS_MODELS_DIR = $customModelsDir
        Assert-Equal (Get-ODSModelsDir -InstallDir $tempRoot) (Resolve-Path $customModelsDir).Path "ODS_MODELS_DIR override"
        $env:ODS_MODELS_DIR = $null

        $env:MODELS_DIR = $customModelsDir
        Assert-Equal (Get-ODSModelsDir -InstallDir $tempRoot) (Resolve-Path $customModelsDir).Path "MODELS_DIR override"
        $env:MODELS_DIR = $null

        Assert-Equal (Get-ODSModelsDir -InstallDir $tempRoot -ModelsDirOverride $customModelsDir) `
            (Resolve-Path $customModelsDir).Path "Explicit ModelsDir parameter override"

        # Catalog resolution with custom models dir
        Assert-Equal (Resolve-WindowsODSModelCatalogId -InstallDir $tempRoot -GgufFile "model.gguf" -ModelsDirOverride $customModelsDir) `
            "catalog-model" "Custom models dir catalog resolution"
    } finally {
        $env:MODEL_PROFILE  = $previousModelProfile
        $env:ODS_MODELS_DIR = $previousOdsModelsDir
        $env:MODELS_DIR     = $previousModelsDir
        Remove-Item -LiteralPath $customModelsDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Assert-Equal (Resolve-WindowsODSModelCatalogId -InstallDir $tempRoot -GgufFile "model.gguf") `
        "catalog-model" "Catalog model resolution"
    Assert-Throws { Resolve-WindowsODSModelCatalogId -InstallDir $tempRoot -GgufFile "..\model.gguf" } `
        "invalid GGUF filename" "GGUF traversal rejection"

    Assert-Equal (Resolve-WindowsODSAgentBaseUri -EnvMap @{ ODS_AGENT_BIND = "0.0.0.0" }) `
        "http://127.0.0.1:7710" "Wildcard bind normalization"
    Assert-Equal (Resolve-WindowsODSAgentBaseUri -EnvMap @{
        ODS_AGENT_BIND = "::1"; ODS_AGENT_PORT = "7788"
    }) "http://[::1]:7788" "IPv6 agent URI"
    Assert-Throws {
        Resolve-WindowsODSAgentBaseUri -EnvMap @{ ODS_AGENT_PORT = "70000" }
    } "between 1 and 65535" "Invalid agent port"

    $script:activationMode = "success"
    $script:lastActivation = $null
    function Invoke-WebRequest {
        param($Method, $Uri, [switch]$UseBasicParsing, $TimeoutSec, $ErrorAction)
        return [pscustomobject]@{ StatusCode = 200 }
    }
    function Invoke-RestMethod {
        param($Method, $Uri, $Headers, $ContentType, $Body, [switch]$UseBasicParsing, $TimeoutSec, $ErrorAction)
        $script:lastActivation = @{
            Uri = $Uri; Headers = $Headers; ContentType = $ContentType
            Body = $Body; TimeoutSec = $TimeoutSec
        }
        if ($script:activationMode -eq "failure") { throw "simulated activation failure" }
        if ($script:activationMode -eq "invalid") {
            return [pscustomobject]@{ status = "activated"; model_id = "wrong-model"; tier = "1"; context_length = 8192 }
        }
        return [pscustomobject]@{
            status = "activated"; model_id = "catalog-model"; tier = "1"
            context_length = 8192; consumers = @{ hermes = "restarted" }
        }
    }

    $envMap = @{ ODS_AGENT_KEY = "secret"; ODS_AGENT_PORT = "7710" }
    $receipt = Invoke-WindowsODSModelActivationTransaction -EnvMap $envMap `
        -ModelId "catalog-model" -Tier "1" -ContextLength 8192
    Assert-Equal $receipt.status "activated" "Activation status"
    Assert-Equal $script:lastActivation.Uri "http://127.0.0.1:7710/v1/model/activate" "Activation URI"
    Assert-Equal $script:lastActivation.Headers.Authorization "Bearer secret" "Bearer header"
    Assert-Equal $script:lastActivation.TimeoutSec 2700 "Activation timeout"
    $request = $script:lastActivation.Body | ConvertFrom-Json
    Assert-Equal $request.model_id "catalog-model" "Activation model ID"
    Assert-Equal $request.tier "1" "Activation tier"
    Assert-Equal $request.context_length 8192 "Activation context"

    $fallbackEnv = @{ DASHBOARD_API_KEY = "dashboard-secret" }
    $null = Invoke-WindowsODSModelActivationTransaction -EnvMap $fallbackEnv `
        -ModelId "catalog-model" -Tier "1" -ContextLength 8192
    Assert-Equal $script:lastActivation.Headers.Authorization `
        "Bearer dashboard-secret" "Dashboard key fallback"

    $script:activationMode = "invalid"
    Assert-Throws {
        Invoke-WindowsODSModelActivationTransaction -EnvMap $envMap `
            -ModelId "catalog-model" -Tier "1" -ContextLength 8192
    } "invalid model activation receipt" "Receipt identity validation"

    Assert-Throws {
        Invoke-WindowsODSModelActivationTransaction `
            -EnvMap @{ ODS_AGENT_KEY = "bad`nkey" } `
            -ModelId "catalog-model" -Tier "1" -ContextLength 8192
    } "invalid newline" "Bearer newline rejection"

    $script:activationMode = "failure"
    Assert-Throws {
        Invoke-WindowsODSModelActivationTransaction -EnvMap $envMap `
            -ModelId "catalog-model" -Tier "1" -ContextLength 8192
    } "Model activation failed" "Activation failure propagation"

    Write-Host "[PASS] Windows transactional model activation"
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
