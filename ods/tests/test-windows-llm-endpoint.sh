#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v powershell.exe >/dev/null 2>&1; then
  PS_BIN="powershell.exe"
elif command -v pwsh >/dev/null 2>&1; then
  PS_BIN="pwsh"
else
  echo "[SKIP] PowerShell unavailable"
  exit 0
fi

tmp_ps="$(mktemp "${TMPDIR:-/tmp}/ods-llm-endpoint.XXXXXX.ps1")"
trap 'rm -f "$tmp_ps"' EXIT

cat > "$tmp_ps" <<'PS_EOF'
$ErrorActionPreference = "Stop"

$repo = $env:ODS_TEST_ROOT
. (Join-Path $repo "installers\windows\lib\llm-endpoint.ps1")
. (Join-Path $repo "installers\windows\lib\env-generator.ps1")

$script:LEMONADE_PORT = "8080"
$script:LEMONADE_HEALTH_URL = "http://127.0.0.1:8080/api/v1/health"

function Assert-EndpointValue {
    param(
        [hashtable]$Endpoint,
        [string]$Key,
        [string]$Expected,
        [string]$Label
    )

    $actual = [string]$Endpoint[$Key]
    if ($actual -ne $Expected) {
        throw "$Label expected $Key='$Expected', got '$actual'"
    }
}

function Assert-ResolvedEndpoint {
    param(
        [string]$Label,
        [hashtable]$EnvMap,
        [string]$GpuBackend = "",
        [string]$NativeBackend = "",
        [switch]$UseLemonade,
        [switch]$CloudMode,
        [string]$ExpectedBackend,
        [string]$ExpectedHealthUrl,
        [string]$ExpectedChatUrl
    )

    $endpoint = Get-WindowsLocalLlmEndpoint -EnvMap $EnvMap `
        -GpuBackend $GpuBackend -NativeBackend $NativeBackend `
        -UseLemonade:$UseLemonade -CloudMode:$CloudMode
    Assert-EndpointValue -Endpoint $endpoint -Key "Backend" -Expected $ExpectedBackend -Label $Label
    Assert-EndpointValue -Endpoint $endpoint -Key "HealthUrl" -Expected $ExpectedHealthUrl -Label $Label
    Assert-EndpointValue -Endpoint $endpoint -Key "ChatCompletionsUrl" -Expected $ExpectedChatUrl -Label $Label
}

$nvidiaDocker = @{
    "ODS_MODE" = "local"
    "LLM_BACKEND" = "llama-server"
    "LLM_API_BASE_PATH" = "/v1"
    "GPU_BACKEND" = "nvidia"
    "OLLAMA_PORT" = "11434"
    "AMD_INFERENCE_RUNTIME" = ""
    "AMD_INFERENCE_LOCATION" = ""
    "AMD_INFERENCE_RUNTIME_MODE" = ""
}
Assert-ResolvedEndpoint -Label "NVIDIA Docker llama-server with native exe present" `
    -EnvMap $nvidiaDocker -NativeBackend "llama-server" `
    -ExpectedBackend "docker-llama-server" `
    -ExpectedHealthUrl "http://localhost:11434/health" `
    -ExpectedChatUrl "http://localhost:11434/v1/chat/completions"

$cpuDocker = @{
    "ODS_MODE" = "local"
    "LLM_BACKEND" = "llama-server"
    "LLM_API_BASE_PATH" = "/v1"
    "GPU_BACKEND" = "none"
    "LLAMA_SERVER_PORT" = "18080"
}
Assert-ResolvedEndpoint -Label "CPU Docker llama-server with native exe present" `
    -EnvMap $cpuDocker -NativeBackend "llama-server" `
    -ExpectedBackend "docker-llama-server" `
    -ExpectedHealthUrl "http://localhost:18080/health" `
    -ExpectedChatUrl "http://localhost:18080/v1/chat/completions"

