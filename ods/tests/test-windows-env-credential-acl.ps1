[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve the production command signatures.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidOverwritingBuiltInCmdlets', '', Justification='A scoped Get-Acl failure proves credential readback failure aborts before writing secrets.')]
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Windows ACL fixture required' }
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
function Write-AIWarn { param([string]$Message); throw "Unexpected warning: $Message" }
function Get-LlamaCpuBudget { @{Limit='4.0';Reservation='1.0';Available='4.0'} }
function Get-ODSDockerMemoryGB { 8 }
function Resolve-WindowsODSPort { param($Name,$DefaultPort,$ExistingEnv,$InstallDir); $DefaultPort }
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$tier = @{TierName='Fixture';LlmModel='fixture';GgufFile='fixture.gguf';MaxContext=8192}
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$tempRoot = Join-Path $tempParent ('ods-env-credential-acl-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$checks = 0
function Assert-Private([string]$Path) {
    $acl = Get-Acl -LiteralPath $Path
    $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 1 -or
        $rules[0].IdentityReference -ne $sid -or $rules[0].IsInherited -or
        $rules[0].AccessControlType -ne 'Allow' -or $rules[0].FileSystemRights -ne 'FullControl') {
        throw 'Credential ACL is not exactly current-user FullControl'
    }
}
try {
    # A custom install directory can inherit public read access. No live secrets
    # or installed paths are used; exercise the real generator in a fresh fixture.
    $acl = Get-Acl -LiteralPath $tempRoot
    $everyone = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($everyone,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
    Set-Acl -LiteralPath $tempRoot -AclObject $acl
    foreach ($attempt in 1..3) {
        $result = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
        if (-not $result) { throw 'Generator did not complete' }
        Assert-Private (Join-Path $tempRoot '.env')
        $checks++
    }
    # Reinstall must remove explicit broad grants, not just inherited ones.
    $path = Join-Path $tempRoot '.env'
    $item = Get-Item -LiteralPath $path -Force
    if ($PSVersionTable.PSEdition -eq 'Core') {
        $acl = [IO.FileSystemAclExtensions]::GetAccessControl($item, [Security.AccessControl.AccessControlSections]::Access)
    } else { $acl = $item.GetAccessControl([Security.AccessControl.AccessControlSections]::Access) }
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($everyone,'Read','Allow'))
    if ($PSVersionTable.PSEdition -eq 'Core') {
        [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl)
    } else { $item.SetAccessControl($acl) }
    Write-ODSPrivateEnvFile -Path $path -Content 'fixture-only-placeholder'
    Assert-Private $path
    $checks++
    foreach ($failure in @('apply','verify')) {
        $failureRoot = Join-Path $tempRoot $failure
        $threw = $false
        try {
            & {
                if ($failure -eq 'apply') {
                    function Protect-ODSPrivateEnvFile { param($Path); throw 'Injected ACL write failure' }
                } else {
                    function Get-Acl { param($LiteralPath); throw 'Injected ACL readback failure' }
                }
                New-ODSEnv -InstallDir $failureRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8 | Out-Null
            }
        } catch {
            if ($_.Exception.Message -notlike 'Injected ACL*failure') { throw }
            $threw = $true
        }
        if (-not $threw) { throw 'Generator swallowed a credential protection failure' }
        if ((Get-Item -LiteralPath (Join-Path $failureRoot '.env')).Length -ne 0) { throw 'Secret payload was written before ACL verification' }
        $checks++
    }
    $badPath = Join-Path $tempRoot 'directory.env'
    New-Item -ItemType Directory -Path $badPath | Out-Null
    $threw = $false
    try { Write-ODSPrivateEnvFile -Path $badPath -Content 'must-not-write' } catch { $threw=$true }
    if (-not $threw -or -not (Test-Path -LiteralPath $badPath -PathType Container)) { throw 'Non-file credential target was not preserved and rejected' }
    $checks++
} finally {
    $resolved = [IO.Path]::GetFullPath($tempRoot)
    if ([IO.Path]::GetDirectoryName($resolved) -ne $tempParent -or
        [IO.Path]::GetFileName($resolved) -notmatch '^ods-env-credential-acl-[a-f0-9]{32}$') { throw 'Unsafe temporary cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Host "Windows env credential ACL: $checks checks passed; $($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)."
