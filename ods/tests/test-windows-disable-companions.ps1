# Regression test: '.\ods.ps1 disable <id>' stops the extension's namespaced
# companions (its database, worker, cache: <id>-* in the same fragment), as
# the host agent and the Linux CLI already do (#8071, #8072). Before, only the
# primary service stopped; the rest kept running with restart: unless-stopped
# after the fragment left the Compose project, so '.\ods.ps1 stop' no longer
# reached them.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in @('Get-ODSExtensionStopTargets', 'Stop-ODSOwnedContainersForRecovery', 'Invoke-Disable')) {
    $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    # Main has no Get-ODSExtensionStopTargets; load what exists so the
    # assertions below report the behavior, not a missing function.
    if ($definition) { . ([scriptblock]::Create($definition.Extent.Text)) }
}
function Test-ODSInstallFiles { }
function Get-ExtensionServiceDir { param($ServiceId) return (Join-Path $InstallDir "extensions/services/$ServiceId") }
function Get-ExtensionCategory { return 'optional' }
function Get-EnabledDependents { return @() }
function Write-AIError { param($Message) throw $Message }
function Write-AIWarn { param($Message) Write-Host $Message }
function Write-AI { param($Message) Write-Host $Message }
function Write-AISuccess { param($Message) Write-Host $Message }
function Update-ComposeFlags { param($ServiceId, $Action) $script:flagsUpdated = "$ServiceId/$Action" }
function Resolve-ODSHostAgentPython {
    # CI runners and Windows have python; a Mac may only have python3.
    $command = Get-Command python, python3 -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    return [pscustomobject]@{ FilePath = $command.Source; PrefixArgs = @() }
}
$script:composeFlagsFail = $false
function Get-ComposeFlags {
    if ($script:composeFlagsFail) { throw 'Compose validation failed' }
    return @('-f', 'docker-compose.base.yml')
}
$script:stopped = @()
function docker {
    if ($args[0] -eq 'info') { $global:LASTEXITCODE = 0; return }
    if ($args[0] -eq 'compose' -and $args[3] -eq 'stop') { $script:stopped = @($args[4..($args.Count - 1)]) }
    $global:LASTEXITCODE = 0
}

function Assert-Same {
    param([string[]]$Expected, [string[]]$Actual, [string]$Label)
    $wanted = @($Expected | Sort-Object) -join ','
    $got = @($Actual | Sort-Object) -join ','
    if ($wanted -ne $got) { throw "${Label}: expected [$wanted], got [$got]" }
}

# A fragment like langfuse's: the extension, two companions, a service with
# its own extension directory (keeps its own lifecycle), a service another
# extension's name starts with, and top-level volumes named like companions.
$fragment = @'
# example extension
services:
  example:
    image: example:1
    # example-commented: not a service
  example-db:
    image: postgres:16
  example-worker:  # background jobs
    image: example:1
  example-search:
    image: search:1
  other-cache:
    image: redis:7

volumes:
  example-data:
'@

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-disable-companions-' + [Guid]::NewGuid().ToString('N'))
$InstallDir = Join-Path $fixture ('ODS space-' + [char]0x00e9)
try {
    New-Item -ItemType Directory -Path (Join-Path $InstallDir 'scripts') -Force | Out-Null
    New-Item -ItemType Directory -Path (Get-ExtensionServiceDir 'example-search') -Force | Out-Null
    $serviceDirectory = Get-ExtensionServiceDir 'example'
    New-Item -ItemType Directory -Path $serviceDirectory -Force | Out-Null
    $recipe = Join-Path $serviceDirectory 'compose.yaml'
    [IO.File]::WriteAllText($recipe, $fragment.Replace("`r`n", "`n").Replace("`n", "`r`n"))

    Invoke-Disable -ServiceId 'example'
    Assert-Same @('example', 'example-db', 'example-worker') $script:stopped 'Compose stop targets'
    if ((Test-Path -LiteralPath $recipe) -or $script:flagsUpdated -ne 'example/disable') { throw 'Disable left compose.yaml in place or skipped the saved flags' }

    # Compose cannot load the project: the ownership-checked recovery stop
    # must target the same services.
    Rename-Item -LiteralPath "$recipe.disabled" -NewName 'compose.yaml'
    $helper = Join-Path $InstallDir 'scripts/stop-owned-containers.py'
    [IO.File]::WriteAllText($helper, @'
import json, sys
from pathlib import Path
root = Path(sys.argv[sys.argv.index('--install-dir') + 1])
(root / 'receipt.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')
'@)
    $script:composeFlagsFail = $true
    $script:stopped = @()
    Invoke-Disable -ServiceId 'example'
    # No @(): Windows PowerShell 5.1 emits a JSON array as one object, so
    # wrapping it would nest the whole receipt in element 0.
    $receipt = Get-Content -LiteralPath (Join-Path $InstallDir 'receipt.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($receipt[0] -ne '--install-dir' -or $receipt[1] -ne $InstallDir) { throw 'Recovery stop lost the install path' }
    $services = @()
    for ($i = 2; $i -lt $receipt.Count; $i += 2) {
        if ($receipt[$i] -ne '--service') { throw "Unexpected recovery argument: $($receipt[$i])" }
        $services += $receipt[$i + 1]
    }
    Assert-Same @('example', 'example-db', 'example-worker') $services 'Recovery stop targets'
    if ($script:stopped.Count) { throw 'Recovery path reached docker compose stop' }

    Write-Host '[PASS] Windows disable stops the extension and the companions its fragment owns'
} finally {
    $absolute = [IO.Path]::GetFullPath($fixture)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $absolute -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
