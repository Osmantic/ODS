# ============================================================================
# ODS Windows Desktop -- Pinned embeddable Python
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Give the Windows Desktop profile its own Python without touching
#          the PC: the python.org embeddable zip and a pip wheel, both pinned
#          by size and SHA-256 in config/windows-desktop.json, then the
#          dashboard-api lock installed wheels-only with --require-hashes.
#          No system Python, no winget, no PYTHONPATH or user site-packages.
# Expects: native-llama-runtime.ps1 (Expand-ODSNativeLlamaArchive,
#          Get-ODSNativeLlamaFileManifest, Get-ODSNativeLlamaFileSha256),
#          private-file.ps1 (Write-ODSPrivateEnvFile).
# Provides: Get-ODSDesktopPythonPins, New-ODSDesktopPythonPathContent,
#           Install-ODSDesktopPython, Test-ODSDesktopPythonInstall
# Modder notes: the ._pth file both fixes sys.path and puts Python in
#   isolated mode, so environment variables cannot add import paths. Bytecode
#   in __pycache__ is derived from verified sources and is not part of the
#   manifest; everything else is.
# ============================================================================

$script:ODSDesktopPythonManifest = 'ods-python.json'

function Assert-ODSDesktopDownloadPin($Pin, [string]$Name, [string]$UrlPattern) {
    if ($null -eq $Pin) { throw "config/windows-desktop.json has no $Name pin." }
    if ([string]$Pin.file -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,120}\.(zip|whl)$') { throw "The $Name pin names an unexpected file: $($Pin.file)" }
    if ([string]$Pin.url -notmatch $UrlPattern -or -not ([string]$Pin.url).EndsWith('/' + [string]$Pin.file)) {
        throw "Refusing to download $Name from an unexpected location: $($Pin.url)"
    }
    if ([string]$Pin.sha256 -cnotmatch '^[a-f0-9]{64}$') { throw "The $Name pin has no valid SHA-256." }
    if (-not ($Pin.size -is [int] -or $Pin.size -is [long]) -or [long]$Pin.size -le 0) { throw "The $Name pin has no valid size." }
}

