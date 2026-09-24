#!/usr/bin/env pwsh
<#
ODS Windows installer (WSL2-delegated MVP).
Runs preflight checks on Windows, then delegates to install-core.sh inside WSL.
#>

[CmdletBinding()]
param(
    [switch]$NoDelegate,
    [switch]$SkipDockerCheck,
    [string]$Distro = "",
    [string]$ModelsDirectory = "",
    [ValidateRange(1,65535)][int]$InferencePort = 18080,
    [string]$ReportPath = "$env:TEMP\\ods-windows-preflight.json",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PassthroughArgs
)

$ErrorActionPreference = "Stop"
$checks = @()
. (Join-Path $PSScriptRoot 'windows/lib/portal-install-plan.ps1')
$PassthroughArgs = @(Get-ODSWindowsPortalArguments -Arguments $PassthroughArgs)
. (Join-Path $PSScriptRoot "wsl-lifecycle.ps1") -Distro $Distro
. (Join-Path $PSScriptRoot 'windows/lib/detection.ps1')
. (Join-Path $PSScriptRoot 'windows/lib/portal-runtime-plan.ps1')

function Write-Section([string]$Message) {
    Write-Host ""
    Write-Host $Message -ForegroundColor Cyan
}

function Add-Check([string]$Id, [string]$Status, [string]$Message, [string]$Action = "") {
    $script:checks += [pscustomobject]@{
        id = $Id
        status = $Status
        message = $Message
        action = $Action
    }
}

function Convert-ToWslPath([string]$WindowsPath) {
    if ($WindowsPath -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLower()
        $rest = $Matches[2] -replace '\\', '/'
        return "/mnt/$drive/$rest"
    }
    return $WindowsPath -replace '\\', '/'
}

Write-Host "ODS Windows installer (WSL2 path)" -ForegroundColor Cyan
$windowsGpu = Get-GpuInfo
$runtimePlan = Get-ODSWindowsPortalRuntimePlan -GpuInfo $windowsGpu -Cloud:($PassthroughArgs -contains '--cloud')
Write-Host "Windows GPU: $($windowsGpu.Name)"
Write-Host "Requested placement: Pixel in WSL; $($runtimePlan.Backend) inference on $($runtimePlan.InferenceHost)."

Write-Section "Checking prerequisites"
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] WSL is not installed." -ForegroundColor Red
    Write-Host "Install WSL first: wsl --install"
    Add-Check "wsl-installed" "blocker" "WSL is not installed." "Run: wsl --install"
} else {
    Add-Check "wsl-installed" "pass" "WSL command is available."
}

$wslStatus = ""
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    try {
        $wslStatus = (& wsl.exe --status 2>$null | Out-String)
    } catch { }
    if ($wslStatus -match "Default Version:\s*2") {
        Add-Check "wsl-default-version" "pass" "WSL default version is 2."
    } else {
        Add-Check "wsl-default-version" "warn" "WSL default version is not clearly set to 2." "Run: wsl --set-default-version 2"
    }
}

$distroList = @()
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    $distroList = @(& wsl.exe -l -q 2>$null | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ -and $_ -notmatch "^docker-desktop(?:-data)?$" })
}
if (-not $distroList) {
    Write-Host "[ERROR] No WSL distro found." -ForegroundColor Red
    Write-Host "Install Ubuntu (example): wsl --install -d Ubuntu"
    Add-Check "wsl-distro" "blocker" "No WSL distro found." "Run: wsl --install -d Ubuntu"
} else {
    Add-Check "wsl-distro" "pass" "Detected WSL distro(s): $($distroList -join ', ')"
}

if ([string]::IsNullOrWhiteSpace($Distro)) {
    if ($distroList.Count -gt 0) {
        $Distro = $distroList[0].Trim()
    }
}

