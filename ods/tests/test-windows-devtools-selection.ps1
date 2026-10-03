$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget { return @{Limit='4.0';Reservation='1.0';Available='4.0'} }
function Get-ODSDockerMemoryGB { return 8 }
function Resolve-WindowsODSPort { param($Name,$DefaultPort,$ExistingEnv,$InstallDir); return $DefaultPort }

$tier = @{TierName='Fixture';LlmModel='fixture';GgufFile='fixture.gguf';MaxContext=8192}
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-devtools-selection-' + [guid]::NewGuid().ToString('N'))
$originalSelection = [Environment]::GetEnvironmentVariable('ODS_WINDOWS_DEVTOOLS_SELECTED', 'Process')
function Assert-Selection {
    param([string]$Expected)
    $content = Get-Content -LiteralPath (Join-Path $testRoot '.env') -Raw
    $matches = [regex]::Matches($content, '(?m)^ENABLE_DEVTOOLS=([^\r\n]*)')
    if ($matches.Count -ne 1 -or $matches[0].Groups[1].Value -ne $Expected) {
        throw "Expected exactly one ENABLE_DEVTOOLS=$Expected line"
    }
}
function Generate-Env {
    New-ODSEnv -InstallDir $testRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8 | Out-Null
}
try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $env:ODS_WINDOWS_DEVTOOLS_SELECTED = 'false'
    Generate-Env
    Assert-Selection 'false'

    [Environment]::SetEnvironmentVariable('ODS_WINDOWS_DEVTOOLS_SELECTED', $null, 'Process')
    Generate-Env
    Assert-Selection 'false'

    $env:ODS_WINDOWS_DEVTOOLS_SELECTED = 'true'
    Generate-Env
    Assert-Selection 'true'

    $env:ODS_WINDOWS_DEVTOOLS_SELECTED = 'TRUE'
    Generate-Env
    Assert-Selection 'true'

    $env:ODS_WINDOWS_DEVTOOLS_SELECTED = 'invalid'
    $failed = $false
    try { Generate-Env } catch { $failed = $_.Exception.Message -eq 'Invalid ODS_WINDOWS_DEVTOOLS_SELECTED value' }
    if (-not $failed) { throw 'Invalid installer selection did not fail' }
    Assert-Selection 'true'

    Remove-Item -LiteralPath (Join-Path $testRoot '.env') -Force
    [Environment]::SetEnvironmentVariable('ODS_WINDOWS_DEVTOOLS_SELECTED', $null, 'Process')
    Generate-Env
    Assert-Selection 'true'
    Write-Host '[PASS] Windows Dev Tools selection survives reruns, honors explicit changes, and preserves legacy default'
} finally {
    [Environment]::SetEnvironmentVariable('ODS_WINDOWS_DEVTOOLS_SELECTED', $originalSelection, 'Process')
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
