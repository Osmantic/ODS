$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "installers\windows\lib\env-generator.ps1")

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

Assert-Equal (Get-ODSEffectiveContainerMemoryGB -SystemRamGB 64 -DockerRamGB 8) 8 "Docker VM lower bound"
Assert-Equal (Get-ODSEffectiveContainerMemoryGB -SystemRamGB 8 -DockerRamGB 64) 8 "Host lower bound"
Assert-Equal (Get-ODSEffectiveContainerMemoryGB -SystemRamGB 32 -DockerRamGB 0) 32 "Host fallback"

Assert-Equal (Get-ODSDefaultNvidiaLlamaMemoryLimit -AvailableRamGB 0) "64G" "Unknown RAM"
Assert-Equal (Get-ODSDefaultNvidiaLlamaMemoryLimit -AvailableRamGB 2) "1G" "Minimum"
Assert-Equal (Get-ODSDefaultNvidiaLlamaMemoryLimit -AvailableRamGB 8) "5G" "8 GiB"
Assert-Equal (Get-ODSDefaultNvidiaLlamaMemoryLimit -AvailableRamGB 16) "12G" "16 GiB"
Assert-Equal (Get-ODSDefaultNvidiaLlamaMemoryLimit -AvailableRamGB 32) "28G" "32 GiB"
Assert-Equal (Get-ODSDefaultNvidiaLlamaMemoryLimit -AvailableRamGB 64) "60G" "64 GiB"
Assert-Equal (Get-ODSDefaultNvidiaLlamaMemoryLimit -AvailableRamGB 128) "64G" "Absolute cap"

function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget {
    return @{ Limit = "4.0"; Reservation = "1.0"; Available = "4.0" }
}

$script:dockerRamGB = 8
function Get-ODSDockerMemoryGB {
    return $script:dockerRamGB
}

$tier = @{
    TierName = "Test"
    LlmModel = "test-model"
    GgufFile = "test-model.gguf"
    MaxContext = 8192
}
$testRoot = Join-Path ([IO.Path]::GetTempPath()) "ods-memory-budget-$([Guid]::NewGuid().ToString('N'))"
try {
    $nvidiaDir = Join-Path $testRoot "nvidia"
    New-Item -ItemType Directory -Path $nvidiaDir -Force | Out-Null
    New-ODSEnv -InstallDir $nvidiaDir -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 64 | Out-Null
    $nvidiaEnvPath = Join-Path $nvidiaDir ".env"
    $nvidiaEnv = Get-Content -LiteralPath $nvidiaEnvPath -Raw
    if ($nvidiaEnv -notmatch '(?m)^LLAMA_SERVER_MEMORY_LIMIT=5G\r?$') {
        throw "Fresh NVIDIA install did not use Docker's lower 8 GiB memory reading"
    }
    if ($nvidiaEnv -notmatch '(?m)^TTS_WORKERS=1\r?$' -or $nvidiaEnv -notmatch '(?m)^TTS_THREADS=4\r?$') {
        throw "Fresh Windows install did not give one Kokoro worker a bounded four-thread budget"
    }

    $nvidiaEnv = $nvidiaEnv -replace '(?m)^LLAMA_SERVER_MEMORY_LIMIT=.*$', 'LLAMA_SERVER_MEMORY_LIMIT=7G'
    $nvidiaEnv = $nvidiaEnv -replace '(?m)^TTS_WORKERS=.*$', 'TTS_WORKERS=2'
    $nvidiaEnv = $nvidiaEnv -replace '(?m)^TTS_THREADS=.*$', 'TTS_THREADS=2'
    [IO.File]::WriteAllText($nvidiaEnvPath, $nvidiaEnv)
    $script:dockerRamGB = 4
    New-ODSEnv -InstallDir $nvidiaDir -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 64 | Out-Null
    $rerunEnv = Get-Content -LiteralPath $nvidiaEnvPath -Raw
    if ($rerunEnv -notmatch '(?m)^LLAMA_SERVER_MEMORY_LIMIT=7G\r?$') {
        throw "NVIDIA reinstall discarded the explicit memory-limit override"
    }
    if ($rerunEnv -notmatch '(?m)^TTS_WORKERS=2\r?$' -or $rerunEnv -notmatch '(?m)^TTS_THREADS=2\r?$') {
        throw "Windows reinstall discarded explicit Kokoro worker or thread overrides"
    }
    $rerunEnv = $rerunEnv -replace '(?m)^TTS_CPU_LIMIT=.*$', 'TTS_CPU_LIMIT=2.0'
    [IO.File]::WriteAllText($nvidiaEnvPath, $rerunEnv)
    New-ODSEnv -InstallDir $nvidiaDir -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 64 | Out-Null
    $reducedCpuEnv = Get-Content -LiteralPath $nvidiaEnvPath -Raw
    if ($reducedCpuEnv -notmatch '(?m)^TTS_CPU_LIMIT=2\.0\r?$' -or $reducedCpuEnv -notmatch '(?m)^TTS_THREADS=1\r?$') {
        throw "Windows reinstall failed to cap stale Kokoro threads after CPU quota reduction"
    }

    foreach ($case in @(
        @{ Name = "cloud"; Backend = "nvidia"; Mode = "cloud" },
        @{ Name = "amd"; Backend = "amd"; Mode = "local" },
        @{ Name = "cpu"; Backend = "none"; Mode = "local" }
    )) {
        $caseDir = Join-Path $testRoot $case.Name
        New-Item -ItemType Directory -Path $caseDir -Force | Out-Null
        New-ODSEnv -InstallDir $caseDir -TierConfig $tier -Tier "1" `
            -GpuBackend $case.Backend -ODSMode $case.Mode -SystemRamGB 8 | Out-Null
        $caseEnv = Get-Content -LiteralPath (Join-Path $caseDir ".env") -Raw
        if ($caseEnv -match '(?m)^LLAMA_SERVER_MEMORY_LIMIT=') {
            throw "$($case.Name) mode unexpectedly received the NVIDIA Docker memory limit"
        }
    }
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$source = Get-Content -LiteralPath (Join-Path $root "installers\windows\lib\env-generator.ps1") -Raw
if ($source -notmatch 'Get-EnvOrNew "LLAMA_SERVER_MEMORY_LIMIT" \$llamaMemoryDefault') {
    throw "Windows generator must preserve an explicit LLAMA_SERVER_MEMORY_LIMIT"
}
if ($source -notmatch 'LLAMA_SERVER_MEMORY_LIMIT=\$llamaServerMemoryLimit') {
    throw "Windows generator must write the effective NVIDIA memory limit"
}

Write-Host "[PASS] Windows NVIDIA llama-server memory budget contract"
