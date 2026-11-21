$ErrorActionPreference = "Stop"

# Contract: Test-WindowsLlmModelReadiness must evaluate model paths literally.
# Paths containing bracket characters (e.g., directory names with square brackets
# or quantized model tags like model[q4].gguf) must not fail Test-Path as wildcards.

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/llm-endpoint.ps1')

function Assert($condition, $message) {
    if (-not $condition) { throw $message }
}

# Mock completion handler returning valid content
function Invoke-WebRequest {
    param($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
    return [pscustomobject]@{
        StatusCode = 200
        Content = '{"choices":[{"message":{"content":"OK"}}]}'
    }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) "ods-bracket[models]-$([Guid]::NewGuid().ToString('N'))"
try {
    $modelsDir = Join-Path (Join-Path $testRoot "data") "models"
    [System.IO.Directory]::CreateDirectory($modelsDir) | Out-Null

    # Case 1: Bracketed directory path with standard model file
    $modelFile1 = "qwen-test.gguf"
    Set-Content -LiteralPath (Join-Path $modelsDir $modelFile1) -Value "model-data"

    $endpoint = @{ ChatCompletionsUrl = "http://localhost:8080/v1/chat/completions" }
    $res1 = Test-WindowsLlmModelReadiness -Endpoint $endpoint -InstallDir $testRoot -GgufFile $modelFile1 -TimeoutSec 5
    Assert $res1.FileExists "Model in bracketed directory must be detected on disk (FileExists=true)"
    Assert $res1.Ok "Model readiness in bracketed directory must pass (Ok=true)"

    # Case 2: Bracketed model filename
    $modelFile2 = "qwen[q4_k_m].gguf"
    Set-Content -LiteralPath (Join-Path $modelsDir $modelFile2) -Value "model-data"

    $res2 = Test-WindowsLlmModelReadiness -Endpoint $endpoint -InstallDir $testRoot -GgufFile $modelFile2 -TimeoutSec 5
    Assert $res2.FileExists "Model with bracketed filename must be detected on disk (FileExists=true)"
    Assert $res2.Ok "Model readiness with bracketed filename must pass (Ok=true)"

    # Case 3: Genuinely missing file must still fail
    $resMissing = Test-WindowsLlmModelReadiness -Endpoint $endpoint -InstallDir $testRoot -GgufFile "genuinely-missing.gguf" -TimeoutSec 5
    Assert (-not $resMissing.FileExists) "Missing file must report FileExists=false"
    Assert (-not $resMissing.Ok) "Missing file must report Ok=false"

    Write-Output "Windows LLM model readiness literal path contract OK"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
