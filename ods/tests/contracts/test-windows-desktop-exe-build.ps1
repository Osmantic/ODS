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
