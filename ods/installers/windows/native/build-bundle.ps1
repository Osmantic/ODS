# ============================================================================
# ODS Windows-native app -- bundle builder
# ============================================================================
# Part of: installers/windows/native/
# Purpose: Stage the folder the Inno Setup installer packages:
#            python\                 CPython 3.12 for Windows x64 (python.org's
#                                    NuGet distribution), python312._pth,
#                                    Lib\site-packages from requirements.lock
#            native\supervisor\      the process supervisor package
#            dashboard-api\          the FastAPI service source
#            dashboard\dist\         the built dashboard (npm ci; npm run build)
#            llama\                  llama-server.exe, from fetch-runtime.ps1
#            lib\                    ods-native-lib.ps1, private-file.ps1,
#                                    backend-contract.ps1
#            ods-native.ps1, launch-ods.vbs
#
# Expects: Windows x64, PowerShell 7 or Windows PowerShell 5.1, network access
#          to api.nuget.org and PyPI, Node.js/npm on PATH, and the model
#          runtime script (-FetchRuntimeScript) which must accept
#          -Destination <dir> and leave llama-server.exe there.
# Provides: build-bundle.ps1 -OutputDir <new folder> [-FetchRuntimeScript <ps1>]
#
# Modder notes:
#   Every download is pinned by URL and SHA-256 in $PinnedDownloads and is
#   checked before use; a mismatch stops the build. Python wheels come only
#   from requirements.lock with --require-hashes --only-binary=:all:, so a
#   dependency without a win_amd64 (or pure-Python) wheel fails the build
#   rather than compiling. The python312._pth file puts the interpreter in
#   isolated mode (no PYTHONPATH, no user site-packages) and adds ..\native
#   so `pythonw -m supervisor` works. Python is python.org's NuGet package
#   (same CPython build as the installer, published by the release managers)
#   because it is a complete runtime, pip included, that installs nothing.
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$FetchRuntimeScript = (Join-Path $PSScriptRoot 'fetch-runtime.ps1'),
    [string]$RepoRoot = ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\..')))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# One line per pinned download: Name, Url, Sha256 (64 hex, lower case).
$PinnedDownloads = @(
    @{ Name = 'python'; Url = 'https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg'; Sha256 = '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8' }
)

$PythonPth = @(
    '.'
    'Lib'
    'DLLs'
    'Lib\site-packages'
    '..\native'
    'import site'
)

function Get-PinnedDownload {
    param([Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][string]$Directory)
    $pin = @($PinnedDownloads | Where-Object { $_.Name -ceq $Name })
    if ($pin.Count -ne 1) { throw "No single pinned download named '$Name'." }
    $file = Join-Path $Directory ([IO.Path]::GetFileName(([Uri]$pin[0].Url).AbsolutePath))
    Write-Host "Downloading $($pin[0].Url)"
    Invoke-WebRequest -Uri $pin[0].Url -OutFile $file -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $pin[0].Sha256) {
        Remove-Item -LiteralPath $file -Force
        throw "SHA-256 mismatch for $($pin[0].Url): expected $($pin[0].Sha256), got $actual."
    }
    return $file
}

function Invoke-Checked {
    # Run a native command; any non-zero exit stops the build.
    param([Parameter(Mandatory = $true)][string]$FilePath, [string[]]$ArgumentList = @(), [string]$WorkingDirectory = '')
    if ($WorkingDirectory) { Push-Location -LiteralPath $WorkingDirectory }
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) { throw "$FilePath $($ArgumentList -join ' ') failed with exit code $LASTEXITCODE." }
    } finally {
        if ($WorkingDirectory) { Pop-Location }
    }
}

function Copy-SourceTree {
    # Copy a source folder without tests, caches or container files.
    param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination)
    $skip = @('tests', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'Dockerfile', 'node_modules')
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($item in @(Get-ChildItem -LiteralPath $Source -Force)) {
        if ($skip -contains $item.Name) { continue }
        if ($item.PSIsContainer) {
            Copy-SourceTree -Source $item.FullName -Destination (Join-Path $Destination $item.Name)
        } else {
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name)
        }
    }
}

function Install-BundledPython {
    param([Parameter(Mandatory = $true)][string]$Bundle, [Parameter(Mandatory = $true)][string]$Work)
    $package = Get-PinnedDownload -Name 'python' -Directory $Work
    $extracted = Join-Path $Work 'python-package'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($package, $extracted)
    $python = Join-Path $Bundle 'python'
    Move-Item -LiteralPath (Join-Path $extracted 'tools') -Destination $python
    $exe = Join-Path $python 'python.exe'

    # Locked wheels into a separate folder with the bundled CPython itself
    # (exact ABI match), then drop the bundled pip so the app cannot install.
    $lock = Join-Path $RepoRoot 'ods\extensions\services\dashboard-api\requirements.lock'
    $wheels = Join-Path $Work 'site-packages'
    Invoke-Checked -FilePath $exe -ArgumentList @('-I', '-m', 'pip', 'install', '--isolated', '--no-cache-dir',
        '--disable-pip-version-check', '--no-input', '--require-hashes', '--only-binary=:all:', '--no-deps',
        '--target', $wheels, '-r', $lock)
    $sitePackages = Join-Path $python 'Lib\site-packages'
    foreach ($pip in @(Get-ChildItem -LiteralPath $sitePackages -Force | Where-Object { $_.Name -match '^pip($|-.*\.dist-info$)' })) {
        Remove-Item -LiteralPath $pip.FullName -Recurse -Force
    }
    foreach ($item in @(Get-ChildItem -LiteralPath $wheels -Force)) {
        Move-Item -LiteralPath $item.FullName -Destination $sitePackages
    }
    [IO.File]::WriteAllText((Join-Path $python 'python312._pth'), (($PythonPth -join "`r`n") + "`r`n"), [Text.UTF8Encoding]::new($false))
}

