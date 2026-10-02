# ODS-owned, side-by-side Windows Lemonade runtime. Never use the upstream MSI:
# its upgrade actions can stop a Lemonade process owned by another application.

function Get-ODSManagedLemonadeRoot {
    $local = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($local)) {
        $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    }
    if ([string]::IsNullOrWhiteSpace($local)) { throw 'LOCALAPPDATA is required for managed Lemonade.' }
    return (Join-Path $local 'ODS\lemonade\runtimes')
}

function Get-ODSManagedLemonadePath($Runtime) {
    if ([string]$Runtime.windows_version -notmatch '^\d{4}\.\d+\.\d+$' -or
        [string]$Runtime.windows_executable -cne 'lemond.exe') {
        throw 'Unsupported managed Lemonade release contract.'
    }
    return (Join-Path (Get-ODSManagedLemonadeRoot) ('v' + [string]$Runtime.windows_version))
}

function Assert-ODSManagedLemonadeRoot {
    $root = Get-ODSManagedLemonadeRoot
    $local = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $root))
    $ods = Join-Path $local 'ODS'
    $lemonade = Join-Path $ods 'lemonade'
    foreach ($path in @($ods, $lemonade, $root)) {
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'The managed Lemonade installation path is redirected or is not a directory.'
            }
        }
    }
}

function Get-ODSManagedLemonadePinnedFiles($Runtime) {
    $pins = @{}
    foreach ($property in @($Runtime.windows_files_sha256.PSObject.Properties)) {
        $path = [string]$property.Name
        $sha = [string]$property.Value
        if ($path -notmatch '^[A-Za-z0-9_./-]+$' -or $path -match '(^|/)\.\.?(/|$)' -or
            $path.Contains('//') -or $sha -notmatch '^[0-9a-fA-F]{64}$') {
            throw 'Invalid managed Lemonade file pin.'
        }
        $pins[$path] = $sha
    }
    if ($pins.Count -lt 3 -or -not $pins.ContainsKey('lemond.exe') -or
        -not $pins.ContainsKey('resources/defaults.json') -or
        $pins['lemond.exe'] -ine [string]$Runtime.windows_executable_sha256) {
        throw 'The managed Lemonade file pins are incomplete.'
    }
    return $pins
}

function Assert-ODSManagedLemonadeRelease($Runtime, [string]$ReleaseDir) {
    $pins = Get-ODSManagedLemonadePinnedFiles $Runtime
    $manifestPath = Join-Path $ReleaseDir 'ods-release.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'Managed Lemonade release has no ODS integrity manifest.'
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ([string]$manifest.Version -cne [string]$Runtime.windows_version -or
        [string]$manifest.ArchiveSha256 -ine [string]$Runtime.windows_archive_sha256 -or
        -not $manifest.Files -or @($manifest.Files).Count -ne $pins.Count) {
        throw 'Managed Lemonade release manifest does not match the pinned contract.'
    }
    $root = [IO.Path]::GetFullPath($ReleaseDir).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if ((Get-Item -LiteralPath $ReleaseDir -ErrorAction Stop).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Managed Lemonade release is redirected.'
    }
    $directories = @(Get-ChildItem -LiteralPath $ReleaseDir -Directory -Force)
    if ($directories.Count -ne 1 -or $directories[0].Name -cne 'resources' -or
        ($directories[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        @(Get-ChildItem -LiteralPath $directories[0].FullName -Directory -Force).Count) {
        throw 'Managed Lemonade release contains unpinned or redirected directories.'
    }
    $seen = @{}
    foreach ($file in @($manifest.Files)) {
        $relative = [string]$file.Path
        if (-not $pins.ContainsKey($relative) -or $seen.ContainsKey($relative) -or
            [string]$file.Sha256 -ine $pins[$relative]) {
            throw 'Managed Lemonade release manifest does not match the pinned files.'
        }
        $seen[$relative] = $true
        $path = [IO.Path]::GetFullPath((Join-Path $ReleaseDir ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))))
        if (-not $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $path -PathType Leaf) -or
            ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $pins[$relative]) {
            throw "Managed Lemonade release file failed integrity verification: $relative"
        }
    }
    if (@(Get-ChildItem -LiteralPath $ReleaseDir -Recurse -File -Force | Where-Object {
        $_.FullName -ne $manifestPath
    }).Count -ne $pins.Count) {
        throw 'Managed Lemonade release contains unpinned files.'
    }
    $exe = Join-Path $ReleaseDir ([string]$Runtime.windows_executable)
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf) -or
        (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ine [string]$Runtime.windows_executable_sha256) {
        throw 'Managed Lemonade executable does not match its pinned SHA-256.'
    }
    return $exe
}

