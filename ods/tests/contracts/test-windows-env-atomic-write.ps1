# Contract: Windows .env rewrites stage a new file and swap it in instead of
# truncating .env in place. An interrupted `ods.ps1 start` used to leave an
# empty .env and lose every generated secret (#8502).
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { Write-Host 'SKIP: Windows file-replace semantics'; exit 0 }

$windowsDir = Join-Path $PSScriptRoot '../../installers/windows'
. (Join-Path $windowsDir 'lib/private-file.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $windowsDir 'ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "ods.ps1 does not parse: $($errors[0].Message)" }
$definition = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Set-ODSEnvValue' }, $true)
if (-not $definition) { throw 'ods.ps1 no longer defines Set-ODSEnvValue' }
. ([scriptblock]::Create($definition.Extent.Text))

$InstallDir = Join-Path ([IO.Path]::GetTempPath()) ('ods-env-atomic-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$envFile = Join-Path $InstallDir '.env'
$nl = [Environment]::NewLine
$original = "# ODS`r`nWEBUI_SECRET=keep-me`r`nLLAMA_CPU_LIMIT=16.0`r`nDASHBOARD_API_KEY=also-keep`r`n"
try {
    Write-ODSPrivateEnvFile -Path $envFile -Content $original

    # Hold a reader open across the rewrite. A staged swap leaves this handle
    # on the old file; an in-place truncate would show it the new bytes.
    $reader = [IO.FileStream]::new($envFile, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    try {
        Set-ODSEnvValue -Key 'LLAMA_CPU_LIMIT' -Value '8.0'
        $seen = [IO.StreamReader]::new($reader, [Text.UTF8Encoding]::new($false)).ReadToEnd()
    } finally { $reader.Dispose() }
    if ($seen -cne $original) { throw ".env was rewritten in place; an open reader saw [$seen]" }
    Write-Host 'PASS .env is replaced by a staged file, not truncated in place'

    Set-ODSEnvValue -Key 'NEW_KEY' -Value 'added'
    $bytes = [IO.File]::ReadAllBytes($envFile)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { throw '.env gained a UTF-8 BOM' }
    $expected = "# ODS${nl}WEBUI_SECRET=keep-me${nl}LLAMA_CPU_LIMIT=8.0${nl}DASHBOARD_API_KEY=also-keep${nl}NEW_KEY=added${nl}"
    $actual = [Text.UTF8Encoding]::new($false).GetString($bytes)
    if ($actual -cne $expected) { throw "unexpected .env content: [$actual]" }
    Write-Host 'PASS updated and appended keys keep every other line and the line endings'

    $acl = Get-Acl -LiteralPath $envFile
    if (-not $acl.AreAccessRulesProtected) { throw '.env inherits a broad DACL after rewrite' }
    if (@($acl.Access | Where-Object { $_.IdentityReference -match 'Everyone|Users$' -and $_.AccessControlType -eq 'Allow' }).Count) {
        throw '.env grants a broad group after rewrite'
    }
    Write-Host 'PASS rewritten .env keeps its private ACL'

    $leftovers = @(Get-ChildItem -LiteralPath $InstallDir -Force | Where-Object { $_.Name -ne '.env' })
    if ($leftovers.Count) { throw "staging files left behind: $($leftovers.Name -join ', ')" }
    Write-Host 'PASS no staging files are left next to .env'
} finally {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

$installer = [IO.File]::ReadAllText((Join-Path $windowsDir 'install-windows.ps1'))
if ($installer -match 'WriteAllText\(\$envPath') { throw 'install-windows.ps1 still rewrites .env in place' }
Write-Host 'PASS installer bootstrap patch uses the staged .env writer'
exit 0
