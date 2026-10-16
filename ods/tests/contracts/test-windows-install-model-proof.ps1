$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $root 'installers/windows/lib/llm-endpoint.ps1')
. (Join-Path $root 'installers/windows/lib/backend-contract.ps1')
function Assert($condition, $message) { if (-not $condition) { throw $message } }

foreach ($invalid in @('{}', '{"choices":[]}', '{"choices":[{"message":{"content":" "}}]}',
    '{"choices":[{"message":{"reasoning_content":"thinking"}}]}',
    '{"error":{"message":"failed"},"choices":[{"message":{"content":"OK"}}]}', 'not json')) {
    Assert (-not (Test-ODSCompletionContent $invalid)) "Accepted invalid completion: $invalid"
}
$script:good = '{"choices":[{"message":{"content":"OK"}}]}'
Assert (Test-ODSCompletionContent $good) 'Rejected visible completion'

$script:gatewayCalls = 0
$script:gatewayReady = $false
function Invoke-WebRequest {
    param($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
    if ($Uri -like '*/v1/model/status') { return @{ StatusCode = 200; Content = '{}' } }
    $script:gatewayCalls++
    if ($Uri -eq 'http://127.0.0.1:14000/v1/chat/completions') {
        Assert (($Body | ConvertFrom-Json).model -eq 'ods/current') 'Readiness bypassed switchboard'
        Assert ($Headers.Authorization -eq 'Bearer gateway-test') 'Wrong gateway credentials'
        if (-not $script:gatewayReady) { throw '503 unverified route' }
    }
    return @{ StatusCode = 200; Content = $script:good }
}
$envMap = @{ ODS_MODEL_SWITCHBOARD = 'enabled'; ODS_AGENT_KEY = 'agent-test'; LITELLM_KEY = 'gateway-test'; LITELLM_PORT = '14000' }
Assert (-not (Test-WindowsSwitchboardReadiness $envMap -Attempts 2 -IntervalSec 0).Ok) 'Unverified route accepted'
Assert ($script:gatewayCalls -eq 2) 'Readiness retries are not bounded'
$script:gatewayReady = $true
Assert (Test-WindowsSwitchboardReadiness $envMap -Attempts 1).Ok 'Verified route rejected'
$script:good = '{}'
Assert (-not (Test-WindowsSwitchboardReadiness $envMap -Attempts 1).Ok) 'Empty 200 accepted'
Assert (-not (Test-WindowsLlmModelReadiness -Endpoint @{ ChatCompletionsUrl = 'http://localhost/native' } -InstallDir $root).Ok) 'Native empty 200 accepted'

# The native Windows llama-server (AMD) requires its key; the model id is the
# GGUF file name it serves as its --alias.
$script:nativeRequest = $null
function Invoke-WebRequest {
    param($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
    $script:nativeRequest = @{ Uri = $Uri; Headers = $Headers; Model = ($Body | ConvertFrom-Json).model }
    return @{ StatusCode = 200; Content = '{"choices":[{"message":{"content":"OK"}}]}' }
}
$nativeKey = 'ab' * 32
$native = Test-WindowsLlmModelReadiness -Endpoint @{ ChatCompletionsUrl = 'http://localhost:8080/v1/chat/completions'; ApiKey = $nativeKey } `
    -InstallDir $root -GgufFile 'missing-model.gguf'
Assert ($script:nativeRequest.Headers.Authorization -eq ('Bearer ' + $nativeKey)) 'Native readiness must authenticate with LLAMA_SERVER_API_KEY'
Assert ($script:nativeRequest.Model -eq 'missing-model.gguf' -and $native.ModelId -eq 'missing-model.gguf') 'Native readiness must request the GGUF alias'
Assert (-not $native.Ok -and -not $native.FileExists) 'A completion without the GGUF on disk is not a ready model'
$null = Test-WindowsLlmModelReadiness -Endpoint @{ ChatCompletionsUrl = 'http://localhost:11434/v1/chat/completions' } -InstallDir $root -GgufFile 'x.gguf'
Assert (-not $script:nativeRequest.Headers.ContainsKey('Authorization')) 'Keyless endpoints send no Authorization header'

# Execute the actual administrator refusal branch without requiring an elevated CI runner.
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/phases/01-preflight.ps1'), [ref]$tokens, [ref]$errors)
$branch = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and $node.Clauses[0].Item1.Extent.Text -eq '$_isAdmin' }, $true)
function Write-AI { param($text) }
function Write-AIWarn { param($text) }
function Read-Host { param($text) return 'n' }
$nonInteractive = $false
$aborted = $false
try { & ([scriptblock]::Create($branch.Clauses[0].Item2.Extent.Text.Trim().Substring(1).TrimEnd('}'))) } catch {
    $aborted = $_.Exception.Message -eq 'ODS_INSTALL_ABORTED'
}
Assert $aborted 'Declining administrator install must abort the orchestrator'
Write-Output 'PASS: completion content, native/gateway readiness with the llama-server key, and administrator refusal'
