# AMD GPUs for the Windows -> WSL Portal path.
#
# Docker Desktop passes only NVIDIA GPUs into WSL containers, and ROCm does not
# run inside them, so an AMD GPU is used by Lemonade Server running natively on
# Windows (Vulkan), exactly as the native Windows installer does. The Linux
# installer already supports that layout: --lemonade-url points LiteLLM and
# Pixel at it, and containers reach Windows loopback via host.docker.internal.
# Requires wsl-portal-setup.ps1 (Confirm-ODSPortalPreparation) in scope.

. (Join-Path $PSScriptRoot 'detection.ps1')
. (Join-Path $PSScriptRoot 'tier-map.ps1')
. (Join-Path $PSScriptRoot 'backend-contract.ps1')

$script:ODSPortalLemonadeTaskName = 'ODSLemonadeRuntime'
$script:ODSPortalLemonadeHealthSeconds = 60
# The first load also downloads Lemonade's llama.cpp Vulkan runtime.
$script:ODSPortalLemonadeLoadSeconds = 900
# Tried in order when AMD_INFERENCE_PORT is unset: the pinned port, Lemonade's
# own defaults, then two quiet ports. 8080 is often taken by other programs.
$script:ODSPortalLemonadePortCandidates = @(8080, 13305, 8000, 18080, 28080)

function Get-ODSPortalStateDir {
    return (Join-Path $env:LOCALAPPDATA 'ODS\lemonade')
}

function Get-ODSPortalAmdPlan([string]$SourceRoot) {
    # $null means "no AMD GPU route": NVIDIA and CPU-only machines keep the
    # in-WSL llama-server path.
    $gpu = Get-GpuInfo
    if ($gpu.Backend -ne 'amd') { return $null }
    $ramGB = Get-SystemRamGB
    $tier = [string](ConvertTo-TierFromGpu -GpuInfo $gpu -SystemRamGB $ramGB)
    if ($tier -eq '0') {
        Write-Host "         $($gpu.Name) has too little graphics memory for a local model; ODS will run the model on the CPU."
        return $null
    }
    if (-not $env:MODEL_PROFILE) { $env:MODEL_PROFILE = 'qwen' }
    $config = Resolve-TierConfig -Tier $tier
    $config = Resolve-CatalogModelRecommendation -TierConfig $config -Tier $tier -GpuInfo $gpu `
        -SystemRamGB $ramGB -SourceRoot $SourceRoot -MinContext $script:HERMES_MIN_CONTEXT
    if (-not $config.GgufFile -or -not $config.GgufUrl -or -not $config.MaxContext) {
        throw "No model is defined for AMD tier $tier."
    }
    # The Linux installer accepts tiers 1-4; larger AMD classes use its top tier.
    $linuxTier = if ($tier -match '^[1-4]$') { $tier } else { '4' }
    return [pscustomobject]@{
        GpuName = [string]$gpu.Name
        VramMB = [int]$gpu.VramMB
        MemoryType = [string]$gpu.MemoryType
        Tier = $tier
        LinuxTier = $linuxTier
        Model = [string]$config.LlmModel
        GgufFile = [string]$config.GgufFile
        GgufUrl = [string]$config.GgufUrl
        GgufSha256 = [string]$config.GgufSha256
        ContextSize = [int]$config.MaxContext
    }
}

function Install-ODSPortalLemonade([string]$SourceRoot, [bool]$NonInteractive) {
    # Returns the Lemonade executable path, or $null when the user declines.
    $runtime = Get-ODSAmdLemonadeRuntime -RootPath $SourceRoot
    $exe = Resolve-ODSLemonadeExe -ExecutableName $runtime.windows_executable
    if ($exe) {
        Write-Host "         Lemonade Server found: $exe"
        return $exe
    }
    if (-not (Confirm-ODSPortalPreparation "Install Lemonade Server $($runtime.windows_version) to run the AI model on your AMD GPU? It installs for your Windows user and runs only on this computer (127.0.0.1)." $NonInteractive)) {
        return $null
    }
    $msi = Join-Path $env:TEMP $runtime.windows_msi_file
    $url = "https://github.com/lemonade-sdk/lemonade/releases/download/v$($runtime.windows_version)/$($runtime.windows_msi_file)"
    Write-Host "         Downloading $url"
    & curl.exe --fail --location --silent --show-error --output $msi $url
    if ($LASTEXITCODE -ne 0) { throw "Could not download Lemonade Server (curl exit $LASTEXITCODE): $url" }
    $installDir = Get-ODSLemonadeUserInstallDir
    $log = Join-Path (Get-ODSPortalStateDir) 'lemonade-msi-install.log'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    Write-Host '         Installing Lemonade Server...'
    $msiArgs = "/i `"$msi`" /quiet /norestart INSTALLDIR=`"$installDir`" /L*V `"$log`""
    $process = Start-Process -FilePath msiexec.exe -ArgumentList $msiArgs -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "Lemonade Server setup failed (msiexec exit $($process.ExitCode)). Log: $log" }
    $exe = Resolve-ODSLemonadeExe -ExecutableName $runtime.windows_executable
    if (-not $exe) { throw "Lemonade Server setup finished but $($runtime.windows_executable) was not found under $installDir. Log: $log" }
    return $exe
}

function Get-ODSPortalLemonadeModel($Plan) {
    # Downloads the planned GGUF once into the Windows models folder Lemonade
    # serves, verifying the pinned SHA-256.
    $modelsDir = Join-Path (Get-ODSPortalStateDir) 'models'
    New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
    $target = Join-Path $modelsDir $Plan.GgufFile
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if (-not $Plan.GgufSha256 -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $Plan.GgufSha256) {
            Write-Host "         Model already downloaded: $($Plan.GgufFile)"
            return $modelsDir
        }
        Write-Host "         $($Plan.GgufFile) does not match its checksum; downloading it again."
    }
    $partial = "$target.partial"
    Write-Host "         Downloading $($Plan.GgufFile) for your GPU. This is the large download."
    & curl.exe --fail --location --continue-at - --output $partial $Plan.GgufUrl
    if ($LASTEXITCODE -ne 0) { throw "Model download failed (curl exit $LASTEXITCODE). Rerun the same command to resume it." }
    if ($Plan.GgufSha256) {
        $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash
        if ($actual -ne $Plan.GgufSha256) {
            Remove-Item -LiteralPath $partial
            throw "The downloaded model does not match its checksum ($actual, expected $($Plan.GgufSha256)). Rerun the same command."
        }
    }
    Move-Item -LiteralPath $partial -Destination $target -Force
    return $modelsDir
}

function Test-ODSPortalLemonadeHealth([int]$Port) {
    # HttpWebRequest raises WebException for refused, timed-out and non-2xx
    # requests on both Windows PowerShell 5.1 and PowerShell 7.
    $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/api/v1/health")
    $request.Timeout = 3000
    try {
        $response = $request.GetResponse()
        $code = [int]$response.StatusCode
        $response.Close()
        return $code -eq 200
    } catch [System.Net.WebException] {
        return $false
    }
}

function Wait-ODSPortalLemonadeHealth([int]$Port, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ODSPortalLemonadeHealth $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Stop-ODSPortalLemonade([string]$ExecutablePath) {
    # A rerun (or a Lemonade the user started) must release its port before a
    # port is chosen, so the same port is picked again.
    $existing = Get-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName -ErrorAction SilentlyContinue
    if ($existing -and $existing.State -eq 'Running') { Stop-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName }
    $exeDir = Split-Path -Parent $ExecutablePath
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($exeDir, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force
}

function Get-ODSPortalPortOwner([int]$Port) {
    # Process name listening on the port, or $null when it is free.
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($process) { return $process.ProcessName }
    return "process $($listener.OwningProcess)"
}

function Select-ODSPortalLemonadePort {
    if ($env:AMD_INFERENCE_PORT) {
        $port = [int]$env:AMD_INFERENCE_PORT
        $owner = Get-ODSPortalPortOwner $port
        if ($owner) { throw "AMD_INFERENCE_PORT $port is already used by '$owner'. Choose a free port or remove AMD_INFERENCE_PORT, then rerun." }
        return $port
    }
    $taken = @()
    foreach ($port in $script:ODSPortalLemonadePortCandidates) {
        $owner = Get-ODSPortalPortOwner $port
        if (-not $owner) { return $port }
        $taken += "$port ($owner)"
    }
    throw "No free port for Lemonade Server; all are in use: $($taken -join ', '). Set AMD_INFERENCE_PORT to a free port, then rerun."
}

function Register-ODSPortalLemonadeTask($Contract) {
    # One task for this Windows user: starts at sign-in (so the model survives a
    # restart) and now. It binds 127.0.0.1 only.
    $action = New-ODSLemonadeScheduledTaskAction -Contract $Contract `
        -DiagnosticLogPath (Join-Path (Get-ODSPortalStateDir) 'lemonade-launch.log')
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
        -Description 'ODS: Lemonade Server for the AMD GPU (127.0.0.1). Starts at sign-in.' -Force | Out-Null
    Start-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName
}

