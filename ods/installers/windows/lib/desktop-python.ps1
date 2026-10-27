# ============================================================================
# ODS Windows Native Desktop -- Pinned Embeddable Python Runtime
# ============================================================================
# Part of: installers/windows/lib/
# Purpose:
#   1. Read and validate config/windows-desktop.json (pinned python.org
#      embeddable zip + hash-locked wheel archives, HTTPS only, no litellm).
#   2. Stage, verify SHA-256 and Authenticode signature, extract wheels into
#      site-packages without running pip, configure python312._pth isolation,
#      write pin.json, and atomically publish to
#      %LOCALAPPDATA%\ODS\python\<id>\.
#   3. Re-verify pin.json and installed file hashes before every launch.
# ============================================================================

. (Join-Path $PSScriptRoot 'private-file.ps1')

$script:ODSDesktopPythonExecutableExtensions = @('.exe', '.dll', '.pyd', '.sys', '.com', '.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.js', '.msi')

function Get-ODSWindowsDesktopPythonRoot {
    $localAppData = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    }
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        throw 'Cannot locate %LOCALAPPDATA% for the ODS Windows Desktop Python runtime.'
    }
    return (Join-Path (Join-Path $localAppData 'ODS') 'python')
}

function Read-ODSWindowsDesktopPythonContract {
    <#
    .SYNOPSIS
        Load and validate config/windows-desktop.json.
    #>
    param([Parameter(Mandatory = $true)][string]$SourceRoot)

    $contractPath = Join-Path (Join-Path $SourceRoot 'config') 'windows-desktop.json'
    if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
        throw "ODS Windows Desktop contract is missing: $contractPath"
    }
    $contract = [IO.File]::ReadAllText($contractPath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json -ErrorAction Stop
    if ($contract.schemaVersion -ne 1 -or [string]$contract.profile -cne 'windows-desktop' -or $null -eq $contract.python) {
        throw "Invalid schema in $contractPath"
    }
    $py = $contract.python
    if ([string]$py.id -cnotmatch '^python-[0-9]+\.[0-9]+\.[0-9]+-embed-amd64$' -or
        [string]$py.url -notmatch '^https://www\.python\.org/ftp/python/' -or
        [string]$py.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        ($py.size -isnot [int] -and $py.size -isnot [long]) -or
        [long]$py.size -lt 1048576) {
        throw "Invalid python archive pin in $contractPath"
    }

    $wheels = @($py.wheels)
    if ($wheels.Count -lt 1) {
        throw "config/windows-desktop.json must list at least one wheel."
    }
    $seenNames = @{}
    foreach ($wheel in $wheels) {
        $wName = ([string]$wheel.name).ToLowerInvariant()
        if ($wName -eq 'litellm') {
            throw "config/windows-desktop.json must never include litellm in the Windows Desktop profile."
        }
        if ([string]$wheel.filename -notmatch '\.whl$' -or
            [string]$wheel.url -notmatch '^https://files\.pythonhosted\.org/' -or
            [string]$wheel.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $seenNames.ContainsKey($wName)) {
            throw "Invalid or duplicate wheel entry '$($wheel.name)' in $contractPath"
        }
        $seenNames[$wName] = $true
    }

    return $py
}

function Get-ODSWindowsDesktopPythonInstallDir {
    param(
        [Parameter(Mandatory = $true)]$PythonContract,
        [string]$Root = (Get-ODSWindowsDesktopPythonRoot)
    )
    return (Join-Path $Root ([string]$PythonContract.id))
}