# Check the same qualification contract as the Linux installer before starting
# a persistent WSL lifetime or touching the installation. Docker's WSL backend
# alone does not qualify a user distribution for Pixel.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$repoRootWsl = Convert-ToWslPath $repoRoot
if ($Distro) {
    $pixelProbe = 'source ' + (ConvertTo-ODSBashArgument "$repoRootWsl/installers/lib/pixel-integration.sh") + '; ods_pixel_host_qualified'
    & wsl.exe --distribution $Distro --exec bash -lc $pixelProbe
    if ($LASTEXITCODE -eq 0) {
        Add-Check 'pixel-host' 'pass' "Distribution '$Distro' supports the Portal runtime."
    } else {
        Add-Check 'pixel-host' 'blocker' "Distribution '$Distro' is not qualified for Portal." 'Use Ubuntu 24.04/26.04 or Debian 12 under WSL2 with systemd enabled. Select it with -Distro; Hermes will not be substituted.'
    }
}

if (-not $SkipDockerCheck) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "[WARN] docker CLI not found on Windows PATH." -ForegroundColor Yellow
        Write-Host "Install Docker Desktop and enable WSL integration."
        Add-Check "docker-cli" "warn" "docker CLI not found on Windows PATH." "Install Docker Desktop and reopen terminal."
    } else {
        Add-Check "docker-cli" "pass" "docker CLI found."
        try {
            $dockerInfo = docker info 2>$null | Out-String
            if ($LASTEXITCODE -ne 0) { throw 'Docker info failed' }
            $null = docker version --format '{{.Server.Version}}' 2>$null
            if ($LASTEXITCODE -ne 0) { throw 'Docker server version probe failed' }
            Write-Host "[OK] Docker Desktop engine reachable."
            Add-Check "docker-daemon" "pass" "Docker Desktop engine reachable."
            if ($dockerInfo -match "WSL2:\s*true") {
                Add-Check "docker-wsl2" "pass" "Docker reports WSL2 backend enabled."
            } else {
                Add-Check "docker-wsl2" "warn" "Docker WSL2 backend not confirmed from docker info output." "Enable 'Use the WSL2 based engine' in Docker Desktop settings."
            }
        } catch {
            Write-Host "[WARN] Docker Desktop not reachable yet." -ForegroundColor Yellow
            Write-Host "Start Docker Desktop before running install for real."
            Add-Check "docker-daemon" "warn" "Docker Desktop not reachable." "Start Docker Desktop and retry."
        }
    }
}

if ($Distro) {
    try {
        $wslDocker = (& wsl.exe -d $Distro -- bash -lc "command -v docker >/dev/null && echo ok || echo missing" 2>$null).Trim()
        if ($wslDocker -eq "ok") {
            Add-Check "wsl-docker-cli" "pass" "docker CLI available inside WSL distro '$Distro'."
        } else {
            Add-Check "wsl-docker-cli" "warn" "docker CLI unavailable inside WSL distro '$Distro'." "Enable Docker Desktop WSL integration for this distro."
        }
    } catch {
        Add-Check "wsl-docker-cli" "warn" "Could not verify docker CLI inside WSL distro '$Distro'." "Open WSL and run: docker info"
    }
}

if ($runtimePlan.Backend -eq 'cuda' -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Host "[OK] NVIDIA tooling detected on Windows host."
    Add-Check "windows-nvidia-smi" "pass" "nvidia-smi available on Windows host."
} elseif ($runtimePlan.Backend -eq 'cuda') {
    Write-Host "[INFO] nvidia-smi not found on Windows host (non-NVIDIA or not installed)."
    Add-Check "windows-nvidia-smi" "warn" "nvidia-smi not detected on Windows host." "Install/update NVIDIA driver if targeting NVIDIA acceleration."
}

if ($Distro -and $runtimePlan.Backend -eq 'cuda') {
    try {
        $wslNvidia = (& wsl.exe -d $Distro -- bash -lc "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi -L >/dev/null 2>&1 && echo ok || echo missing; else echo missing; fi" 2>$null).Trim()
        if ($wslNvidia -eq "ok") {
            Add-Check "wsl-nvidia-smi" "pass" "NVIDIA GPU visible inside WSL."
        } else {
            Add-Check "wsl-nvidia-smi" "warn" "NVIDIA GPU not visible inside WSL." "Verify WSL GPU support and Docker Desktop GPU passthrough."
        }
    } catch {
        Add-Check "wsl-nvidia-smi" "warn" "Could not verify NVIDIA GPU inside WSL." "Open WSL and run: nvidia-smi"
    }
}

