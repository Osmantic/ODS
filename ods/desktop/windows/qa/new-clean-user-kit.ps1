# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallerDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$ExpectedGuestUuid,
    [switch]$RequireWindows11)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'clean-user-preflight.ps1')
$expected=ConvertTo-CleanUserUuid $ExpectedGuestUuid
$hostUuid=ConvertTo-CleanUserUuid (Get-CimInstance Win32_ComputerSystemProduct -OperationTimeoutSec 15).UUID
if($expected -eq $hostUuid){throw 'The designated guest UUID must differ from this generator host.'}
$installer=[IO.Path]::GetFullPath($InstallerDirectory)
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Use a new kit directory; previous evidence is preserved.'}
$setup=Join-Path $installer 'ODS-Windows-Preview-Setup.exe'
$receiptPath=Join-Path $installer 'INSTALLER-BUILD-INFO.json'
$receipt=[IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
if($receipt.installer_sha256 -notmatch '^[0-9a-f]{64}$' -or
    (Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant() -cne $receipt.installer_sha256){throw 'Installer hash does not match its build receipt.'}
$source=(& git -C $PSScriptRoot rev-parse HEAD)
if($LASTEXITCODE -ne 0 -or $source -notmatch '^[0-9a-f]{40}$'){throw 'Cannot identify QA source head.'}
$root=Join-Path $PSScriptRoot '../../..'
$mapping=@{'INSTALLER-BUILD-INFO.json'=$receiptPath;'ODS-Windows-Preview-Setup.exe'=$setup}
foreach($name in @('run-clean-user-qa.ps1','clean-user-preflight.ps1')){$mapping[$name]=Join-Path $PSScriptRoot $name}
foreach($name in @('desktop/windows/tests/InstallerUiTests.cs','desktop/windows/tests/PortalUiTests.cs','desktop/windows/tests/NativePortalTests.cs','desktop/windows/tests/DocumentSaveDialogTests.cs','tests/test-windows-native-portal.ps1')){$mapping['ods/'+$name]=Join-Path $root $name}
# Read/hash every source before creating the kit; copied hashes remain authoritative
# even when these new handoff helpers have not yet been committed.
$files=@(foreach($name in ($mapping.Keys | Sort-Object)){
    $item=Get-Item -LiteralPath $mapping[$name]
    @{path=$name;bytes=$item.Length;sha256=(Get-FileHash -LiteralPath $item.FullName).Hash.ToLowerInvariant()}
})
$null=New-Item -ItemType Directory -Path $output
foreach($name in $mapping.Keys){
    $target=Join-Path $output $name
    $null=New-Item -ItemType Directory -Path (Split-Path $target) -Force
    Copy-Item -LiteralPath $mapping[$name] -Destination $target
}
$policy=@{schemaVersion=1;expected_guest_uuid=$expected;generator_host_uuid=$hostUuid;require_windows11=[bool]$RequireWindows11;
    source_head=$source;files=$files;scope='Frozen QA bytes; source_head identifies checkout ancestry, file hashes identify exact inputs'}
[IO.File]::WriteAllText((Join-Path $output 'QA-SOURCE.json'),($policy | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
Assert-CleanUserInput $output $policy
Write-Output "Kit ready: $output"
Write-Output 'Transfer this entire directory to the designated disposable Microsoft VM as read-only input. Run run-clean-user-qa.ps1 in a non-elevated Windows PowerShell session for preflight. Explicit -RunQualification installs and downloads only after all guards pass. Results use a new private LOCALAPPDATA directory. No VM/account/security configuration was changed.'
