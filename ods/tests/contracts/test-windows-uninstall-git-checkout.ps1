# Contract: ods.ps1 uninstall must refuse to delete an in-place install that
# is a git source checkout. The real guard is loaded from ods.ps1's AST and
# exercised against temporary fixture directories.
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../installers/windows/ods.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "ods.ps1 does not parse: $($errors[0].Message)" }
$definition = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-ODSInstallDirSafeForRemoval' }, $true)
if (-not $definition) { throw 'ods.ps1 no longer defines Assert-ODSInstallDirSafeForRemoval' }
. ([scriptblock]::Create($definition.Extent.Text))

$root = Join-Path ([IO.Path]::GetTempPath()) ("ods-uninstall-git-" + [guid]::NewGuid().ToString('N'))
# The guard compares against the user profile; pwsh on Linux runners has none.
if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) { $env:USERPROFILE = [IO.Path]::GetTempPath() }
try {
    $InstallDir = Join-Path $root 'ods'
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    foreach ($marker in @('ods.ps1', 'docker-compose.base.yml')) {
        Set-Content -LiteralPath (Join-Path $InstallDir $marker) -Value ''
    }

    Assert-ODSInstallDirSafeForRemoval
    Write-Host 'PASS runtime install without .git is removable'

    New-Item -ItemType Directory -Path (Join-Path $InstallDir '.git') -Force | Out-Null
    $refused = $false
    try { Assert-ODSInstallDirSafeForRemoval } catch {
        if ($_.Exception.Message -notmatch 'git source checkout') { throw }
        $refused = $true
    }
    if (-not $refused) { throw 'uninstall guard allowed removing a git source checkout' }
    Write-Host 'PASS git source checkout is refused'

    Remove-Item -LiteralPath (Join-Path $InstallDir '.git') -Recurse -Force
    Set-Content -LiteralPath (Join-Path $InstallDir '.git') -Value 'gitdir: ../.git/worktrees/ods'
    $refused = $false
    try { Assert-ODSInstallDirSafeForRemoval } catch {
        if ($_.Exception.Message -notmatch 'git source checkout') { throw }
        $refused = $true
    }
    if (-not $refused) { throw 'uninstall guard allowed removing a git worktree checkout' }
    Write-Host 'PASS git worktree checkout (.git file) is refused'
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
