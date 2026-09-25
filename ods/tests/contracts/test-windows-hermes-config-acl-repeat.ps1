param([string]$PhasePath = "", [switch]$RequireNonElevated)
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This contract requires Windows file ACLs.'
}
if (-not $PhasePath) {
    $PhasePath = Join-Path $PSScriptRoot '../../installers/windows/phases/06-directories.ps1'
}
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path -LiteralPath $PhasePath).Path, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw $parseErrors[0].Message }
$functionAst = $ast.Find({ param($n)
    $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $n.Name -eq 'Update-HermesConfigFile'
}, $true)
if (-not $functionAst) { throw 'Hermes updater not found' }
# Load only the parsed production helper, never execute installer top-level code.
. ([scriptblock]::Create($functionAst.Extent.Text))
function Write-AIWarn([string]$Message) { Write-Warning $Message }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($RequireNonElevated -and $elevated) {
    throw 'Run this regression as a non-elevated Windows user.'
}
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$tempRoot = Join-Path $tempParent ('ods-hermes-acl-repeat-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$checks = 0
function Get-TestFileAccess([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    if ($PSVersionTable.PSEdition -eq 'Core') {
        return [IO.FileSystemAclExtensions]::GetAccessControl($item, [Security.AccessControl.AccessControlSections]::Access)
    }
    return $item.GetAccessControl([Security.AccessControl.AccessControlSections]::Access)
}
function Set-TestFileAccess([string]$Path, $Acl) {
    $item = Get-Item -LiteralPath $Path
    if ($PSVersionTable.PSEdition -eq 'Core') {
        [IO.FileSystemAclExtensions]::SetAccessControl($item, $Acl)
    } else { $item.SetAccessControl($Acl) }
}
function Assert-OwnerOnly([string]$Path) {
    $acl = Get-TestFileAccess $Path
    if (-not $acl.AreAccessRulesProtected) { throw 'Inheritance remains enabled' }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 1 -or $rules[0].IdentityReference -ne $sid -or
        $rules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rules[0].FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rules[0].IsInherited) { throw 'DACL is not exactly current-user FullControl' }
}
try {
    foreach ($name in @('template', 'live')) {
        $path = Join-Path $tempRoot ($name + '.yaml')
        [IO.File]::WriteAllText($path, "model:`n  default: old`n  base_url: http://old.invalid/v1`nterminal:`n  backend: local`n")
        # Reinstall setup can explicitly grant Everyone access. Verify removal,
        # not merely absence of an inherited broad grant on a private temp root.
        $acl = Get-TestFileAccess $path
        $everyone = New-Object Security.Principal.SecurityIdentifier('S-1-1-0')
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($everyone, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
        Set-TestFileAccess $path $acl
        foreach ($model in @('full-model', 'bootstrap-model', 'promoted-model')) {
            if (-not (Update-HermesConfigFile -Path $path -Model $model -BaseUrl 'http://litellm:4000/v1' -ApiKey 'test-only-placeholder' -ContextLength 65536)) {
                throw "Updater refused $name at $model"
            }
            Assert-OwnerOnly $path
            if (-not ([IO.File]::ReadAllText($path).Contains("  default: `"$model`""))) { throw 'Model rewrite was not retained' }
            $checks++
        }
        # Simulate an ordinary same-owner live YAML rewrite, then relock again.
        [IO.File]::WriteAllText($path, ([IO.File]::ReadAllText($path) + "`n# live rewrite`n"))
        if (-not (Update-HermesConfigFile -Path $path -Model 'after-rewrite' -BaseUrl 'http://litellm:4000/v1' -ApiKey 'test-only-placeholder' -ContextLength 65536)) { throw 'Relock after rewrite failed' }
        Assert-OwnerOnly $path
        $checks++
    }
} finally {
    $resolved = [IO.Path]::GetFullPath($tempRoot)
    if ([IO.Path]::GetDirectoryName($resolved) -ne $tempParent -or
        [IO.Path]::GetFileName($resolved) -notmatch '^ods-hermes-acl-repeat-[a-f0-9]{32}$') { throw 'Unsafe temporary cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Host "Windows Hermes ACL repeat contract passed: $checks checks; elevated=$elevated; current-user-only DACL."
