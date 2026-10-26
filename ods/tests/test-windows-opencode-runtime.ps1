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
    param([string]$Path, [string]$Version, [switch]$Corrupt)
    $fixtureSrc = Join-Path ([IO.Path]::GetTempPath()) ('ods-fixture-src-' + [guid]::NewGuid().ToString('N'))
    [System.IO.Directory]::CreateDirectory($fixtureSrc) | Out-Null
    try {
        [IO.File]::WriteAllText((Join-Path $fixtureSrc 'opencode.exe'), $Version)
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Force
        }
        $parent = Split-Path -Parent $Path
        if (-not (Test-Path -LiteralPath $parent)) {
            [System.IO.Directory]::CreateDirectory($parent) | Out-Null
        }
        [System.IO.Compression.ZipFile]::CreateFromDirectory($fixtureSrc, $Path)
        if ($Corrupt) {
            $bytes = [IO.File]::ReadAllBytes($Path)
            for ($i = [Math]::Max(0, $bytes.Length - 30); $i -lt $bytes.Length; $i++) {
                $bytes[$i] = 0
            }
            [IO.File]::WriteAllBytes($Path, $bytes)
        }
    } finally {
        Remove-Item -LiteralPath $fixtureSrc -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Assert($condition, $message) {
    if (-not $condition) { throw $message }
    Write-Output "PASS $message"
}

$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods-opencode-test-' + [guid]::NewGuid().ToString('N'))
[System.IO.Directory]::CreateDirectory($scratch) | Out-Null

$originalPath = $env:PATH
try {
    # ── Fixtures Setup ────────────────────────────────────────────────────────
    $fixtureZip = Join-Path $scratch 'fixture-release.zip'
    $badFixtureZip = Join-Path $scratch 'fixture-bad-version.zip'
    $corruptFixtureZip = Join-Path $scratch 'fixture-corrupt.zip'
    New-FixtureZip -Path $fixtureZip -Version '1.18.32'
    New-FixtureZip -Path $badFixtureZip -Version '0.0.0'
    New-FixtureZip -Path $corruptFixtureZip -Version '1.18.32' -Corrupt
    $validHash = (Get-FileHash -LiteralPath $fixtureZip -Algorithm SHA256).Hash

    $script:badDigest = $false
    $script:badVersion = $false
    $script:corruptZip = $false

    function Get-ODSOpenCodeRelease {
        $hash = $validHash
        if ($script:badDigest) { $hash = 'bad-hash-mismatch' }
        return [pscustomobject]@{ version = '1.18.32'; asset = 'opencode-windows-x64-baseline.zip'; sha256 = $hash }
    }

    # Only native execution is substituted; real hashing, staging, extraction and File.Replace
    # operate on disk, including an open executable's sharing violation.
    function Test-ODSOpenCodeVersion {
        param($Path, $Version)
        return ((Test-Path -LiteralPath $Path -PathType Leaf) -and [IO.File]::ReadAllText($Path) -eq $Version)
    }

    # ── Part 1: Direct production download helpers with real curl and file:// ──
    $fixtureUri = ([System.Uri]([IO.Path]::GetFullPath($fixtureZip))).AbsoluteUri
    $plainDlDir = Join-Path $scratch 'download-plain'
    [System.IO.Directory]::CreateDirectory($plainDlDir) | Out-Null
    $bracketDlDir = Join-Path $scratch 'download-bracket-[test]'
    [System.IO.Directory]::CreateDirectory($bracketDlDir) | Out-Null

    # Test plain destination
    $plainDlTarget = Join-Path $plainDlDir 'release.zip'
    Assert (Show-ProgressDownload -Url $fixtureUri -Destination $plainDlTarget -Label "Plain test download") 'Show-ProgressDownload plain succeeds'
    Assert (Test-Path -LiteralPath $plainDlTarget) 'Plain download target exists'
    Assert (-not (Test-Path -LiteralPath "$plainDlTarget.part")) 'Plain .part file removed'
    Assert ((Get-FileHash -LiteralPath $plainDlTarget -Algorithm SHA256).Hash -eq $validHash) 'Plain download bytes match fixture'

    # Plain-path regression assertion for GGUF download caller (install-windows.ps1:386)
    $plainGgufTarget = Join-Path $plainDlDir 'model.gguf'
    Assert (Invoke-DownloadWithRetry -Url $fixtureUri -Destination $plainGgufTarget -Label "Downloading model.gguf" -MaxRetries 4) 'Invoke-DownloadWithRetry plain GGUF caller succeeds'
    Assert (Test-Path -LiteralPath $plainGgufTarget) 'Plain GGUF download target exists'
    Assert (-not (Test-Path -LiteralPath "$plainGgufTarget.part")) 'Plain GGUF .part file removed'
    Assert ((Get-FileHash -LiteralPath $plainGgufTarget -Algorithm SHA256).Hash -eq $validHash) 'Plain GGUF download bytes match fixture'

    # Test bracketed directory destination
    $bracketDlTarget = Join-Path $bracketDlDir 'release.zip'
    Assert (Show-ProgressDownload -Url $fixtureUri -Destination $bracketDlTarget -Label "Bracket test download") 'Show-ProgressDownload bracketed succeeds'
    Assert (Test-Path -LiteralPath $bracketDlTarget) 'Bracketed download target exists'
    Assert (-not (Test-Path -LiteralPath "$bracketDlTarget.part")) 'Bracketed .part file removed'
    Assert ((Get-FileHash -LiteralPath $bracketDlTarget -Algorithm SHA256).Hash -eq $validHash) 'Bracketed download bytes match fixture'

    # Test bracketed filename destinations ([a]b.zip, a[.zip, ]a.zip)
    $bracketAbTarget = Join-Path $bracketDlDir '[a]b.zip'
    Assert (Invoke-DownloadWithRetry -Url $fixtureUri -Destination $bracketAbTarget -Label "Bracket [a]b download") 'Invoke-DownloadWithRetry [a]b succeeds'
    Assert (Test-Path -LiteralPath $bracketAbTarget) '[a]b download target exists'
    Assert (-not (Test-Path -LiteralPath "$bracketAbTarget.part")) '[a]b .part file removed'
    Assert ((Get-FileHash -LiteralPath $bracketAbTarget -Algorithm SHA256).Hash -eq $validHash) '[a]b download bytes match fixture'

    $bracketOpenTarget = Join-Path $bracketDlDir 'a[.zip'
    Assert (Invoke-DownloadWithRetry -Url $fixtureUri -Destination $bracketOpenTarget -Label "Bracket a[ download") 'Invoke-DownloadWithRetry a[ succeeds'
    Assert (Test-Path -LiteralPath $bracketOpenTarget) 'a[ download target exists'
    Assert (-not (Test-Path -LiteralPath "$bracketOpenTarget.part")) 'a[ .part file removed'

    $bracketCloseTarget = Join-Path $bracketDlDir ']a.zip'
    Assert (Invoke-DownloadWithRetry -Url $fixtureUri -Destination $bracketCloseTarget -Label "Bracket ]a download") 'Invoke-DownloadWithRetry ]a succeeds'
    Assert (Test-Path -LiteralPath $bracketCloseTarget) ']a download target exists'
    Assert (-not (Test-Path -LiteralPath "$bracketCloseTarget.part")) ']a .part file removed'

    # Curl failure injection
    $invalidUri = 'file:///C:/nonexistent-ods-fixture-dir-404/release.zip'
    $failTarget = Join-Path $bracketDlDir 'fail-target.zip'
    Assert (-not (Invoke-DownloadWithRetry -Url $invalidUri -Destination $failTarget -Label "Failing download" -MaxRetries 1)) 'Curl failure rejected'
    Assert (-not (Test-Path -LiteralPath $failTarget)) 'Failed download destination not created'

    # ── Part 2: Transport shim for Install-ODSOpenCode full chain ──────────────
    $shimDir = Join-Path $scratch 'shim-bin'
    [System.IO.Directory]::CreateDirectory($shimDir) | Out-Null
    $csFile = Join-Path $scratch 'curl_shim.cs'
    $csSource = @"
using System;
using System.IO;
class Program {
    static int Main(string[] args) {
        if (Environment.GetEnvironmentVariable("ODS_TEST_CURL_FAIL") == "1") {
            return 28;
        }
        string fixture = Environment.GetEnvironmentVariable("ODS_TEST_FIXTURE_ZIP");
        for (int i = 0; i < args.Length - 1; i++) {
            if (args[i] == "-o") {
                string dest = args[i + 1];
                if (!string.IsNullOrEmpty(fixture) && File.Exists(fixture)) {
                    File.Copy(fixture, dest, true);
                    return 0;
                }
            }
        }
        return 1;
    }
}
"@
    [IO.File]::WriteAllText($csFile, $csSource)
    $csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path -LiteralPath $csc)) {
        $csc = "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    }
    if (Test-Path -LiteralPath $csc) {
        & $csc /nologo /out:"$shimDir\curl.exe" "$csFile" | Out-Null
    } else {
        Add-Type -TypeDefinition $csSource -OutputAssembly "$shimDir\curl.exe" -OutputType ConsoleApplication
    }
    $env:PATH = "$shimDir;$originalPath"
    $env:ODS_TEST_FIXTURE_ZIP = $fixtureZip
    $env:ODS_TEST_CURL_FAIL = '0'

    # Helper to select active fixture zip in shim
    function Set-TestFixtureMode {
        if ($script:corruptZip) {
            $env:ODS_TEST_FIXTURE_ZIP = $corruptFixtureZip
        } elseif ($script:badVersion) {
            $env:ODS_TEST_FIXTURE_ZIP = $badFixtureZip
        } else {
            $env:ODS_TEST_FIXTURE_ZIP = $fixtureZip
        }
    }

    # ── Part 3: Install-ODSOpenCode on plain destination ──────────────────────
    $destination = Join-Path $scratch 'bin/opencode.exe'
    [System.IO.Directory]::CreateDirectory((Split-Path $destination)) | Out-Null
    $config = Join-Path $scratch 'custom-config.json'
    Set-Content -LiteralPath $config -Value 'custom config'

    # Fresh install
    Assert (Install-ODSOpenCode -Destination $destination) 'plain fresh install succeeds'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.18.32') 'plain fresh bytes correct'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $destination) -Filter '.ods-update-*').Count -eq 0) 'plain fresh staging cleaned'

    # Upgrade older binary
    [IO.File]::WriteAllText($destination, '1.2.18')
    Assert (Install-ODSOpenCode -Destination $destination) 'older binary upgraded'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.18.32') 'replacement bytes correct'
    Assert (Install-ODSOpenCode -Destination $destination) 'current version reusable'

    # Checksum failure
    [IO.File]::WriteAllText($destination, '1.2.18'); $script:badDigest = $true
    Assert (-not (Install-ODSOpenCode -Destination $destination)) 'checksum mismatch rejected'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.2.18') 'checksum failure preserves old binary'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $destination) -Filter '.ods-update-*').Count -eq 0) 'checksum failure staging cleaned'
    $script:badDigest = $false

    # Wrong staged version failure
    $script:badVersion = $true; Set-TestFixtureMode
    Assert (-not (Install-ODSOpenCode -Destination $destination)) 'wrong staged version rejected'
    Assert ([IO.File]::ReadAllText($destination) -eq '1.2.18') 'wrong version failure preserves old binary'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $destination) -Filter '.ods-update-*').Count -eq 0) 'wrong version staging cleaned'
    $script:badVersion = $false; Set-TestFixtureMode

    # Busy executable lock failure
    $handle = [IO.File]::Open($destination, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        Assert (-not (Install-ODSOpenCode -Destination $destination)) 'busy executable preserved without process termination'
    } finally {
        $handle.Dispose()
    }
    Assert ([IO.File]::ReadAllText($destination) -eq '1.2.18') 'busy failure preserves old binary'
    Assert ((Get-Content -LiteralPath $config -Raw).Trim() -eq 'custom config') 'custom config untouched'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $destination) -Filter '.ods-update-*').Count -eq 0) 'busy staging cleaned'

    # ── Part 4: Install-ODSOpenCode on bracketed destinations ──────────────────
    $bracketDestination = Join-Path $scratch 'bracket-[test]/bin/opencode.exe'
    [System.IO.Directory]::CreateDirectory((Split-Path $bracketDestination)) | Out-Null
    [IO.File]::WriteAllText($bracketDestination, '1.2.18')
    Assert (Install-ODSOpenCode -Destination $bracketDestination) 'bracketed destination upgraded'
    Assert ([IO.File]::ReadAllText($bracketDestination) -eq '1.18.32') 'bracketed replacement bytes correct'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $bracketDestination) -Filter '.ods-update-*').Count -eq 0) 'bracketed staging cleaned'

    # Destination with bracketed filename ([a]b.exe)
    $bracketAbExe = Join-Path $scratch 'bracket-[test]/bin/[a]b.exe'
    [IO.File]::WriteAllText($bracketAbExe, '1.2.18')
    Assert (Install-ODSOpenCode -Destination $bracketAbExe) 'bracketed filename [a]b.exe upgraded'
    Assert ([IO.File]::ReadAllText($bracketAbExe) -eq '1.18.32') '[a]b.exe replacement bytes correct'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $bracketAbExe) -Filter '.ods-update-*').Count -eq 0) '[a]b.exe staging cleaned'

    # Destination with unmatched open bracket (a[.exe)
    $bracketOpenExe = Join-Path $scratch 'bracket-[test]/bin/a[.exe'
    [IO.File]::WriteAllText($bracketOpenExe, '1.2.18')
    Assert (Install-ODSOpenCode -Destination $bracketOpenExe) 'bracketed filename a[.exe upgraded'
    Assert ([IO.File]::ReadAllText($bracketOpenExe) -eq '1.18.32') 'a[.exe replacement bytes correct'

    # Destination with unmatched close bracket (]a.exe)
    $bracketCloseExe = Join-Path $scratch 'bracket-[test]/bin/]a.exe'
    [IO.File]::WriteAllText($bracketCloseExe, '1.2.18')
    Assert (Install-ODSOpenCode -Destination $bracketCloseExe) 'bracketed filename ]a.exe upgraded'
    Assert ([IO.File]::ReadAllText($bracketCloseExe) -eq '1.18.32') ']a.exe replacement bytes correct'

    # Failure injection in bracketed destination: corrupt ZIP
    $script:corruptZip = $true; Set-TestFixtureMode
    [IO.File]::WriteAllText($bracketDestination, '1.2.18')
    Assert (-not (Install-ODSOpenCode -Destination $bracketDestination)) 'bracketed corrupt zip rejected'
    Assert ([IO.File]::ReadAllText($bracketDestination) -eq '1.2.18') 'corrupt zip preserves old binary'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $bracketDestination) -Filter '.ods-update-*').Count -eq 0) 'corrupt zip staging cleaned'
    $script:corruptZip = $false; Set-TestFixtureMode

    # Failure injection in bracketed destination: curl download failure
    $env:ODS_TEST_CURL_FAIL = '1'
    [IO.File]::WriteAllText($bracketDestination, '1.2.18')
    Assert (-not (Install-ODSOpenCode -Destination $bracketDestination)) 'bracketed curl failure rejected'
    Assert ([IO.File]::ReadAllText($bracketDestination) -eq '1.2.18') 'curl failure preserves old binary'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path $bracketDestination) -Filter '.ods-update-*').Count -eq 0) 'curl failure staging cleaned'
    $env:ODS_TEST_CURL_FAIL = '0'

} finally {
    $env:PATH = $originalPath
    $resolved = [IO.Path]::GetFullPath($scratch)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
}