function Install-Dashboard {
    param([Parameter(Mandatory = $true)][string]$Bundle)
    $source = Join-Path $RepoRoot 'ods\extensions\services\dashboard'
    $npm = @(Get-Command -Name 'npm.cmd' -CommandType Application -ErrorAction Stop)[0].Source
    Invoke-Checked -FilePath $npm -ArgumentList @('ci', '--no-audit', '--no-fund') -WorkingDirectory $source
    Invoke-Checked -FilePath $npm -ArgumentList @('run', 'build') -WorkingDirectory $source
    $dist = Join-Path $source 'dist'
    if (-not (Test-Path -LiteralPath (Join-Path $dist 'index.html') -PathType Leaf)) { throw "The dashboard build did not produce $dist\index.html." }
    New-Item -ItemType Directory -Force -Path (Join-Path $Bundle 'dashboard') | Out-Null
    Copy-Item -LiteralPath $dist -Destination (Join-Path $Bundle 'dashboard\dist') -Recurse
}

function Install-LlamaRuntime {
    # Hook: the model-runtime workstream's fetch-runtime.ps1 downloads and
    # verifies its own pinned llama.cpp build into -Destination.
    param([Parameter(Mandatory = $true)][string]$Bundle, [Parameter(Mandatory = $true)][string]$Work,
        [Parameter(Mandatory = $true)][string]$Script)
    if (-not (Test-Path -LiteralPath $Script -PathType Leaf)) {
        throw "The llama.cpp runtime script was not found at $Script. Pass -FetchRuntimeScript <path to fetch-runtime.ps1>."
    }
    $llama = Join-Path $Bundle 'llama'
    # The bundled Python is x64, so the bundle is x64 whatever machine builds it.
    # Download records go to the build's scratch folder, not the bundle. The
    # runtime library is not written for strict mode, so it runs in its own
    # PowerShell process rather than inheriting this script's Set-StrictMode.
    $shell = (Get-Process -Id $PID).Path
    $result = & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script `
        -StateRoot (Join-Path $Work 'llama-state') -Destination $llama -Architecture x64 -NativeApp
    if ($LASTEXITCODE -ne 0) { throw "$Script failed with exit code $LASTEXITCODE." }
    Write-Host "llama.cpp runtime: $result"
    if (-not (Test-Path -LiteralPath (Join-Path $llama 'llama-server.exe') -PathType Leaf)) {
        throw "$Script did not leave llama-server.exe in $llama."
    }
}

function Test-Bundle {
    # The bundled interpreter, isolated by its ._pth, imports the supervisor
    # and every service dependency from a neutral working directory.
    param([Parameter(Mandatory = $true)][string]$Bundle, [Parameter(Mandatory = $true)][string]$Work)
    $probe = 'import supervisor, fastapi, uvicorn, aiohttp, httpx, PIL, yaml, jsonschema, qrcode, httptools, watchfiles, websockets'
    Invoke-Checked -FilePath (Join-Path $Bundle 'python\python.exe') -ArgumentList @('-c', $probe) -WorkingDirectory $Work
    foreach ($required in @('python\pythonw.exe', 'native\supervisor\__main__.py', 'dashboard-api\main.py', 'dashboard-api\native_chat.py', 'llama-server\manifest.yaml',
            'dashboard\dist\index.html', 'llama\llama-server.exe', 'ods-native.ps1', 'launch-ods.vbs',
            'lib\ods-native-lib.ps1', 'lib\private-file.ps1', 'lib\backend-contract.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Bundle $required) -PathType Leaf)) { throw "The bundle is missing $required." }
    }
}

if (Test-Path -LiteralPath $OutputDir) { throw "$OutputDir already exists; give a new folder." }
$bundle = [IO.Path]::GetFullPath($OutputDir)
$work = Join-Path ([IO.Path]::GetTempPath()) ('ods-bundle-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $bundle, $work | Out-Null
try {
    $native = Join-Path $RepoRoot 'ods\installers\windows\native'
    $windowsLib = Join-Path $RepoRoot 'ods\installers\windows\lib'
    Install-BundledPython -Bundle $bundle -Work $work
    Copy-SourceTree -Source (Join-Path $native 'supervisor') -Destination (Join-Path $bundle 'native\supervisor')
    Copy-SourceTree -Source (Join-Path $RepoRoot 'ods\extensions\services\dashboard-api') -Destination (Join-Path $bundle 'dashboard-api')
    # Native dashboard-api reads the manifests of the services it runs from its parent folder.
    New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'llama-server') | Out-Null
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'ods\extensions\services\llama-server\manifest.yaml') -Destination (Join-Path $bundle 'llama-server')
    Install-Dashboard -Bundle $bundle
    Install-LlamaRuntime -Bundle $bundle -Work $work -Script $FetchRuntimeScript
    New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'lib') | Out-Null
    Copy-Item -LiteralPath (Join-Path $native 'ods-native.ps1'), (Join-Path $native 'launch-ods.vbs') -Destination $bundle
    Copy-Item -LiteralPath (Join-Path $native 'ods-native-lib.ps1'), (Join-Path $windowsLib 'private-file.ps1'),
        (Join-Path $windowsLib 'backend-contract.ps1') -Destination (Join-Path $bundle 'lib')
    Test-Bundle -Bundle $bundle -Work $work
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force
}
Write-Host "ODS bundle ready: $bundle"
