$ErrorActionPreference = "Stop"

# The Gemma 4 tier config pins the CUDA llama.cpp build. Phase 02 used to hand
# it to every backend, so a Windows CPU install wrote the CUDA image into .env
# and docker-compose.cpu.yml started it on a host without an NVIDIA runtime.
# Evaluate phase 02's real assignment for each Windows backend.

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "installers\windows\lib\tier-map.ps1")

$phase = Get-Content -LiteralPath (Join-Path $root "installers\windows\phases\02-detection.ps1")
$assignment = @($phase | Where-Object { $_ -match '^\$llamaServerImage = ' })
if ($assignment.Count -ne 1) {
    throw "expected exactly one `$llamaServerImage assignment in phase 02, found $($assignment.Count)"
}

$tierConfig = Resolve-TierConfig -Tier "2" -ModelProfile "gemma4"
$cudaImage = "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014"
if ($tierConfig.LlamaServerImage -ne $cudaImage) {
    throw "gemma4 tier config should pin $cudaImage, got '$($tierConfig.LlamaServerImage)'"
}

foreach ($case in @(
    @{ Backend = "nvidia"; Expected = $cudaImage },
    @{ Backend = "amd"; Expected = "" },
    @{ Backend = "none"; Expected = "" }
)) {
    $gpuInfo = @{ Backend = $case.Backend }
    $llamaServerImage = $null
    Invoke-Expression $assignment[0]
    if ($llamaServerImage -ne $case.Expected) {
        throw "backend '$($case.Backend)' expected image '$($case.Expected)', got '$llamaServerImage'"
    }
}

Write-Host "[PASS] Windows Gemma 4 runtime image follows the GPU backend"
