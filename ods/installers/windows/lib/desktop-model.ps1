# ============================================================================
# ODS Windows Desktop -- Model acquisition
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Download the Windows Desktop profile's catalog model so a slow or
#          interrupted connection always finishes eventually, and verify it
#          once. A later run or launch trusts a size/mtime stamp instead of
#          hashing gigabytes again on an ordinary laptop disk.
# Expects: curl.exe (Windows 10 1803+); the caller supplies the catalog pin.
# Provides: Assert-ODSDesktopModelPin, Get-ODSDesktopFileSha256,
#           New-ODSDesktopModelStamp, Test-ODSDesktopModelStamp,
#           Get-ODSDesktopModel
# Modder notes: there is no total time limit by design. Only a stalled
#   transfer (under 1 KiB/s for two minutes) ends an attempt; the partial file
#   is kept and the next attempt resumes it. The partial is deleted only when
#   it is provably wrong: larger than the pin, or complete with the wrong hash.
# ============================================================================

$script:ODSDesktopModelAttempts = 20
$script:ODSDesktopModelStampSchema = 1

function Assert-ODSDesktopModelPin($Pin) {
    if ([string]$Pin.File -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.gguf$') {
        throw "The model file name '$($Pin.File)' is not a plain .gguf file name."
    }
    if ([string]$Pin.Url -notmatch '^https://huggingface\.co/[^\s?#]+$') {
        throw "Refusing to download a model from an unexpected location: $($Pin.Url)"
    }
    if ([string]$Pin.Sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw "The model pin for $($Pin.File) has no valid SHA-256."
    }
    if ($null -ne $Pin.Size -and -not ([long]$Pin.Size -gt 0)) {
        throw "The model pin for $($Pin.File) has an invalid size."
    }
}

function Get-ODSDesktopFileSha256([string]$Path) {
    # CNG: never slower than the managed SHA256 that Get-FileHash uses in
    # Windows PowerShell 5.1, and several times faster on CPUs with SHA
    # extensions. .NET Framework needs SHA256Cng asked for by name; modern .NET
    # (PowerShell 7) has no such type, and its SHA256.Create() is already CNG
    # on Windows. The stamp below is what keeps launches from hashing at all.
    $algorithm = if ($PSVersionTable.PSEdition -eq 'Desktop') { [Security.Cryptography.SHA256Cng]::new() } else { [Security.Cryptography.SHA256]::Create() }
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream)) -replace '-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function New-ODSDesktopModelStamp([IO.FileInfo]$File, [string]$Sha256) {
    return [ordered]@{
        schemaVersion = $script:ODSDesktopModelStampSchema
        file = $File.Name
        size = [long]$File.Length
        lastWriteUtcTicks = [long]$File.LastWriteTimeUtc.Ticks
        sha256 = $Sha256
    }
}

function Test-ODSDesktopModelStamp($Stamp, [IO.FileInfo]$File, [string]$Sha256) {
    # Pure: the stamp vouches only for this exact file, unchanged since it was
    # hashed, and only for the hash the current pin expects.
    if ($null -eq $Stamp -or -not $File.Exists) { return $false }
    return ([int]$Stamp.schemaVersion -eq $script:ODSDesktopModelStampSchema -and
        [string]$Stamp.file -ceq $File.Name -and
        [long]$Stamp.size -eq [long]$File.Length -and
        [long]$Stamp.lastWriteUtcTicks -eq [long]$File.LastWriteTimeUtc.Ticks -and
        [string]$Stamp.sha256 -ceq $Sha256)
}

function Read-ODSDesktopModelStamp([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $text = [IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false, $true))
    # A torn or hand-edited stamp only costs one re-hash; nothing else is caught.
    try { return ($text | ConvertFrom-Json) } catch [ArgumentException] { return $null }
}

function Write-ODSDesktopModelStamp([string]$Path, $Stamp) {
    $json = $Stamp | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($Path, $json, [Text.UTF8Encoding]::new($false))
}

