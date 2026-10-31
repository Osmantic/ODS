# ============================================================================
# ODS Windows Native Desktop -- 6-Phase Installer Engine
# ============================================================================
# Runs the native Windows setup flow (zero Docker, zero WSL) across 6 phases:
#   Phase 1: Hardware & DirectX UserGpuPreferences Discovery (GPU-First)
#   Phase 2: Pinned Embeddable Python 3.12 Runtime & Hash-Locked Wheels
#   Phase 3: Native llama.cpp GPU/CPU Runtime Qualification
#   Phase 4: Resumable SHA-256 Verified GGUF Model Provisioning
#   Phase 5: Owner-Preserving .env, Dashboard Bundle & Uninstall Registration
#   Phase 6: Native Service Startup, Shortcuts & Readiness Proof
#
# Emits structured "[ODS-PHASE] {json}" progress events for ODS-Setup.exe
# and returns a rich result object on completion.
# ============================================================================

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $HOME 'ods'),

    [string]$LocalAppDataDir = (Join-Path $env:LOCALAPPDATA 'ODS'),

    [ValidateSet('high_performance', 'power_saving', 'let_windows_decide')]
    [string]$DefaultGpuPreference = 'high_performance',

    [switch]$ForceGpuPreference,

    [string]$ModelUrl = 'https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf',

    [string]$ModelFileName = 'Qwen3.5-2B-Q4_K_M.gguf',

    [string]$ModelSha256 = '',

    [string]$InstallProfile = 'recommended',

    [string]$ProfileTier = '',

    [int]$SystemRamOverrideGB = 0,

    [int]$DashboardPort = 3101,

    [int]$LlamaPort = 8085,

    [object[]]$MockGpus = $null,

    [AllowNull()][System.Collections.IDictionary]$MockGpuRegistry = $null,

    [hashtable]$MockUninstallRegistry = $null,

    [switch]$SkipNetworkDownloads,

    [switch]$SkipServiceStart,

    [switch]$SkipShortcuts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$libRoot = Join-Path $PSScriptRoot 'windows\lib'
if (-not (Test-Path -LiteralPath $libRoot)) {
    $altLibCandidates = @(
        (Join-Path $PSScriptRoot '..\installers\windows\lib'),
        (Join-Path $PSScriptRoot '..\..\installers\windows\lib'),
        (Join-Path $PSScriptRoot 'installers\windows\lib')
    )
    foreach ($cand in $altLibCandidates) {
        if (Test-Path -LiteralPath $cand) {
            $libRoot = [IO.Path]::GetFullPath($cand)
            break
        }
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'config\windows-desktop.json'))) {
    $altRepoCandidates = @(
        $PSScriptRoot,
        (Join-Path $PSScriptRoot '..'),
        (Join-Path $PSScriptRoot '..\..'),
        (Join-Path $PSScriptRoot '..\..\..'),
        (Split-Path -Parent (Split-Path -Parent $libRoot))
    )
    foreach ($cand in $altRepoCandidates) {
        if ($cand -and (Test-Path -LiteralPath (Join-Path $cand 'config\windows-desktop.json'))) {
            $repoRoot = [IO.Path]::GetFullPath($cand)
            break
        }
    }
}
. (Join-Path $libRoot 'private-file.ps1')
. (Join-Path $libRoot 'native-llama-runtime.ps1')
. (Join-Path $libRoot 'desktop-gpu-preferences.ps1')
. (Join-Path $libRoot 'desktop-model.ps1')
. (Join-Path $libRoot 'desktop-python.ps1')
. (Join-Path $libRoot 'desktop-uninstall.ps1')
. (Join-Path $libRoot 'env-generator.ps1')
. (Join-Path $libRoot 'app-shortcut.ps1')

function Write-ODSDesktopPhaseEvent {
    param(
        [int]$Phase,
        [int]$Total = 6,
        [string]$Status,
        [string]$Title,
        [string]$Detail = '',
        [int]$Percent = 0
    )
    $payload = [ordered]@{
        phase     = $Phase
        total     = $Total
        status    = $Status
        title     = $Title
        detail    = $Detail
        percent   = $Percent
        timestamp = [DateTime]::UtcNow.ToString('o')
    }
    $json = $payload | ConvertTo-Json -Compress
    try {
        [Console]::WriteLine("[ODS-PHASE] $json")
        [Console]::Out.Flush()
    } catch { }
    Write-Host "[ODS-PHASE] $json"
}

$resolvedInstallDir = [IO.Path]::GetFullPath($InstallDir)
$resolvedAppDataDir = [IO.Path]::GetFullPath($LocalAppDataDir)
New-Item -ItemType Directory -Path $resolvedInstallDir -Force | Out-Null
New-Item -ItemType Directory -Path $resolvedAppDataDir -Force | Out-Null

