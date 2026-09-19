$ErrorActionPreference = "Stop"
. (Resolve-Path (Join-Path $PSScriptRoot "..\installers\windows\lib\build-diagnostics.ps1"))
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ods-build-diagnostics-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $log = Join-Path $tmp "build.log"
    @("#1 [stage] CACHED", "done") | Set-Content -LiteralPath $log
    $started = [datetime]::Parse("2026-01-01T00:00:00Z")
    $finished = $started.AddSeconds(2.5)
    $record = New-ODSWindowsBuildDiagnostic -Service "dashboard" -StartedAt $started -FinishedAt $finished -ExitCode 0 -CacheMode "no-cache" -LogPath $log
    if ($record.service -ne "dashboard" -or $record.durationSeconds -ne 2.5 -or -not $record.cacheHit) { throw "diagnostic record incorrect" }
    $path = Join-Path $tmp "diagnostics.json"
    Write-ODSWindowsBuildDiagnostics -Path $path -Records @($record)
    $json = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($json.builds.Count -ne 1 -or $json.builds[0].cacheEvidenceLines.Count -ne 1) { throw "diagnostic JSON incorrect" }
    Write-Output "PASS: build timing and cache diagnostics"
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