try {
    $blockers = @($checks | Where-Object { $_.status -eq "blocker" }).Count
    $warnings = @($checks | Where-Object { $_.status -eq "warn" }).Count
    $report = [pscustomobject]@{
        version = "1"
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        distro = $Distro
        requestedRuntime = $runtimePlan
        summary = [pscustomobject]@{
            checks = $checks.Count
            blockers = $blockers
            warnings = $warnings
            can_proceed = ($blockers -eq 0)
        }
        checks = $checks
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -Path $ReportPath -Encoding UTF8
    Write-Host "[INFO] Preflight report: $ReportPath"
} catch {
    Write-Host "[WARN] Could not write preflight report: $($_.Exception.Message)" -ForegroundColor Yellow
}

if (@($checks | Where-Object { $_.status -eq "blocker" }).Count -gt 0) {
    Write-Host "[ERROR] Preflight blockers found. Fix them, then retry." -ForegroundColor Red
    $checks | Where-Object { $_.status -eq "blocker" } | ForEach-Object {
        Write-Host "  - $($_.message)" -ForegroundColor Red
        if ($_.action) { Write-Host "    Fix: $($_.action)" }
    }
    exit 1
}

Write-Section "WSL delegation target"
Write-Host "Repo path (Windows): $repoRoot"
Write-Host "Repo path (WSL):     $repoRootWsl"

$wslCommand = New-ODSWslInstallerCommand $repoRootWsl $PassthroughArgs ""
Write-Host "Command:"
Write-Host "  wsl.exe bash -lc `"$wslCommand`""

if ($NoDelegate) {
    Write-Host ""
    Write-Host "Delegation skipped (--NoDelegate)." -ForegroundColor Yellow
    exit 0
}

# Establish the independent Windows-owned WSL client before the installer's
# client can exit. The installed directory may not exist until install-core runs.
$rootCommand = New-ODSWslRootCommand $repoRootWsl
$linuxInstallRoot = (& wsl.exe --distribution $Distro --exec bash -lc $rootCommand | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve the Linux installation directory" }
$lifetimeIdentity = Get-ODSWslIdentity $Distro $linuxInstallRoot
# Pin the resolver result into the actual installer invocation, even when a
# later login shell would choose different environment defaults.
$wslCommand = New-ODSWslInstallerCommand $repoRootWsl $PassthroughArgs $lifetimeIdentity.installRoot
# Help and dry-run retain their preview semantics: no persistent Windows task.
$lifetimeRequired = -not (@($PassthroughArgs | Where-Object { $_ -cin @('--dry-run','--help','-h') }).Count -gt 0)
if ($lifetimeRequired) {
    Initialize-ODSPrivateDirectory $lifetimeIdentity.directory
    $lifetimeLock = Open-ODSPrivateLock (Join-Path $lifetimeIdentity.directory 'command.lock')
    try { $null = Start-ODSWslLifetime $lifetimeIdentity } finally { $lifetimeLock.Dispose() }
    Write-Host "ODS WSL lifetime is active independently of this installer window."
    Write-Host "Lifecycle: powershell -File `"$PSScriptRoot\wsl-lifecycle.ps1`" -Action status|stop|start|restart -Distro `"$Distro`" -InstallRoot `"$linuxInstallRoot`""
}

if ($runtimePlan.InferenceHost -eq 'windows' -and $lifetimeRequired) {
    . (Join-Path $PSScriptRoot 'windows/lib/constants.ps1')
    . (Join-Path $PSScriptRoot 'windows/lib/ui.ps1')
    . (Join-Path $PSScriptRoot 'windows/lib/tier-map.ps1')
    . (Join-Path $PSScriptRoot 'windows/lib/portal-runtime.ps1')
    if (-not $ModelsDirectory) { $ModelsDirectory = Join-Path $script:ODS_INSTALL_DIR 'data/models' }
    $hostRam = Get-SystemRamGB
    $modelTier = ConvertTo-TierFromGpu -GpuInfo $windowsGpu -SystemRamGB $hostRam
    $tierIndex = [Array]::IndexOf($PassthroughArgs, '--tier')
    if ($tierIndex -ge 0 -and $tierIndex + 1 -lt $PassthroughArgs.Count) {
        $modelTier = $PassthroughArgs[$tierIndex + 1].ToUpper() -replace '^T([0-4])$', '$1'
    }
    $selectedModel = Resolve-TierConfig -Tier $modelTier
    $selectedModel = Resolve-CatalogModelRecommendation -TierConfig $selectedModel -Tier $modelTier `
        -GpuInfo $windowsGpu -SystemRamGB $hostRam -SourceRoot $repoRoot
    if ($PassthroughArgs -notcontains '--no-bootstrap') {
        $selectedModel = @{
            GgufFile=$script:BOOTSTRAP_GGUF_FILE; GgufUrl=$script:BOOTSTRAP_GGUF_URL
            GgufSha256=$script:BOOTSTRAP_GGUF_SHA256; LlmModel=$script:BOOTSTRAP_LLM_MODEL
            MaxContext=$script:BOOTSTRAP_MAX_CONTEXT
        }
    }
    Write-Host "Preparing the ODS Windows GPU runtime and model: $($selectedModel.GgufFile)"
    $prepared = Initialize-ODSPortalWindowsRuntime -SourceRoot $repoRoot -Identity $lifetimeIdentity `
        -ModelsDirectory $ModelsDirectory -Model $selectedModel -Port $InferencePort
    $handoff = [ordered]@{
        GPU_BACKEND='amd'; ODS_MODE='lemonade'; AMD_INFERENCE_RUNTIME='lemonade'
        AMD_INFERENCE_RUNTIME_MODE='wsl-windows-lemonade'; AMD_INFERENCE_MANAGED='true'
        AMD_INFERENCE_LOCATION='host'; AMD_INFERENCE_PORT=[string]$prepared.Port
        LEMONADE_EXTERNAL='false'; EXTERNAL_LLM_URL=''; EXTERNAL_LLM_CONTAINER_URL=''; EXTERNAL_LLM_MODEL=''
        LEMONADE_BASE_URL="http://127.0.0.1:$($prepared.Port)"
        LEMONADE_CONTAINER_BASE_URL="http://host.docker.internal:$($prepared.Port)"
        LEMONADE_MODEL=$prepared.ModelId; LEMONADE_API_KEY=$prepared.ApiKey
        ODS_WINDOWS_MODELS_PATH=$prepared.ModelsWslPath
        ODS_WINDOWS_GPU_NAME=[string]$windowsGpu.Name; ODS_WINDOWS_GPU_VRAM_MB=[string]$windowsGpu.VramMB
        ODS_WINDOWS_GPU_MEMORY_TYPE=[string]$windowsGpu.MemoryType; ODS_WINDOWS_GPU_COUNT='1'
        ODS_WINDOWS_TIER=$modelTier; ODS_WINDOWS_MODEL_FILE=[string]$selectedModel.GgufFile
        ODS_WINDOWS_MODEL_ID=[string]$selectedModel.LlmModel; ODS_WINDOWS_MODEL_CONTEXT=[string]$prepared.Context
    }
    $lines = foreach ($name in $handoff.Keys) {
        $value = [string]$handoff[$name]
        if ($value -match "[\x00\r\n']") { throw "Invalid handoff value for $name" }
        "$name='$value'"
    }
    $handoffPath = Join-Path $lifetimeIdentity.directory 'linux-inference.env'
    [IO.File]::WriteAllText($handoffPath, ($lines -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
    # Only the private file path crosses the process boundary, never the key.
    $PassthroughArgs += @('--no-external-llm','--no-bootstrap')
    $wslCommand = New-ODSWslInstallerCommand $repoRootWsl $PassthroughArgs $lifetimeIdentity.installRoot (Convert-ToWslPath $handoffPath)
}

Write-Section "Running installer in WSL"
if ($Distro) {
    & wsl.exe -d $Distro bash -lc $wslCommand
} else {
    & wsl.exe bash -lc $wslCommand
}
$installerExitCode = $LASTEXITCODE
if ($installerExitCode -ne 0 -and $lifetimeRequired) {
    Write-Warning "Installation failed. The ODS WSL lifetime remains available for diagnosis; use lifecycle release to release only its WSL client if the incomplete install cannot stop normally."
}
exit $installerExitCode
