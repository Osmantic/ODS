[CmdletBinding()]
param()
# Contract: a Windows installer rerun keeps an owner-edited SearXNG
# settings.yml, and regenerates it only when it is missing or still holds the
# stock CHANGEME secret, as installers/phases/06-directories.sh does on Linux.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
$checks = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}

$utf8 = [Text.UTF8Encoding]::new($false)
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-searxng-[lab]-' + [guid]::NewGuid().ToString('N'))
$settings = Join-Path (Join-Path (Join-Path $tempRoot 'config') 'searxng') 'settings.yml'
$null = [IO.Directory]::CreateDirectory((Split-Path -Parent $settings))
try {
    Assert-True (-not (Test-SearxngSettingsKept -Path $settings)) 'a missing settings.yml was kept'

    # The template shipped in the source tree must be regenerated.
    $template = [IO.File]::ReadAllText((Join-Path $root 'config/searxng/settings.yml'))
    [IO.File]::WriteAllText($settings, $template, $utf8)
    Assert-True (-not (Test-SearxngSettingsKept -Path $settings)) 'the CHANGEME template was kept'
    [IO.File]::WriteAllText($settings, $template.Replace("`n", "`r`n"), $utf8)
    Assert-True (-not (Test-SearxngSettingsKept -Path $settings)) 'the CHANGEME template with CRLF endings was kept'

    $null = New-SearxngConfig -InstallDir $tempRoot -SecretKey 'fixture-secret' -SearchLanguage 'en-US'
    Assert-True (Test-SearxngSettingsKept -Path $settings) 'a generated settings.yml was not kept'

    # The owner follows the SearXNG README and edits the file.
    $owner = [IO.File]::ReadAllText($settings).Replace('safe_search: 0', 'safe_search: 1')
    [IO.File]::WriteAllText($settings, $owner, $utf8)
    Assert-True (Test-SearxngSettingsKept -Path $settings) 'an owner-edited settings.yml was not kept'

    [IO.File]::WriteAllText($settings, "  `n", $utf8)
    $threw = $false
    try { $null = Test-SearxngSettingsKept -Path $settings } catch { $threw = $_.Exception.Message -like '*empty*' }
    Assert-True $threw 'an empty settings.yml did not stop with a clear error'
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}

# Phase 06 must use the check and keep the source template out of the copy.
$phase = [IO.File]::ReadAllText((Join-Path $root 'installers/windows/phases/06-directories.ps1'))
Assert-True ($phase -match '-not \$force -and \(Test-SearxngSettingsKept -Path \$_searxngSettings\)') `
    'phase 06 does not gate New-SearxngConfig on Test-SearxngSettingsKept'
Assert-True ($phase.Contains('$robocopyArgs += (Join-Path $sourceRoot "config\searxng\settings.yml")')) `
    'phase 06 copies the source settings.yml template over the installed file'
Write-Host "[PASS] Windows installer rerun keeps an owner-edited SearXNG settings.yml ($checks checks)"
