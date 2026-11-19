# ============================================================================
# Contract Tests for Milestone PR 6:
#   - installers/windows/build-windows-exe.ps1
#   - installers/windows/native-shell/OdsDesktopShell.cs (ODS.exe)
#   - installers/windows/native-shell/OdsSetupWizard.cs (ODS-Setup.exe)
# ============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$buildScript = Join-Path $repoRoot 'installers\windows\build-windows-exe.ps1'

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

Write-Host "`n=== Test Suite: ODS Windows Native .exe Compilation & Shell Execution ===" -ForegroundColor Cyan

$tmpRoot = Join-Path ([IO.Path]::GetTempPath()) ("ods-exe-build-test-" + [Guid]::NewGuid().ToString("N"))
$outDir = Join-Path $tmpRoot 'dist'
$testRegSubKey = "Software\ODS\TestGpuPrefs_" + [Guid]::NewGuid().ToString("N")
$testRegFull = "HKCU:\$testRegSubKey"

try {
    # 1. Compile ODS.exe and ODS-Setup.exe via build-windows-exe.ps1
    $manifest = & $buildScript -OutputDir $outDir

    $odsExe = Join-Path $outDir 'ODS.exe'
    $setupExe = Join-Path $outDir 'ODS-Setup.exe'
    $manifestFile = Join-Path $outDir 'build-manifest.json'

    Assert-True (Test-Path -LiteralPath $odsExe -PathType Leaf) "build-windows-exe.ps1 compiles ODS.exe"
    Assert-True (Test-Path -LiteralPath $setupExe -PathType Leaf) "build-windows-exe.ps1 compiles ODS-Setup.exe"
    Assert-True (Test-Path -LiteralPath $manifestFile -PathType Leaf) "build-windows-exe.ps1 writes build-manifest.json"
    Assert-True ($manifest.artifacts['ODS.exe'].size -gt 10240) "ODS.exe binary size is valid (>10 KB)"
    Assert-True ($manifest.artifacts['ODS-Setup.exe'].size -gt 10240) "ODS-Setup.exe binary size is valid (>10 KB)"

    # Copy windows-desktop.ps1 next to ODS-Setup.exe so --dry-run-check locates it inside temp dir
    Copy-Item -LiteralPath (Join-Path $repoRoot 'installers\windows-desktop.ps1') -Destination (Join-Path $outDir 'windows-desktop.ps1') -Force

    # 2. Test ODS-Setup.exe CLI diagnostics
    $setupVer = (& $setupExe --version | Out-String).Trim()
    Assert-True ($setupVer -match 'ODS-Setup\.exe 3\.0\.0') "ODS-Setup.exe --version returns version string"

    $setupJsonRaw = (& $setupExe --dry-run-check | Out-String).Trim()
    $setupStatus = $setupJsonRaw | ConvertFrom-Json
    Assert-True ($setupStatus.executable -eq 'ODS-Setup.exe') "ODS-Setup.exe --dry-run-check outputs executable=ODS-Setup.exe"
    Assert-True ($setupStatus.profile -eq 'windows-desktop') "ODS-Setup.exe --dry-run-check outputs profile=windows-desktop"
    Assert-True ($setupStatus.defaultGpuPreference -eq 'high_performance') "ODS-Setup.exe defaults to high_performance GPU preference"
    Assert-True ($setupStatus.installerScriptFound -eq $true) "ODS-Setup.exe locates windows-desktop.ps1"

    # 3. Test ODS.exe default GPU-First status
    $odsVer = (& $odsExe --version | Out-String).Trim()
    Assert-True ($odsVer -match 'ODS\.exe 3\.0\.0') "ODS.exe --version returns version string"

    $status1Raw = (& $odsExe "--reg-subkey=$testRegSubKey" "--appdata-dir=$tmpRoot" --status-json | Out-String).Trim()
    $status1 = $status1Raw | ConvertFrom-Json
    Assert-True ($status1.gpuPreferenceMode -eq 'high_performance') "ODS.exe --status-json defaults to high_performance when registry unset"
    Assert-True ($status1.gpuPreferenceCode -eq 2) "ODS.exe --status-json reports gpuPreferenceCode=2 by default"
    Assert-True ($status1.preferDiscreteGpu -eq $true) "ODS.exe --status-json reports preferDiscreteGpu=true by default"

    # 4. Seed AppStatus=0; in registry and test ODS.exe --set-gpu-preference=power_saving
    New-Item -Path $testRegFull -Force | Out-Null
    $llamaKey = Join-Path $tmpRoot 'runtimes\llama\llama-server.exe'
    New-ItemProperty -LiteralPath $testRegFull -Name $llamaKey -Value 'AppStatus=0;GpuPreference=2;' -PropertyType String -Force | Out-Null

    $setPowerSaveRaw = (& $odsExe "--reg-subkey=$testRegSubKey" "--appdata-dir=$tmpRoot" --set-gpu-preference=power_saving | Out-String).Trim()
    $setPowerSave = $setPowerSaveRaw | ConvertFrom-Json
    Assert-True ($setPowerSave.ok -eq $true -and $setPowerSave.preferenceCode -eq 1) "ODS.exe --set-gpu-preference=power_saving succeeds"

    $regValAfterPowerSave = (Get-Item -LiteralPath $testRegFull).GetValue($llamaKey)
    Assert-True ($regValAfterPowerSave -eq 'AppStatus=0;GpuPreference=1;') "ODS.exe preserves AppStatus=0; while writing GpuPreference=1;"

    $status2Raw = (& $odsExe "--reg-subkey=$testRegSubKey" "--appdata-dir=$tmpRoot" --status-json | Out-String).Trim()
    $status2 = $status2Raw | ConvertFrom-Json
    Assert-True ($status2.gpuPreferenceMode -eq 'power_saving') "ODS.exe --status-json reflects power_saving mode"
    Assert-True ($status2.preferDiscreteGpu -eq $false) "ODS.exe --status-json sets preferDiscreteGpu=false in power_saving mode"

    # 5. Switch back to high_performance via ODS.exe
    $null = (& $odsExe "--reg-subkey=$testRegSubKey" "--appdata-dir=$tmpRoot" --set-gpu-preference=high_performance | Out-String)
    $regValAfterHighPerf = (Get-Item -LiteralPath $testRegFull).GetValue($llamaKey)
    Assert-True ($regValAfterHighPerf -eq 'AppStatus=0;GpuPreference=2;') "ODS.exe restores GpuPreference=2; while preserving AppStatus=0;"

    # 6. Test custom directory containing spaces and verify launch/status with services stopped
    $customInstallDir = Join-Path $tmpRoot 'Custom Install Dir With Spaces'
    $customAppDataDir = Join-Path $tmpRoot 'Custom AppData Dir With Spaces'
    New-Item -ItemType Directory -Path $customInstallDir -Force | Out-Null
    New-Item -ItemType Directory -Path $customAppDataDir -Force | Out-Null

    # Create dummy .env in custom install directory
    $customEnvPath = Join-Path $customInstallDir '.env'
    Set-Content -LiteralPath $customEnvPath -Value "ODS_CUSTOM_MARKER=space_path_verified`nPORT=3999"

    # Test ODS-Setup.exe dry-run-check with space-containing paths
    $setupCustomRaw = (& $setupExe "--install-dir=$customInstallDir" "--appdata-dir=$customAppDataDir" --dry-run-check | Out-String).Trim()
    $setupCustom = $setupCustomRaw | ConvertFrom-Json
    Assert-True ($setupCustom.defaultInstallDir -eq $customInstallDir) "ODS-Setup.exe parses custom install dir containing spaces"
    Assert-True ($setupCustom.defaultAppDataDir -eq $customAppDataDir) "ODS-Setup.exe parses custom appdata dir containing spaces"

    # Test shortcut creation with space-containing paths and verify Arguments via WScript.Shell
    $shortcutTestDir = Join-Path $tmpRoot 'Shortcut Test'
    New-Item -ItemType Directory -Path $shortcutTestDir -Force | Out-Null
    $testLnkPath = Join-Path $shortcutTestDir 'ODS.lnk'
    $testUninstLnkPath = Join-Path $shortcutTestDir 'Uninstall ODS.lnk'

    $shellObj = New-Object -ComObject WScript.Shell
    try {
        $link = $shellObj.CreateShortcut($testLnkPath)
        $link.TargetPath = $odsExe
        $link.Arguments = "--install-dir=`"$customInstallDir`" --appdata-dir=`"$customAppDataDir`""
        $link.Save()

        $uLink = $shellObj.CreateShortcut($testUninstLnkPath)
        $uLink.TargetPath = "powershell.exe"
        $uLink.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"uninstall-ods.ps1`" -InstallDir `"$customInstallDir`" -LocalAppDataDir `"$customAppDataDir`""
        $uLink.Save()

        # Read back shortcuts
        $readLink = $shellObj.CreateShortcut($testLnkPath)
        Assert-True ($readLink.TargetPath -eq $odsExe) "ODS shortcut TargetPath matches ODS.exe"
        Assert-True ($readLink.Arguments -match [regex]::Escape($customInstallDir)) "ODS shortcut Arguments includes custom install dir with spaces"
        Assert-True ($readLink.Arguments -match [regex]::Escape($customAppDataDir)) "ODS shortcut Arguments includes custom appdata dir with spaces"

        $readUninstLink = $shellObj.CreateShortcut($testUninstLnkPath)
        Assert-True ($readUninstLink.Arguments -match [regex]::Escape($customInstallDir)) "Uninstall shortcut Arguments includes custom install dir with spaces"
        Assert-True ($readUninstLink.Arguments -match [regex]::Escape($customAppDataDir)) "Uninstall shortcut Arguments includes custom appdata dir with spaces"

        # 7. Test nondefault port configuration derived from .env and CLI arguments
        $portInstallDir = Join-Path $tmpRoot 'Custom Port Install'
        New-Item -ItemType Directory -Path $portInstallDir -Force | Out-Null
        $portEnvPath = Join-Path $portInstallDir '.env'
        Set-Content -LiteralPath $portEnvPath -Value "DASHBOARD_PORT=3901`nLLAMA_PORT=8985`nODS_NATIVE_LLM_URL=http://127.0.0.1:8985`nODS_LLAMA_ENDPOINT=http://127.0.0.1:8985"

        $portStatusRaw = (& $odsExe "--install-dir=$portInstallDir" "--appdata-dir=$customAppDataDir" --status-json | Out-String).Trim()
        $portStatus = $portStatusRaw | ConvertFrom-Json
        Assert-True ($portStatus.dashboardPort -eq 3901) "ODS.exe --status-json derives custom dashboardPort (3901) from .env"
        Assert-True ($portStatus.llamaPort -eq 8985) "ODS.exe --status-json derives custom llamaPort (8985) from .env"
        Assert-True ($portStatus.portalUrl -eq 'http://localhost:3901/pixel') "ODS.exe --status-json computes portalUrl from custom dashboard port"
        Assert-True ($portStatus.dashboardApiListening -eq $false) "Custom port 3901 is not running while services stopped"
        Assert-True ($portStatus.llamaServerListening -eq $false) "Custom port 8985 is not running while services stopped"

        # Test CLI argument port overrides
        $overrideStatusRaw = (& $odsExe "--install-dir=$portInstallDir" "--appdata-dir=$customAppDataDir" "--dashboard-port=4101" "--llama-port=9085" --status-json | Out-String).Trim()
        $overrideStatus = $overrideStatusRaw | ConvertFrom-Json
        Assert-True ($overrideStatus.dashboardPort -eq 4101) "ODS.exe honors --dashboard-port=4101 CLI override"
        Assert-True ($overrideStatus.llamaPort -eq 9085) "ODS.exe honors --llama-port=9085 CLI override"
        Assert-True ($overrideStatus.portalUrl -eq 'http://localhost:4101/pixel') "ODS.exe computes portalUrl from CLI override port"

        # Test shortcut generation with nondefault ports
        $customPortLinkPath = Join-Path $shortcutTestDir 'ODS-CustomPort.lnk'
        $cpLink = $shellObj.CreateShortcut($customPortLinkPath)
        $cpLink.TargetPath = $odsExe
        $cpLink.Arguments = "--install-dir=`"$portInstallDir`" --appdata-dir=`"$customAppDataDir`" --dashboard-port=3901 --llama-port=8985"
        $cpLink.Save()

        $readCpLink = $shellObj.CreateShortcut($customPortLinkPath)
        Assert-True ($readCpLink.Arguments -match '--dashboard-port=3901') "Shortcut Arguments include --dashboard-port=3901"
        Assert-True ($readCpLink.Arguments -match '--llama-port=8985') "Shortcut Arguments include --llama-port=8985"
    } finally {
        $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shellObj)
    }

    # Verify ODS.exe --status-json with custom paths containing spaces when native services are stopped
    $customStatusRaw = (& $odsExe "--install-dir=$customInstallDir" "--appdata-dir=$customAppDataDir" --status-json | Out-String).Trim()
    $customStatus = $customStatusRaw | ConvertFrom-Json
    Assert-True ($customStatus.installDir -eq $customInstallDir) "ODS.exe --status-json reports custom install directory with spaces"
    Assert-True ($customStatus.appDataDir -eq $customAppDataDir) "ODS.exe --status-json reports custom appdata directory with spaces"
    Assert-True ($customStatus.envFileFound -eq $true) "ODS.exe locates .env inside custom install directory with spaces"
    Assert-True ($customStatus.dashboardApiListening -eq $false) "Port 3101 is not running, ensuring daemon does not mask custom directory resolution"
    Assert-True ($customStatus.llamaServerListening -eq $false) "Port 8085 is not running, ensuring llama service does not mask custom directory resolution"
} finally {
    if (Test-Path -LiteralPath $testRegFull) {
        Remove-Item -LiteralPath $testRegFull -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tmpRoot) {
        Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`n=== Summary: $passed passed, $failed failed ===" -ForegroundColor Cyan
if ($failed -gt 0) { exit 1 }
