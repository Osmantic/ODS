$ErrorActionPreference = "Stop"
. (Resolve-Path (Join-Path $PSScriptRoot "..\installers\windows\lib\service-smoke.ps1"))
$pass = Test-ODSWindowsComposeServiceRecords -EnabledServices @("dashboard", "api") -ServiceRecords @(
    [pscustomobject]@{ Service = "dashboard"; State = "running"; Health = "healthy" },
    [pscustomobject]@{ Service = "api"; State = "up"; Health = "none" }
)
if (-not $pass.Passed) { throw "healthy services should pass" }
$fail = Test-ODSWindowsComposeServiceRecords -EnabledServices @("dashboard", "api", "missing") -ServiceRecords @(
    [pscustomobject]@{ Service = "dashboard"; State = "running"; Health = "unhealthy" },
    [pscustomobject]@{ Service = "api"; State = "exited"; Health = "none" }
)
if ($fail.Passed -or $fail.Failures.Count -ne 3) { throw "missing, exited, and unhealthy services should fail" }
Write-Output "PASS: enabled-service smoke gate"
