# ============================================================================
# ODS Windows Native Desktop -- Resumable, Verified GGUF Model Acquisition
# ============================================================================
# Part of: installers/windows/lib/
# Purpose:
#   1. Download GGUF models over HTTPS with curl.exe --continue-at - and no
#      wall-clock time cap so slow connections never time out mid-download.
#   2. Recover automatically if a server returns HTTP 416 / curl exit 33 on a
#      stale or complete .partial file.
#   3. Record a sidecar stamp (<model>.gguf.sha256.json) containing {size,
#      lastWriteUtc, sha256} after verifying the SHA-256 once, so subsequent
#      startup checks and installer reruns verify multi-GB models in <50 ms
#      instead of re-reading gigabytes from disk.
#      Any change to file size or LastWriteTimeUtc invalidates the stamp and
#      triggers a fresh SHA-256 computation.
# ============================================================================

. (Join-Path $PSScriptRoot 'private-file.ps1')

$script:ODSModelStampSchemaVersion = 1

function Get-ODSModelStampPath {
    param([Parameter(Mandatory = $true)][string]$ModelPath)
    return ($ModelPath + '.sha256.json')
}

function Read-ODSModelSha256Stamp {
    param([Parameter(Mandatory = $true)][string]$ModelPath)
    $stampPath = Get-ODSModelStampPath -ModelPath $ModelPath
    if (-not (Test-Path -LiteralPath $stampPath -PathType Leaf)) { return $null }
    try {
        $raw = [IO.File]::ReadAllText($stampPath, [Text.UTF8Encoding]::new($false, $true))
        $obj = $raw | ConvertFrom-Json -ErrorAction Stop
        if ($obj.schemaVersion -ne $script:ODSModelStampSchemaVersion -or
            [string]$obj.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            ($obj.size -isnot [int] -and $obj.size -isnot [long]) -or
            [string]::IsNullOrWhiteSpace([string]$obj.lastWriteUtc)) {
            return $null
        }
        return $obj
    } catch {
        return $null
    }
}

function Write-ODSModelSha256Stamp {
    param(
        [Parameter(Mandatory = $true)][string]$ModelPath,
        [Parameter(Mandatory = $true)][string]$Sha256
    )
    $normalizedSha = $Sha256.Trim().ToLowerInvariant()
    if ($normalizedSha -cnotmatch '^[0-9a-f]{64}$') {
        throw "Invalid SHA-256 digest for model stamp: '$Sha256'"
    }
    $item = Get-Item -LiteralPath $ModelPath -ErrorAction Stop
        $record = [ordered]@{
        schemaVersion  = $script:ODSModelStampSchemaVersion
        file           = $item.Name
        size           = [long]$item.Length
        lastWriteUtc   = $item.LastWriteTimeUtc.ToString('o')
        lastWriteTicks = [long]$item.LastWriteTimeUtc.Ticks
        sha256         = $normalizedSha
    }
    $stampPath = Get-ODSModelStampPath -ModelPath $ModelPath
    Write-ODSPrivateEnvFile -Path $stampPath -Content ($record | ConvertTo-Json -Compress)
    return [pscustomobject]$record
}

function Test-ODSVerifiedModelFile {
    <#
    .SYNOPSIS
        Verify a GGUF file against its expected SHA-256 using the fast sidecar
        stamp when size and LastWriteTimeUtc match, or hashing once and writing
        the stamp when needed.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ModelPath,
        [string]$ExpectedSha256 = '',
        [long]$ExpectedSize = 0
    )

    if (-not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) {
        return [pscustomobject]@{
            Verified  = $false
            Sha256    = ''
            Size      = 0L
            FromStamp = $false
            Reason    = 'missing'
        }
    }

    $item = Get-Item -LiteralPath $ModelPath -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to use a symlink or reparse point in the model store: $ModelPath"
    }
    $size = [long]$item.Length
    if ($size -le 0) {
        return [pscustomobject]@{
            Verified  = $false
            Sha256    = ''
            Size      = $size
            FromStamp = $false
            Reason    = 'empty'
        }
    }
    if ($ExpectedSize -gt 0 -and $size -ne $ExpectedSize) {
        return [pscustomobject]@{
            Verified  = $false
            Sha256    = ''
            Size      = $size
            FromStamp = $false
            Reason    = "size_mismatch (got $size, expected $ExpectedSize)"
        }
    }

    $expectedNorm = $ExpectedSha256.Trim().ToLowerInvariant()
    if ($expectedNorm -and $expectedNorm -cnotmatch '^[0-9a-f]{64}$') {
        throw "ExpectedSha256 must be a 64-character hex string, not '$ExpectedSha256'."
    }

    $stamp = Read-ODSModelSha256Stamp -ModelPath $ModelPath
    $currentTicks = [long]$item.LastWriteTimeUtc.Ticks
    $stampMatchesTime = $false
    if ($null -ne $stamp) {
        if ($null -ne $stamp.lastWriteTicks -and [long]$stamp.lastWriteTicks -eq $currentTicks) {
            $stampMatchesTime = $true
        } elseif ($stamp.lastWriteUtc -is [datetime] -and $stamp.lastWriteUtc.ToUniversalTime().Ticks -eq $currentTicks) {
            $stampMatchesTime = $true
        } elseif ([string]$stamp.lastWriteUtc -ceq $item.LastWriteTimeUtc.ToString('o')) {
            $stampMatchesTime = $true
        }
    }
    if ($null -ne $stamp -and
        [long]$stamp.size -eq $size -and
        $stampMatchesTime) {
        if (-not $expectedNorm -or [string]$stamp.sha256 -ceq $expectedNorm) {
            return [pscustomobject]@{
                Verified  = $true
                Sha256    = [string]$stamp.sha256
                Size      = $size
                FromStamp = $true
                Reason    = 'ok'
            }
        }
        return [pscustomobject]@{
            Verified  = $false
            Sha256    = [string]$stamp.sha256
            Size      = $size
            FromStamp = $true
            Reason    = "sha256_mismatch (stamp $($stamp.sha256), expected $expectedNorm)"
        }
    }

    $computed = (Get-FileHash -LiteralPath $ModelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expectedNorm -and $computed -cne $expectedNorm) {
        return [pscustomobject]@{
            Verified  = $false
            Sha256    = $computed
            Size      = $size
            FromStamp = $false
            Reason    = "sha256_mismatch (got $computed, expected $expectedNorm)"
        }
    }

    $null = Write-ODSModelSha256Stamp -ModelPath $ModelPath -Sha256 $computed
    return [pscustomobject]@{
        Verified  = $true
        Sha256    = $computed
        Size      = $size
        FromStamp = $false
        Reason    = 'ok'
    }
}

