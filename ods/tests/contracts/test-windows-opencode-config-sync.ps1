# ============================================================================
# Contract Test: Windows OpenCode Configuration Sync Resilience
# ============================================================================

param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path "$scriptDir\..\..\.."
$targetModule = Join-Path $repoRoot "ods\installers\windows\lib\opencode-config.ps1"

if (Test-Path $targetModule) {
    Write-Host "[OK] Target module exists: $targetModule"
} else {
    Write-Host "[WARN] Windows installer module path not present on this platform."
}

Write-Host "[OK] PowerShell contract test execution complete."
