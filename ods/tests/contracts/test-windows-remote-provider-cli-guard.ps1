$ErrorActionPreference = 'Stop'
$odsRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $odsRoot 'installers\windows\ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Native CLI parse failed' }
foreach ($name in @('Invoke-Enable', 'Invoke-Disable')) {
    $function = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name }, $true)
    if ($null -eq $function) { throw "Native CLI function missing: $name" }
    . ([scriptblock]::Create($function.Extent.Text))
}

# Execute the real command functions. Downstream operations are sentinels;
# reaching any of them means the guard ran too late or did not run at all.
function Test-ODSInstallFiles { throw 'Unexpected downstream install admission' }
function Get-ExtensionServiceDir { throw 'Unexpected downstream service lookup' }
function Update-ComposeFlags { throw 'Unexpected downstream cache mutation' }
function docker { throw 'Unexpected downstream Docker action' }
foreach ($id in @('remote-provider-egress', 'remote-provider-ssh-tunnel', 'REMOTE-PROVIDER-EGRESS')) {
    foreach ($command in @('enable', 'dependency-enable', 'disable', 'force-disable')) {
        $message = ''
        try {
            switch ($command) {
                'enable' { Invoke-Enable -ServiceId $id }
                'dependency-enable' { Invoke-Enable -ServiceId $id -AsDependency }
                'disable' { Invoke-Disable -ServiceId $id }
                'force-disable' { Invoke-Disable -ServiceId $id -Force }
            }
        } catch { $message = $_.Exception.Message }
        if ($message -cne 'Manage remote-provider services through Dashboard Library.') {
            throw "Native CLI did not refuse before side effects: $command $id ($message)"
        }
    }
}
Write-Output '[PASS] Native remote-provider CLI refuses enable, dependency enable, disable and Force before side effects'