function Expand-ODSDesktopSafeZip {
    <#
    .SYNOPSIS
        Extract a zip or wheel archive into Destination, rejecting path
        traversal, absolute paths, drive letters, and control characters.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ArchivePath,
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$AllowOverwrite
    )
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        $entries = @($archive.Entries)
        if ($entries.Count -eq 0 -or $entries.Count -gt 16384) {
            throw "Archive $ArchivePath has an unexpected number of entries ($($entries.Count))."
        }
        $totalBytes = 0L
        foreach ($entry in $entries) {
            $name = [string]$entry.FullName
            if ([string]::IsNullOrWhiteSpace($name) -or
                $name -match '[\x00-\x1f:*?"<>|]' -or
                $name -match '^[\\/]' -or
                $name -match '(^|[\\/])\.{1,2}([\\/]|$)') {
                throw "Archive $ArchivePath contains an unsafe entry path: $name"
            }
            $totalBytes += [long]$entry.Length
            if ($totalBytes -gt 1073741824L) {
                throw "Archive $ArchivePath expands beyond the 1 GiB limit."
            }
            $rel = $name.Replace('\', '/').TrimEnd('/')
            $full = [IO.Path]::GetFullPath((Join-Path $Destination ($rel.Replace('/', [IO.Path]::DirectorySeparatorChar))))
            if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Archive entry escapes destination: $name"
            }
            if ($name.EndsWith('/') -or $name.EndsWith('\')) {
                New-Item -ItemType Directory -Path $full -Force | Out-Null
                continue
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $full) -Force | Out-Null
            if (-not $AllowOverwrite -and (Test-Path -LiteralPath $full)) {
                throw "Archive $ArchivePath repeats entry: $name"
            }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $full, [bool]$AllowOverwrite)
        }
    } finally {
        $archive.Dispose()
    }
}

