$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "installers\windows\lib\model-activation.ps1")
. (Join-Path $root "installers\windows\lib\tier-map.ps1")
. (Join-Path $root "installers\windows\lib\backend-contract.ps1")
. (Join-Path $root "installers\windows\lib\llm-endpoint.ps1")
. (Join-Path $root "installers\windows\lib\opencode-config.ps1")

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

# Real PowerShell HTTP exception shapes with in-memory response bodies only.
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Net;
using System.Text;
public sealed class ODSActivationErrorResponse : WebResponse {
    private readonly string body;
    public ODSActivationErrorResponse(string value) { body = value; }
    public override Stream GetResponseStream() {
        return new MemoryStream(Encoding.UTF8.GetBytes(body));
    }
}
'@
function New-ActivationError {
    param([string]$Body, [switch]$Modern)
    if ($Modern) {
        $response = [Net.Http.HttpResponseMessage]::new([Net.HttpStatusCode]::Conflict)
        $response.Content = [Net.Http.StringContent]::new($Body)
        $exception = [Microsoft.PowerShell.Commands.HttpResponseException]::new('HTTP fixture fallback', $response)
    } else {
        $response = [ODSActivationErrorResponse]::new($Body)
        $exception = [Net.WebException]::new('HTTP fixture fallback', $null,
            [Net.WebExceptionStatus]::ProtocolError, $response)
    }
    return [Management.Automation.ErrorRecord]::new($exception, 'fixture409',
        [Management.Automation.ErrorCategory]::InvalidOperation, $null)
}

$errorCases = @(
    @('{"error":"local_mode_required","code":"ods_mode_mismatch","reason":"mode_mismatch","message":"Effective cloud does not match configured local."}', 'ods_mode_mismatch: Effective cloud does not match configured local.'),
    @('{"error":"local_mode_required","code":"ods_mode_unknown","message":"The effective or configured mode is unknown."}', 'ods_mode_unknown: The effective or configured mode is unknown.'),
    @('{"error":"local_mode_required","code":"local_mode_required","message":"Effective mode is cloud."}', 'local_mode_required: Effective mode is cloud.'),
    @('{"error":"legacy error","message":"Actionable message"}', 'legacy error: Actionable message'),
    @('{"error":"legacy error","code":"specific_code","message":{},"reason":"specific_reason"}', 'specific_code: specific_reason'),
    @('{"code":"specific_code","message":"   ","reason":false}', 'specific_code'),
    @('{"message":"Message without code"}', 'Message without code'),
    @('{"reason":"Reason without code"}', 'Reason without code'),
    @('{"error":"legacy error"}', 'legacy error'),
    @('{"code":"same","message":"same"}', 'same'),
    @('not JSON', 'not JSON'),
    @(('x' * 600), ('x' * 500)),
    @('', 'HTTP fixture fallback')
)
$responseShapes = @($false)
if ($PSVersionTable.PSVersion.Major -ge 7) { $responseShapes += $true }
foreach ($modern in $responseShapes) {
    foreach ($case in $errorCases) {
        $record = New-ActivationError -Body $case[0] -Modern:$modern
        try {
            Assert-Equal (Get-WindowsODSHttpErrorDetail -ErrorRecord $record) $case[1] "HTTP error detail modern=$modern"
        } finally { $record.Exception.Response.Dispose() }
    }
}
$noResponse = [Management.Automation.ErrorRecord]::new([Exception]::new('no response fallback'),
    'fixture', [Management.Automation.ErrorCategory]::InvalidOperation, $null)
Assert-Equal (Get-WindowsODSHttpErrorDetail -ErrorRecord $noResponse) 'no response fallback' 'Missing response fallback'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "ods-windows-model-activation-contract"
Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Join-Path $tempRoot "config") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tempRoot "data\models") -Force | Out-Null
Set-Content -LiteralPath (Join-Path $tempRoot "data\models\model.gguf") -Value "fixture"
@{
    models = @(@{ id = "catalog-model"; gguf_file = "model.gguf" })
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $tempRoot "config\model-library.json")

