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

$script:NATIVE_LLM_PORT = 8080

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
        [switch]$CloudMode,
        [string]$ExpectedBackend,
        [string]$ExpectedHealthUrl,
        [string]$ExpectedChatUrl
    )

    $endpoint = Get-WindowsLocalLlmEndpoint -EnvMap $EnvMap `
        -GpuBackend $GpuBackend -NativeBackend $NativeBackend `
        -CloudMode:$CloudMode
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

$amdNative = @{
    "ODS_MODE" = "local"
    "LLM_BACKEND" = "llama-server"
    "LLM_API_BASE_PATH" = "/v1"
    "GPU_BACKEND" = "amd"
    "AMD_INFERENCE_RUNTIME" = "llama-server"
    "AMD_INFERENCE_LOCATION" = "host"
    "AMD_INFERENCE_RUNTIME_MODE" = "windows-native-llama-server"
    "AMD_INFERENCE_PORT" = "19080"
    "LLAMA_SERVER_API_KEY" = ("ab" * 32)
}
Assert-ResolvedEndpoint -Label "AMD native llama-server (Round F)" `
    -EnvMap $amdNative -GpuBackend "amd" -NativeBackend "llama-server" `
    -ExpectedBackend "native-llama-server" `
    -ExpectedHealthUrl "http://localhost:19080/health" `
    -ExpectedChatUrl "http://localhost:19080/v1/chat/completions"
$nativeEndpoint = Get-WindowsLocalLlmEndpoint -EnvMap $amdNative -GpuBackend "amd" -NativeBackend "llama-server"
if ($nativeEndpoint.ApiKey -ne ("ab" * 32)) { throw "The native endpoint must carry LLAMA_SERVER_API_KEY" }

# An install not yet rerun still has Lemonade-era keys; it resolves to the
# native llama-server endpoint (/v1, /health), never to Lemonade's /api/v1.
$staleLemonade = @{
    "ODS_MODE" = "lemonade"
    "LLM_BACKEND" = "lemonade"
    "GPU_BACKEND" = "amd"
    "AMD_INFERENCE_RUNTIME" = "lemonade"
    "AMD_INFERENCE_LOCATION" = "host"
    "AMD_INFERENCE_PORT" = "19080"
}
Assert-ResolvedEndpoint -Label "Lemonade-era AMD .env" `
    -EnvMap $staleLemonade -GpuBackend "amd" -NativeBackend "llama-server" `
    -ExpectedBackend "native-llama-server" `
    -ExpectedHealthUrl "http://localhost:19080/health" `
    -ExpectedChatUrl "http://localhost:19080/v1/chat/completions"

function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget {
    return @{ Limit = "4.0"; Reservation = "1.0"; Available = "4.0" }
}

$modelTestDir = Join-Path ([IO.Path]::GetTempPath()) "ods-windows-native-model-$([Guid]::NewGuid().ToString('N'))"
try {
    New-Item -ItemType Directory -Path $modelTestDir -Force | Out-Null
    $tier = @{
        TierName = "Test"
        LlmModel = "modern-model"
        GgufFile = "Modern-Model.gguf"
        MaxContext = 4096
    }
    $envResult = New-ODSEnv -InstallDir $modelTestDir -TierConfig $tier -Tier "SH" `
        -GpuBackend "amd" -AmdInferenceRuntime "llama-server" -AmdInferenceBackend "vulkan" `
        -AmdInferenceLocation "host" -AmdInferencePort "8080" `
        -AmdInferenceRuntimeMode "windows-native-llama-server" -AmdInferenceManaged "true"
    if ($envResult.ContainsKey("LemonadeModel")) {
        throw "Windows AMD env generation still resolves a Lemonade model id"
    }
    $envMap = Get-WindowsODSEnvMap -InstallDir $modelTestDir
    if ($envMap["GGUF_FILE"] -ne "Modern-Model.gguf" -or $envMap["LLAMA_SERVER_API_KEY"] -notmatch '^[0-9a-f]{64}$') {
        throw "Windows AMD env must name the GGUF alias and a 64-hex LLAMA_SERVER_API_KEY"
    }

    $modelsDir = Join-Path (Join-Path $modelTestDir "data") "models"
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $modelsDir $tier.GgufFile) -Value "test"

    $script:completionBody = ""
    $script:completionHeaders = $null
    function Invoke-WebRequest {
        param($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
        $script:completionBody = [string]$Body
        $script:completionHeaders = $Headers
        return [pscustomobject]@{ StatusCode = 200; Content = '{"choices":[{"message":{"content":"OK"}}]}' }
    }

    $readinessEndpoint = Get-WindowsLocalLlmEndpoint -EnvMap $envMap -GpuBackend "amd" -NativeBackend "llama-server"
    $readiness = Test-WindowsLlmModelReadiness `
        -Endpoint $readinessEndpoint -InstallDir $modelTestDir `
        -GgufFile $tier.GgufFile -TimeoutSec 5
    $request = $script:completionBody | ConvertFrom-Json
    if (-not $readiness.Ok -or $readiness.ModelId -ne "Modern-Model.gguf" -or
        $request.model -ne "Modern-Model.gguf") {
        throw "Readiness did not request the GGUF alias"
    }
    if ($script:completionHeaders.Authorization -ne ("Bearer " + $envMap["LLAMA_SERVER_API_KEY"])) {
        throw "Readiness did not authenticate with LLAMA_SERVER_API_KEY"
    }
} finally {
    Remove-Item -LiteralPath $modelTestDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[PASS] Windows local LLM endpoint and native llama-server readiness"
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
