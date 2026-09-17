$ErrorActionPreference = "Stop"
$helper = Join-Path $PSScriptRoot "..\installers\windows\lib\build-receipt.ps1"
. (Resolve-Path $helper)
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ods-build-receipt-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $path = Join-Path $tmp "compose-build-failure.json"
    $result = Write-ODSWindowsBuildFailureReceipt -ReceiptPath $path -Service "comfyui" -BuildLog "C:\logs\compose-build.log" -Recovery "restart Docker"
    $json = Get-Content $path -Raw | ConvertFrom-Json
    if ($json.status -ne "docker-daemon-unavailable") { throw "wrong status" }
    if ($json.service -ne "comfyui") { throw "wrong service" }
    if ($json.build_log -ne "C:\logs\compose-build.log") { throw "wrong build log" }
    if ($json.recovery -ne "restart Docker") { throw "wrong recovery" }
    if (-not $result.timestamp) { throw "missing timestamp" }
    Write-Output "PASS: daemon-loss receipt contains expected fields"
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
