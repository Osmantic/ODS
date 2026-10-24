# SPDX-License-Identifier: Apache-2.0
# Pure validation plus read-only machine observation. No host bypass exists.
function ConvertTo-CleanUserUuid($Value){
    $id=[guid]::Empty
    if(-not [guid]::TryParse([string]$Value,[ref]$id) -or $id -eq [guid]::Empty -or $id.ToString() -eq 'ffffffff-ffff-ffff-ffff-ffffffffffff'){throw 'A real, explicit machine UUID is required.'}
    return $id.ToString()
}
function Get-CleanUserMachine {
    $computer=Get-CimInstance Win32_ComputerSystem -OperationTimeoutSec 15
    $product=Get-CimInstance Win32_ComputerSystemProduct -OperationTimeoutSec 15
    $os=Get-CimInstance Win32_OperatingSystem -OperationTimeoutSec 15
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=[Security.Principal.WindowsPrincipal]::new($identity)
    return @{Manufacturer=$computer.Manufacturer;Model=$computer.Model;Uuid=$product.UUID;ProductType=$os.ProductType;
        Build=$os.BuildNumber;Is64Bit=[Environment]::Is64BitOperatingSystem;Elevated=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
        Account=$identity.Name;Caption=$os.Caption;Version=$os.Version;Architecture=$os.OSArchitecture}
}
function Assert-CleanUserMachine($Policy,$Machine){
    if($Policy.schemaVersion -ne 1 -or $Policy.require_windows11 -isnot [bool]){throw 'Unsupported clean-user policy.'}
    $expected=ConvertTo-CleanUserUuid $Policy.expected_guest_uuid
    $origin=ConvertTo-CleanUserUuid $Policy.generator_host_uuid
    $actual=ConvertTo-CleanUserUuid $Machine.Uuid
    if($expected -eq $origin -or $actual -ne $expected -or $actual -eq $origin){throw 'VM UUID does not match the explicitly designated disposable guest.'}
    if($Machine.Manufacturer -cne 'Microsoft Corporation' -or $Machine.Model -cne 'Virtual Machine'){throw 'Only an identified Microsoft Virtual Machine is supported.'}
    if($Machine.ProductType -ne 1 -or $Machine.Is64Bit -ne $true -or [int]$Machine.Build -lt 10240){throw 'A Windows 10/11 x64 workstation guest is required.'}
    if($Policy.require_windows11 -and [int]$Machine.Build -lt 22000){throw 'This kit requires Windows 11.'}
    if($Machine.Elevated -ne $false){throw 'Use a non-elevated standard-user token.'}
}
function Assert-CleanUserInput([string]$Root,$Manifest){
    $expected=@('INSTALLER-BUILD-INFO.json','ODS-Windows-Preview-Setup.exe','run-clean-user-qa.ps1','clean-user-preflight.ps1',
        'ods/desktop/windows/tests/InstallerUiTests.cs','ods/desktop/windows/tests/PortalUiTests.cs','ods/desktop/windows/tests/NativePortalTests.cs',
        'ods/desktop/windows/tests/DocumentSaveDialogTests.cs','ods/tests/test-windows-native-portal.ps1')
    if($Manifest.source_head -notmatch '^[0-9a-f]{40}$' -or @($Manifest.files).Count -ne $expected.Count){throw 'Malformed frozen input manifest.'}
    $actualFiles=@(Get-ChildItem -LiteralPath $Root -Recurse -File -Force)
    if($actualFiles.Count -ne $expected.Count+1 -or -not (Test-Path -LiteralPath (Join-Path $Root 'QA-SOURCE.json'))){throw 'Unexpected files in frozen input kit.'}
    foreach($name in $expected){
        $entry=@($Manifest.files | Where-Object path -CEQ $name)
        if($entry.Count -ne 1 -or $entry[0].sha256 -notmatch '^[0-9a-f]{64}$'){throw 'Missing or malformed frozen input.'}
        $path=Join-Path $Root $name
        $item=Get-Item -LiteralPath $path -Force
        if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -ne $entry[0].bytes -or
            (Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -cne $entry[0].sha256){throw 'Frozen input bytes changed.'}
        $parent=$item.Directory
        while($parent -and $parent.FullName.Length -ge ([IO.Path]::GetFullPath($Root)).Length){
            if($parent.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked input directories are refused.'};$parent=$parent.Parent
        }
    }
    $receipt=[IO.File]::ReadAllText((Join-Path $Root 'INSTALLER-BUILD-INFO.json')) | ConvertFrom-Json
    if($receipt.installer_sha256 -cne (Get-FileHash -LiteralPath (Join-Path $Root 'ODS-Windows-Preview-Setup.exe')).Hash.ToLowerInvariant() -or
        $receipt.application_sha256 -notmatch '^[0-9a-f]{64}$'){throw 'Installer receipt failed verification.'}
}
