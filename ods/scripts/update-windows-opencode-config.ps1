[CmdletBinding()]
param(
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    if ($env:ODS_HOME) {
        $InstallDir = $env:ODS_HOME
    } else {
        $InstallDir = Join-Path $env:USERPROFILE "ods"
    }
}
$inSourceLib = Join-Path (Join-Path (Join-Path (Split-Path $ScriptDir -Parent) "installers") "windows") "lib"
$installLib = Join-Path (Join-Path (Join-Path $InstallDir "installers") "windows") "lib"

if (Test-Path (Join-Path $inSourceLib "constants.ps1")) {
    $LibDir = $inSourceLib
} elseif (Test-Path (Join-Path $installLib "constants.ps1")) {
    $LibDir = $installLib
} else {
    throw "Could not locate ODS Windows installer lib (installers/windows/lib). Looked in source tree: $inSourceLib and installed tree: $installLib"
}

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
