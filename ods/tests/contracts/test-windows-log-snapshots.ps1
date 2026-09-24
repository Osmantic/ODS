$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$cli = Get-Content -Raw (Join-Path $root "installers\windows\ods.ps1")

$required = @(
    '[string]$Since = ""',
    '[string]$Until = ""',
    '[bool]$Follow = $true',
    'if ($Follow) { $logArgs += "-f" }',
    '$logArgs += @("--since", $Since)',
    '$logArgs += @("--until", $Until)',
    'Invoke-Logs -Service $svc -Lines $n -Since $since -Until $until -Follow $follow'
)

foreach ($snippet in $required) {
    if (-not $cli.Contains($snippet)) {
        throw "Missing Windows log snapshot contract: $snippet"
    }
}

Write-Host "[PASS] Windows log snapshot argument contract"
