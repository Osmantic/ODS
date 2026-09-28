$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in @('Stop-ODSOwnedContainersForRecovery', 'Invoke-Stop', 'Invoke-Disable')) {
    $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Test-Install { }
function Write-AIWarn { param($Message) Write-Host $Message }
function Get-ComposeFlags { throw 'Legacy source recipe is unconfined' }
function Resolve-ODSHostAgentPython { return [pscustomobject]@{ FilePath = (Get-Command python -CommandType Application | Select-Object -First 1).Source; PrefixArgs = @() } }
function Invoke-ODSDockerCompose { throw 'Refused recipe must not reach Compose' }
function Test-ODSInstallFiles { }
function Get-ExtensionServiceDir { param($ServiceId) return (Join-Path $InstallDir "extensions/services/$ServiceId") }
function Get-ExtensionCategory { return 'optional' }
function Get-EnabledDependents { return @() }
function Write-AIError { param($Message) throw $Message }
function Write-AI { param($Message) Write-Host $Message }
function Write-AISuccess { param($Message) Write-Host $Message }
function Update-ComposeFlags { param($ServiceId, $Action) $script:flagsUpdated = "$ServiceId/$Action" }
function docker { if ($args[0] -ne 'info') { throw 'Refused recipe reached Compose' }; $global:LASTEXITCODE = 0 }
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-recovery-' + [Guid]::NewGuid().ToString('N'))
$InstallDir = Join-Path $fixture ('ODS space-' + [char]0x00e9 + [char]0x6a21)
try {
    New-Item -ItemType Directory -Path (Join-Path $InstallDir 'scripts') -Force | Out-Null
    $helper = Join-Path $InstallDir 'scripts/stop-owned-containers.py'
    $body = @'
import json, sys
from pathlib import Path
root = Path(sys.argv[sys.argv.index('--install-dir') + 1])
(root / 'receipt.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')
'@
    [IO.File]::WriteAllText($helper, $body)
    Invoke-Stop -Service 'example'
    $receipt = Get-Content -LiteralPath (Join-Path $InstallDir 'receipt.json') -Raw | ConvertFrom-Json
    if ($receipt.Count -ne 4 -or $receipt[0] -ne '--install-dir' -or $receipt[1] -ne $InstallDir -or
        $receipt[2] -ne '--service' -or $receipt[3] -ne 'example') {
        throw 'Recovery lost the owner path or targeted a different service'
    }
    $serviceDirectory = Get-ExtensionServiceDir 'example'
    New-Item -ItemType Directory -Path $serviceDirectory -Force | Out-Null
    $recipe = Join-Path $serviceDirectory 'compose.yaml'
    [IO.File]::WriteAllText($recipe, 'legacy recipe preserved')
    Invoke-Disable -ServiceId 'example'
    if ((Test-Path -LiteralPath $recipe) -or $script:flagsUpdated -ne 'example/disable' -or
        [IO.File]::ReadAllText("$recipe.disabled") -ne 'legacy recipe preserved') {
        throw 'Disable did not preserve the rejected recipe or refresh its saved flags'
    }
    Rename-Item -LiteralPath "$recipe.disabled" -NewName 'compose.yaml'
    [IO.File]::WriteAllText($helper, 'raise SystemExit(1)')
    $failed = $false
    try { Invoke-Stop -Service 'example' } catch { $failed = $true }
    if (-not $failed) { throw 'Recovery failure was reported as success' }
    $script:flagsUpdated = ''
    $failed = $false
    try { Invoke-Disable -ServiceId 'example' } catch { $failed = $true }
    if (-not $failed -or -not (Test-Path -LiteralPath $recipe) -or $script:flagsUpdated) {
        throw 'Disable mutated the recipe after ownership recovery failed'
    }
    Write-Host '[PASS] Windows stop preserves Unicode ownership and fails closed when recovery fails'
} finally {
    $absolute = [IO.Path]::GetFullPath($fixture)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $absolute -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
