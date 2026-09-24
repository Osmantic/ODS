# ODS Root Installer (Windows)
# Portal uses the existing Ubuntu/WSL installer; native mode is explicit.

param(
    [switch]$NativeWindows,
    [string]$Distro = "",
    [string]$ModelsDirectory = "",
    [ValidateRange(1,65535)][int]$InferencePort = 18080,
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
    [string]$InstallDir = "",
    [string]$SummaryJsonPath = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $NativeWindows) {
    . (Join-Path $ScriptDir 'ods/installers/windows/lib/portal-install-plan.ps1')
    $portalPlan = Get-ODSWindowsPortalInstallPlan $PSBoundParameters
    if ($Distro) { $portalPlan.Distro = $Distro }
    if ($ModelsDirectory) { $portalPlan.ModelsDirectory = $ModelsDirectory }
    if ($PSBoundParameters.ContainsKey('InferencePort')) { $portalPlan.InferencePort = $InferencePort }
    Write-Host 'Installing Portal (Pixel/OpenClaw) through Ubuntu/WSL.'
    $global:LASTEXITCODE = 0
    & (Join-Path $ScriptDir 'ods/installers/windows.ps1') @portalPlan
    $portalSucceeded = $?
    $portalExitCode = $LASTEXITCODE
    if ($portalExitCode -ne 0) { exit $portalExitCode }
    if (-not $portalSucceeded) { exit 1 }
    exit 0
}
if ($Distro) { throw '-Distro applies only to the Portal WSL installation.' }
if ($ModelsDirectory -or $PSBoundParameters.ContainsKey('InferencePort')) { throw '-ModelsDirectory and -InferencePort apply only to the Portal WSL installation.' }
Write-Warning 'Native Windows mode does not install Portal. Hermes is optional; use the default WSL path for Pixel.'
$null = $PSBoundParameters.Remove('NativeWindows')
$null = $PSBoundParameters.Remove('Distro')

# Delegate to Windows installer
$ODSInstaller = Join-Path (Join-Path (Join-Path $ScriptDir "ods") "installers") "windows" | Join-Path -ChildPath "install-windows.ps1"
if (-not (Test-Path $ODSInstaller)) {
    Write-Host "Error: Windows installer not found" -ForegroundColor Red
    Write-Host "Expected: $ODSInstaller" -ForegroundColor Red
    exit 1
}

# Forward all bound parameters to the real installer.
# A successful PowerShell script can leave a stale $LASTEXITCODE from a handled
# native command, so only use $LASTEXITCODE when the delegated installer fails.
$global:LASTEXITCODE = 0
& $ODSInstaller @PSBoundParameters
$installerSucceeded = $?
$installerExit = if ($null -ne $global:LASTEXITCODE) { [int]$global:LASTEXITCODE } else { 0 }
if ($installerExit -ne 0) {
    exit $installerExit
}
if ($installerSucceeded) {
    exit 0
}
exit 1