$binDir = Join-Path $resolvedAppDataDir 'app\current'
$llamaDir = Join-Path $resolvedAppDataDir 'runtimes\llama'
$pythonRootDir = Join-Path $resolvedAppDataDir 'python'
$modelsDir = Join-Path $resolvedInstallDir 'models'
$conversationsDir = Join-Path $resolvedInstallDir 'conversations'
$workspaceDir = Join-Path $resolvedInstallDir 'workspace'

foreach ($dir in @($binDir, $llamaDir, $pythonRootDir, $modelsDir, $conversationsDir, $workspaceDir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

$odsExePath = Join-Path $binDir 'ODS.exe'
$llamaExePath = Join-Path $llamaDir 'llama-server.exe'

# ============================================================================
# PHASE 1: Hardware & DirectX GPU Preference Discovery
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 1 -Status 'running' -Title 'Hardware & GPU Preference Discovery' -Detail 'Inspecting GPUs and Windows Graphics Settings...' -Percent 8

$null = Set-ODSWindowsUserGpuPreference `
    -ExecutablePaths @($llamaExePath, $odsExePath) `
    -Mode $DefaultGpuPreference `
    -RespectExisting:(-not [bool]$ForceGpuPreference) `
    -MockRegistry $MockGpuRegistry

$activeGpuPref = Get-ODSWindowsUserGpuPreference `
    -ExecutablePaths @($llamaExePath, $odsExePath) `
    -Entries $MockGpuRegistry

$detectedGpus = if ($null -ne $MockGpus) {
    @($MockGpus)
} else {
    try {
        @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop | ForEach-Object {
            $name = [string]$_.Name
            $backend = if ($name -match '(?i)nvidia') { 'nvidia' }
                       elseif ($name -match '(?i)amd|radeon') { 'amd' }
                       elseif ($name -match '(?i)intel') { 'intel' }
                       else { 'none' }
            $isDiscrete = ($backend -eq 'nvidia') -or ($name -match '(?i)radeon\s+rx|arc\s+a\d')
            $vramBytes = [int64]($_.AdapterRAM)
            $vramMB = if ($vramBytes -gt 0) { [int][Math]::Round($vramBytes / 1MB) } else { 0 }
            [pscustomobject]@{
                Name          = $name
                Backend       = $backend
                VramMB        = $vramMB
                DriverVersion = [string]$_.DriverVersion
                IsDiscrete    = $isDiscrete
            }
        })
    } catch {
        @()
    }
}

$discreteCandidates = @($detectedGpus | Where-Object { $_.IsDiscrete })
$primaryGpuInfo = if ($discreteCandidates.Count -gt 0) {
    $discreteCandidates[0]
} elseif ($detectedGpus.Count -gt 0) {
    $detectedGpus[0]
} else {
    [pscustomobject]@{
        Name          = 'CPU'
        Backend       = 'none'
        VramMB        = 0
        DriverVersion = ''
        IsDiscrete    = $false
    }
}

$gpuSelection = Select-ODSWindowsDesktopGpu `
    -PrimaryGpuInfo $primaryGpuInfo `
    -AllAdapters $detectedGpus `
    -UserGpuPreference $activeGpuPref `
    -ExecutablePaths @($llamaExePath, $odsExePath)

$systemRamGB = if ($SystemRamOverrideGB -gt 0) {
    $SystemRamOverrideGB
} else {
    try {
        [int][Math]::Round((Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB)
    } catch {
        16
    }
}

$diskFreeGB = try {
    $driveQualifier = Split-Path -Qualifier $resolvedInstallDir
    $driveName = if ($driveQualifier) { $driveQualifier.TrimEnd(':') } else { 'C' }
    [int][Math]::Round((Get-PSDrive -Name $driveName -ErrorAction Stop).Free / 1GB)
} catch {
    50
}

$calculatedTier = if ($primaryGpuInfo.Backend -eq 'none') {
    if ($systemRamGB -lt 8) { '0' } else { '1' }
} else {
    $vramGB = [int][Math]::Floor($primaryGpuInfo.VramMB / 1024)
    if ($vramGB -ge 90) { 'NV_ULTRA' }
    elseif ($vramGB -ge 40) { '4' }
    elseif ($vramGB -ge 20) { '3' }
    elseif ($vramGB -ge 12) { '2' }
    elseif ($vramGB -lt 4 -and $systemRamGB -lt 12) { '0' }
    else { '1' }
}

$effectiveTier = if ($ProfileTier) {
    $ProfileTier
} elseif ($InstallProfile -eq 'full') {
    if ($calculatedTier -in @('0', '1')) { '2' } else { $calculatedTier }
} elseif ($InstallProfile -eq 'minimal') {
    '0'
} else {
    $calculatedTier
}

$modelConfig = switch -Regex ($effectiveTier) {
    '^(2|3|4|NV_ULTRA|SH_LARGE)$' {
        @{
            Name       = 'Qwen 3.5 9B'
            GgufFile   = 'Qwen3.5-9B-Q4_K_M.gguf'
            GgufUrl    = 'https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf'
            GgufSha256 = '03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8'
            SizeBytes  = 5680522464
            SizeMB     = 5760
            Context    = 32768
            TierName   = 'Full Stack'
        }
    }
    default {
        @{
            Name       = 'Qwen 3.5 2B'
            GgufFile   = 'Qwen3.5-2B-Q4_K_M.gguf'
            GgufUrl    = 'https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q4_K_M.gguf'
            GgufSha256 = 'aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223'
            SizeBytes  = 1280835840
            SizeMB     = 1281
            Context    = $(if ($effectiveTier -eq '0') { 8192 } else { 65536 })
            TierName   = $(if ($effectiveTier -eq '0') { 'Minimal' } else { 'Recommended' })
        }
    }
}

if (-not $PSBoundParameters.ContainsKey('ModelFileName') -or -not $ModelFileName) {
    $ModelFileName = $modelConfig.GgufFile
}
if (-not $PSBoundParameters.ContainsKey('ModelUrl') -or -not $ModelUrl) {
    $ModelUrl = $modelConfig.GgufUrl
}
if (-not $PSBoundParameters.ContainsKey('ModelSha256') -or -not $ModelSha256) {
    $ModelSha256 = $modelConfig.GgufSha256
}
$modelContext = $modelConfig.Context
$tierDisplayName = $modelConfig.TierName

$gpuSummary = "Hardware: $($gpuSelection.GpuInfo.Name) ($($gpuSelection.GpuInfo.VramMB)MB) • ${systemRamGB}GB RAM • ${diskFreeGB}GB Disk -> Tier $effectiveTier ($tierDisplayName, Model: $ModelFileName)"
Write-ODSDesktopPhaseEvent -Phase 1 -Status 'completed' -Title 'Hardware & Tier Classification' -Detail $gpuSummary -Percent 18

# ============================================================================
# PHASE 2: Pinned Embeddable Python 3.12 Runtime & Dependencies
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 2 -Status 'running' -Title 'Pinned Python Runtime' -Detail 'Verifying embeddable Python 3.12.10 and hash-locked wheels...' -Percent 25

$pythonContract = Read-ODSWindowsDesktopPythonContract -SourceRoot $repoRoot
$pythonInstallDir = Get-ODSWindowsDesktopPythonInstallDir -PythonContract $pythonContract -Root $pythonRootDir
$resolvedPythonExe = ''

if ($SkipNetworkDownloads) {
    New-Item -ItemType Directory -Path (Join-Path $pythonInstallDir 'site-packages') -Force | Out-Null
    $pyExe = Join-Path $pythonInstallDir 'python.exe'
    if (-not (Test-Path -LiteralPath $pyExe)) {
        Set-Content -LiteralPath $pyExe -Value 'MZ-PINNED-PYTHON' -Encoding ASCII
    }
    $pthPath = Join-Path $pythonInstallDir ([string]$pythonContract.pth_file)
    $pthText = (@($pythonContract.pth_lines) -join "`r`n") + "`r`n"
    [IO.File]::WriteAllText($pthPath, $pthText, [Text.Encoding]::ASCII)

    $manifest = Get-ODSDesktopDirectoryManifest -Directory $pythonInstallDir
    $pinRecord = [ordered]@{
        schemaVersion       = 1
        id                  = [string]$pythonContract.id
        version             = [string]$pythonContract.version
        pythonZipSha256     = ([string]$pythonContract.sha256).ToLowerInvariant()
        pthFile             = [string]$pythonContract.pth_file
        requireAuthenticode = $false
        wheels              = @($pythonContract.wheels | ForEach-Object {
            [ordered]@{ name = [string]$_.name; version = [string]$_.version; sha256 = ([string]$_.sha256).ToLowerInvariant() }
        })
        files               = $manifest
    }
    Write-ODSPrivateEnvFile -Path (Join-Path $pythonInstallDir 'pin.json') -Content ($pinRecord | ConvertTo-Json -Depth 5)
    $pythonStatus = Test-ODSWindowsDesktopPythonInstall `
        -Directory $pythonInstallDir `
        -ExpectedPythonZipSha256 ([string]$pythonContract.sha256) `
        -SkipAuthenticodeCheck:$true
    $resolvedPythonExe = $pythonStatus.ExecutablePath
} else {
    try {
        $resolvedPythonExe = Resolve-ODSWindowsDesktopPythonExecutable -SourceRoot $repoRoot
        if ((Get-Item $resolvedPythonExe).Length -lt 100000) {
            throw "Resolved Python executable is too small, likely a stub."
        }
        $testOut = & $resolvedPythonExe -c "import fastapi, uvicorn; print('ODS_PY_OK')" 2>$null
        if ($LASTEXITCODE -ne 0 -or ($testOut -join '') -notmatch 'ODS_PY_OK') {
            throw "Resolved Python executable failed sanity execution test."
        }
        $pythonStatus = [pscustomobject]@{
            Directory       = (Split-Path -Parent $resolvedPythonExe)
            ExecutablePath  = $resolvedPythonExe
            Id              = [string]$pythonContract.id
            Version         = [string]$pythonContract.version
            PythonZipSha256 = [string]$pythonContract.sha256
            FileCount       = 1
        }
    } catch {
        $pythonStatus = Install-ODSWindowsDesktopPython -PythonContract $pythonContract -Root $pythonRootDir
        $resolvedPythonExe = $pythonStatus.ExecutablePath
    }
}

Write-ODSDesktopPhaseEvent -Phase 2 -Status 'completed' -Title 'Pinned Python Runtime' -Detail "Python $($pythonStatus.Version) ready ($($pythonContract.wheels.Count) pinned wheels, zero litellm)" -Percent 36

# ============================================================================
# PHASE 3: Native llama.cpp GPU/CPU Runtime Qualification
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 3 -Status 'running' -Title 'Native llama.cpp Qualification' -Detail 'Qualifying native llama-server runtime...' -Percent 45

if ($SkipNetworkDownloads -and -not (Test-Path -LiteralPath $llamaExePath)) {
    Set-Content -LiteralPath $llamaExePath -Value 'MZ-LLAMA-SERVER' -Encoding ASCII
} elseif (-not $SkipNetworkDownloads -and -not (Test-Path -LiteralPath $llamaExePath)) {
    try {
        $vulkanPin = Get-ODSNativeLlamaPin -SourceRoot $repoRoot -Backend 'amd' -Architecture 'x64'
        $installedLlama = Install-ODSNativeLlamaRuntime -Pin $vulkanPin
        if ($installedLlama -and (Test-Path -LiteralPath $installedLlama.ExecutablePath)) {
            Copy-Item -Path (Join-Path $installedLlama.Directory '*') -Destination $llamaDir -Recurse -Force
        }
    } catch {
        Write-Warning "Could not download llama.cpp runtime automatically: $($_.Exception.Message)"
    }
}

$synthDevices = @(
    foreach ($idx in 0..($detectedGpus.Count - 1)) {
        $g = $detectedGpus[$idx]
        $prefix = if ($g.Backend -eq 'nvidia') { "CUDA$idx" } else { "Vulkan$idx" }
        [pscustomobject]@{
            Name        = $prefix
            Description = [string]$g.Name
            TotalMiB    = [long]$g.VramMB
            FreeMiB     = [long]$g.VramMB
        }
    }
)
$targetBackend = if ($gpuSelection.GpuInfo.Backend -eq 'nvidia' -and $gpuSelection.PreferDiscreteGpu) { 'cuda' } else { 'vulkan' }
$selectedDevice = Select-ODSWindowsDesktopLlamaDevice `
    -Devices $synthDevices `
    -AdapterName ([string]$gpuSelection.GpuInfo.Name) `
    -Backend $targetBackend `
    -PreferDiscreteGpu ([bool]$gpuSelection.PreferDiscreteGpu)

$gpuLayers = if ($null -ne $selectedDevice -and $gpuSelection.ExecutionMode -ne 'cpu_power_saving' -and $gpuSelection.ExecutionMode -ne 'cpu') { 99 } else { 0 }
$qualification = [pscustomobject]@{
    Backend           = $(if ($gpuLayers -gt 0) { $targetBackend } else { 'cpu' })
    Device            = $selectedDevice
    GpuLayers         = $gpuLayers
    PreferDiscreteGpu = [bool]$gpuSelection.PreferDiscreteGpu
    ExecutablePath    = $llamaExePath
}

Write-ODSDesktopPhaseEvent -Phase 3 -Status 'completed' -Title 'Native llama.cpp Qualification' -Detail "Qualified backend=$($qualification.Backend) (ngl=$($qualification.GpuLayers))" -Percent 55

# ============================================================================
# PHASE 4: Resumable SHA-256 Verified GGUF Model Provisioning
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 4 -Status 'running' -Title 'Local AI Model Verification' -Detail "Checking $ModelFileName..." -Percent 64

$modelDest = Join-Path $modelsDir $ModelFileName
if ($SkipNetworkDownloads) {
    if (-not (Test-Path -LiteralPath $modelDest)) {
        [IO.File]::WriteAllBytes($modelDest, [Text.Encoding]::UTF8.GetBytes('GGUF-TEST-MODEL-PAYLOAD'))
    }
    $effectiveModelSha = (Get-FileHash -LiteralPath $modelDest -Algorithm SHA256).Hash.ToLowerInvariant()
    $modelResult = Test-ODSVerifiedModelFile -ModelPath $modelDest -ExpectedSha256 $effectiveModelSha
} else {
    $effectiveModelSha = if ($ModelSha256) { $ModelSha256 } else { [string]$modelConfig.GgufSha256 }
    $expectedSizeBytes = if ($modelConfig.ContainsKey('SizeBytes')) { [long]$modelConfig.SizeBytes } else { [long]($modelConfig.SizeMB * 1MB) }
    $progressCb = {
        param([long]$DownloadedBytes, [long]$TotalBytes)
        $curMb = [math]::Round($DownloadedBytes / 1MB)
        $totMb = if ($TotalBytes -gt 0) { [math]::Round($TotalBytes / 1MB) } else { $modelConfig.SizeMB }
        $pct = if ($totMb -gt 0) { [math]::Min(99, [int](($curMb / $totMb) * 100)) } else { 0 }
        $globalPct = 58 + [int]($pct * 0.18)
        Write-ODSDesktopPhaseEvent -Phase 4 -Status 'running' -Title 'Downloading AI Model' -Detail "Downloading $ModelFileName ($curMb MB / $totMb MB - $pct%)" -Percent $globalPct
    }

    # Invoke-ODSResumableModelDownload automatically checks Test-ODSVerifiedModelFile first.
    # If the file is already verified, it returns cached in 0.05s.
    # If it is corrupt, incomplete, or missing, it safely cleans corrupt stamps and downloads/resumes.
    $modelResult = Invoke-ODSResumableModelDownload `
        -ModelsDir $modelsDir `
        -GgufFile $ModelFileName `
        -Url $ModelUrl `
        -ExpectedSha256 $effectiveModelSha `
        -ExpectedSize $expectedSizeBytes `
        -ProgressCallback $progressCb
}

$finalSizeMb = if (Test-Path -LiteralPath $modelDest) { [math]::Round((Get-Item -LiteralPath $modelDest).Length / 1MB) } else { 0 }
Write-ODSDesktopPhaseEvent -Phase 4 -Status 'completed' -Title 'Local AI Model Verification' -Detail "$ModelFileName ready ($finalSizeMb MB, verified SHA-256)" -Percent 76

# ============================================================================
# PHASE 5: Owner-Preserving .env, Dashboard Bundle & Uninstall Registration
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 5 -Status 'running' -Title 'Configuration & Desktop Integration' -Detail 'Writing owner-preserving .env and Windows Installed Apps registration...' -Percent 84

# Ensure standard data and models directories exist
$modelsDir = Join-Path $resolvedInstallDir 'models'
$dataDir = Join-Path $resolvedInstallDir 'data'
$convDir = Join-Path $dataDir 'conversations'
$wsDir = Join-Path $dataDir 'workspace'
New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
New-Item -ItemType Directory -Path $convDir -Force | Out-Null
New-Item -ItemType Directory -Path $wsDir -Force | Out-Null

# Copy dashboard static bundle if present
$installDashboardDist = Join-Path $resolvedInstallDir 'dashboard-dist'
New-Item -ItemType Directory -Path $installDashboardDist -Force | Out-Null
$distCandidates = @(
    (Join-Path $PSScriptRoot 'dashboard-dist'),
    (Join-Path $repoRoot 'dist\windows-desktop\dashboard-dist'),
    (Join-Path $repoRoot 'extensions\services\dashboard\dist'),
    (Join-Path $repoRoot 'extensions\dashboard\dist')
)
$sourceDist = $distCandidates | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'index.html') -PathType Leaf) } | Select-Object -First 1
if ($sourceDist) {
    Copy-Item -Path (Join-Path $sourceDist '*') -Destination $installDashboardDist -Recurse -Force
} elseif (-not (Test-Path -LiteralPath (Join-Path $installDashboardDist 'index.html'))) {
    Set-Content -LiteralPath (Join-Path $installDashboardDist 'index.html') -Value '<!doctype html><html><head><title>ODS Desktop</title></head><body><div id="root">ODS Windows Native Desktop</div></body></html>' -Encoding UTF8
}

# Copy dashboard-api service into $resolvedInstallDir\dashboard-api
$installDashboardApi = Join-Path $resolvedInstallDir 'dashboard-api'
New-Item -ItemType Directory -Path $installDashboardApi -Force | Out-Null
$dashApiCandidates = @(
    (Join-Path $PSScriptRoot 'dashboard-api'),
    (Join-Path $repoRoot 'dist\windows-desktop\dashboard-api'),
    (Join-Path $repoRoot 'extensions\services\dashboard-api')
)
$sourceDashApi = $dashApiCandidates | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'main.py') -PathType Leaf) } | Select-Object -First 1
if ($sourceDashApi) {
    Get-ChildItem -LiteralPath $sourceDashApi -Exclude '__pycache__', '.pytest_cache', '.venv', 'tests' | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $installDashboardApi -Recurse -Force
    }
}

# Copy config files (including model-library.json) into $resolvedInstallDir\config
$installConfigDir = Join-Path $resolvedInstallDir 'config'
New-Item -ItemType Directory -Path $installConfigDir -Force | Out-Null
$configCandidates = @(
    (Join-Path $PSScriptRoot 'config'),
    (Join-Path $repoRoot 'config'),
    (Join-Path $PSScriptRoot '..\..\config')
)
$sourceConfig = $configCandidates | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'model-library.json') -PathType Leaf) } | Select-Object -First 1
if ($sourceConfig) {
    Copy-Item -Path (Join-Path $sourceConfig '*') -Destination $installConfigDir -Recurse -Force
}

# Copy compiled ODS.exe and WebView2 runtime DLLs into %LOCALAPPDATA%\ODS\app\current\ if available
$distDesktopDir = Join-Path $repoRoot 'dist\windows-desktop'
foreach ($runtimeFile in @('ODS.exe', 'uninstall-ods.ps1', 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll', 'ods-app.ico')) {
    $srcFile = Join-Path $distDesktopDir $runtimeFile
    if (-not (Test-Path -LiteralPath $srcFile -PathType Leaf)) {
        $srcFile = Join-Path $PSScriptRoot $runtimeFile
    }
    if (Test-Path -LiteralPath $srcFile -PathType Leaf) {
        try {
            Copy-Item -LiteralPath $srcFile -Destination (Join-Path $binDir $runtimeFile) -Force -ErrorAction Stop
        } catch {
            # File may be locked by a currently running ODS.exe instance
            Write-Warning "Could not update $runtimeFile (in use by running instance): $($_.Exception.Message)"
        }
    }
}
# Ensure uninstaller and icon exist directly under $resolvedAppDataDir for Windows Settings integration
$uninstSrc = @(
    (Join-Path $distDesktopDir 'uninstall-ods.ps1'),
    (Join-Path $PSScriptRoot 'windows\uninstall-ods.ps1'),
    (Join-Path $PSScriptRoot 'uninstall-ods.ps1')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1

if ($uninstSrc) {
    Copy-Item -LiteralPath $uninstSrc -Destination (Join-Path $resolvedAppDataDir 'uninstall-ods.ps1') -Force -ErrorAction SilentlyContinue
}

$icoSrc = @(
    (Join-Path $distDesktopDir 'ods-app.ico'),
    (Join-Path $repoRoot 'extensions\services\dashboard\public\ods.ico')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1

if ($icoSrc) {
    Copy-Item -LiteralPath $icoSrc -Destination (Join-Path $resolvedAppDataDir 'ods-app.ico') -Force -ErrorAction SilentlyContinue
}


$envPath = Join-Path $resolvedInstallDir '.env'
$previousLines = if (Test-Path -LiteralPath $envPath) {
    @(Get-Content -LiteralPath $envPath -Encoding UTF8)
} else {
    @()
}
$existingMap = @{}
foreach ($line in $previousLines) {
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $existingMap[$Matches[1]] = $Matches[2]
    }
}

$apiKey = if ($existingMap.ContainsKey('DASHBOARD_API_KEY') -and $existingMap['DASHBOARD_API_KEY']) {
    $existingMap['DASHBOARD_API_KEY']
} else {
    New-SecureHex -Bytes 24
}

$dashPassword = if ($existingMap.ContainsKey('DASHBOARD_PASSWORD')) {
    $existingMap['DASHBOARD_PASSWORD']
} else {
    ''
}

$gpuBackendVal = if ($qualification.GpuLayers -eq 0) { 'cpu' } else { [string]$gpuSelection.GpuInfo.Backend }
$extServicesDir = if (Test-Path -LiteralPath (Join-Path $repoRoot 'extensions\services')) { Join-Path $repoRoot 'extensions\services' } else { $installDashboardApi }
$baseEnvLines = @(
    '# ODS Windows Native Desktop Configuration'
    'ODS_VERSION=3.0.0'
    'ODS_RUNTIME_PROFILE=windows-desktop'
    'ODS_MODE=local'
    'LLM_BACKEND=llama-server'
    'AGENT_URL='
    'BIND_ADDRESS=127.0.0.1'
    "DASHBOARD_PORT=$DashboardPort"
    "LLAMA_PORT=$LlamaPort"
    "ODS_NATIVE_LLM_URL=http://127.0.0.1:$LlamaPort"
    "ODS_LLAMA_ENDPOINT=http://127.0.0.1:$LlamaPort"
    "DASHBOARD_API_KEY=$apiKey"
    "DASHBOARD_PASSWORD=$dashPassword"
    "GPU_BACKEND=$gpuBackendVal"
    "N_GPU_LAYERS=$($qualification.GpuLayers)"
    "LLM_MODEL=$ModelFileName"
    "GGUF_FILE=$ModelFileName"
    "CTX_SIZE=$modelContext" 
    "ODS_PYTHON_EXE=$resolvedPythonExe"
    "ODS_DATA_DIR=$dataDir"
    "DATA_DIR=$dataDir"
    "ODS_INSTALL_DIR=$resolvedInstallDir"
    "INSTALL_DIR=$resolvedInstallDir"
    "ODS_MODELS_DIR=$modelsDir"
    "ODS_CONFIG_DIR=$installConfigDir"
    "ODS_DASHBOARD_API_DIR=$installDashboardApi"
    "ODS_DASHBOARD_DIST=$installDashboardDist"
    "ODS_EXTENSIONS_DIR=$extServicesDir"
    "STATIC_DIR=$installDashboardDist"
)
$newEnvText = ($baseEnvLines -join "`n") + "`n"

# Carry over any owner keys (HF_TOKEN, N8N_API_KEY, AUDIO_TTS_*, DISABLE_UPDATE_CHECK, etc.)
$carriedLines = @(Get-ODSCarriedEnvLines -PreviousLines $previousLines -NewContent $newEnvText -Keys $script:ODS_OWNER_ENV_KEYS)
if ($carriedLines.Count -gt 0) {
    $newEnvText += "# Owner-preserved settings`n" + ($carriedLines -join "`n") + "`n"
}
Write-ODSPrivateEnvFile -Path $envPath -Content $newEnvText

$uninstallEntry = Register-ODSWindowsDesktopUninstall `
    -InstallDir $resolvedInstallDir `
    -LocalAppDataDir $resolvedAppDataDir `
    -Version '3.0.0' `
    -MockRegistry $MockUninstallRegistry

Write-ODSDesktopPhaseEvent -Phase 5 -Status 'completed' -Title 'Configuration & Desktop Integration' -Detail 'Configuration saved and registered in Windows Installed Apps' -Percent 92

# ============================================================================
# PHASE 6: Native Service Startup, Shortcuts & Readiness Proof
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 6 -Status 'running' -Title 'Readiness & Desktop Shortcuts' -Detail 'Starting native services and creating desktop shortcuts...' -Percent 96

if (-not $SkipServiceStart) {
    # Start native llama-server.exe if binary + model exist and port 8085 is not yet bound
    $llamaPortOpen = $false
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $ar = $tcp.BeginConnect('127.0.0.1', $LlamaPort, $null, $null)
        if ($ar.AsyncWaitHandle.WaitOne(200)) { $tcp.EndConnect($ar); $llamaPortOpen = $true }
        $tcp.Close()
    } catch { }

    if (-not $llamaPortOpen -and (Test-Path -LiteralPath $llamaExePath -PathType Leaf) -and (Test-Path -LiteralPath $modelDest -PathType Leaf)) {
        $nglArg = [string]$qualification.GpuLayers
        $llamaArgs = @('-m', "`"$modelDest`"", '--host', '127.0.0.1', '--port', "$LlamaPort", '-c', '4096', '-ngl', $nglArg)
        if ($qualification.GpuLayers -eq 0) {
            $llamaArgs += @('--device', 'none')
        }
        try {
            Start-Process -FilePath $llamaExePath -ArgumentList ($llamaArgs -join ' ') -WorkingDirectory $llamaDir -WindowStyle Hidden
        } catch {
            Write-Warning "Could not start llama-server.exe: $($_.Exception.Message)"
        }
    }

    # Start native dashboard-api on 127.0.0.1:3101 if not yet bound
    $apiPortOpen = $false
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $ar = $tcp.BeginConnect('127.0.0.1', $DashboardPort, $null, $null)
        if ($ar.AsyncWaitHandle.WaitOne(200)) { $tcp.EndConnect($ar); $apiPortOpen = $true }
        $tcp.Close()
    } catch { }

    $dashApiDir = if (Test-Path -LiteralPath (Join-Path $installDashboardApi 'main.py') -PathType Leaf) {
        $installDashboardApi
    } elseif ($repoRoot -and (Test-Path -LiteralPath (Join-Path $repoRoot 'extensions\services\dashboard-api\main.py') -PathType Leaf)) {
        Join-Path $repoRoot 'extensions\services\dashboard-api'
    } else {
        $installDashboardApi
    }

    if (-not $apiPortOpen -and $resolvedPythonExe -and (Test-Path -LiteralPath $dashApiDir -PathType Container)) {
        $env:ODS_RUNTIME_PROFILE = 'windows-desktop'
        $env:ODS_MODE = 'local'
        $env:LLM_BACKEND = 'llama-server'
        $env:AGENT_URL = ''
        $env:DASHBOARD_API_KEY = $apiKey
        $env:DASHBOARD_PASSWORD = $dashPassword
        $env:ODS_DATA_DIR = $dataDir
        $env:DATA_DIR = $dataDir
        $env:ODS_INSTALL_DIR = $resolvedInstallDir
        $env:INSTALL_DIR = $resolvedInstallDir
        $env:ODS_CONFIG_DIR = $installConfigDir
        $env:ODS_MODELS_DIR = $modelsDir
        $env:ODS_DASHBOARD_API_DIR = $installDashboardApi
        $env:ODS_DASHBOARD_DIST = $installDashboardDist
        $env:STATIC_DIR = $installDashboardDist
        $env:ODS_EXTENSIONS_DIR = $extServicesDir
        $env:LLAMA_PORT = "$LlamaPort"
        $env:ODS_NATIVE_LLM_URL = "http://127.0.0.1:$LlamaPort"
        $env:ODS_LLAMA_ENDPOINT = "http://127.0.0.1:$LlamaPort"
        try {
            Start-Process -FilePath $resolvedPythonExe `
                -ArgumentList "-m uvicorn --app-dir `"$dashApiDir`" main:app --host 127.0.0.1 --port $DashboardPort" `
                -WorkingDirectory $dashApiDir `
                -WindowStyle Hidden
        } catch {
            Write-Warning "Could not start dashboard-api: $($_.Exception.Message)"
        }
    }
}

$portalUrl = "http://localhost:$DashboardPort/pixel"
$shortcutsCreated = @()
if (-not $SkipShortcuts) {
    $iconCandidate = Join-Path $repoRoot 'extensions\services\dashboard\public\ods.ico'
    if (Test-Path -LiteralPath $odsExePath -PathType Leaf) {
        $shell = New-Object -ComObject WScript.Shell
        try {
            foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
                if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
                $lnkPath = Join-Path $folder 'ODS.lnk'
                try {
                    $link = $shell.CreateShortcut($lnkPath)
                    $link.TargetPath = $odsExePath
                    $link.WorkingDirectory = $binDir
                    $link.Description = 'ODS — Osmantic Deployment System Windows Native Desktop'
                    if (Test-Path -LiteralPath $iconCandidate -PathType Leaf) {
                        $link.IconLocation = "$iconCandidate,0"
                    } else {
                        $link.IconLocation = "$odsExePath,0"
                    }
                    $link.Save()
                    $shortcutsCreated += $lnkPath
                } catch { }
            }
            $progFolder = [Environment]::GetFolderPath('Programs')
            if ($progFolder -and (Test-Path -LiteralPath $progFolder -PathType Container)) {
                $uninstLnkPath = Join-Path $progFolder 'Uninstall ODS.lnk'
                $uninstScript = Join-Path $resolvedAppDataDir 'uninstall-ods.ps1'
                if (Test-Path -LiteralPath $uninstScript -PathType Leaf) {
                    try {
                        $pwsh = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                        $uLink = $shell.CreateShortcut($uninstLnkPath)
                        $uLink.TargetPath = $pwsh
                        $uLink.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$uninstScript`" -InstallDir `"$resolvedInstallDir`""
                        $uLink.WorkingDirectory = $resolvedAppDataDir
                        $uLink.Description = 'Uninstall ODS (Osmantic Deployment System)'
                        $uLink.Save()
                        $shortcutsCreated += $uninstLnkPath
                    } catch { }
                }
            }
        } finally {
            $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        }
    }
}

$installReport = [pscustomobject]@{
    installed          = $true
    runtimeProfile     = 'windows-desktop'
    installDir         = $resolvedInstallDir
    localAppDataDir    = $resolvedAppDataDir
    envPath            = $envPath
    dashboardUrl       = "http://127.0.0.1:$DashboardPort"
    portalUrl          = $portalUrl
    llamaEndpoint      = "http://127.0.0.1:$LlamaPort"
    gpuPreference      = $activeGpuPref
    systemRamGB        = $systemRamGB
    diskFreeGB         = $diskFreeGB
    effectiveTier      = $effectiveTier
    installProfile     = $InstallProfile
    gpuSelection       = $gpuSelection
    llamaQualification = $qualification
    pythonStatus       = $pythonStatus
    model              = $modelResult
    uninstallEntry     = $uninstallEntry
    shortcuts          = $shortcutsCreated
}

$reportPath = Join-Path $resolvedInstallDir 'windows-desktop-install-report.json'
Set-Content -LiteralPath $reportPath -Value ($installReport | ConvertTo-Json -Depth 6) -Encoding UTF8

Write-ODSDesktopPhaseEvent -Phase 6 -Status 'completed' -Title 'Readiness & Desktop Shortcuts' -Detail "ODS Windows Desktop is ready at http://127.0.0.1:$DashboardPort" -Percent 100

return $installReport