function Initialize-ODSPortalAmdLemonade($Plan, [string]$SourceRoot, [bool]$NonInteractive) {
    # Returns the Linux installer arguments for the Windows Lemonade route, or
    # an empty array when the user keeps the CPU route.
    $exe = Install-ODSPortalLemonade $SourceRoot $NonInteractive
    if (-not $exe) {
        Write-Host '         Continuing without the GPU: the model will run on the CPU (slower).'
        return @()
    }
    $modelsDir = Get-ODSPortalLemonadeModel $Plan
    Stop-ODSPortalLemonade $exe
    $port = Select-ODSPortalLemonadePort
    $contract = Get-ODSLemonadeLaunchContract -ExecutablePath $exe -Port $port -ModelsDir $modelsDir -ContextSize $Plan.ContextSize
    Write-Host "         Starting Lemonade Server $($contract.Version) on 127.0.0.1:$port..."
    Register-ODSPortalLemonadeTask $contract
    if (-not (Wait-ODSPortalLemonadeHealth $port $script:ODSPortalLemonadeHealthSeconds)) {
        throw "Lemonade Server did not answer on http://127.0.0.1:$port/api/v1/health within $($script:ODSPortalLemonadeHealthSeconds) seconds. Check $env:TEMP\lemonade-server.log."
    }
    if ($contract.Modern) {
        # Lemonade 10.7+ takes the models folder, Vulkan backend and context
        # through its local API instead of startup flags (loopback needs no key).
        Set-ODSLemonadeModernRuntimeConfig -Port $port -ModelsDir $modelsDir -ContextSize $Plan.ContextSize
    }
    $modelId = Resolve-ODSLemonadeModelId -Port $port -GgufFile $Plan.GgufFile
    Write-Host "         Loading $modelId on $($Plan.GpuName) (the first load also downloads the GPU runtime)..."
    Set-ODSLemonadeLoadedModel -Port $port -ModelId $modelId -ContextSize $Plan.ContextSize -TimeoutSec $script:ODSPortalLemonadeLoadSeconds
    Write-Host "         GPU model ready: $modelId ($($Plan.ContextSize) tokens of context)."
    return @(
        '--lemonade-url', "http://localhost:$port",
        '--lemonade-model', $modelId,
        '--lemonade-gpu-name', $Plan.GpuName,
        '--lemonade-gpu-vram-mb', [string]$Plan.VramMB
    )
}
