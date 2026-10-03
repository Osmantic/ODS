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

Assert-Equal (ConvertTo-ODSMemoryLimitMiB -Value "12G") 12288 "12G in MiB"
Assert-Equal (ConvertTo-ODSMemoryLimitMiB -Value "12gb") 12288 "12gb in MiB"
Assert-Equal (ConvertTo-ODSMemoryLimitMiB -Value "512m") 512 "512m in MiB"
Assert-Equal (ConvertTo-ODSMemoryLimitMiB -Value "12884901888") 12288 "Plain bytes in MiB"
Assert-Equal (ConvertTo-ODSMemoryLimitMiB -Value "1.5G") 0 "Decimal is not read"

# Same table as tests/test-llama-memory-budget.sh.
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 15 -ContainerMemoryLimit "12G") "3072" "16 GB WSL VM cache"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 16 -ContainerMemoryLimit "64G") "3413" "16 GiB cache"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 24 -ContainerMemoryLimit "20G") "5120" "24 GiB cache"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 29 -ContainerMemoryLimit "64G") "7850" "29 GiB cache"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 31 -ContainerMemoryLimit "27G") "6912" "32 GB host cache"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 30 -ContainerMemoryLimit "64G") "" "30 GiB keeps the llama.cpp default"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 62 -ContainerMemoryLimit "12G") "3072" "Container limit bounds the cache"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 125 -ContainerMemoryLimit "64G") "" "Tower keeps the llama.cpp default"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 64 -ContainerMemoryLimit "6G") "1536" "CPU compose limit"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 8 -ContainerMemoryLimit "5G") "682" "8 GiB cache"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 7 -ContainerMemoryLimit "4G") "512" "Floor"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 0 -ContainerMemoryLimit "12G") "3072" "Unknown memory"
Assert-Equal (Get-ODSDefaultLlamaCacheRamMiB -AvailableRamGB 0 -ContainerMemoryLimit "") "" "Nothing known"

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

    $nvidiaEnv = $nvidiaEnv -replace '(?m)^LLAMA_SERVER_MEMORY_LIMIT=.*$', 'LLAMA_SERVER_MEMORY_LIMIT=7G'
    [IO.File]::WriteAllText($nvidiaEnvPath, $nvidiaEnv)
    $script:dockerRamGB = 4
    New-ODSEnv -InstallDir $nvidiaDir -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 64 | Out-Null
    $rerunEnv = Get-Content -LiteralPath $nvidiaEnvPath -Raw
    if ($rerunEnv -notmatch '(?m)^LLAMA_SERVER_MEMORY_LIMIT=7G\r?$') {
        throw "NVIDIA reinstall discarded the explicit memory-limit override"
    }

    # The fresh install sized the prompt cache for Docker's 8 GiB (a third of
    # 8 - 6 GiB), and the rerun kept it although Docker now reports 4 GiB.
    if ($rerunEnv -notmatch '(?m)^LLAMA_ARG_CACHE_RAM=682\r?$') {
        throw "NVIDIA install did not keep the prompt cache sized for Docker's 8 GiB"
    }

    # An owner's prompt-cache size survives a rerun.
    [IO.File]::WriteAllText($nvidiaEnvPath, ($rerunEnv -replace '(?m)^LLAMA_ARG_CACHE_RAM=.*$', 'LLAMA_ARG_CACHE_RAM=4096'))
    New-ODSEnv -InstallDir $nvidiaDir -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 64 | Out-Null
    if ((Get-Content -LiteralPath $nvidiaEnvPath -Raw) -notmatch '(?m)^LLAMA_ARG_CACHE_RAM=4096\r?$') {
        throw "NVIDIA reinstall discarded the owner's LLAMA_ARG_CACHE_RAM"
    }

    # A 16 GB Docker VM with the 12G 8 GB-GPU profile, and a profile's own size.
    $script:dockerRamGB = 15
    $wslDir = Join-Path $testRoot "wsl16"
    New-Item -ItemType Directory -Path $wslDir -Force | Out-Null
    $profileTier = $tier.Clone()
    $profileTier.LLAMA_SERVER_MEMORY_LIMIT = "12G"
    New-ODSEnv -InstallDir $wslDir -TierConfig $profileTier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 31 | Out-Null
    if ((Get-Content -LiteralPath (Join-Path $wslDir ".env") -Raw) -notmatch '(?m)^LLAMA_ARG_CACHE_RAM=3072\r?$') {
        throw "A 15 GiB Docker VM did not get a 3072 MiB prompt cache"
    }
    $profileDir = Join-Path $testRoot "profile-cache"
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
    $cappedTier = $tier.Clone()
    $cappedTier.LLAMA_ARG_CACHE_RAM = "1024"
    New-ODSEnv -InstallDir $profileDir -TierConfig $cappedTier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 31 | Out-Null
    if ((Get-Content -LiteralPath (Join-Path $profileDir ".env") -Raw) -notmatch '(?m)^LLAMA_ARG_CACHE_RAM=1024\r?$') {
        throw "A runtime profile's LLAMA_ARG_CACHE_RAM was not written"
    }
    $profileEnvPath = Join-Path $profileDir ".env"
    $profileEnv = Get-Content -LiteralPath $profileEnvPath -Raw
    [IO.File]::WriteAllText($profileEnvPath, ($profileEnv -replace '(?m)^LLAMA_ARG_CACHE_RAM=.*$', 'LLAMA_ARG_CACHE_RAM=4096'))
    New-ODSEnv -InstallDir $profileDir -TierConfig $cappedTier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 31 | Out-Null
    if ((Get-Content -LiteralPath $profileEnvPath -Raw) -notmatch '(?m)^LLAMA_ARG_CACHE_RAM=1024\r?$') {
        throw "A runtime profile's LLAMA_ARG_CACHE_RAM must win over .env, as on Linux"
    }
    $script:dockerRamGB = 128
    $towerDir = Join-Path $testRoot "tower"
    New-Item -ItemType Directory -Path $towerDir -Force | Out-Null
    New-ODSEnv -InstallDir $towerDir -TierConfig $tier -Tier "1" `
        -GpuBackend "nvidia" -ODSMode "local" -SystemRamGB 128 | Out-Null
    if ((Get-Content -LiteralPath (Join-Path $towerDir ".env") -Raw) -match '(?m)^LLAMA_ARG_CACHE_RAM=') {
        throw "A 128 GiB host must keep llama.cpp's own prompt-cache default"
    }
    $script:dockerRamGB = 4

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
        $hasCache = $caseEnv -match '(?m)^LLAMA_ARG_CACHE_RAM='
        if ($case.Name -eq "cpu") {
            # 4 GiB Docker VM under the 6G CPU compose limit: the floor.
            if ($caseEnv -notmatch '(?m)^LLAMA_ARG_CACHE_RAM=512\r?$') {
                throw "CPU install did not size the prompt cache"
            }
        } elseif ($hasCache) {
            throw "$($case.Name) mode unexpectedly received a llama-server prompt-cache size"
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

Write-Host "[PASS] Windows NVIDIA llama-server memory budget and prompt-cache contract"