try {
    $openCodeInstall = Join-Path $tempRoot "opencode-context"
    $openCodeConfig = Join-Path $tempRoot "opencode-config"
    New-Item -ItemType Directory -Path $openCodeInstall -Force | Out-Null
    @"
LLM_URL=http://127.0.0.1:11435
GGUF_FILE=LocalUpgrade.gguf
LLM_MODEL=LocalUpgrade
CTX_SIZE=131072
MAX_CONTEXT=65536
"@ | Set-Content -LiteralPath (Join-Path $openCodeInstall ".env")
    Sync-WindowsOpenCodeConfigFromEnv `
        -InstallDir $openCodeInstall `
        -ConfigDir $openCodeConfig | Out-Null
    $openCodeJson = Get-Content -LiteralPath (Join-Path $openCodeConfig "opencode.json") `
        -Raw | ConvertFrom-Json
    Assert-Equal `
        $openCodeJson.provider.'llama-server'.models.'LocalUpgrade.gguf'.limit.context `
        131072 "OpenCode canonical context precedence"

    Set-Content -LiteralPath (Join-Path $openCodeInstall ".env") -Value @(
        "LLM_URL=http://127.0.0.1:11435"
        "GGUF_FILE=LocalUpgrade.gguf"
        "LLM_MODEL=LocalUpgrade"
        "CTX_SIZE=auto"
        "MAX_CONTEXT=65536"
    )
    Sync-WindowsOpenCodeConfigFromEnv `
        -InstallDir $openCodeInstall `
        -ConfigDir $openCodeConfig | Out-Null
    $openCodeJson = Get-Content -LiteralPath (Join-Path $openCodeConfig "opencode.json") `
        -Raw | ConvertFrom-Json
    Assert-Equal `
        $openCodeJson.provider.'llama-server'.models.'LocalUpgrade.gguf'.limit.context `
        65536 "OpenCode invalid canonical context fallback"

    $largeContext = [long]9007199254740991
    Set-Content -LiteralPath (Join-Path $openCodeInstall ".env") -Value @(
        "LLM_URL=http://127.0.0.1:11435"
        "GGUF_FILE=LocalUpgrade.gguf"
        "LLM_MODEL=LocalUpgrade"
        "CTX_SIZE=$largeContext"
        "MAX_CONTEXT=$largeContext"
    )
    Sync-WindowsOpenCodeConfigFromEnv `
        -InstallDir $openCodeInstall `
        -ConfigDir $openCodeConfig | Out-Null
    $openCodeJson = Get-Content -LiteralPath (Join-Path $openCodeConfig "opencode.json") `
        -Raw | ConvertFrom-Json
    Assert-Equal `
        $openCodeJson.provider.'llama-server'.models.'LocalUpgrade.gguf'.limit.context `
        $largeContext "OpenCode context above Int32"

    $previousModelProfile = $env:MODEL_PROFILE
    try {
        $env:MODEL_PROFILE = "qwen"
        Assert-Equal `
            (ConvertTo-ModelFromTier -Tier "T1" -ModelProfile "gemma4") `
            "gemma-4-e2b-it" "Explicit model profile selection"
        $gemmaTier = Resolve-TierConfig -Tier "1" -ModelProfile "gemma4"
        Assert-Equal $gemmaTier.LlmModel "gemma-4-e2b-it" "Explicit tier profile model"
        Assert-Equal $gemmaTier.GgufFile `
            "gemma-4-E2B-it-Q4_K_M.gguf" "Explicit tier profile GGUF"
    } finally {
        $env:MODEL_PROFILE = $previousModelProfile
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
    $script:activationCalls = 0
    function Invoke-WebRequest {
        param($Method, $Uri, [switch]$UseBasicParsing, $TimeoutSec, $ErrorAction)
        return [pscustomobject]@{ StatusCode = 200 }
    }
    function Invoke-RestMethod {
        param($Method, $Uri, $Headers, $ContentType, $Body, [switch]$UseBasicParsing, $TimeoutSec, $ErrorAction)
        $script:activationCalls++
        $script:lastActivation = @{
            Uri = $Uri; Headers = $Headers; ContentType = $ContentType
            Body = $Body; TimeoutSec = $TimeoutSec
        }
        if ($script:activationMode -eq "failure") { throw "simulated activation failure" }
        if ($script:activationMode -eq "structured") {
            throw (New-ActivationError -Body $errorCases[0][0] -Modern:$script:modernResponse)
        }
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

    $script:activationMode = "structured"
    foreach ($modern in $responseShapes) {
        $script:modernResponse = $modern
        $beforeCalls = $script:activationCalls
        Assert-Throws {
            Invoke-WindowsODSModelActivationTransaction -EnvMap $envMap `
                -ModelId "catalog-model" -Tier "1" -ContextLength 8192
        } 'Model activation failed: ods_mode_mismatch: Effective cloud does not match configured local\.' 'Structured activation failure'
        Assert-Equal $script:activationCalls ($beforeCalls + 1) 'Failure is not retried'
    }

    Write-Host "[PASS] Windows transactional model activation"
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
