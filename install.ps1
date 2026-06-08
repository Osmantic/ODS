# Dream Server Root Installer (Windows)
# Delegates to dream-server/installers/windows/install-windows.ps1

param(
    [switch]$DryRun,
    [switch]$Force,
    [switch]$NonInteractive,
    [string]$Tier = "",
    [switch]$Voice,
    [switch]$Workflows,
    [switch]$Rag,
    [switch]$Recommended,
    [switch]$NoRecommended,
    [switch]$Hermes,
    [switch]$NoHermes,
    [switch]$OpenClaw,
    [switch]$All,
    [switch]$Cloud,
    [switch]$Comfyui,
    [switch]$NoComfyui,
    [switch]$Langfuse,
    [switch]$NoLangfuse,
    [switch]$NoBootstrap,
    [switch]$Lan,
    [string]$SummaryJsonPath = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Dream Server Installer" -ForegroundColor Cyan
Write-Host ""

# Delegate to Windows installer
$DreamServerInstaller = Join-Path (Join-Path (Join-Path $ScriptDir "dream-server") "installers") "windows" | Join-Path -ChildPath "install-windows.ps1"
if (-not (Test-Path $DreamServerInstaller)) {
    Write-Host "Error: Windows installer not found" -ForegroundColor Red
    Write-Host "Expected: $DreamServerInstaller" -ForegroundColor Red
    exit 1
}

# Forward all bound parameters to the real installer.
# A successful PowerShell script can leave a stale $LASTEXITCODE from a handled
# native command, so only use $LASTEXITCODE when the delegated installer fails.
$global:LASTEXITCODE = 0
& $DreamServerInstaller @PSBoundParameters
$installerSucceeded = $?
$installerExit = if ($null -ne $global:LASTEXITCODE) { [int]$global:LASTEXITCODE } else { 0 }
if ($installerExit -ne 0) {
    exit $installerExit
}
if ($installerSucceeded) {
    exit 0
}
exit 1
