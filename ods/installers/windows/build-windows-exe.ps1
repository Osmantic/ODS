# ============================================================================
# ODS Windows Native Desktop -- .exe Packaging & Compiler Script
# ============================================================================
# Compiles OdsDesktopShell.cs -> ODS.exe and OdsSetupWizard.cs -> ODS-Setup.exe
# using Windows' built-in .NET Framework compiler (csc.exe) with zero external
# SDK requirements. Embeds the ODS application icon and generates a SHA-256
# build manifest.
# ============================================================================

[CmdletBinding()]
param(
    [string]$OutputDir = '',

    [string]$IconPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot 'dist\windows-desktop'
}
$resolvedOutDir = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $resolvedOutDir -Force | Out-Null

# Locate built-in Windows .NET Framework C# compiler (csc.exe)
$cscCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$cscExe = $cscCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $cscExe) {
    throw "Could not locate built-in Windows C# compiler (csc.exe) under $env:WINDIR\Microsoft.NET."
}

# Locate ODS icon
if (-not $IconPath) {
    $iconCandidates = @(
        (Join-Path $repoRoot 'extensions\services\dashboard\public\ods.ico'),
        (Join-Path $repoRoot 'extensions\services\dashboard\dist\ods.ico')
    )
    $IconPath = $iconCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

$shellSource = Join-Path $PSScriptRoot 'native-shell\OdsDesktopShell.cs'
$wizardSource = Join-Path $PSScriptRoot 'native-shell\OdsSetupWizard.cs'

$odsExeOut = Join-Path $resolvedOutDir 'ODS.exe'
$setupExeOut = Join-Path $resolvedOutDir 'ODS-Setup.exe'

$commonArgs = @(
    '/nologo',
    '/target:winexe',
    '/optimize+',
    '/platform:anycpu',
    '/r:System.dll',
    '/r:System.Core.dll',
    '/r:System.Drawing.dll',
    '/r:System.Windows.Forms.dll'
)
if ($IconPath -and (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
    $commonArgs += "/win32icon:$IconPath"
    Copy-Item -LiteralPath $IconPath -Destination (Join-Path $resolvedOutDir 'ods-app.ico') -Force
}

# 1. Compile ODS.exe
$shellArgs = $commonArgs + @("/out:$odsExeOut", $shellSource)
$shellBuildOut = & $cscExe @shellArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Failed to compile ODS.exe (exit $LASTEXITCODE): $($shellBuildOut -join "`n")"
}

# 2. Compile ODS-Setup.exe
$wizardArgs = $commonArgs + @("/out:$setupExeOut", $wizardSource)
$wizardBuildOut = & $cscExe @wizardArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Failed to compile ODS-Setup.exe (exit $LASTEXITCODE): $($wizardBuildOut -join "`n")"
}

$odsItem = Get-Item -LiteralPath $odsExeOut -ErrorAction Stop
$setupItem = Get-Item -LiteralPath $setupExeOut -ErrorAction Stop
$odsHash = (Get-FileHash -LiteralPath $odsExeOut -Algorithm SHA256).Hash.ToLowerInvariant()
$setupHash = (Get-FileHash -LiteralPath $setupExeOut -Algorithm SHA256).Hash.ToLowerInvariant()

$manifest = [ordered]@{
    schemaVersion = 1
    version       = '3.0.0'
    profile       = 'windows-desktop'
    compiler      = $cscExe
    builtAtUtc    = [DateTime]::UtcNow.ToString('o')
    artifacts     = [ordered]@{
        'ODS.exe'       = [ordered]@{
            path   = $odsExeOut
            size   = [long]$odsItem.Length
            sha256 = $odsHash
        }
        'ODS-Setup.exe' = [ordered]@{
            path   = $setupExeOut
            size   = [long]$setupItem.Length
            sha256 = $setupHash
        }
    }
}

$manifestPath = Join-Path $resolvedOutDir 'build-manifest.json'
Set-Content -LiteralPath $manifestPath -Value ($manifest | ConvertTo-Json -Depth 5) -Encoding UTF8

return [pscustomobject]$manifest
