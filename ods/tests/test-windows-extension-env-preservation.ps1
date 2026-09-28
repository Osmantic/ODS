[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
function Write-AIWarn { param([string]$Message); throw "Unexpected warning: $Message" }
function Get-LlamaCpuBudget { @{Limit='4.0';Reservation='1.0';Available='4.0'} }
function Get-ODSDockerMemoryGB { 8 }
function Resolve-WindowsODSPort { param($Name,$DefaultPort,$ExistingEnv,$InstallDir); $DefaultPort }

$tier = @{TierName='Fixture';LlmModel='fixture';GgufFile='fixture.gguf';MaxContext=8192}
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$installDir = Join-Path $tempParent ('ods-extension-env-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $installDir | Out-Null
try {
    # This is the supported Windows re-install path: generate .env once, then
    # an enabled extension stores its manifest-declared settings before a later
    # installer run regenerates the core configuration.
    New-ODSEnv -InstallDir $installDir -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8 | Out-Null
    Add-Content -LiteralPath (Join-Path $installDir '.env') -Value @(
        'BRAVE_SEARCH_API_KEY=fixture-brave-secret'
        'BRAVE_SEARCH_SEARXNG_COMPAT=1'
    )
    New-ODSEnv -InstallDir $installDir -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8 | Out-Null

    $envText = Get-Content -LiteralPath (Join-Path $installDir '.env') -Raw
    foreach ($setting in @('BRAVE_SEARCH_API_KEY=fixture-brave-secret', 'BRAVE_SEARCH_SEARXNG_COMPAT=1')) {
        if ($envText -notmatch [regex]::Escape($setting)) { throw "Extension setting was lost on re-install: $setting" }
    }
    if ($envText -notmatch '#=== Preserved extension settings ===') { throw 'Preserved extension settings section missing' }
    Write-Host 'Windows extension env preservation: 3 checks passed.'
} finally {
    $resolved = [IO.Path]::GetFullPath($installDir)
    if ([IO.Path]::GetDirectoryName($resolved) -ne $tempParent -or [IO.Path]::GetFileName($resolved) -notmatch '^ods-extension-env-[a-f0-9]{32}$') { throw 'Unsafe temporary cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