$amdNativeFallback = @{
    "ODS_MODE" = "local"
    "LLM_BACKEND" = "llama-server"
    "LLM_API_BASE_PATH" = "/v1"
    "GPU_BACKEND" = "amd"
    "OLLAMA_PORT" = "11434"
    "AMD_INFERENCE_RUNTIME" = "llama-server"
    "AMD_INFERENCE_LOCATION" = "host"
    "AMD_INFERENCE_RUNTIME_MODE" = "windows-llama-server-fallback"
    "AMD_INFERENCE_PORT" = "18080"
}
Assert-ResolvedEndpoint -Label "AMD native llama-server fallback" `
    -EnvMap $amdNativeFallback -GpuBackend "amd" -NativeBackend "llama-server" `
    -ExpectedBackend "native-llama-server" `
    -ExpectedHealthUrl "http://localhost:18080/health" `
    -ExpectedChatUrl "http://localhost:18080/v1/chat/completions"

$legacyAmdNative = @{
    "ODS_MODE" = "local"
    "LLM_BACKEND" = "llama-server"
    "LLM_API_BASE_PATH" = "/v1"
    "GPU_BACKEND" = "amd"
    "OLLAMA_PORT" = "11434"
}
Assert-ResolvedEndpoint -Label "legacy AMD native inference metadata" `
    -EnvMap $legacyAmdNative -GpuBackend "amd" -NativeBackend "llama-server" `
    -ExpectedBackend "native-llama-server" `
    -ExpectedHealthUrl "http://localhost:8080/health" `
    -ExpectedChatUrl "http://localhost:8080/v1/chat/completions"

$amdLemonade = @{
    "ODS_MODE" = "lemonade"
    "LLM_BACKEND" = "lemonade"
    "GPU_BACKEND" = "amd"
    "AMD_INFERENCE_RUNTIME" = "lemonade"
    "AMD_INFERENCE_LOCATION" = "host"
    "AMD_INFERENCE_RUNTIME_MODE" = "external-lemonade"
    "AMD_INFERENCE_PORT" = "19080"
}
Assert-ResolvedEndpoint -Label "AMD Lemonade endpoint" `
    -EnvMap $amdLemonade -GpuBackend "amd" -NativeBackend "lemonade" -UseLemonade `
    -ExpectedBackend "lemonade" `
    -ExpectedHealthUrl "http://127.0.0.1:19080/api/v1/health" `
    -ExpectedChatUrl "http://localhost:19080/api/v1/chat/completions"

function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget {
    return @{ Limit = "4.0"; Reservation = "1.0"; Available = "4.0" }
}