function Invoke-ODSDesktopModelCurl([string]$Url, [string]$Partial) {
    # HTTPS only, including redirects to Hugging Face's CDN. --continue-at -
    # resumes whatever the partial already holds. A transfer under 1 KiB/s for
    # 120 s is a stall; there is deliberately no total --max-time.
    & curl.exe --fail --location --proto '=https' --proto-redir '=https' --continue-at - `
        --speed-limit 1024 --speed-time 120 --connect-timeout 30 --output $Partial $Url | Out-Host
    return $LASTEXITCODE
}

function Invoke-ODSDesktopModelStep([scriptblock]$Step, [object[]]$Arguments) {
    # A step returns its exit code last. Anything a native tool printed before
    # it is shown to the owner, never mistaken for part of the exit code.
    $results = @(& $Step @Arguments)
    if ($results.Count -eq 0) { throw 'The model download step finished without an exit code.' }
    if ($results.Count -gt 1) { $results[0..($results.Count - 2)] | Out-Host }
    return [int]$results[-1]
}

function Get-ODSDesktopModel {
    param(
        [Parameter(Mandatory = $true)]$Pin,
        [Parameter(Mandatory = $true)][string]$ModelsDir,
        [switch]$Repair,
        [scriptblock]$Download = ${function:Invoke-ODSDesktopModelCurl},
        [int]$Attempts = $script:ODSDesktopModelAttempts
    )
    Assert-ODSDesktopModelPin $Pin
    $null = New-Item -ItemType Directory -Force -Path $ModelsDir
    $target = Join-Path $ModelsDir ([string]$Pin.File)
    $stampPath = "$target.ods-verified.json"
    $partial = "$target.partial"
    $expected = [string]$Pin.Sha256

    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $file = Get-Item -LiteralPath $target
        if (-not $Repair -and (Test-ODSDesktopModelStamp (Read-ODSDesktopModelStamp $stampPath) $file $expected)) {
            return [pscustomobject]@{ Path = $target; Hashed = $false; Downloaded = $false }
        }
        if ((Get-ODSDesktopFileSha256 $target) -ceq $expected) {
            Write-ODSDesktopModelStamp $stampPath (New-ODSDesktopModelStamp (Get-Item -LiteralPath $target) $expected)
            return [pscustomobject]@{ Path = $target; Hashed = $true; Downloaded = $false }
        }
        Write-Host "         $($Pin.File) does not match its checksum; downloading a fresh copy."
    }

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $before = if (Test-Path -LiteralPath $partial) { (Get-Item -LiteralPath $partial).Length } else { 0 }
        if ($null -ne $Pin.Size -and $before -gt [long]$Pin.Size) {
            Remove-Item -LiteralPath $partial -Force  # Larger than the pin: it can never verify.
            $before = 0
        }
        $code = Invoke-ODSDesktopModelStep $Download @([string]$Pin.Url, $partial)
        if ($code -eq 0) { break }
        $after = if (Test-Path -LiteralPath $partial) { (Get-Item -LiteralPath $partial).Length } else { 0 }
        if ($attempt -eq $Attempts) {
            throw ("The model download stopped (curl exit $code) after $Attempts attempts. " +
                "Your progress ($([math]::Round($after / 1GB, 2)) GB) is kept; run setup again to continue.")
        }
        Write-Host ("         The download paused (curl exit $code) at $([math]::Round($after / 1GB, 2)) GB. " +
            "Resuming ($attempt/$Attempts)...")
    }

    $actual = Get-ODSDesktopFileSha256 $partial
    if ($actual -cne $expected) {
        Remove-Item -LiteralPath $partial -Force
        throw "The downloaded model does not match its checksum ($actual, expected $expected). Run setup again to download it again."
    }
    if (Test-Path -LiteralPath $target) {
        [IO.File]::Replace($partial, $target, [System.Management.Automation.Language.NullString]::Value)
    } else {
        [IO.File]::Move($partial, $target)
    }
    Write-ODSDesktopModelStamp $stampPath (New-ODSDesktopModelStamp (Get-Item -LiteralPath $target) $expected)
    return [pscustomobject]@{ Path = $target; Hashed = $true; Downloaded = $true }
}
