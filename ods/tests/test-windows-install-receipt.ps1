$ErrorActionPreference = "Stop"
. (Resolve-Path (Join-Path $PSScriptRoot "..\installers\windows\lib\install-receipt.ps1"))
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ods-install-receipt-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $path = Join-Path $tmp "install-receipt.json"
    $payload = New-ODSWindowsInstallReceiptPayload -AllHealthy $true `
        -DegradedCapabilities @("xet-model-downloads") `
        -FailedOptionalSetup @("huggingface-hub-xet", "huggingface-hub-xet") `
        -SkippedServices @("claude-code (Node.js unavailable)") `
        -StartupFallbacks @("host-agent-startup-folder") `
        -Features @{ hermes = $true } -Readiness @{ AllReady = $true }
    if ($payload.status -ne "degraded" -or $payload.failedOptionalSetup.Count -ne 1) { throw "payload status or deduplication incorrect" }
    Write-ODSWindowsInstallReceipt -Path $path -Payload $payload | Out-Null
    $json = Get-Content $path -Raw | ConvertFrom-Json
    foreach ($field in @("status", "degradedCapabilities", "failedOptionalSetup", "skippedServices", "startupFallbacks", "features", "readiness", "generatedAt")) {
        if ($null -eq $json.$field) { throw "missing receipt field: $field" }
    }
    if ($json.status -ne "degraded" -or $json.failedOptionalSetup[0] -ne "huggingface-hub-xet" -or $json.startupFallbacks[0] -ne "host-agent-startup-folder") {
        throw "receipt values incorrect"
    }
    Write-Output "PASS: install receipt captures degraded setup and startup fallback"
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
