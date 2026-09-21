$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$uiPath = Join-Path $root "installers\windows\lib\ui.ps1"
if (-not (Test-Path -LiteralPath $uiPath)) {
    throw "Windows installer UI helper not found: $uiPath"
}

$env:ODS_UI_MODE = "plain"
$env:NO_COLOR = "1"
$script:ODS_VERSION = "test"
$script:ODS_INSTALL_DIR = Join-Path ([IO.Path]::GetTempPath()) "ods-presentation-contract-missing"
$script:INSTALL_START = Get-Date
$script:nonInteractive = $true
$script:cloudMode = $false

. $uiPath

# Keep the renderer contract portable across the workflow's Windows and Ubuntu
# PowerShell lanes without depending on the Windows-only NetTCPIP module.
function Get-NetIPAddress {
    param(
        [string]$AddressFamily,
        [System.Management.Automation.ActionPreference]$ErrorAction
    )
    return @()
}

$rendered = (& {
    Write-ODSBanner
    foreach ($phase in 1..9) {
        Write-Phase -Phase $phase -Total 13 -Name "INTERNAL-$phase" -Estimate "test"
    }
    Write-SuccessCard -WebUIPort "3000" -DashboardPort "3001"
} 6>&1 | Out-String)

if ($rendered -notmatch "O D S G A T E") {
    throw "Restored ODSGATE identity is missing from the Windows banner"
}
if ($rendered -notmatch "THE ODS GATEWAY IS OPEN") {
    throw "Restored gateway completion line is missing"
}
if ($rendered -match [char]27) {
    throw "Plain Windows output contains an ANSI escape"
}

$phaseMatches = [regex]::Matches($rendered, "PHASE ([1-6])/6")
if ($phaseMatches.Count -ne 6) {
    throw "Expected six user-facing phase headings, found $($phaseMatches.Count)"
}
$actual = @($phaseMatches | ForEach-Object { [int]$_.Groups[1].Value })
if (($actual -join ",") -ne "1,2,3,4,5,6") {
    throw "User-facing phases are not stable and contiguous: $($actual -join ',')"
}

Write-Host "[PASS] Windows installer presentation preserves the six-phase plain-output contract"
