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
    # Ignored: the legacy OpenClaw extension was removed. Still accepted so
    # existing commands keep working; the setup prints a notice.
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
    [string]$SummaryJsonPath = "",
    [string]$StateRoot = "",
    # API mode: an OpenAI-compatible server (or Ollama / LM Studio) serves the
    # model instead of a model on this computer. The key file stays on Windows;
    # setup passes the key to Ubuntu in an environment variable, never argv.
    [string]$ExternalLlmUrl = "",
    [string]$ExternalLlmModel = "",
    [string]$ExternalLlmProvider = "",
    [string]$ExternalLlmKeyFile = "",
    # Leave API mode: the model runs on this computer again.
    [switch]$NoExternalLlm
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows/lib/wsl-portal-setup.ps1')
# The ODS community Discord. Keep in sync with ODS_HELP_DISCORD_URL in
# installers/lib/constants.sh (tests/test-help-links.sh checks every surface).
$helpLine = 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC (share the messages above).'
try {
    $result = Invoke-ODSPortalSetup -Options $PSBoundParameters -InstallerRoot $PSScriptRoot
    if ($result -ne 0) { Write-Host $helpLine -ForegroundColor Yellow }
    exit $result
} catch {
    Write-Host ("ODS Portal setup stopped: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Correct the reported prerequisite, then rerun the same install.ps1 command. No native Windows or Hermes fallback was started.'
    Write-Host $helpLine -ForegroundColor Yellow
    exit 1
}
