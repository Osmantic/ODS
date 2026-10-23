# SPDX-License-Identifier: Apache-2.0
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../desktop/windows/qa/clean-user-preflight.ps1')
$guest=[guid]::NewGuid().ToString();$hostId=[guid]::NewGuid().ToString()
$machine=@{Manufacturer='Microsoft Corporation';Model='Virtual Machine';Uuid=$guest;ProductType=1;Build=22631;Is64Bit=$true;Elevated=$false;Account='fixture\\standard'}
$policy=@{schemaVersion=1;expected_guest_uuid=$guest;generator_host_uuid=$hostId;require_windows11=$true}
$checks=0
function Expect-Refusal($Action){$refused=$false;try{& $Action}catch{$refused=$true};if(-not $refused){throw 'Expected preflight refusal.'};$script:checks++}
$null=Assert-CleanUserMachine $policy $machine;$checks++
foreach($field in @('Uuid','Manufacturer','Model','ProductType','Build','Is64Bit','Elevated')){
    $bad=$machine.Clone();$bad[$field]=switch($field){Uuid{$hostId} Manufacturer{'physical'} Model{'physical'} ProductType{3} Build{19045} Is64Bit{$false} Elevated{$true}}
    Expect-Refusal {Assert-CleanUserMachine $policy $bad}
}
$badPolicy=$policy.Clone();$badPolicy.expected_guest_uuid=$hostId;Expect-Refusal {Assert-CleanUserMachine $badPolicy $machine}
$badPolicy=$policy.Clone();$badPolicy.expected_guest_uuid='not-a-uuid';Expect-Refusal {Assert-CleanUserMachine $badPolicy $machine}
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-clean-user-contract-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $fixture
$utf8=[Text.UTF8Encoding]::new($false)
try {
    $qa=Join-Path $PSScriptRoot '../desktop/windows/qa'
    Copy-Item -LiteralPath (Join-Path $qa 'run-clean-user-qa.ps1') -Destination $fixture
    $library=[IO.File]::ReadAllText((Join-Path $qa 'clean-user-preflight.ps1'))
    # The copied production entrypoint uses the actual guard and hash validator.
    # Only observation is replaced; no Windows identity, UI or process is touched.
    $library+="`nfunction Get-CleanUserMachine { return `$global:OdsCleanUserFixtureMachine }`n"
    [IO.File]::WriteAllText((Join-Path $fixture 'clean-user-preflight.ps1'),$library,$utf8)
    $names=@('INSTALLER-BUILD-INFO.json','ODS-Windows-Preview-Setup.exe','run-clean-user-qa.ps1','clean-user-preflight.ps1',
        'ods/desktop/windows/tests/InstallerUiTests.cs','ods/desktop/windows/tests/PortalUiTests.cs','ods/desktop/windows/tests/NativePortalTests.cs',
        'ods/desktop/windows/tests/DocumentSaveDialogTests.cs','ods/tests/test-windows-native-portal.ps1')
    foreach($name in $names){
        $path=Join-Path $fixture $name
        if(-not (Test-Path -LiteralPath $path)){
            $null=New-Item -ItemType Directory -Path (Split-Path $path) -Force
            [IO.File]::WriteAllText($path,'Harmless fixture, never executable.',$utf8)
        }
    }
    $receipt=@{installer_sha256=(Get-FileHash (Join-Path $fixture 'ODS-Windows-Preview-Setup.exe')).Hash.ToLowerInvariant();application_sha256=('a'*64)}
    [IO.File]::WriteAllText((Join-Path $fixture 'INSTALLER-BUILD-INFO.json'),($receipt|ConvertTo-Json),$utf8)
    $policy.source_head='a'*40
    $policy.files=@(foreach($name in $names){$item=Get-Item (Join-Path $fixture $name);@{path=$name;bytes=$item.Length;sha256=(Get-FileHash $item.FullName).Hash.ToLowerInvariant()}})
    [IO.File]::WriteAllText((Join-Path $fixture 'QA-SOURCE.json'),($policy|ConvertTo-Json -Depth 5),$utf8)
    $global:OdsCleanUserFixtureMachine=$machine.Clone()
    function Invoke-GuardedFixture([switch]$Run){
        # All PowerShell mutation boundaries must remain unreachable. The only
        # native invocation in the runner is below these blocked boundaries.
        function New-Item {throw 'UNSAFE MUTATOR REACHED'}
        function Copy-Item {throw 'UNSAFE MUTATOR REACHED'}
        function Set-Content {throw 'UNSAFE MUTATOR REACHED'}
        function Start-Transcript {throw 'UNSAFE MUTATOR REACHED'}
        function Start-Process {throw 'UNSAFE MUTATOR REACHED'}
        function Get-Command {return $null}
        function Test-Path {param($LiteralPath) if($LiteralPath.StartsWith($fixture)){return Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath};return $false} # Declared fresh private user profile.
        & (Join-Path $fixture 'run-clean-user-qa.ps1') -RunQualification:$Run
    }
    $result=@(Invoke-GuardedFixture)
    if(-not ($result -match 'PASS read-only')){throw 'Default did not stop at read-only preflight.'};$checks++
    foreach($case in @('elevated','wrong-uuid','physical','server','old-windows')){
        $global:OdsCleanUserFixtureMachine=$machine.Clone()
        switch($case){elevated{$global:OdsCleanUserFixtureMachine.Elevated=$true} wrong-uuid{$global:OdsCleanUserFixtureMachine.Uuid=$hostId}
            physical{$global:OdsCleanUserFixtureMachine.Model='Physical'} server{$global:OdsCleanUserFixtureMachine.ProductType=3} old-windows{$global:OdsCleanUserFixtureMachine.Build=19045}}
        try{Invoke-GuardedFixture -Run;throw 'Missing guard refusal.'}catch{
            if($_.Exception.Message -match 'UNSAFE MUTATOR|Missing guard'){throw};$checks++
        }
    }
    $global:OdsCleanUserFixtureMachine=$machine.Clone()
    $extra=Join-Path $fixture 'unexpected.txt'
    [IO.File]::WriteAllText($extra,'Not in frozen input manifest.',$utf8)
    try{Invoke-GuardedFixture -Run;throw 'Missing extra-file refusal.'}catch{
        if($_.Exception.Message -notmatch 'Unexpected files'){throw};$checks++
    }
    Remove-Item -LiteralPath $extra
    $policyPath=Join-Path $fixture 'QA-SOURCE.json'
    $validPolicy=[IO.File]::ReadAllText($policyPath)
    [IO.File]::WriteAllText($policyPath,'{malformed',$utf8)
    try{Invoke-GuardedFixture -Run;throw 'Missing malformed-manifest refusal.'}catch{
        if($_.Exception.Message -match 'UNSAFE MUTATOR|Missing malformed'){throw};$checks++
    }
    [IO.File]::WriteAllText($policyPath,$validPolicy,$utf8)
    [IO.File]::AppendAllText((Join-Path $fixture 'ods/desktop/windows/tests/DocumentSaveDialogTests.cs'),'tampered',$utf8)
    try{Invoke-GuardedFixture -Run;throw 'Missing hash refusal.'}catch{
        if($_.Exception.Message -notmatch 'Frozen input bytes changed'){throw};$checks++
    }
}finally{
    Remove-Variable -Name OdsCleanUserFixtureMachine -Scope Global -ErrorAction SilentlyContinue
    if([IO.Path]::GetDirectoryName($fixture) -ne [IO.Path]::GetTempPath().TrimEnd('\')){throw 'Unexpected private fixture path.'}
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
Write-Output "PASS $checks standard-user VM contracts; no UI or installer executed"

