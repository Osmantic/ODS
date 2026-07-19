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

$script:LEMONADE_PORT = "8080"
$script:LEMONADE_HEALTH_URL = "http://localhost:8080/api/v1/health"

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
}
Assert-ResolvedEndpoint -Label "AMD native llama-server fallback" `
    -EnvMap $amdNativeFallback -GpuBackend "amd" -NativeBackend "llama-server" `
    -ExpectedBackend "native-llama-server" `
    -ExpectedHealthUrl "http://localhost:8080/health" `
    -ExpectedChatUrl "http://localhost:8080/v1/chat/completions"

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
}
Assert-ResolvedEndpoint -Label "AMD Lemonade endpoint" `
    -EnvMap $amdLemonade -GpuBackend "amd" -NativeBackend "lemonade" -UseLemonade `
    -ExpectedBackend "lemonade" `
    -ExpectedHealthUrl "http://localhost:8080/api/v1/health" `
    -ExpectedChatUrl "http://localhost:8080/api/v1/chat/completions"

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