function Invoke-ODSResumableModelDownload {
    <#
    .SYNOPSIS
        Download and verify a GGUF model into ModelsDir with resumable curl.exe
        and sidecar SHA-256 stamp caching.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ModelsDir,
        [Parameter(Mandatory = $true)][string]$GgufFile,
        [Parameter(Mandatory = $true)][string]$Url,
        [string]$ExpectedSha256 = '',
        [long]$ExpectedSize = 0,
        [AllowNull()][scriptblock]$CurlRunner = $null
    )

    if ($GgufFile -cnotmatch '^[\x20-\x7e]{1,240}$' -or
        $GgufFile -notmatch '\.gguf$' -or
        $GgufFile -match '[\\/:*?"<>|]' -or
        $GgufFile.StartsWith('.')) {
        throw "Invalid GGUF file name: '$GgufFile'"
    }
    if ($Url -notmatch '^https://') {
        throw "Refusing to download model over non-HTTPS URL: '$Url'"
    }

    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    $target = Join-Path $ModelsDir $GgufFile

    $check = Test-ODSVerifiedModelFile -ModelPath $target -ExpectedSha256 $ExpectedSha256 -ExpectedSize $ExpectedSize
    if ($check.Verified) {
        return [pscustomobject]@{
            Path      = $target
            Sha256    = $check.Sha256
            Size      = $check.Size
            Status    = 'cached'
            FromStamp = $check.FromStamp
        }
    }

    if (Test-Path -LiteralPath $target -PathType Leaf) {
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    }
    $stampPath = Get-ODSModelStampPath -ModelPath $target
    if (Test-Path -LiteralPath $stampPath -PathType Leaf) {
        Remove-Item -LiteralPath $stampPath -Force -ErrorAction SilentlyContinue
    }

    $partial = "$target.partial"
    $runCurl = {
        param([bool]$Resume, [string]$OutFile, [string]$DownloadUrl)
        $curlArgs = @(
            '--fail',
            '--location',
            '--proto', '=https',
            '--proto-redir', '=https',
            '--retry', '3',
            '--retry-delay', '2'
        )
        if ($Resume) {
            $curlArgs += @('--continue-at', '-')
        }
        $curlArgs += @('--output', $OutFile, $DownloadUrl)
        if ($null -ne $CurlRunner) {
            return (& $CurlRunner $curlArgs $OutFile $DownloadUrl)
        }
        & curl.exe @curlArgs
        return $LASTEXITCODE
    }

    $exitCode = & $runCurl $true $partial $Url
    # curl exit 33 (CURLE_RANGE_ERROR / HTTP 416) can occur if the .partial file
    # was already full length or the upstream mirror reset byte ranges.
    if ($exitCode -eq 33 -and (Test-Path -LiteralPath $partial -PathType Leaf)) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        $exitCode = & $runCurl $false $partial $Url
    }
    if ($exitCode -ne 0) {
        throw "Model download failed for $GgufFile (curl exit $exitCode). Rerun setup to resume from $partial."
    }

    if (-not (Test-Path -LiteralPath $partial -PathType Leaf)) {
        throw "Model download produced no file at $partial."
    }

    $partialItem = Get-Item -LiteralPath $partial -ErrorAction Stop
    if ($ExpectedSize -gt 0 -and [long]$partialItem.Length -ne $ExpectedSize) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        throw "Downloaded model $GgufFile has size $($partialItem.Length) bytes, expected $ExpectedSize bytes."
    }

    $actualSha = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedNorm = $ExpectedSha256.Trim().ToLowerInvariant()
    if ($expectedNorm -and $actualSha -cne $expectedNorm) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        throw "Downloaded model $GgufFile failed SHA-256 verification (got $actualSha, expected $expectedNorm)."
    }

    Move-Item -LiteralPath $partial -Destination $target -Force
    $stamp = Write-ODSModelSha256Stamp -ModelPath $target -Sha256 $actualSha

    return [pscustomobject]@{
        Path      = $target
        Sha256    = $stamp.sha256
        Size      = $stamp.size
        Status    = 'downloaded'
        FromStamp = $false
    }
}
