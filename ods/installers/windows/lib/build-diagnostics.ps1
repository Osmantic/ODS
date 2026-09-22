function New-ODSWindowsBuildDiagnostic {
    param(
        [Parameter(Mandatory = $true)][string]$Service,
        [Parameter(Mandatory = $true)][datetime]$StartedAt,
        [Parameter(Mandatory = $true)][datetime]$FinishedAt,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][string]$CacheMode,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [int]$LogStartLine = 0
    )

    $newLog = @()
    if (Test-Path -LiteralPath $LogPath) {
        $allLog = @(Get-Content -LiteralPath $LogPath -ErrorAction SilentlyContinue)
        if ($LogStartLine -lt $allLog.Count) { $newLog = @($allLog | Select-Object -Skip $LogStartLine) }
    }
    $cacheLines = @($newLog | Where-Object { $_ -match '(?i)\b(?:CACHED|Using cache)\b' })
    [ordered]@{
        service = $Service
        startedAt = $StartedAt.ToUniversalTime().ToString("o")
        finishedAt = $FinishedAt.ToUniversalTime().ToString("o")
        durationSeconds = [math]::Round(($FinishedAt - $StartedAt).TotalSeconds, 3)
        exitCode = $ExitCode
        cacheMode = $CacheMode
        cacheHit = ($cacheLines.Count -gt 0)
        cacheEvidenceLines = @($cacheLines | Select-Object -First 5)
        logPath = $LogPath
    }
}

function Write-ODSWindowsBuildDiagnostics {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object[]]$Records
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null }
    $payload = [ordered]@{ version = "1"; generatedAt = (Get-Date).ToUniversalTime().ToString("o"); builds = @($Records) }
    $payload | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction Stop
}