$modelTestDir = Join-Path ([IO.Path]::GetTempPath()) "ods-windows-lemonade-model-$([Guid]::NewGuid().ToString('N'))"
try {
    New-Item -ItemType Directory -Path $modelTestDir -Force | Out-Null
    $tier = @{
        TierName = "Test"
        LlmModel = "modern-model"
        GgufFile = "Modern-Model.gguf"
        MaxContext = 4096
    }
    $envResult = New-ODSEnv -InstallDir $modelTestDir -TierConfig $tier -Tier "SH" `
        -GpuBackend "amd" -AmdInferenceRuntime "lemonade" `
        -AmdInferenceLocation "host" -AmdInferencePort "8080"
    if ($envResult.LemonadeModel -ne "extra.Modern-Model.gguf") {
        throw "Legacy Lemonade fallback changed: $($envResult.LemonadeModel)"
    }

    $null = Set-WindowsODSLemonadeModelConfiguration `
        -InstallDir $modelTestDir -ModelId "Modern-Model" -Port "8080"
    $envText = Get-Content -LiteralPath (Join-Path $modelTestDir ".env") -Raw
    if ($envText -notmatch '(?m)^LEMONADE_MODEL=Modern-Model\r?$') {
        throw "Resolved Lemonade model ID was not persisted to .env"
    }
    $litellmPath = Join-Path (Join-Path (Join-Path $modelTestDir "config") "litellm") "lemonade.yaml"
    $litellmText = Get-Content -LiteralPath $litellmPath -Raw
    if ($litellmText -notmatch '(?m)^      model: openai/Modern-Model\r?$') {
        throw "Resolved Lemonade model ID was not written to lemonade.yaml"
    }

    $reinstallResult = New-ODSEnv -InstallDir $modelTestDir -TierConfig $tier -Tier "SH" `
        -GpuBackend "amd" -AmdInferenceRuntime "lemonade" `
        -AmdInferenceLocation "host" -AmdInferencePort "8080"
    if ($reinstallResult.LemonadeModel -ne "Modern-Model") {
        throw "Reinstall discarded the resolved Lemonade model ID: $($reinstallResult.LemonadeModel)"
    }

    $modelsDir = Join-Path (Join-Path $modelTestDir "data") "models"
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $modelsDir $tier.GgufFile) -Value "test"

    $script:resolvedModelPort = 0
    $script:resolvedModelFile = ""
    $script:completionBody = ""
    function Resolve-ODSLemonadeModelId {
        param([int]$Port, [string]$GgufFile)
        $script:resolvedModelPort = $Port
        $script:resolvedModelFile = $GgufFile
        return "Modern-Model"
    }
    function Invoke-WebRequest {
        param($Method, $Uri, $ContentType, $Body, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
        $script:completionBody = [string]$Body
        return [pscustomobject]@{ StatusCode = 200 }
    }

    $readinessEndpoint = @{
        Backend = "lemonade"
        Port = "8080"
        ApiBasePath = "/api/v1"
        ChatCompletionsUrl = "http://localhost:8080/api/v1/chat/completions"
    }
    $readiness = Test-WindowsLlmModelReadiness `
        -Endpoint $readinessEndpoint -InstallDir $modelTestDir `
        -GgufFile $tier.GgufFile -TimeoutSec 5
    $request = $script:completionBody | ConvertFrom-Json
    if (-not $readiness.Ok -or $readiness.ModelId -ne "Modern-Model" -or
        $request.model -ne "Modern-Model") {
        throw "Readiness did not use the live Lemonade model ID"
    }
    if ($script:resolvedModelPort -ne 8080 -or $script:resolvedModelFile -ne $tier.GgufFile) {
        throw "Readiness passed the wrong endpoint/model to Resolve-ODSLemonadeModelId"
    }
} finally {
    Remove-Item -LiteralPath $modelTestDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[PASS] Windows local LLM endpoint and Lemonade model resolver"
# --- Get-WindowsODSEnvMap quote handling ---
# The parser must strip exactly one MATCHING pair of surrounding quotes.
# Stripping each quote type independently corrupts values that contain or
# end with the other quote character (mirrors lib/safe-env.sh on Linux).
$envFixture = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-envmap-test-" + [System.IO.Path]::GetRandomFileName() + ".env")
@'
PLAIN=plain-value
DQ="hello world"
SQ='single quoted'
DQ_INNER_SQ="'literal'"
SQ_INNER_DQ='"x"'
MISMATCH=trailing-quote"
LONE_DQ="
EMPTY_DQ=""
'@ | Set-Content -LiteralPath $envFixture -Encoding Ascii

try {
    $map = Get-WindowsODSEnvMap -Path $envFixture
    $expected = @{
        "PLAIN"       = 'plain-value'
        "DQ"          = 'hello world'
        "SQ"          = 'single quoted'
        "DQ_INNER_SQ" = "'literal'"
        "SQ_INNER_DQ" = '"x"'
        "MISMATCH"    = 'trailing-quote"'
        "LONE_DQ"     = '"'
        "EMPTY_DQ"    = ''
    }
    foreach ($key in $expected.Keys) {
        if ($map[$key] -cne $expected[$key]) {
            throw "EnvMap quote handling: $key = <$($map[$key])> expected <$($expected[$key])>"
        }
    }
    Write-Host "[PASS] Get-WindowsODSEnvMap strips only matched surrounding quote pairs"
}
finally {
    Remove-Item -LiteralPath $envFixture -Force -ErrorAction SilentlyContinue
}

Write-Host "[PASS] Windows local LLM endpoint resolver"
PS_EOF

if command -v cygpath >/dev/null 2>&1; then
  ODS_TEST_ROOT="$(cygpath -w "$ROOT_DIR")" "$PS_BIN" -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$tmp_ps")"
else
  ODS_TEST_ROOT="$ROOT_DIR" "$PS_BIN" -NoProfile -ExecutionPolicy Bypass -File "$tmp_ps"
fi
