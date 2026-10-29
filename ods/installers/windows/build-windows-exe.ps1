# ============================================================================
# ODS Windows Native Desktop -- .exe Packaging & Compiler Script
# ============================================================================
# Compiles OdsDesktopShell.cs -> ODS.exe (with embedded Microsoft.Web.WebView2)
# and OdsSetupWizard.cs -> ODS-Setup.exe using Windows' built-in .NET
# Framework compiler (csc.exe). Embeds the ODS application icon and generates
# a SHA-256 build manifest.
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

# Ensure WebView2 .NET interop DLLs are available in native-shell\webview2-lib
$wvLibDir = Join-Path $PSScriptRoot 'native-shell\webview2-lib'
New-Item -ItemType Directory -Path $wvLibDir -Force | Out-Null
$wvCoreDll = Join-Path $wvLibDir 'Microsoft.Web.WebView2.Core.dll'
$wvWinFormsDll = Join-Path $wvLibDir 'Microsoft.Web.WebView2.WinForms.dll'
$wvLoaderDll = Join-Path $wvLibDir 'WebView2Loader.dll'

if (-not (Test-Path -LiteralPath $wvCoreDll) -or -not (Test-Path -LiteralPath $wvWinFormsDll)) {
    $nupkgZip = Join-Path $wvLibDir 'webview2.zip'
    & curl.exe -fsSL 'https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/1.0.2903.40' -o $nupkgZip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to fetch Microsoft.Web.WebView2 package for compilation."
    }
    $extractTmp = Join-Path $wvLibDir 'extracted'
    Expand-Archive -LiteralPath $nupkgZip -DestinationPath $extractTmp -Force
    Copy-Item -LiteralPath (Join-Path $extractTmp 'lib\net462\Microsoft.Web.WebView2.Core.dll') -Destination $wvCoreDll -Force
    Copy-Item -LiteralPath (Join-Path $extractTmp 'lib\net462\Microsoft.Web.WebView2.WinForms.dll') -Destination $wvWinFormsDll -Force
    Copy-Item -LiteralPath (Join-Path $extractTmp 'runtimes\win-x64\native\WebView2Loader.dll') -Destination $wvLoaderDll -Force
    Remove-Item -LiteralPath $extractTmp, $nupkgZip -Recurse -Force -ErrorAction SilentlyContinue
}

# Copy WebView2 DLLs alongside ODS.exe in OutputDir
foreach ($dll in @($wvCoreDll, $wvWinFormsDll, $wvLoaderDll)) {
    if (Test-Path -LiteralPath $dll -PathType Leaf) {
        Copy-Item -LiteralPath $dll -Destination $resolvedOutDir -Force
    }
}

# Copy installer script and windows\lib into OutputDir
$installerPs1 = Join-Path $PSScriptRoot '..\windows-desktop.ps1'
if (Test-Path -LiteralPath $installerPs1) {
    Copy-Item -LiteralPath $installerPs1 -Destination (Join-Path $resolvedOutDir 'windows-desktop.ps1') -Force
}
$libSrc = Join-Path $PSScriptRoot 'lib'
if (Test-Path -LiteralPath $libSrc) {
    $outLibDir = Join-Path $resolvedOutDir 'windows\lib'
    New-Item -ItemType Directory -Path $outLibDir -Force | Out-Null
    Copy-Item -Path (Join-Path $libSrc '*') -Destination $outLibDir -Recurse -Force
}

# Bundle standalone dependencies: config, dashboard-api, and dashboard-dist
$configSrc = Join-Path $repoRoot 'config'
if (Test-Path -LiteralPath $configSrc) {
    $outConfigDir = Join-Path $resolvedOutDir 'config'
    New-Item -ItemType Directory -Path $outConfigDir -Force | Out-Null
    Copy-Item -Path (Join-Path $configSrc '*') -Destination $outConfigDir -Recurse -Force
}

$dashApiSrc = Join-Path $repoRoot 'extensions\services\dashboard-api'
if (Test-Path -LiteralPath $dashApiSrc) {
    $outApiDir = Join-Path $resolvedOutDir 'dashboard-api'
    New-Item -ItemType Directory -Path $outApiDir -Force | Out-Null
    Get-ChildItem -LiteralPath $dashApiSrc -Exclude '__pycache__', '.pytest_cache', '.venv', 'tests' | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $outApiDir -Recurse -Force
    }
}

$dashDistSrc = Join-Path $repoRoot 'extensions\services\dashboard\dist'
if (Test-Path -LiteralPath $dashDistSrc) {
    $outDistDir = Join-Path $resolvedOutDir 'dashboard-dist'
    New-Item -ItemType Directory -Path $outDistDir -Force | Out-Null
    Copy-Item -Path (Join-Path $dashDistSrc '*') -Destination $outDistDir -Recurse -Force
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
    '/platform:x64',
    '/r:System.dll',
    '/r:System.Core.dll',
    '/r:System.Drawing.dll',
    '/r:System.Windows.Forms.dll',
    '/r:System.Management.dll'
)
if ($IconPath -and (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
    $commonArgs += "/win32icon:$IconPath"
    Copy-Item -LiteralPath $IconPath -Destination (Join-Path $resolvedOutDir 'ods-app.ico') -Force
}

# 1. Compile ODS.exe with embedded WebView2 references
$shellArgs = $commonArgs + @(
    "/r:$wvCoreDll",
    "/r:$wvWinFormsDll",
    "/out:$odsExeOut",
    $shellSource
)
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
