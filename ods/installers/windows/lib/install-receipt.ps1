function Write-ODSWindowsInstallReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Payload
    )

    $receipt = [ordered]@{
        version = "1"
        status = if ($Payload.ContainsKey("status")) { $Payload.status } else { "completed" }
        degradedCapabilities = @($Payload.degradedCapabilities)
        failedOptionalSetup = @($Payload.failedOptionalSetup)
        skippedServices = @($Payload.skippedServices)
        startupFallbacks = @($Payload.startupFallbacks)
        features = if ($Payload.ContainsKey("features")) { $Payload.features } else { @{} }
        readiness = if ($Payload.ContainsKey("readiness")) { $Payload.readiness } else { @{} }
        generatedAt = (Get-Date).ToUniversalTime().ToString("o")
    }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null
    }
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction Stop
    return $receipt
}

function New-ODSWindowsInstallReceiptPayload {
    param(
        [Parameter(Mandatory = $true)][bool]$AllHealthy,
        [string[]]$DegradedCapabilities = @(),
        [string[]]$FailedOptionalSetup = @(),
        [string[]]$SkippedServices = @(),
        [string[]]$StartupFallbacks = @(),
        [hashtable]$Features = @{},
        $Readiness = @{}
    )

    $degraded = @($DegradedCapabilities | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $failed = @($FailedOptionalSetup | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $skipped = @($SkippedServices | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $fallbacks = @($StartupFallbacks | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    [ordered]@{
        status = if ($AllHealthy -and $degraded.Count -eq 0 -and $failed.Count -eq 0 -and $skipped.Count -eq 0 -and $fallbacks.Count -eq 0) { "ready" } else { "degraded" }
        degradedCapabilities = @($degraded | Select-Object -Unique)
        failedOptionalSetup = @($failed | Select-Object -Unique)
        skippedServices = @($skipped | Select-Object -Unique)
        startupFallbacks = @($fallbacks | Select-Object -Unique)
        features = $Features
        readiness = $Readiness
    }
}
