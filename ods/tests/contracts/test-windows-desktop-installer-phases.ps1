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
        'DASHBOARD_API_KEY=fixture_owner_key_for_test'
        'HF_TOKEN=fixture_hf_token_for_test'
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
    Assert-True ($envContent -match 'DASHBOARD_API_KEY=fixture_owner_key_for_test') ".env preserves owner DASHBOARD_API_KEY across install"
    Assert-True ($envContent -match 'HF_TOKEN=fixture_hf_token_for_test') ".env carries over owner HF_TOKEN"
    Assert-True ($envContent -match 'DISABLE_UPDATE_CHECK=true') ".env carries over owner DISABLE_UPDATE_CHECK"

    # 3. Verify Windows Installed Apps registration
    Assert-True ($mockUninstallReg['DisplayName'] -eq 'ODS (Osmantic Deployment System)') "Phase 5 registers DisplayName in Windows Uninstall registry"
    Assert-True ($mockUninstallReg['Publisher'] -eq 'Osmantic Deployment System') "Phase 5 registers Publisher in Windows Uninstall registry"
    Assert-True ($mockUninstallReg['UninstallString'] -match ('-LocalAppDataDir\s+"' + [regex]::Escape($appDataDir) + '"')) "Phase 5 registers UninstallString with quoted custom LocalAppDataDir"
    Assert-True ($mockUninstallReg['QuietUninstallString'] -match ('-LocalAppDataDir\s+"' + [regex]::Escape($appDataDir) + '"')) "Phase 5 registers QuietUninstallString with quoted custom LocalAppDataDir"

    # Test library registration with distinct default vs custom roots
    $libScript = Join-Path (Join-Path (Join-Path $PSScriptRoot '..') '..') 'installers\windows\lib\desktop-uninstall.ps1'
    . $libScript
    $mockCustomReg = @{}
    Register-ODSWindowsDesktopUninstall -InstallDir 'C:\custom\ods' -LocalAppDataDir 'D:\custom\appdata' -MockRegistry $mockCustomReg | Out-Null
    Assert-True ($mockCustomReg['UninstallString'] -match '-InstallDir\s+"C:\\custom\\ods"') "Register-ODSWindowsDesktopUninstall includes quoted custom InstallDir"
    Assert-True ($mockCustomReg['UninstallString'] -match '-LocalAppDataDir\s+"D:\\custom\\appdata"') "Register-ODSWindowsDesktopUninstall includes quoted custom LocalAppDataDir"

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

    # 5. Test standalone uninstall-ods.ps1 entrypoint preserves data by default without -KeepData
    $uninstallerScript = Join-Path (Join-Path (Join-Path $PSScriptRoot '..') '..') 'installers\windows\uninstall-ods.ps1'
    $testModelDir = Join-Path $installDir 'models'
    New-Item -ItemType Directory -Path $testModelDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $testModelDir 'test.gguf') -Value 'model-bytes'
    Set-Content -LiteralPath (Join-Path $installDir '.env') -Value 'ODS_VERSION=3.0.0'
    New-Item -ItemType Directory -Path (Join-Path $appDataDir 'python') -Force | Out-Null

    # Pre-populate DirectX UserGpuPreferences with both an ODS path and an unrelated application path
    $dxKey = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
    $odsDxVal = Join-Path $appDataDir 'runtimes\llama\llama-server.exe'
    $foreignDxVal = 'C:\unrelated-app\llama-server.exe'
    if (-not (Test-Path -LiteralPath $dxKey)) {
        New-Item -Path $dxKey -Force | Out-Null
    }
    Set-ItemProperty -LiteralPath $dxKey -Name $odsDxVal -Value 'GpuPreference=2;' -Force
    Set-ItemProperty -LiteralPath $dxKey -Name $foreignDxVal -Value 'GpuPreference=1;' -Force

    # Invoke standalone uninstaller without -KeepData
    & $uninstallerScript -InstallDir $installDir -LocalAppDataDir $appDataDir -Quiet

    Assert-True (Test-Path -LiteralPath (Join-Path $installDir 'models\test.gguf')) "Standalone uninstaller preserves user models by default"
    Assert-True (Test-Path -LiteralPath (Join-Path $installDir '.env')) "Standalone uninstaller preserves user .env by default"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $appDataDir 'python'))) "Standalone uninstaller removes runtime python directory"

    # Verify ODS GPU preference was cleaned up, while unrelated app GPU preference survived
    $dxValNames = (Get-Item -LiteralPath $dxKey).GetValueNames()
    Assert-True ($dxValNames -notcontains $odsDxVal) "Standalone uninstaller cleans up ODS DirectX GPU preference"
    Assert-True ($dxValNames -contains $foreignDxVal) "Standalone uninstaller preserves unrelated app DirectX GPU preference"

    Remove-ItemProperty -LiteralPath $dxKey -Name $foreignDxVal -Force -ErrorAction SilentlyContinue

    # 6. Test process ownership filtering: stale PID and foreign executables survive
    $mockPid = 99991
    $foreignPid = 99992
    $foreignLlamaPid = 99993
    $ownedPid = 99994

    Set-Content -LiteralPath (Join-Path $appDataDir '.llama-server.pid') -Value "$foreignPid"

    $fakeProcesses = @(
        [pscustomobject]@{ Id = $ownedPid; Path = (Join-Path $installDir 'ODS.exe'); ProcessName = 'ODS' },
        [pscustomobject]@{ Id = $foreignPid; Path = 'C:\Windows\System32\notepad.exe'; ProcessName = 'notepad' },
        [pscustomobject]@{ Id = $foreignLlamaPid; Path = 'C:\other-app\llama-server.exe'; ProcessName = 'llama-server' }
    )

    $stopped = [System.Collections.Generic.List[int]]::new()
    $testManagedPids = @()
    foreach ($base in @($installDir, $appDataDir)) {
        $pfile = Join-Path $base '.llama-server.pid'
        if (Test-Path -LiteralPath $pfile) {
            $c = (Get-Content -LiteralPath $pfile -Raw).Trim()
            if ($c -match '^\d+$') { $testManagedPids += [int]$c }
        }
    }
    foreach ($p in $fakeProcesses) {
        $isManaged = $p.Id -in $testManagedPids
        $pPath = $p.Path
        $belongs = $false
        if ($pPath) {
            $normP = [System.IO.Path]::GetFullPath($pPath)
            if ($installDir -and ($normP.StartsWith("$installDir\", [System.StringComparison]::OrdinalIgnoreCase) -or $normP.Equals($installDir, [System.StringComparison]::OrdinalIgnoreCase))) {
                $belongs = $true
            }
            if ($appDataDir -and ($normP.StartsWith("$appDataDir\", [System.StringComparison]::OrdinalIgnoreCase) -or $normP.Equals($appDataDir, [System.StringComparison]::OrdinalIgnoreCase))) {
                $belongs = $true
            }
        }
        if ($belongs -and ($isManaged -or $p.ProcessName -in @('ODS', 'llama-server', 'python', 'pythonw'))) {
            $stopped.Add($p.Id)
        }
    }
    Assert-True ($stopped -contains $ownedPid) "Process loop terminates owned ODS.exe"
    Assert-True ($stopped -notcontains $foreignPid) "Process loop does not terminate foreign process with stale recycled PID"
    Assert-True ($stopped -notcontains $foreignLlamaPid) "Process loop does not terminate unrelated llama-server outside install directories"

    # 7. Test standalone uninstaller with -RemoveAllData
    & $uninstallerScript -InstallDir $installDir -LocalAppDataDir $appDataDir -RemoveAllData -Quiet

    Assert-True (-not (Test-Path -LiteralPath $installDir)) "Standalone uninstaller -RemoveAllData removes installDir completely"
} finally {
    if (Test-Path -LiteralPath $tmpRoot) {
        Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`n=== Summary: $passed passed, $failed failed ===" -ForegroundColor Cyan
if ($failed -gt 0) { exit 1 }
