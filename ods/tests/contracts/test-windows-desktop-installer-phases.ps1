# ============================================================================
# Contract Tests for Milestone PR 5:
#   - installers/windows-desktop.ps1 (6-phase native Windows installer)
#   - installers/windows/lib/desktop-uninstall.ps1 (Installed Apps + Uninstall)
# ============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installerScript = Join-Path $repoRoot 'installers\windows-desktop.ps1'
. (Join-Path $repoRoot 'installers\windows\lib\desktop-uninstall.ps1')

$passed = 0
$failed = 0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) {
        $script:passed++
        Write-Host "  [PASS] $Message" -ForegroundColor Green
    } else {
        $script:failed++
        Write-Host "  [FAIL] $Message" -ForegroundColor Red
    }
}

Write-Host "`n=== Test Suite: ODS Windows Desktop 6-Phase Installer & Uninstaller ===" -ForegroundColor Cyan

$tmpRoot = Join-Path ([IO.Path]::GetTempPath()) ("ods-desktop-installer-test-" + [Guid]::NewGuid().ToString("N"))
$installDir = Join-Path $tmpRoot 'ods-home'
$appDataDir = Join-Path $tmpRoot 'ods-appdata'
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
New-Item -ItemType Directory -Path $appDataDir -Force | Out-Null

try {
    # Pre-seed owner settings in .env to prove rerun preservation
    $envPath = Join-Path $installDir '.env'
    Set-Content -LiteralPath $envPath -Value @(
        'DASHBOARD_PASSWORD=my-custom-owner-password'
        'DASHBOARD_API_KEY=mock-owner-key'
        'HF_TOKEN=hf_secret_owner_token'
        'DISABLE_UPDATE_CHECK=true'
    ) -Encoding UTF8

    $mockGpus = @(
        [pscustomobject]@{ Name = 'Intel(R) UHD Graphics'; Backend = 'intel'; VramMB = 2048; DriverVersion = '31.0.101'; IsDiscrete = $false },
        [pscustomobject]@{ Name = 'NVIDIA GeForce GTX 1650'; Backend = 'nvidia'; VramMB = 4096; DriverVersion = '560.94'; IsDiscrete = $true }
    )
    $mockGpuReg = [ordered]@{}
    $mockUninstallReg = @{}

    # 1. Run full 6-phase installer (Run 1 - GPU-First Default)
    $report1 = & $installerScript `
        -InstallDir $installDir `
        -LocalAppDataDir $appDataDir `
        -MockGpus $mockGpus `
        -MockGpuRegistry $mockGpuReg `
        -MockUninstallRegistry $mockUninstallReg `
        -SkipNetworkDownloads `
        -SkipServiceStart `
        -SkipShortcuts

    Assert-True ($report1.installed -eq $true) "6-phase installer completes and returns installed=true"
    Assert-True ($report1.runtimeProfile -eq 'windows-desktop') "Installer sets runtimeProfile='windows-desktop'"
    Assert-True ($report1.gpuSelection.GpuInfo.Name -eq 'NVIDIA GeForce GTX 1650') "Phase 1 selects discrete GPU (GTX 1650) by default"
    Assert-True ($report1.llamaQualification.Backend -eq 'cuda') "Phase 3 qualifies CUDA runtime for discrete NVIDIA GPU"
    Assert-True ($report1.pythonStatus.Version -eq '3.12.10') "Phase 2 validates pinned embeddable Python 3.12.10 layout"
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'windows-desktop-install-report.json')) "Phase 6 writes windows-desktop-install-report.json"

    # 2. Verify owner .env settings were preserved
    $envContent = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8
    Assert-True ($envContent -match 'ODS_RUNTIME_PROFILE=windows-desktop') ".env writes ODS_RUNTIME_PROFILE=windows-desktop"
    Assert-True ($envContent -match 'DASHBOARD_PASSWORD=my-custom-owner-password') ".env preserves owner DASHBOARD_PASSWORD across install"
    Assert-True ($envContent -match 'DASHBOARD_API_KEY=mock-owner-key') ".env preserves owner DASHBOARD_API_KEY across install"
    Assert-True ($envContent -match 'HF_TOKEN=hf_secret_owner_token') ".env carries over owner HF_TOKEN"
    Assert-True ($envContent -match 'DISABLE_UPDATE_CHECK=true') ".env carries over owner DISABLE_UPDATE_CHECK"

    # 3. Verify Windows Installed Apps registration
    Assert-True ($mockUninstallReg['DisplayName'] -eq 'ODS (Osmantic Deployment System)') "Phase 5 registers DisplayName in Windows Uninstall registry"
    Assert-True ($mockUninstallReg['Publisher'] -eq 'Osmantic Deployment System') "Phase 5 registers Publisher in Windows Uninstall registry"

    # 4. Simulate user switching llama-server.exe to Power Saving (GpuPreference=1) in Windows Graphics Settings
    $llamaExeKey = [IO.Path]::GetFullPath((Join-Path $appDataDir 'runtimes\llama\llama-server.exe'))
    $mockGpuReg[$llamaExeKey] = 'AppStatus=0;GpuPreference=1;'

    $report2 = & $installerScript `
        -InstallDir $installDir `
        -LocalAppDataDir $appDataDir `
        -MockGpus $mockGpus `
        -MockGpuRegistry $mockGpuReg `
        -MockUninstallRegistry $mockUninstallReg `
        -SkipNetworkDownloads `
        -SkipServiceStart `
        -SkipShortcuts

    Assert-True ($report2.gpuPreference.PreferenceCode -eq 1) "Rerun preserves user's Windows Graphics Settings Power Saving override (GpuPreference=1)"
    Assert-True ($report2.gpuSelection.GpuInfo.Name -eq 'Intel(R) UHD Graphics') "Power Saving override routes ODS to integrated GPU instead of discrete GPU"
    Assert-True ($report2.model.FromStamp -eq $true) "Phase 4 model verification hits fast sidecar cache on rerun"

    # 5. Test Uninstall with -KeepData (default)
    $uninstKeep = Uninstall-ODSWindowsDesktop `
        -InstallDir $installDir `
        -LocalAppDataDir $appDataDir `
        -KeepData `
        -MockRegistry $mockUninstallReg

    Assert-True ($uninstKeep.uninstalled -eq $true) "Uninstall-ODSWindowsDesktop -KeepData succeeds"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $appDataDir 'python'))) "Uninstall removes python runtime directory"
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir '.env')) "Uninstall -KeepData preserves owner .env"
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'models')) "Uninstall -KeepData preserves downloaded GGUF models"
    Assert-True ($mockUninstallReg.Count -eq 0) "Uninstall removes Windows Installed Apps registry key"

    # 6. Test Uninstall with -RemoveAllData
    $uninstAll = Uninstall-ODSWindowsDesktop `
        -InstallDir $installDir `
        -LocalAppDataDir $appDataDir `
        -RemoveAllData `
        -MockRegistry $mockUninstallReg

    Assert-True (-not (Test-Path -LiteralPath $installDir)) "Uninstall -RemoveAllData removes InstallDir completely"
    Assert-True (-not (Test-Path -LiteralPath $appDataDir)) "Uninstall -RemoveAllData removes LocalAppDataDir completely"
} finally {
    if (Test-Path -LiteralPath $tmpRoot) {
        Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`n=== Summary: $passed passed, $failed failed ===" -ForegroundColor Cyan
if ($failed -gt 0) { exit 1 }