function Get-ODSDesktopPythonPins([string]$SourceRoot) {
    $path = Join-Path (Join-Path $SourceRoot 'config') 'windows-desktop.json'
    $config = [IO.File]::ReadAllText($path, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json
    if ([int]$config.schemaVersion -ne 1) { throw "Unsupported config/windows-desktop.json schema: $($config.schemaVersion)" }
    Assert-ODSDesktopDownloadPin $config.python 'Python' '^https://www\.python\.org/ftp/python/[0-9]+\.[0-9]+\.[0-9]+/'
    Assert-ODSDesktopDownloadPin $config.pip 'pip' '^https://files\.pythonhosted\.org/packages/'
    if ([string]$config.python.file -notmatch '-embed-amd64\.zip$') { throw 'The Python pin must be the amd64 embeddable zip.' }
    if ([string]$config.python.pathFile -notmatch '^python[0-9]+\._pth$' -or [string]$config.python.zipLibrary -notmatch '^python[0-9]+\.zip$') {
        throw 'The Python pin must name its ._pth file and standard-library zip.'
    }
    return $config
}

function New-ODSDesktopPythonPathContent($PythonPin) {
    # Pure. No "import site": user site-packages and .pth files stay off, and
    # the presence of the file ignores PYTHONPATH and PYTHONHOME.
    return ([string]$PythonPin.zipLibrary + "`r`n.`r`nLib\site-packages`r`n")
}

function Invoke-ODSDesktopStep([scriptblock]$Step, [object[]]$Arguments) {
    # A step returns its exit code last. Anything a native tool printed before
    # it is shown to the owner, never mistaken for part of the exit code.
    $results = @(& $Step @Arguments)
    if ($results.Count -eq 0) { throw 'A setup step finished without an exit code.' }
    if ($results.Count -gt 1) { $results[0..($results.Count - 2)] | Out-Host }
    return [int]$results[-1]
}

function Invoke-ODSDesktopPinnedDownload([string]$Url, [string]$Destination) {
    & curl.exe --fail --location --proto '=https' --proto-redir '=https' --silent --show-error `
        --retry 3 --connect-timeout 30 --speed-limit 1024 --speed-time 120 --output $Destination $Url | Out-Host
    return $LASTEXITCODE
}

function Save-ODSDesktopPinnedFile($Pin, [string]$Directory, [scriptblock]$Download) {
    $target = Join-Path $Directory ([string]$Pin.file)
    $code = Invoke-ODSDesktopStep $Download @([string]$Pin.url, $target)
    if ($code -ne 0) { throw "Could not download $($Pin.file) (curl exit $code). Check the connection and run setup again." }
    $size = (Get-Item -LiteralPath $target).Length
    $actual = Get-ODSNativeLlamaFileSha256 $target
    if ($size -ne [long]$Pin.size -or $actual -cne [string]$Pin.sha256) {
        Remove-Item -LiteralPath $target -Force
        throw "$($Pin.file) does not match its pin (size $size, SHA-256 $actual). It was not used."
    }
    return $target
}

function Invoke-ODSDesktopPip([string]$Python, [string]$PipWheel, [string]$Requirements, [string]$Target) {
    # Wheels only, every one hash-checked, no dependency resolution beyond the
    # lock, no cache and no config from the user's profile. pip's output goes
    # to the screen: left in the pipeline it would become part of the return
    # value, and a successful install would read as a failure.
    & $Python (Join-Path $PipWheel 'pip') install --isolated --no-cache-dir --disable-pip-version-check --no-input `
        --require-hashes --only-binary=:all: --no-deps --target $Target -r $Requirements | Out-Host
    return $LASTEXITCODE
}

function Get-ODSDesktopPythonFiles([string]$Directory) {
    return @(Get-ODSNativeLlamaFileManifest $Directory | Where-Object {
        $_.path -cne $script:ODSDesktopPythonManifest -and $_.path -notmatch '(^|/)__pycache__/'
    })
}

function Install-ODSDesktopPython {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Requirements,
        [scriptblock]$Download = ${function:Invoke-ODSDesktopPinnedDownload},
        [scriptblock]$Pip = ${function:Invoke-ODSDesktopPip}
    )
    $pins = Get-ODSDesktopPythonPins $SourceRoot
    $parent = Split-Path -Parent $Destination
    $null = New-Item -ItemType Directory -Force -Path $parent
    $staging = Join-Path $parent ('.ods-python-staging-' + [guid]::NewGuid().ToString('N'))
    $downloads = Join-Path $staging 'downloads'
    $python = Join-Path $staging 'python'
    $null = New-Item -ItemType Directory -Force -Path $downloads
    try {
        $zip = Save-ODSDesktopPinnedFile $pins.python $downloads $Download
        $wheel = Save-ODSDesktopPinnedFile $pins.pip $downloads $Download
        Expand-ODSNativeLlamaArchive -ZipPath $zip -Destination $python
        $pathFile = Join-Path $python ([string]$pins.python.pathFile)
        if (-not (Test-Path -LiteralPath $pathFile -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $python 'python.exe') -PathType Leaf)) {
            throw "The Python $($pins.python.version) archive does not have the embeddable layout."
        }
        [IO.File]::WriteAllText($pathFile, (New-ODSDesktopPythonPathContent $pins.python), [Text.Encoding]::ASCII)
        $site = Join-Path (Join-Path $python 'Lib') 'site-packages'
        $null = New-Item -ItemType Directory -Force -Path $site
        $code = Invoke-ODSDesktopStep $Pip @((Join-Path $python 'python.exe'), $wheel, $Requirements, $site)
        if ($code -ne 0) { throw "Installing the Python packages failed (pip exit $code). Run setup again." }

        $record = [ordered]@{
            schemaVersion = 1
            python = [string]$pins.python.version
            pip = [string]$pins.pip.version
            requirementsSha256 = Get-ODSNativeLlamaFileSha256 $Requirements
            files = @(Get-ODSDesktopPythonFiles $python)
        }
        Write-ODSPrivateEnvFile -Path (Join-Path $python $script:ODSDesktopPythonManifest) -Content ($record | ConvertTo-Json -Depth 4 -Compress)

        # Publish whole: a failed install never leaves a half-replaced runtime.
        $previous = $null
        if (Test-Path -LiteralPath $Destination) {
            $previous = "$Destination.previous-" + [guid]::NewGuid().ToString('N')
            Move-Item -LiteralPath $Destination -Destination $previous
        }
        try {
            Move-Item -LiteralPath $python -Destination $Destination
        } catch {
            if ($null -ne $previous) { Move-Item -LiteralPath $previous -Destination $Destination }
            throw
        }
        if ($null -ne $previous) { Remove-Item -LiteralPath $previous -Recurse -Force }
        return [pscustomobject]@{ Path = $Destination; Python = $record.python; Files = $record.files.Count }
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
}

function Test-ODSDesktopPythonInstall {
    # Every launch: names and sizes of every recorded file, plus no extra
    # file. -Full (repair) also compares every SHA-256.
    param([Parameter(Mandatory = $true)][string]$Directory, [string]$Requirements, [switch]$Full)
    $manifestPath = Join-Path $Directory $script:ODSDesktopPythonManifest
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return 'The Windows Desktop Python runtime is not installed.' }
    $record = [IO.File]::ReadAllText($manifestPath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json
    if ($Requirements -and (Get-ODSNativeLlamaFileSha256 $Requirements) -cne [string]$record.requirementsSha256) {
        return 'The installed Python packages are from an older ODS version.'
    }
    $expected = @{}
    foreach ($file in @($record.files)) { $expected[[string]$file.path] = $file }
    $root = [IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    $seen = 0
    foreach ($item in @(Get-ChildItem -LiteralPath $Directory -Recurse -Force -File)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return "The Python runtime contains a link: $($item.FullName)" }
        $relative = $item.FullName.Substring($root.Length).Replace('\', '/')
        if ($relative -ceq $script:ODSDesktopPythonManifest -or $relative -match '(^|/)__pycache__/') { continue }
        $want = $expected[$relative]
        if ($null -eq $want) { return "The Python runtime has an unexpected file: $relative" }
        if ([long]$want.size -ne $item.Length) { return "The Python runtime file changed size: $relative" }
        if ($Full -and (Get-ODSNativeLlamaFileSha256 $item.FullName) -cne [string]$want.sha256) { return "The Python runtime file changed: $relative" }
        $seen++
    }
    if ($seen -ne $expected.Count) { return 'The Python runtime is missing files.' }
    return $null
}
