<#
.SYNOPSIS
    Standalone clean uninstaller for ODS (Osmantic Deployment System) Windows Desktop.
.DESCRIPTION
    Stops all running ODS processes (ODS.exe, llama-server.exe, python.exe),
    removes shortcuts, cleans Windows Installed Apps registration, and completely
    removes the installation directory, runtime binaries, and user data.
#>
param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'ods'),
    [string]$LocalAppDataDir = (Join-Path $env:LOCALAPPDATA 'ODS'),
    [switch]$KeepData = $false,
    [switch]$Quiet = $false
)

$ErrorActionPreference = 'SilentlyContinue'

$resolvedInstall = if ($InstallDir) { [System.IO.Path]::GetFullPath($InstallDir) } else { '' }
$resolvedAppData = if ($LocalAppDataDir) { [System.IO.Path]::GetFullPath($LocalAppDataDir) } else { '' }

if (-not $Quiet) {
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  ODS (Osmantic Deployment System) — Windows Uninstaller    " -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "Installation directory: $resolvedInstall"
    Write-Host "App data directory:     $resolvedAppData"
    Write-Host "Keep user data:         $KeepData`n"

    $confirmation = Read-Host "Are you sure you want to completely uninstall ODS? (Y/N)"
    if ($confirmation -notmatch '^(y|yes)$') {
        Write-Host "Uninstallation cancelled." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host "Stopping any running ODS processes..." -ForegroundColor Yellow
$procNames = @('ODS', 'ODS.exe', 'llama-server', 'llama-server.exe')
foreach ($p in Get-Process -ErrorAction SilentlyContinue) {
    try {
        if ($p.ProcessName -in @('ODS', 'llama-server')) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
        elseif ($p.ProcessName -like 'python*' -and $resolvedInstall -and $p.Path -and ($p.Path.StartsWith($resolvedInstall, [System.StringComparison]::OrdinalIgnoreCase) -or $p.Path.StartsWith($resolvedAppData, [System.StringComparison]::OrdinalIgnoreCase))) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    } catch { }
}
Start-Sleep -Milliseconds 600

Write-Host "Removing Desktop and Start Menu shortcuts..." -ForegroundColor Yellow
$shortcutFolders = @(
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('Programs'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'ODS')
)
foreach ($f in $shortcutFolders) {
    if ($f -and (Test-Path -LiteralPath $f)) {
        Remove-Item -Path (Join-Path $f 'ODS.lnk') -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $f 'ODS Portal.lnk') -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $f 'Uninstall ODS.lnk') -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Removing Windows Installed Apps registration..." -ForegroundColor Yellow
$regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ODS'
if (Test-Path -LiteralPath $regPath) {
    Remove-Item -LiteralPath $regPath -Recurse -Force -ErrorAction SilentlyContinue
}

# Remove DirectX UserGpuPreferences entries
try {
    $dxKey = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
    if (Test-Path -LiteralPath $dxKey) {
        $key = Get-Item -LiteralPath $dxKey
        foreach ($valName in $key.GetValueNames()) {
            if ($valName -like "*\ODS.exe" -or $valName -like "*\llama-server.exe") {
                Remove-ItemProperty -LiteralPath $dxKey -Name $valName -Force -ErrorAction SilentlyContinue
            }
        }
    }
} catch { }

if ($KeepData) {
    Write-Host "Removing application runtimes and keeping user data..." -ForegroundColor Yellow
    $runtimeDirs = @(
        (Join-Path $resolvedAppData 'python'),
        (Join-Path $resolvedAppData 'runtimes'),
        (Join-Path $resolvedAppData 'app'),
        (Join-Path $resolvedAppData 'updates'),
        (Join-Path $resolvedAppData 'webview2-profile'),
        (Join-Path $resolvedInstall 'python'),
        (Join-Path $resolvedInstall 'runtimes'),
        (Join-Path $resolvedInstall 'dashboard-api'),
        (Join-Path $resolvedInstall 'dashboard-dist')
    )
    foreach ($d in $runtimeDirs) {
        if ($d -and (Test-Path -LiteralPath $d)) {
            Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "User data preserved in: $resolvedInstall (models, workspace, chats)" -ForegroundColor Green
} else {
    Write-Host "Completely removing ODS and all user data..." -ForegroundColor Yellow
    if ($resolvedInstall -and (Test-Path -LiteralPath $resolvedInstall)) {
        Remove-Item -LiteralPath $resolvedInstall -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($resolvedAppData -and (Test-Path -LiteralPath $resolvedAppData)) {
        # Delete child items in AppData, avoiding immediate lock on the currently running uninstaller
        Get-ChildItem -LiteralPath $resolvedAppData -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $MyInvocation.MyCommand.Path } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        # Deferred removal of the AppData root folder once PowerShell exits
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c ping 127.0.0.1 -n 2 >nul & rmdir /s /q `"$resolvedAppData`"" -WindowStyle Hidden -ErrorAction SilentlyContinue
    }
}

Write-Host "`nODS has been successfully uninstalled." -ForegroundColor Green
if (-not $Quiet) {
    Start-Sleep -Seconds 1
}
