# SPDX-License-Identifier: Apache-2.0
# Execute the real workflow block with isolated package-source fixtures and the
# actual Actions pwsh footer. No Gallery access, module installation or settings.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$workflow = Join-Path $PSScriptRoot '../workflows/lint-powershell.yml'
$lines = [IO.File]::ReadAllLines($workflow)
$start = @((0..($lines.Length - 1)) | Where-Object { $lines[$_] -match '^      - name: Install PSScriptAnalyzer\s*$' })
if ($start.Count -ne 1) { throw 'Expected exactly one analyzer bootstrap step.' }
$run = $start[0] + 1
while ($run -lt $lines.Length -and $lines[$run] -notmatch '^        run: \|\s*$') { $run++ }
if ($run -ge $lines.Length) { throw 'Analyzer bootstrap run block was not found.' }
$body = [Collections.Generic.List[string]]::new()
for ($line = $run + 1; $line -lt $lines.Length; $line++) {
    if ([String]::IsNullOrWhiteSpace($lines[$line])) { $body.Add(''); continue }
    if (-not $lines[$line].StartsWith('          ')) { break }
    $body.Add($lines[$line].Substring(10))
}
$bootstrap = $body -join "`n"
$parseErrors = $null
$null = [Management.Automation.Language.Parser]::ParseInput($bootstrap, [ref]$null, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'The extracted analyzer bootstrap does not parse.' }
$marker = '# Declare success only after checking the actual imported dependency.'
$boundary = $bootstrap.IndexOf($marker, [StringComparison]::Ordinal)
if ($boundary -lt 0) { throw 'The verified-success boundary was not found.' }
# This prefix is the original bootstrap, before the dependency postconditions
# and explicit success. Its negative control must still leak the native exit.
$oldBootstrap = $bootstrap.Substring(0, $boundary)
$footer = 'if ((Test-Path -LiteralPath variable:\LASTEXITCODE)) { exit $LASTEXITCODE }'
$pwsh = (Get-Process -Id $PID).Path
if ([IO.Path]::GetFileNameWithoutExtension($pwsh) -ne 'pwsh') { throw 'Run this contract with PowerShell 7.' }
$root = Join-Path ([IO.Path]::GetTempPath()) ('ods-pssa-bootstrap-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $root
$checks = 0
try {
    foreach ($case in @('handled-native', 'install-failure', 'import-failure', 'missing-module', 'missing-cmdlet', 'old-negative')) {
        $directory = Join-Path $root $case
        $null = New-Item -ItemType Directory -Path $directory
        $module = Join-Path $directory 'PSScriptAnalyzer.psm1'
        $manifest = Join-Path $directory 'PSScriptAnalyzer.psd1'
        $source = if ($case -eq 'import-failure') { "throw 'fixture import failure'" }
            elseif ($case -eq 'missing-cmdlet') { "function Get-FixtureMarker { 'fixture' }; Export-ModuleMember -Function Get-FixtureMarker" }
            else { "function Invoke-ScriptAnalyzer { 'fixture analyzer' }; Export-ModuleMember -Function Invoke-ScriptAnalyzer" }
        [IO.File]::WriteAllText($module, $source, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($manifest, "@{ RootModule='PSScriptAnalyzer.psm1'; ModuleVersion='1.25.0' }", [Text.UTF8Encoding]::new($false))
        $quote = { param($value) "'" + $value.Replace("'", "''") + "'" }
        $prefix = @'
$ErrorActionPreference = 'Stop'
Microsoft.PowerShell.Core\Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
Microsoft.PowerShell.Core\Import-Module Microsoft.PowerShell.Management -ErrorAction Stop
$PSModuleAutoLoadingPreference = 'None'
function Get-PackageProvider { [CmdletBinding()]param($Name,[switch]$ListAvailable) [pscustomobject]@{Name='NuGet'} }
function Install-PackageProvider { [CmdletBinding()]param($Name,$MinimumVersion,$Scope,[switch]$Force,[switch]$ForceBootstrap) throw 'Unexpected provider installation' }
function Get-PSRepository { [CmdletBinding()]param($Name) [pscustomobject]@{Name='PSGallery'} }
function Register-PSRepository { [CmdletBinding()]param([switch]$Default) throw 'Unexpected repository registration' }
function Set-PSRepository { [CmdletBinding()]param($Name,$InstallationPolicy) }
function Install-Module {
    [CmdletBinding()]param($Name,$Repository,$Scope,[switch]$Force,[switch]$AllowClobber)
    if ($Name -ne 'PSScriptAnalyzer' -or $Repository -ne 'PSGallery' -or $Scope -ne 'CurrentUser') { throw 'Unexpected install contract' }
    if ($fixtureCase -eq 'install-failure') { Write-Error 'fixture install failure'; return }
    & $fixturePwsh -NoProfile -Command 'exit 23'
}
function Import-Module {
    [CmdletBinding()]param([Parameter(Position=0)]$Name)
    if ($Name -ne 'PSScriptAnalyzer') { throw 'Unexpected import contract' }
    if ($fixtureCase -eq 'missing-module') { return }
    Microsoft.PowerShell.Core\Import-Module -Name $fixtureManifest -ErrorAction Stop
}
'@
        $variables = '$fixtureCase=' + (& $quote $case) + "`n" + '$fixturePwsh=' + (& $quote $pwsh) + "`n" +
            '$fixtureManifest=' + (& $quote $manifest) + "`n"
        $script = Join-Path $directory 'runner.ps1'
        $selected = if ($case -eq 'old-negative') { $oldBootstrap } else { $bootstrap }
        $observation = if ($case -eq 'old-negative') { @'
if ($LASTEXITCODE -ne 23 -or -not (Get-Module PSScriptAnalyzer)) { throw 'Old control did not reach the residual-exit boundary.' }
Get-Command Invoke-ScriptAnalyzer -Module PSScriptAnalyzer -ErrorAction Stop | Out-Null
Write-Output 'FIXTURE imported dependency with residual native exit 23'
'@ } else { '' }
        [IO.File]::WriteAllText($script, ($variables + $prefix + "`n" + $selected + "`n" + $observation + "`n" + $footer), [Text.UTF8Encoding]::new($false))
        # Use -Command dot-sourcing, matching actions/runner rather than -File.
        $command = '. ' + (& $quote $script)
        $output = @(& $pwsh -NoProfile -ExecutionPolicy Bypass -Command $command 2>&1)
        $code = $LASTEXITCODE
        # pwsh -Command reports a nonzero dot-sourced script exit as process 1.
        $expected = if ($case -eq 'handled-native') { 0 } else { 1 }
        if ($code -ne $expected) { throw "$case returned $code instead of $expected. $($output -join "`n")" }
        $expectedError = switch ($case) {
            'install-failure' { 'fixture install failure' }
            'import-failure' { 'fixture import failure' }
            'missing-module' { 'PSScriptAnalyzer was not imported.' }
            'missing-cmdlet' { 'Invoke-ScriptAnalyzer' }
            'old-negative' { 'FIXTURE imported dependency with residual native exit 23' }
        }
        if ($expectedError -and -not (($output -join "`n").Contains($expectedError))) { throw "$case failed at an unexpected boundary: $($output -join "`n")" }
        $checks++
        Write-Output "PASS analyzer bootstrap $case exits $expected with the Actions footer"
    }
    Write-Output "PASS $checks analyzer bootstrap exit contracts"
} finally {
    if ([IO.Path]::GetDirectoryName($root) -ne [IO.Path]::GetTempPath().TrimEnd([IO.Path]::DirectorySeparatorChar)) { throw 'Unexpected bootstrap fixture path.' }
    Remove-Item -LiteralPath $root -Recurse -Force
}
# Negative child processes intentionally set nonzero codes; this script has
# asserted each status and must not leak the last negative control into CI.
exit 0
