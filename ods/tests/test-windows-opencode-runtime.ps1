$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../installers/windows/lib/opencode-runtime.ps1')
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods-opencode-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$destination = Join-Path $scratch 'bin/opencode.exe'
New-Item -ItemType Directory -Path (Split-Path $destination) | Out-Null
$config = Join-Path $scratch 'custom-config.json'
Set-Content -LiteralPath $config -Value 'custom config'
$script:downloads = 0
$script:badDigest = $false
$script:badVersion = $false
function Write-AIWarn { param($Message) }
function Get-ODSOpenCodeRelease {
    $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes('archive')))
    if ($script:badDigest) { $hash = 'bad' }
    return [pscustomobject]@{ version='1.18.32'; asset='opencode-windows-x64-baseline.zip'; sha256=$hash }
}
function Invoke-DownloadWithRetry { param($Url,$Destination,$Label)
    if ($Url -ne 'https://github.com/anomalyco/opencode/releases/download/v1.18.32/opencode-windows-x64-baseline.zip') { throw 'Wrong asset URL' }
    $script:downloads++; [IO.File]::WriteAllText($Destination,'archive'); return $true
}
function Invoke-ExtractionWithRetry { param($ZipPath,$DestinationPath)
    New-Item -ItemType Directory -Path $DestinationPath | Out-Null
    [IO.File]::WriteAllText((Join-Path $DestinationPath 'opencode.exe'), $(if ($script:badVersion) {'0.0.0'} else {'1.18.32'})); return $true
}
# Only native execution is substituted; real hashing, staging and File.Replace
# operate on disk, including an open executable's sharing violation.
function Test-ODSOpenCodeVersion { param($Path,$Version)
    return ((Test-Path -LiteralPath $Path -PathType Leaf) -and [IO.File]::ReadAllText($Path) -eq $Version)
}
function Assert($condition, $message) { if (-not $condition) { throw $message }; Write-Output "PASS $message" }
try {
    [IO.File]::WriteAllText($destination,'1.2.18')
    Assert (Install-ODSOpenCode -Destination $destination) 'older binary upgraded'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.18.32') 'replacement bytes correct'
    Assert (Install-ODSOpenCode -Destination $destination) 'current version reusable'
    Assert ($script:downloads -eq 1) 'no redundant download'
    [IO.File]::WriteAllText($destination,'1.2.18'); $script:badDigest=$true
    Assert (-not (Install-ODSOpenCode -Destination $destination)) 'checksum mismatch rejected'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.2.18') 'checksum failure preserves old binary'
    $script:badDigest=$false; $script:badVersion=$true
    Assert (-not (Install-ODSOpenCode -Destination $destination)) 'wrong staged version rejected'
    $script:badVersion=$false
    $handle=[IO.File]::Open($destination,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try { Assert (-not (Install-ODSOpenCode -Destination $destination)) 'busy executable preserved without process termination' } finally { $handle.Dispose() }
    Assert ([IO.File]::ReadAllText($destination) -eq '1.2.18') 'busy failure preserves old binary'
    Assert ((Get-Content -LiteralPath $config -Raw).Trim() -eq 'custom config') 'custom config untouched'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $destination) -Filter '.ods-update-*').Count -eq 0) 'staging cleaned'
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