function Install-ODSManagedLemonade {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Runtime,
        [string]$ArchivePath
    )
    Assert-ODSManagedLemonadeRoot
    $releaseDir = Get-ODSManagedLemonadePath $Runtime
    if (Test-Path -LiteralPath $releaseDir) {
        return (Assert-ODSManagedLemonadeRelease $Runtime $releaseDir)
    }
    $root = Get-ODSManagedLemonadeRoot
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    Assert-ODSManagedLemonadeRoot
    $nonce = [guid]::NewGuid().ToString('N')
    $download = Join-Path $root ('.download-' + $nonce + '.zip')
    $stage = Join-Path $root ('.stage-' + $nonce)
    try {
        $pins = Get-ODSManagedLemonadePinnedFiles $Runtime
        if ([string]::IsNullOrWhiteSpace($ArchivePath)) {
            if ([string]$Runtime.windows_archive_file -notmatch '^lemonade-embeddable-[0-9.]+-windows-x64\.zip$') {
                throw 'Invalid managed Lemonade archive name.'
            }
            $url = 'https://github.com/lemonade-sdk/lemonade/releases/download/v' +
                [string]$Runtime.windows_version + '/' + [string]$Runtime.windows_archive_file
            $curl = Get-Command curl.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
            & $curl.Source --fail --location --silent --show-error --connect-timeout 20 `
                --max-time 300 --retry 3 --max-filesize 100000000 --output $download $url
            if ($LASTEXITCODE -ne 0) { throw "Could not download managed Lemonade (curl exit $LASTEXITCODE)." }
            $ArchivePath = $download
        }
        if ((Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256 -ErrorAction Stop).Hash -ine
            [string]$Runtime.windows_archive_sha256) {
            throw 'Managed Lemonade archive does not match its pinned SHA-256.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
        try {
            $prefix = 'lemonade-embeddable-' + [string]$Runtime.windows_version + '-windows-x64/'
            $files = New-Object System.Collections.Generic.List[object]
            $total = [long]0
            New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
            foreach ($entry in $zip.Entries) {
                $name = [string]$entry.FullName
                if (-not $name.StartsWith($prefix, [StringComparison]::Ordinal) -or
                    $name -match '\\' -or $name -match '(^|/)\.\.?(/|$)' -or
                    (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
                    throw 'Managed Lemonade archive contains an unsafe entry.'
                }
                $relative = $name.Substring($prefix.Length)
                if (-not $relative -or $relative.EndsWith('/')) { continue }
                if ($relative -notmatch '^[A-Za-z0-9_./-]+$' -or $relative.Contains('//')) {
                    throw 'Managed Lemonade archive contains an invalid file name.'
                }
                if (-not $pins.ContainsKey($relative)) {
                    throw 'Managed Lemonade archive contains an unpinned file.'
                }
                $total += [long]$entry.Length
                if ($total -gt 100MB -or $files.Count -ge 100) {
                    throw 'Managed Lemonade archive exceeds its file or size limit.'
                }
                $target = [IO.Path]::GetFullPath((Join-Path $stage ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))))
                $stagePrefix = [IO.Path]::GetFullPath($stage).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
                if (-not $target.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase) -or
                    (Test-Path -LiteralPath $target)) {
                    throw 'Managed Lemonade archive has a duplicate or escaping path.'
                }
                New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
                $inputStream = $entry.Open()
                try {
                    $outputStream = [IO.File]::Open($target, [IO.FileMode]::CreateNew)
                    try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
                } finally { $inputStream.Dispose() }
                $actualSha = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($actualSha -ine $pins[$relative]) { throw "Managed Lemonade archive file failed integrity verification: $relative" }
                $files.Add(@{ Path = $relative; Sha256 = $actualSha })
            }
            if ($files.Count -ne $pins.Count) {
                throw 'Managed Lemonade archive is missing pinned files.'
            }
            $manifest = @{ Version = [string]$Runtime.windows_version
                ArchiveSha256 = ([string]$Runtime.windows_archive_sha256).ToLowerInvariant(); Files = $files.ToArray() }
            [IO.File]::WriteAllText((Join-Path $stage 'ods-release.json'),
                ($manifest | ConvertTo-Json -Depth 5 -Compress), (New-Object Text.UTF8Encoding($false)))
            $null = Assert-ODSManagedLemonadeRelease $Runtime $stage
        } finally { $zip.Dispose() }
        if (Test-Path -LiteralPath $releaseDir) {
            return (Assert-ODSManagedLemonadeRelease $Runtime $releaseDir)
        }
        Move-Item -LiteralPath $stage -Destination $releaseDir -ErrorAction Stop
        return (Assert-ODSManagedLemonadeRelease $Runtime $releaseDir)
    } finally {
        if (Test-Path -LiteralPath $download -PathType Leaf) { Remove-Item -LiteralPath $download -Force }
        if (Test-Path -LiteralPath $stage -PathType Container) { Remove-Item -LiteralPath $stage -Recurse -Force }
    }
}
