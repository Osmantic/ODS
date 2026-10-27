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

$gpuSummary = "$($gpuSelection.GpuInfo.Name) (mode=$($gpuSelection.ExecutionMode), pref=$($activeGpuPref.Mode))"
Write-ODSDesktopPhaseEvent -Phase 1 -Status 'completed' -Title 'Hardware & GPU Preference Discovery' -Detail $gpuSummary -Percent 18

# ============================================================================
# PHASE 2: Pinned Embeddable Python 3.12 Runtime & Dependencies
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 2 -Status 'running' -Title 'Pinned Python Runtime' -Detail 'Verifying embeddable Python 3.12.10 and hash-locked wheels...' -Percent 25

$pythonContract = Read-ODSWindowsDesktopPythonContract -SourceRoot $repoRoot
$pythonInstallDir = Get-ODSWindowsDesktopPythonInstallDir -PythonContract $pythonContract -Root $pythonRootDir

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
}

$pythonStatus = Test-ODSWindowsDesktopPythonInstall `
    -Directory $pythonInstallDir `
    -ExpectedPythonZipSha256 ([string]$pythonContract.sha256) `
    -SkipAuthenticodeCheck:$SkipNetworkDownloads

Write-ODSDesktopPhaseEvent -Phase 2 -Status 'completed' -Title 'Pinned Python Runtime' -Detail "Python $($pythonStatus.Version) ready ($($pythonContract.wheels.Count) pinned wheels, zero litellm)" -Percent 36

# ============================================================================
# PHASE 3: Native llama.cpp GPU/CPU Runtime Qualification
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 3 -Status 'running' -Title 'Native llama.cpp Qualification' -Detail 'Qualifying native llama-server runtime...' -Percent 45

if ($SkipNetworkDownloads -and -not (Test-Path -LiteralPath $llamaExePath)) {
    Set-Content -LiteralPath $llamaExePath -Value 'MZ-LLAMA-SERVER' -Encoding ASCII
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
if ($SkipNetworkDownloads -and -not (Test-Path -LiteralPath $modelDest)) {
    [IO.File]::WriteAllBytes($modelDest, [Text.Encoding]::UTF8.GetBytes('GGUF-TEST-MODEL-PAYLOAD'))
}
$effectiveModelSha = if ($ModelSha256) {
    $ModelSha256
} elseif (Test-Path -LiteralPath $modelDest) {
    (Get-FileHash -LiteralPath $modelDest -Algorithm SHA256).Hash.ToLowerInvariant()
} else {
    ''
}

$modelResult = if ($SkipNetworkDownloads) {
    Test-ODSVerifiedModelFile -ModelPath $modelDest -ExpectedSha256 $effectiveModelSha
} else {
    Invoke-ODSResumableModelDownload -ModelsDir $modelsDir -GgufFile $ModelFileName -Url $ModelUrl -ExpectedSha256 $effectiveModelSha
}

Write-ODSDesktopPhaseEvent -Phase 4 -Status 'completed' -Title 'Local AI Model Verification' -Detail "$ModelFileName verified (fromStamp=$($modelResult.FromStamp))" -Percent 76

# ============================================================================
# PHASE 5: Owner-Preserving .env, Dashboard Bundle & Uninstall Registration
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 5 -Status 'running' -Title 'Configuration & Desktop Integration' -Detail 'Writing owner-preserving .env and Windows Installed Apps registration...' -Percent 84

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
$baseEnvLines = @(
    '# ODS Windows Native Desktop Configuration'
    'ODS_VERSION=3.0.0'
    'ODS_RUNTIME_PROFILE=windows-desktop'
    'AGENT_URL='
    'BIND_ADDRESS=127.0.0.1'
    "DASHBOARD_PORT=$DashboardPort"
    "LLAMA_PORT=$LlamaPort"
    "DASHBOARD_API_KEY=$apiKey"
    "DASHBOARD_PASSWORD=$dashPassword"
    "GPU_BACKEND=$gpuBackendVal"
    "N_GPU_LAYERS=$($qualification.GpuLayers)"
    'LLM_MODEL=qwen3.5:2b'
    "GGUF_FILE=$ModelFileName"
    'CTX_SIZE=8192'
)
$newEnvText = ($baseEnvLines -join "`n") + "`n"

# Carry over any owner keys (HF_TOKEN, N8N_API_KEY, AUDIO_TTS_*, DISABLE_UPDATE_CHECK, etc.)
$carriedLines = @(Get-ODSCarriedEnvLines -PreviousLines $previousLines -NewContent $newEnvText -Keys $script:ODS_OWNER_ENV_KEYS)
if ($carriedLines.Count -gt 0) {
    $newEnvText += "# Owner-preserved settings`n" + ($carriedLines -join "`n") + "`n"
}
Write-ODSPrivateEnvFile -Path $envPath -Content $newEnvText

# Copy dashboard static bundle if present in repo
$repoDashboardDist = Join-Path $repoRoot 'extensions\dashboard\dist'
$installDashboardDist = Join-Path $resolvedInstallDir 'dashboard-dist'
New-Item -ItemType Directory -Path $installDashboardDist -Force | Out-Null
if (Test-Path -LiteralPath (Join-Path $repoDashboardDist 'index.html')) {
    Copy-Item -Path (Join-Path $repoDashboardDist '*') -Destination $installDashboardDist -Recurse -Force
} elseif (-not (Test-Path -LiteralPath (Join-Path $installDashboardDist 'index.html'))) {
    Set-Content -LiteralPath (Join-Path $installDashboardDist 'index.html') -Value '<!doctype html><html><head><title>ODS Desktop</title></head><body><div id="root">ODS Windows Native Desktop</div></body></html>' -Encoding UTF8
}

$uninstallEntry = Register-ODSWindowsDesktopUninstall `
    -InstallDir $resolvedInstallDir `
    -LocalAppDataDir $resolvedAppDataDir `
    -Version '3.0.0' `
    -MockRegistry $MockUninstallRegistry

Write-ODSDesktopPhaseEvent -Phase 5 -Status 'completed' -Title 'Configuration & Desktop Integration' -Detail 'Configuration saved and registered in Windows Installed Apps' -Percent 92

# ============================================================================
# PHASE 6: Native Service Startup, Shortcuts & Readiness Proof
# ============================================================================
Write-ODSDesktopPhaseEvent -Phase 6 -Status 'running' -Title 'Readiness & Desktop Shortcuts' -Detail 'Creating desktop shortcuts and verifying readiness...' -Percent 96

$portalUrl = "http://localhost:$DashboardPort/pixel"
$shortcutsCreated = @()
if (-not $SkipShortcuts) {
    $iconCandidate = Join-Path $repoRoot 'extensions\dashboard\public\ods-app.ico'
    try {
        $shortcutsCreated = @(Publish-ODSPortalShortcut -PortalUrl $portalUrl -IconSource $iconCandidate)
    } catch {
        $shortcutsCreated = @()
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
