# ============================================================================
# ODS Windows Native Desktop -- Installed Apps Registration & Clean Uninstaller
# ============================================================================
# Registers ODS in HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ODS
# so it appears in Windows Settings > Apps > Installed Apps like a first-class
# desktop application, and provides clean uninstallation with optional user
# data preservation (-KeepData vs -RemoveAllData).
# ============================================================================

Set-StrictMode -Version Latest

$script:ODSDesktopUninstallRegistryPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ODS'

function Register-ODSWindowsDesktopUninstall {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,

        [string]$LocalAppDataDir = (Join-Path $env:LOCALAPPDATA 'ODS'),

        [string]$Version = '3.0.0',

        [string]$DisplayIcon = '',

        [string]$RegistryPath = $script:ODSDesktopUninstallRegistryPath,

        [hashtable]$MockRegistry = $null
    )

    $resolvedInstall = [IO.Path]::GetFullPath($InstallDir)
    $resolvedAppData = [IO.Path]::GetFullPath($LocalAppDataDir)
    $uninstallScript = Join-Path $resolvedAppData 'uninstall-ods.ps1'
    if (-not $DisplayIcon) {
        $DisplayIcon = Join-Path $resolvedAppData 'ods-app.ico'
    }

    $pwshExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $uninstallCmd = "`"$pwshExe`" -NoProfile -ExecutionPolicy Bypass -File `"$uninstallScript`" -InstallDir `"$resolvedInstall`""
    $quietUninstallCmd = "$uninstallCmd -KeepData -Quiet"

    $entries = [ordered]@{
        DisplayName          = 'ODS (Open Dev Society)'
        DisplayVersion       = $Version.TrimStart('v')
        Publisher            = 'Open Dev Society'
        InstallLocation      = $resolvedInstall
        DisplayIcon          = $DisplayIcon
        UninstallString      = $uninstallCmd
        QuietUninstallString = $quietUninstallCmd
        URLInfoAbout         = 'https://open-dev-society.org'
        NoModify             = 1
        NoRepair             = 1
        InstallDate          = (Get-Date).ToString('yyyyMMdd')
    }

    if ($null -ne $MockRegistry) {
        foreach ($k in $entries.Keys) {
            $MockRegistry[$k] = $entries[$k]
        }
        return [pscustomobject]$entries
    }

    if (-not (Test-Path -LiteralPath $RegistryPath)) {
        New-Item -Path $RegistryPath -Force | Out-Null
    }
    foreach ($k in $entries.Keys) {
        $val = $entries[$k]
        $propType = if ($val -is [int]) { 'DWord' } else { 'String' }
        New-ItemProperty -LiteralPath $RegistryPath -Name $k -Value $val -PropertyType $propType -Force | Out-Null
    }

    return [pscustomobject]$entries
}

function Uninstall-ODSWindowsDesktop {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,

        [string]$LocalAppDataDir = (Join-Path $env:LOCALAPPDATA 'ODS'),

        [switch]$RemoveAllData,

        [switch]$KeepData,

        [string]$RegistryPath = $script:ODSDesktopUninstallRegistryPath,

        [hashtable]$MockRegistry = $null
    )

    $preserveUserData = if ($RemoveAllData) { $false } else { $true }
    $removedPaths = New-Object 'System.Collections.Generic.List[string]'
    $preservedPaths = New-Object 'System.Collections.Generic.List[string]'

    # 1. Remove runtime binaries & python while preserving models/.env/conversations when KeepData is active
    $runtimeDirs = @(
        (Join-Path $LocalAppDataDir 'python'),
        (Join-Path $LocalAppDataDir 'runtimes'),
        (Join-Path $LocalAppDataDir 'app'),
        (Join-Path $LocalAppDataDir 'updates'),
        (Join-Path $InstallDir 'python'),
        (Join-Path $InstallDir 'runtimes'),
        (Join-Path $InstallDir 'app'),
        (Join-Path $InstallDir 'dashboard-dist')
    )

    foreach ($dir in $runtimeDirs) {
        if (Test-Path -LiteralPath $dir) {
            Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
            [void]$removedPaths.Add($dir)
        }
    }

    if ($preserveUserData) {
        foreach ($keepCandidate in @(
            (Join-Path $InstallDir '.env'),
            (Join-Path $InstallDir 'models'),
            (Join-Path $InstallDir 'conversations'),
            (Join-Path $InstallDir 'workspace')
        )) {
            if (Test-Path -LiteralPath $keepCandidate) {
                [void]$preservedPaths.Add($keepCandidate)
            }
        }
    } else {
        foreach ($fullRoot in @($InstallDir, $LocalAppDataDir)) {
            if ($fullRoot -and (Test-Path -LiteralPath $fullRoot)) {
                Remove-Item -LiteralPath $fullRoot -Recurse -Force -ErrorAction SilentlyContinue
                [void]$removedPaths.Add($fullRoot)
            }
        }
    }

    # 2. Remove Uninstall registry key
    if ($null -ne $MockRegistry) {
        $MockRegistry.Clear()
    } elseif (Test-Path -LiteralPath $RegistryPath) {
        Remove-Item -LiteralPath $RegistryPath -Recurse -Force -ErrorAction SilentlyContinue
    }

    return [pscustomobject]@{
        uninstalled    = $true
        keepData       = $preserveUserData
        removedPaths   = @($removedPaths)
        preservedPaths = @($preservedPaths)
    }
}
