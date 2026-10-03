# Hermetic archive, reuse, and corruption tests. No network or product runtime.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/managed-lemonade.ps1')
. (Join-Path $PSScriptRoot '../../installers/windows/lib/backend-contract.ps1')
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    Write-Output "PASS $Message"
}
function New-FixtureArchive([string]$Path, [string]$Version, [bool]$Traversal = $false) {
    $prefix = "lemonade-embeddable-$Version-windows-x64/"
    $payload = [ordered]@{
        'lemond.exe' = 'fixture server binary'
        'resources/defaults.json' = '{"backend":"fixture"}'
        'LICENSE' = 'fixture license'
    }
    $pins = [ordered]@{}
    $zip = [IO.Compression.ZipFile]::Open($Path, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($key in $payload.Keys) {
            $bytes = [Text.Encoding]::UTF8.GetBytes($payload[$key])
            $hash = [Security.Cryptography.SHA256]::Create()
            try { $pins[$key] = ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
            finally { $hash.Dispose() }
            $entry = $zip.CreateEntry($prefix + $key)
            $stream = $entry.Open()
            try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
        }
        if ($Traversal) {
            $entry = $zip.CreateEntry($prefix + '../foreign.txt')
            $stream = $entry.Open()
            try { $stream.WriteByte(65) } finally { $stream.Dispose() }
        }
    } finally { $zip.Dispose() }
    $archiveHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    return [pscustomobject]@{ windows_version = $Version
        windows_archive_file = "lemonade-embeddable-$Version-windows-x64.zip"
        windows_archive_sha256 = $archiveHash
        windows_executable = 'lemond.exe'
        windows_executable_sha256 = $pins['lemond.exe']
        windows_files_sha256 = [pscustomobject]$pins }
}
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-managed-lemonade-' + [guid]::NewGuid().ToString('N'))
$oldLocal = $env:LOCALAPPDATA
try {
    New-Item -ItemType Directory -Path $fixture -ErrorAction Stop | Out-Null
    $env:LOCALAPPDATA = Join-Path $fixture 'appdata'
    $archive = Join-Path $fixture 'good.zip'
    $runtime = New-FixtureArchive $archive '2026.40.0'
    $exe = Install-ODSManagedLemonade -Runtime $runtime -ArchivePath $archive
    Check ((Test-Path -LiteralPath $exe -PathType Leaf) -and
        $exe -eq (Join-Path (Get-ODSManagedLemonadePath $runtime) 'lemond.exe')) 'verified release is installed in an ODS-owned versioned directory'
    Check ((Install-ODSManagedLemonade -Runtime $runtime -ArchivePath $archive) -eq $exe) 'verified release is reused without replacing it'
    $launch = Get-ODSLemonadeLaunchContract -ExecutablePath $exe -VersionOverride '2026.40.0.0' `
        -Port 8080 -ModelsDir (Join-Path $fixture 'models') -ContextSize 131072
    Check ($launch.ManagedEmbeddable -and $launch.ArgumentString -match '^".+managed-data" --port 8080 --host 127\.0\.0\.1$' -and
        $launch.ArgumentList[0] -eq (Join-Path $env:LOCALAPPDATA 'ODS\lemonade\managed-data')) 'calendar runtime uses stable positional cache and no legacy flags'
    $resource = Join-Path (Split-Path -Parent $exe) 'resources/defaults.json'
    Add-Content -LiteralPath $resource -Value 'tamper'
    $message = ''
    try { $null = Install-ODSManagedLemonade -Runtime $runtime -ArchivePath $archive } catch { $message = $_.Exception.Message }
    Check ($message -match 'integrity' -and (Test-Path -LiteralPath $resource)) 'changed resource fails closed without deleting the release'

    $badArchive = Join-Path $fixture 'traversal.zip'
    $badRuntime = New-FixtureArchive $badArchive '2026.41.0' $true
    $message = ''
    try { $null = Install-ODSManagedLemonade -Runtime $badRuntime -ArchivePath $badArchive } catch { $message = $_.Exception.Message }
    Check ($message -match 'unsafe entry' -and -not (Test-Path -LiteralPath (Get-ODSManagedLemonadePath $badRuntime))) 'archive traversal is refused before publication'

    $wrong = New-FixtureArchive (Join-Path $fixture 'wrong.zip') '2026.42.0'
    $wrong.windows_archive_sha256 = '0' * 64
    $message = ''
    try { $null = Install-ODSManagedLemonade -Runtime $wrong -ArchivePath (Join-Path $fixture 'wrong.zip') } catch { $message = $_.Exception.Message }
    Check ($message -match 'pinned SHA-256' -and -not (Test-Path -LiteralPath (Get-ODSManagedLemonadePath $wrong))) 'archive hash mismatch leaves no published runtime'

    $env:LOCALAPPDATA = Join-Path $fixture 'blockedappdata'
    New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $env:LOCALAPPDATA 'ODS'), 'foreign path')
    $message = ''
    try { $null = Install-ODSManagedLemonade -Runtime $runtime -ArchivePath $archive } catch { $message = $_.Exception.Message }
    Check ($message -match 'not a directory' -and
        (Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'ODS') -Raw) -eq 'foreign path') 'foreign managed root is refused without mutation'
} finally {
    $env:LOCALAPPDATA = $oldLocal
    $actual = [IO.Path]::GetFullPath($fixture)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $actual.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($actual) -notmatch '^ods-managed-lemonade-[0-9a-f]{32}$') {
        throw 'Unexpected managed runtime fixture cleanup path.'
    }
    if (Test-Path -LiteralPath $actual) { Remove-Item -LiteralPath $actual -Recurse -Force }
}
