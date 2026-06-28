[CmdletBinding()]
param(
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path $ScriptDir -Parent
$WindowsInstallerDir = Join-Path (Join-Path $RepoRoot "installers") "windows"
$LibDir = Join-Path $WindowsInstallerDir "lib"

. (Join-Path $LibDir "constants.ps1")
. (Join-Path $LibDir "ui.ps1")
. (Join-Path $LibDir "llm-endpoint.ps1")
. (Join-Path $LibDir "opencode-config.ps1")

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = $script:ODS_INSTALL_DIR
}

$sync = Sync-WindowsOpenCodeConfigFromEnv -InstallDir $InstallDir -SkipIfUnavailable
if ($sync.Status -ne "skipped") {
    Write-Host "OpenCode config $($sync.Status) for model $($sync.ModelName)"
}
