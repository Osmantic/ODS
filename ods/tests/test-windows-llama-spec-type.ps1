$ErrorActionPreference = "Stop"

# Contract: Windows NVIDIA installs use docker-compose.nvidia.yml, which defaults
# llama-server to ngram-mod speculative decoding. A fresh .env leaves that
# default implicit, and an owner's LLAMA_SPEC_TYPE=none opt-out survives an
# installer rerun exactly once.

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "installers\windows\lib\env-generator.ps1")

function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget {
    return @{ Limit = "4.0"; Reservation = "1.0"; Available = "4.0" }
}
function Get-ODSDockerMemoryGB { return 16 }

$tier = @{
    TierName = "Test"
    LlmModel = "test-model"
    GgufFile = "test-model.gguf"
    MaxContext = 8192
}
$testRoot = Join-Path ([IO.Path]::GetTempPath()) "ods-llama-spec-$([Guid]::NewGuid().ToString('N'))"
try {
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    $envPath = Join-Path $testRoot ".env"
    New-ODSEnv -InstallDir $testRoot -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 32 | Out-Null
    $fresh = Get-Content -LiteralPath $envPath -Raw
    if ($fresh -match '(?m)^LLAMA_SPEC_TYPE=') {
        throw "Fresh install wrote LLAMA_SPEC_TYPE; the overlay default must stay implicit"
    }
    if ($fresh -match '(?m)^LLAMA_ARG_SPEC_TYPE=') {
        throw "Fresh install wrote a per-model LLAMA_ARG_SPEC_TYPE"
    }
    if ($fresh -notmatch '(?m)^# LLAMA_SPEC_TYPE=none turns it off') {
        throw "Generated .env does not document the LLAMA_SPEC_TYPE opt-out"
    }

    [IO.File]::WriteAllText($envPath, $fresh.TrimEnd() + "`nLLAMA_SPEC_TYPE=none`n")
    New-ODSEnv -InstallDir $testRoot -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 32 | Out-Null
    $rerun = Get-Content -LiteralPath $envPath -Raw
    $optOuts = [regex]::Matches($rerun, '(?m)^LLAMA_SPEC_TYPE=.*$')
    if ($optOuts.Count -ne 1 -or $optOuts[0].Value.TrimEnd("`r") -ne "LLAMA_SPEC_TYPE=none") {
        throw "Reinstall did not preserve exactly one LLAMA_SPEC_TYPE=none line (found $($optOuts.Count))"
    }
    Write-Output "Windows LLAMA_SPEC_TYPE opt-out contract OK"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