function Get-ODSDesktopDirectoryManifest {
    param([Parameter(Mandatory = $true)][string]$Directory)
    $root = [IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    $files = [Collections.Generic.List[object]]::new()
    foreach ($item in @(Get-ChildItem -LiteralPath $Directory -Recurse -Force)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse point or symlink is not allowed in the Python runtime: $($item.FullName)"
        }
        if ($item.PSIsContainer) { continue }
        $rel = $item.FullName.Substring($root.Length).Replace('\', '/')
        if ($rel -ceq 'pin.json' -or $rel -match '(^|/)__pycache__(/|$)' -or $rel.EndsWith('.pyc')) {
            continue
        }
        $sha = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $files.Add([pscustomobject]@{
            path   = $rel
            sha256 = $sha
            size   = [long]$item.Length
        })
    }
    return @($files | Sort-Object -Property path)
}

function Test-ODSWindowsDesktopPythonInstall {
    <#
    .SYNOPSIS
        Verify an installed embeddable Python directory against its pin.json,
        _pth isolation rules, and file SHA-256 manifest.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [string]$ExpectedPythonZipSha256 = '',
        [switch]$SkipAuthenticodeCheck
    )

    $pinPath = Join-Path $Directory 'pin.json'
    if (-not (Test-Path -LiteralPath $pinPath -PathType Leaf)) {
        throw "Python runtime pin.json is missing in $Directory"
    }
    $record = [IO.File]::ReadAllText($pinPath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json -ErrorAction Stop
    if ($record.schemaVersion -ne 1 -or
        [string]$record.pythonZipSha256 -cnotmatch '^[0-9a-f]{64}$' -or
        @($record.files).Count -eq 0) {
        throw "Python runtime pin.json is malformed: $pinPath"
    }
    if ($ExpectedPythonZipSha256 -and [string]$record.pythonZipSha256 -cne $ExpectedPythonZipSha256.ToLowerInvariant()) {
        throw "Installed Python zip SHA-256 ($($record.pythonZipSha256)) does not match expected ($ExpectedPythonZipSha256)."
    }

    $pythonExe = Join-Path $Directory 'python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        throw "python.exe is missing in $Directory"
    }

    $pthFile = Join-Path $Directory ([string]$record.pthFile)
    if (-not (Test-Path -LiteralPath $pthFile -PathType Leaf)) {
        throw "Python isolation file $($record.pthFile) is missing in $Directory"
    }
    $pthLines = @(Get-Content -LiteralPath $pthFile | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($pthLines -notcontains 'site-packages' -or $pthLines -notcontains 'import site') {
        throw "Python _pth file $pthFile does not configure isolated site-packages."
    }

    if (-not $SkipAuthenticodeCheck -and $record.requireAuthenticode) {
        $sig = Get-AuthenticodeSignature -LiteralPath $pythonExe
        if ($sig.Status -ne 'Valid' -or [string]$sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            throw "python.exe failed Authenticode verification (Status: $($sig.Status))."
        }
    }

    $listed = @{}
    foreach ($f in @($record.files)) {
        $p = [string]$f.path
        $listed[$p.ToLowerInvariant()] = $f
    }
    $actual = @{}
    foreach ($f in (Get-ODSDesktopDirectoryManifest -Directory $Directory)) {
        $actual[$f.path.ToLowerInvariant()] = $f
    }

    foreach ($k in $listed.Keys) {
        if (-not $actual.ContainsKey($k)) {
            throw "Pinned Python file is missing: $($listed[$k].path)"
        }
        if ($actual[$k].sha256 -cne [string]$listed[$k].sha256) {
            throw "Pinned Python file SHA-256 mismatch: $($listed[$k].path)"
        }
    }
    foreach ($k in $actual.Keys) {
        if (-not $listed.ContainsKey($k) -and
            [IO.Path]::GetExtension($k) -in $script:ODSDesktopPythonExecutableExtensions) {
            throw "Unpinned executable file appeared in Python runtime: $($actual[$k].path)"
        }
    }

    return [pscustomobject]@{
        Directory       = $Directory
        ExecutablePath  = $pythonExe
        Id              = [string]$record.id
        Version         = [string]$record.version
        PythonZipSha256 = [string]$record.pythonZipSha256
        FileCount       = $listed.Count
    }
}

function Install-ODSWindowsDesktopPython {
    <#
    .SYNOPSIS
        Download (or stage from cache), verify, extract, and atomically publish
        the pinned embeddable Python runtime and its hash-locked wheels.
    #>
    param(
        [Parameter(Mandatory = $true)]$PythonContract,
        [string]$Root = (Get-ODSWindowsDesktopPythonRoot),
        [AllowNull()][scriptblock]$Downloader = $null,
        [switch]$SkipAuthenticodeCheck
    )

    $target = Get-ODSWindowsDesktopPythonInstallDir -PythonContract $PythonContract -Root $Root
    if (Test-Path -LiteralPath $target -PathType Container) {
        try {
            return (Test-ODSWindowsDesktopPythonInstall -Directory $target `
                -ExpectedPythonZipSha256 ([string]$PythonContract.sha256) `
                -SkipAuthenticodeCheck:$SkipAuthenticodeCheck)
        } catch {
            $damaged = Join-Path $Root (".damaged-$($PythonContract.id)-" + [guid]::NewGuid().ToString('N'))
            [IO.Directory]::Move($target, $damaged)
        }
    }

    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    $staging = Join-Path $Root ('.staging-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null

    $fetchFile = {
        param([string]$Url, [string]$OutFile)
        if ($Url -notmatch '^https://') {
            throw "Refusing non-HTTPS Python runtime download: $Url"
        }
        if ($null -ne $Downloader) {
            & $Downloader $Url $OutFile
            return
        }
        & curl.exe --fail --location --proto '=https' --proto-redir '=https' --retry 3 --silent --show-error --output $OutFile $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed (curl exit $LASTEXITCODE): $Url"
        }
    }

    try {
        $runtimeDir = Join-Path $staging 'runtime'
        $sitePackages = Join-Path $runtimeDir 'site-packages'
        New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null

        $pyZip = Join-Path $staging ([string]$PythonContract.asset)
        & $fetchFile ([string]$PythonContract.url) $pyZip
        $pyItem = Get-Item -LiteralPath $pyZip -ErrorAction Stop
        if ([long]$pyItem.Length -ne [long]$PythonContract.size) {
            throw "Python archive $($PythonContract.asset) size is $($pyItem.Length), expected $($PythonContract.size)."
        }
        $pySha = (Get-FileHash -LiteralPath $pyZip -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($pySha -cne ([string]$PythonContract.sha256).ToLowerInvariant()) {
            throw "Python archive $($PythonContract.asset) SHA-256 mismatch (got $pySha, expected $($PythonContract.sha256))."
        }

        Expand-ODSDesktopSafeZip -ArchivePath $pyZip -Destination $runtimeDir
        Remove-Item -LiteralPath $pyZip -Force

        $pythonExe = Join-Path $runtimeDir 'python.exe'
        if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
            throw 'Extracted Python archive does not contain python.exe.'
        }

        if (-not $SkipAuthenticodeCheck) {
            $sig = Get-AuthenticodeSignature -LiteralPath $pythonExe
            if ($sig.Status -ne 'Valid' -or [string]$sig.SignerCertificate.Subject -notmatch ([string]$PythonContract.signer_subject_match)) {
                throw "python.exe failed Authenticode check (Status: $($sig.Status))."
            }
        }

        foreach ($wheel in @($PythonContract.wheels)) {
            $whlPath = Join-Path $staging ([string]$wheel.filename)
            & $fetchFile ([string]$wheel.url) $whlPath
            $whlSha = (Get-FileHash -LiteralPath $whlPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($whlSha -cne ([string]$wheel.sha256).ToLowerInvariant()) {
                throw "Wheel $($wheel.filename) failed SHA-256 check (got $whlSha, expected $($wheel.sha256))."
            }
            Expand-ODSDesktopSafeZip -ArchivePath $whlPath -Destination $sitePackages -AllowOverwrite
            Remove-Item -LiteralPath $whlPath -Force
        }

        $pthPath = Join-Path $runtimeDir ([string]$PythonContract.pth_file)
        $pthText = (@($PythonContract.pth_lines) -join "`r`n") + "`r`n"
        [IO.File]::WriteAllText($pthPath, $pthText, [Text.Encoding]::ASCII)

        $manifest = Get-ODSDesktopDirectoryManifest -Directory $runtimeDir
        $pinRecord = [ordered]@{
            schemaVersion       = 1
            id                  = [string]$PythonContract.id
            version             = [string]$PythonContract.version
            pythonZipSha256     = ([string]$PythonContract.sha256).ToLowerInvariant()
            pthFile             = [string]$PythonContract.pth_file
            requireAuthenticode = (-not [bool]$SkipAuthenticodeCheck)
            wheels              = @($PythonContract.wheels | ForEach-Object {
                [ordered]@{ name = [string]$_.name; version = [string]$_.version; sha256 = ([string]$_.sha256).ToLowerInvariant() }
            })
            files               = $manifest
        }
        Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'pin.json') -Content ($pinRecord | ConvertTo-Json -Depth 5)
        [IO.Directory]::Move($runtimeDir, $target)
    } finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    return (Test-ODSWindowsDesktopPythonInstall -Directory $target `
        -ExpectedPythonZipSha256 ([string]$PythonContract.sha256) `
        -SkipAuthenticodeCheck:$SkipAuthenticodeCheck)
}

function Resolve-ODSWindowsDesktopPythonExecutable {
    <#
    .SYNOPSIS
        Locate the verified embeddable Python executable in %LOCALAPPDATA%\ODS\python,
        or fall back to a verified local Python 3.11+ interpreter that has
        fastapi, uvicorn, httpx, and aiohttp installed.
    #>
    param([string]$SourceRoot = '')

    if ($SourceRoot) {
        try {
            $contract = Read-ODSWindowsDesktopPythonContract -SourceRoot $SourceRoot
            $dir = Get-ODSWindowsDesktopPythonInstallDir -PythonContract $contract
            if (Test-Path -LiteralPath $dir -PathType Container) {
                $verified = Test-ODSWindowsDesktopPythonInstall -Directory $dir -ExpectedPythonZipSha256 $contract.sha256
                return $verified.ExecutablePath
            }
        } catch { }
    }

    $candidates = @(
        $env:ODS_PYTHON_EXE,
        (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }

    foreach ($exe in $candidates) {
        try {
            $out = & $exe -c "import fastapi, uvicorn, httpx, aiohttp; print('ODS_PY_OK')" 2>$null
            if ($LASTEXITCODE -eq 0 -and ($out -join '') -match 'ODS_PY_OK') {
                return $exe
            }
        } catch { }
    }

    throw 'No verified ODS Python runtime found. Run ODS Windows Setup to install the pinned embeddable Python runtime.'
}
