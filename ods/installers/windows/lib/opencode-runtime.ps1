# Verified release upgrade. No user configuration or session files are touched.
function Get-ODSOpenCodeRelease {
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $manifest = Join-Path $PSScriptRoot '../../lib/opencode-release.tsv'
    $release = @(Import-Csv -LiteralPath $manifest -Delimiter "`t" | Where-Object { $_.platform -eq 'Windows' -and $_.arch -eq $arch })
    if ($release.Count -ne 1) { throw "Unsupported OpenCode Windows architecture: $arch" }
    return $release[0]
}

function Test-ODSOpenCodeVersion {
    param([string]$Path, [string]$Version)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try { return ((& $Path --version 2>$null) -eq $Version -and $LASTEXITCODE -eq 0) } catch { return $false }
}

function Install-ODSOpenCode {
    param([string]$Destination = $script:OPENCODE_EXE)
    $release = Get-ODSOpenCodeRelease
    if (Test-ODSOpenCodeVersion -Path $Destination -Version $release.version) {
        return $true
    }
    $bin = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    $stage = Join-Path $bin ('.ods-update-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    try {
        $archive = Join-Path $stage 'release.zip'
        $url = "https://github.com/anomalyco/opencode/releases/download/v$($release.version)/$($release.asset)"
        if (-not (Invoke-DownloadWithRetry -Url $url -Destination $archive -Label "OpenCode $($release.version)")) { throw 'OpenCode download failed' }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $release.sha256) { throw 'OpenCode archive SHA256 mismatch' }
        $unpacked = Join-Path $stage 'extracted'
        if (-not (Invoke-ExtractionWithRetry -ZipPath $archive -DestinationPath $unpacked)) { throw 'OpenCode extraction failed' }
        $binary = @(Get-ChildItem -LiteralPath $unpacked -Filter opencode.exe -File -Recurse)
        if ($binary.Count -ne 1) { throw 'Expected exactly one staged opencode.exe' }
        if (-not (Test-ODSOpenCodeVersion -Path $binary[0].FullName -Version $release.version)) { throw 'OpenCode staged version mismatch' }
        # Windows locks active executables. Fail without stopping arbitrary work;
        # the campaign/owner must settle sessions before rerunning this upgrade.
        if (Test-Path -LiteralPath $Destination) {
            [IO.File]::Replace($binary[0].FullName, $Destination, (Join-Path $stage 'previous.exe'))
        } else {
            [IO.File]::Move($binary[0].FullName, $Destination)
        }
        return $true
    } catch {
        Write-AIWarn "OpenCode upgrade failed; previous binary preserved: $_"
        return $false
    } finally {
        $resolvedStage = [IO.Path]::GetFullPath($stage)
        $resolvedBin = [IO.Path]::GetFullPath($bin).TrimEnd('\') + '\'
        if (-not $resolvedStage.StartsWith($resolvedBin, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid OpenCode staging path' }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
