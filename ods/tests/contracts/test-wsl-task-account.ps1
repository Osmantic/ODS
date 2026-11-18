[CmdletBinding()]
param([switch]$Live)
# Real lifecycle entrypoints; account lookup and all host mutation boundaries
# are simulated. No Windows account, task, WSL distro or ODS runtime is changed.
$ErrorActionPreference='Stop'
Import-Module Microsoft.PowerShell.Utility,Microsoft.PowerShell.Management
. (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1')
if ($Live) {
    # Temporary, harmless tasks only: never start WSL, Docker or an ODS task.
    # Exercise the real SID resolver, CIM principal and scheduler readback.
    Import-Module ScheduledTasks -ErrorAction Stop
    $owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $identity=[pscustomobject]@{ownerSid=$owner}
    $executable=Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
    $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -Command "exit 0"'
    $registered=@()
    try {
        foreach ($kind in @('lifetime','relay','startup')) {
            $name='ODS-Account-Regression-'+[guid]::NewGuid().ToString('N')
            $probeAction=New-ScheduledTaskAction -Execute $executable -Argument $arguments
            $settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::FromMinutes(1)) -MultipleInstances IgnoreNew
            Register-ODSWslTask $identity $name $probeAction $settings 'Temporary ODS account regression; harmless exit only.' -AtLogOn:($kind -eq 'startup')
            $registered+=@($name)
            $task=Get-ScheduledTask -TaskName $name -ErrorAction Stop
            $sid=$task.Principal.UserId
            if ($sid -notmatch '^S-1-') { $sid=([Security.Principal.NTAccount]::new($sid)).Translate([Security.Principal.SecurityIdentifier]).Value }
            if ($sid -cne $owner -or $task.Principal.LogonType -ne 'Interactive' -or $task.Principal.RunLevel -ne 'Limited') { throw 'Real task changed owner or authority' }
            $triggers=@($task.Triggers | Where-Object { $null -ne $_ })
            if ($kind -eq 'startup') {
                if ($triggers.Count -ne 1 -or $triggers[0].Delay -ne 'PT30S') { throw 'Real owner logon trigger changed' }
                $triggerSid=$triggers[0].UserId
                if ($triggerSid -notmatch '^S-1-') { $triggerSid=([Security.Principal.NTAccount]::new($triggerSid)).Translate([Security.Principal.SecurityIdentifier]).Value }
                if ($triggerSid -cne $owner) { throw 'Real logon trigger changed owner' }
            } elseif ($triggers.Count -ne 0) { throw 'On-demand task acquired a trigger' }
            Write-Host "PASS real $kind task registration retains owner, limited interactive principal and trigger scope"
        }
    } finally {
        foreach ($name in $registered) {
            $task=Get-ScheduledTask -TaskName $name -ErrorAction Stop
            $sid=$task.Principal.UserId
            if ($sid -notmatch '^S-1-') { $sid=([Security.Principal.NTAccount]::new($sid)).Translate([Security.Principal.SecurityIdentifier]).Value }
            if ($sid -cne $owner -or @($task.Actions).Count -ne 1 -or
                $task.Actions[0].Execute -ine $executable -or $task.Actions[0].Arguments -cne $arguments) { throw 'Temporary task identity changed; refusing cleanup' }
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
            if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw 'Temporary task cleanup failed' }
        }
    }
    Write-Host '3 real temporary task registrations passed and were removed'
    return
}
$PSModuleAutoLoadingPreference='None'
$realAccountResolver=$null
if (Test-Path Function:\Resolve-ODSWslTaskAccount) { $realAccountResolver=${function:Resolve-ODSWslTaskAccount} }
$script:checks=0
function Check([bool]$Value,[string]$Message) {
    if (-not $Value) { throw $Message }
    $script:checks++; Write-Host "PASS $Message"
}
$script:identity=[pscustomobject]@{
    ownerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    taskName='ODS-WSL-Account-Fixture';id=('a'*64);schemaVersion=1
    directory=(Join-Path $PSScriptRoot '.task-account-memory-fixture')
    distro='Fixture';installRoot='/home/fixture/ods'
}
function Reset([string]$Account) {
    $script:account=$Account; $script:files=@{}; $script:tasks=@{}
    $script:registrations=@(); $script:starts=0; $script:failure=''
    $script:lookupCount=0
}
function Resolve-ODSWslTaskAccount { param([string]$OwnerSid)
    if ($OwnerSid -cne $script:identity.ownerSid) { throw 'Wrong account lookup SID' }
    $script:account
}
function Assert-ODSWslManifest { param($Identity) }
function Initialize-ODSPrivateDirectory { param($Path) }
function Assert-ODSPrivatePath { param($Path,[switch]$Directory) }
function Write-ODSPrivateBytes { param($Path,$Bytes,[switch]$CreateOnly); $script:files[$Path]=$Bytes }
function Write-ODSWslJson { param($Path,$Value); $script:files[$Path]=$Value }
function Read-ODSWslJson { param($Path); $script:files[$Path] }
function Get-ODSWslLifetimeStatus { param($Identity); [pscustomobject]@{state='inactive'} }
function Get-ScheduledTask { param($TaskName,$ErrorAction)
    $script:lookupCount++; $script:tasks[$TaskName]
}
function Get-ODSProcessIdentity { param($ProcessId); [pscustomobject]@{pid=$ProcessId;startTicks='1';executable='fixture.exe';commandLine='fixture'} }
function New-ScheduledTaskAction { param($Execute,$Argument); [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
function New-ScheduledTaskPrincipal {
    [CmdletBinding()] param($UserId,$LogonType,$RunLevel)
    [pscustomobject]@{UserId=$UserId;LogonType=$LogonType;RunLevel=$RunLevel}
}
function New-ScheduledTaskTrigger {
    [CmdletBinding()] param([switch]$AtLogOn,$User)
    [pscustomobject]@{UserId=$User;Delay='';CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}}
}
function New-ScheduledTaskSettingsSet { param([switch]$Hidden,$ExecutionTimeLimit,$MultipleInstances,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries)
    [pscustomobject]@{ExecutionTimeLimit=$(if($ExecutionTimeLimit.TotalMinutes -eq 25){'PT25M'}else{'PT0S'});RestartCount=0;MultipleInstances=$MultipleInstances}
}
function Register-ScheduledTask {
    [CmdletBinding()] param($TaskName,$Action,$Principal,$Settings,$Trigger,$Description)
    $script:registrations+=@{name=$TaskName;user=$Principal.UserId;trigger=$Trigger;level=$Principal.RunLevel;logon=$Principal.LogonType}
    if ($Principal.UserId -cne $script:account -or $script:failure) {
        $message=if($script:failure -eq 'denied'){'Access is denied.'}elseif($script:failure -eq 'settings'){'The parameter is incorrect. (13,4):ExecutionTimeLimit:'}else{'The parameter is incorrect. (12,8):UserId:'}
        $errorId=if($script:failure -eq 'denied'){'HRESULT 0x80070005'}else{'HRESULT 0x80070057'}
        $record=[Management.Automation.ErrorRecord]::new([Microsoft.Management.Infrastructure.CimException]::new($message),$errorId,[Management.Automation.ErrorCategory]::InvalidArgument,$TaskName)
        $PSCmdlet.WriteError($record)
        return
    }
    # Scheduler may return the equivalent SID even when registered by name.
    $storedPrincipal=[pscustomobject]@{UserId=$script:identity.ownerSid;RunLevel=$Principal.RunLevel;LogonType=$Principal.LogonType}
    $storedTrigger=@()
    if ($Trigger) {
        $storedTrigger=@([pscustomobject]@{UserId=$script:identity.ownerSid;Delay=$Trigger.Delay;CimClass=$Trigger.CimClass})
    }
    $script:tasks[$TaskName]=[pscustomobject]@{Actions=@($Action);Principal=$storedPrincipal;Settings=$Settings;Triggers=$storedTrigger;State='Ready'}
}
function Start-ScheduledTask { param($TaskName)
    $script:starts++
    $relay=$TaskName.EndsWith('-Relay')
    $requestName=if($relay){'relay-request.json'}else{'request.json'}
    $runtimeName=if($relay){'relay-runtime.json'}else{'runtime.json'}
    $request=$script:files[(Join-Path $script:identity.directory $requestName)]
    $process=Get-ODSProcessIdentity 123
    $script:files[(Join-Path $script:identity.directory $runtimeName)]=@{generation=$request.generation;state='running';controller=$process;child=$process}
}
function Start-Sleep { throw 'Unexpected wait in account fixture' }
function Test-Path { param($LiteralPath,$PathType)
    if ($LiteralPath -eq 'C:\Fixture\Docker Desktop.exe') { return $true }
    $script:files.ContainsKey($LiteralPath) -or (Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath)
}
function Invoke-Case([string]$Kind) {
    switch($Kind) {
        'lifetime' { $null=Start-ODSWslLifetime $script:identity }
        'relay' { Start-ODSWslAgentRelay $script:identity }
        'startup' {
            # Startup first validates the already-owned lifetime task.
            $action=New-ScheduledTaskAction (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') (Get-ODSWslTaskArguments $script:identity)
            $script:tasks[$script:identity.taskName]=[pscustomobject]@{
                Actions=@($action);Principal=[pscustomobject]@{UserId=$script:identity.ownerSid;RunLevel='Limited';LogonType='Interactive'}
                Settings=[pscustomobject]@{ExecutionTimeLimit='PT0S';RestartCount=0};Triggers=@();State='Ready'
            }
            Enable-ODSWslStartup $script:identity 'C:\Fixture\Docker Desktop.exe'
        }
    }
}
foreach ($kind in @('lifetime','relay','startup')) {
    foreach ($account in @('BLICK\Blick','CONTOSO\Blick','AzureAD\owner@example.invalid','OTHER-PC\Owner')) {
        Reset $account
        Invoke-Case $kind
        Check ($script:registrations.Count -eq 1 -and $script:registrations[0].user -ceq $account) "$kind registers the SID-verified qualified account ($account)"
        Check ($script:registrations[0].level -eq 'Limited' -and $script:registrations[0].logon -eq 'Interactive') "$kind retains limited credential-free owner authority"
        if ($kind -eq 'startup') {
            Check ($script:registrations[0].trigger.UserId -ceq $account -and $script:registrations[0].trigger.Delay -eq 'PT30S') 'sign-in trigger uses the same qualified owner'
        }
    }
    # Reproduce nonterminating CIM failure even when a caller changed the
    # preference: registration must stop this entrypoint before task assertion/start.
    $computer=[Environment]::MachineName
    Reset ($computer+'\'+$computer.ToLowerInvariant())
    $script:failure='invalid-user'
    $message=''; $lookups=0
    $ErrorActionPreference='Continue'
    try { Invoke-Case $kind } catch { $message=$_.Exception.Message; $lookups=$script:lookupCount }
    finally { $ErrorActionPreference='Stop' }
    Check ($message -match 'Rename this PC' -and $message -match 'restart Windows') "$kind explains rejected same-name account recovery"
    Check ($message -notmatch 'task is missing' -and $script:starts -eq 0 -and $script:registrations.Count -eq 1) "$kind does not continue or retry after rejected registration"
    Check ($lookups -eq $(if($kind -eq 'relay'){2}elseif($kind -eq 'startup'){2}else{1})) "$kind performs no post-failure task assertion"
    if ($kind -eq 'lifetime') {
        Check (-not $script:files.ContainsKey((Join-Path $script:identity.directory 'request.json'))) 'failed registration does not publish a lifetime run request'
    }
    $script:failure=''
    Invoke-Case $kind
    Check ($script:registrations.Count -eq 2) "$kind can retry registration using its retained state"
    foreach($account in @(('DOMAIN\'+$computer),'OTHER-PC\Owner')) {
        Reset $account; $script:failure='invalid-user'; $message=''
        try { Invoke-Case $kind } catch { $message=$_.Exception.Message }
        Check ($message -match 'UserId' -and $message -notmatch 'Rename this PC') "$kind preserves unrelated registration failure ($account)"
    }
    Reset ($computer+'\'+$computer)
    $script:failure='denied'; $message=''
    try { Invoke-Case $kind } catch { $message=$_.Exception.Message }
    Check ($message -match 'Access is denied' -and $message -notmatch 'Rename this PC') "$kind does not misdiagnose access denial as a name collision"
    $script:failure='settings'; $message=''
    try { Invoke-Case $kind } catch { $message=$_.Exception.Message }
    Check ($message -match 'ExecutionTimeLimit' -and $message -notmatch 'Rename this PC') "$kind does not misdiagnose another invalid task field as a name collision"
}
# Run the real account lookup separately from the simulated account matrix.
if (-not $realAccountResolver) { throw 'Production SID-bound account resolver is missing' }
Set-Item Function:\Resolve-ODSWslTaskAccount -Value $realAccountResolver
$actual=Resolve-ODSWslTaskAccount $script:identity.ownerSid
$roundTrip=([Security.Principal.NTAccount]::new($actual)).Translate([Security.Principal.SecurityIdentifier]).Value
Check ($actual.Contains('\') -and $roundTrip -ceq $script:identity.ownerSid) 'real Windows account lookup round-trips to the installation owner SID'
$foreign='S-1-5-18'
if ($foreign -ceq $script:identity.ownerSid) { $foreign='S-1-5-19' }
$message=''; try { Resolve-ODSWslTaskAccount $foreign } catch { $message=$_.Exception.Message }
Check ($message -match 'current installation owner') 'account resolution refuses a different SID before registration'
Write-Host "$script:checks WSL task-account checks passed"
