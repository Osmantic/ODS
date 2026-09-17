function Write-ODSWindowsBuildFailureReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$Service,
        [Parameter(Mandatory = $true)][string]$BuildLog,
        [Parameter(Mandatory = $true)][string]$Recovery
    )

    $receipt = [ordered]@{
        status = "docker-daemon-unavailable"
        service = $Service
        build_log = $BuildLog
        recovery = $Recovery
        timestamp = (Get-Date).ToUniversalTime().ToString("o")
    }
    $receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8 -ErrorAction Stop
    return $receipt
}
