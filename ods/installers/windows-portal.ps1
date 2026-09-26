# Windows entry point for the existing Linux Pixel installer.
param(
    [string]$Distro = "",  # empty: reuse the single existing Ubuntu, else Ubuntu-24.04
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

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows/lib/wsl-portal-setup.ps1')
try {
    $result = Invoke-ODSPortalSetup -Options $PSBoundParameters -InstallerRoot $PSScriptRoot
    exit $result
} catch {
    Write-Host ("ODS Portal setup stopped: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Correct the reported prerequisite, then rerun the same install.ps1 command. No native Windows or Hermes fallback was started.'
    exit 1
}
