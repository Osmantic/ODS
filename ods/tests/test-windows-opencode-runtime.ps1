$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../installers/windows/lib/detection.ps1')
. (Join-Path $PSScriptRoot '../installers/windows/lib/ui.ps1')
. (Join-Path $PSScriptRoot '../installers/windows/lib/opencode-runtime.ps1')

function Write-AI { param($Message) }
function Write-AISuccess { param($Message) }
function Write-AIWarn { param($Message) }
function Write-AIError { param($Message) }

Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue

function New-FixtureZip {
    param([string]$Path, [string]$Version)
    $fixtureSrc = Join-Path ([IO.Path]::GetTempPath()) ('ods-fixture-src-' + [guid]::NewGuid().ToString('N'))
    [System.IO.Directory]::CreateDirectory($fixtureSrc) | Out-Null
    try {
        [IO.File]::WriteAllText((Join-Path $fixtureSrc 'opencode.exe'), $Version)
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Force
        }
        [System.IO.Compression.ZipFile]::CreateFromDirectory($fixtureSrc, $Path)
    } finally {
        Remove-Item -LiteralPath $fixtureSrc -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods-opencode-test-' + [guid]::NewGuid().ToString('N'))
[System.IO.Directory]::CreateDirectory($scratch) | Out-Null
$destination = Join-Path $scratch 'bin/opencode.exe'
[System.IO.Directory]::CreateDirectory((Split-Path $destination)) | Out-Null
$config = Join-Path $scratch 'custom-config.json'
Set-Content -LiteralPath $config -Value 'custom config'

$fixtureZip = Join-Path $scratch 'fixture-release.zip'
$badFixtureZip = Join-Path $scratch 'fixture-bad-version.zip'
New-FixtureZip -Path $fixtureZip -Version '1.18.32'
New-FixtureZip -Path $badFixtureZip -Version '0.0.0'
$validHash = (Get-FileHash -LiteralPath $fixtureZip -Algorithm SHA256).Hash

$script:downloads = 0
$script:badDigest = $false
$script:badVersion = $false

function Get-ODSOpenCodeRelease {
    $hash = $validHash
    if ($script:badDigest) { $hash = 'bad' }
    return [pscustomobject]@{ version = '1.18.32'; asset = 'opencode-windows-x64-baseline.zip'; sha256 = $hash }
}

function Invoke-DownloadWithRetry {
    param($Url, $Destination, $Label)
    if ($Url -ne 'https://github.com/anomalyco/opencode/releases/download/v1.18.32/opencode-windows-x64-baseline.zip') { throw 'Wrong asset URL' }
    $script:downloads++
    $sourceZip = if ($script:badVersion) { $badFixtureZip } else { $fixtureZip }
    Copy-Item -LiteralPath $sourceZip -Destination $Destination -Force
    return $true
}

# Only native execution is substituted; real hashing, staging, extraction and File.Replace
# operate on disk, including an open executable's sharing violation.
function Test-ODSOpenCodeVersion {
    param($Path, $Version)
    return ((Test-Path -LiteralPath $Path -PathType Leaf) -and [IO.File]::ReadAllText($Path) -eq $Version)
}

function Assert($condition, $message) {
    if (-not $condition) { throw $message }
    Write-Output "PASS $message"
}

try {
    [IO.File]::WriteAllText($destination, '1.2.18')
    Assert (Install-ODSOpenCode -Destination $destination) 'older binary upgraded'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.18.32') 'replacement bytes correct'
    Assert (Install-ODSOpenCode -Destination $destination) 'current version reusable'
    Assert ($script:downloads -eq 1) 'no redundant download'
    [IO.File]::WriteAllText($destination, '1.2.18'); $script:badDigest = $true
    Assert (-not (Install-ODSOpenCode -Destination $destination)) 'checksum mismatch rejected'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.2.18') 'checksum failure preserves old binary'
    $script:badDigest = $false; $script:badVersion = $true
    Assert (-not (Install-ODSOpenCode -Destination $destination)) 'wrong staged version rejected'
    $script:badVersion = $false
    $handle = [IO.File]::Open($destination, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        Assert (-not (Install-ODSOpenCode -Destination $destination)) 'busy executable preserved without process termination'
    } finally {
        $handle.Dispose()
    }
    Assert ([IO.File]::ReadAllText($destination) -eq '1.2.18') 'busy failure preserves old binary'
    Assert ((Get-Content -LiteralPath $config -Raw).Trim() -eq 'custom config') 'custom config untouched'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $destination) -Filter '.ods-update-*').Count -eq 0) 'staging cleaned'

    # Install-ODSOpenCode must succeed when Destination path contains square brackets
    $bracketDestination = Join-Path $scratch 'bracket-[test]/bin/opencode.exe'
    [System.IO.Directory]::CreateDirectory((Split-Path $bracketDestination)) | Out-Null
    [IO.File]::WriteAllText($bracketDestination, '1.2.18')
    Assert (Install-ODSOpenCode -Destination $bracketDestination) 'bracketed destination upgraded'
    Assert ([IO.File]::ReadAllText($bracketDestination) -eq '1.18.32') 'bracketed replacement bytes correct'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $bracketDestination) -Filter '.ods-update-*').Count -eq 0) 'bracketed staging cleaned'
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
