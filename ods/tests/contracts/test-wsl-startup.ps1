# Real private files/ACLs; all WSL, Docker, Scheduler and service boundaries are mocked.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1')
$count=0
function Check([bool]$Value,[string]$Message) { if(-not $Value){throw $Message};$script:count++;Write-Host "PASS $Message" }
function Reject([scriptblock]$Call,[string]$Message) { $failed=$false;try{& $Call}catch{$failed=$true};Check $failed $Message }
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-wsl-startup-'+[guid]::NewGuid().ToString('N'))
$identity=Get-ODSWslIdentity 'Ubuntu-24.04' '/home/fixture/ods'
$identity.directory=$fixture
$script:fixtureIdentity=$identity
$script:startupTask=$null
$script:registrations=0
$script:events=@()
$script:scenario='ready'
$script:now=[datetime]'2026-01-01T00:00:00Z'
$script:bootId='1000'
function Get-ODSWslWindowsBootId { $script:bootId }
function Get-ODSWslUtcNow { $script:now }
function Start-Sleep { param($Seconds,$Milliseconds);$script:now=$script:now.AddSeconds([Math]::Max(1,$Seconds)) }
function Start-Process { throw 'Unexpected real process launch in startup fixture' }
function Start-ScheduledTask { throw 'Unexpected holder task launch in startup fixture' }
function Stop-ScheduledTask { param($TaskName); if($TaskName -cne $identity.taskName){throw 'Wrong task stop'};$script:events+='stop-task' }
function Disable-ScheduledTask {param($TaskName)
    if($TaskName -cne ($identity.taskName+'-Startup')){throw 'Wrong startup disable'}
    $script:events+='disable-startup'
}
function Get-ODSWslIdentity { param($Distro,$InstallRoot)
    if($Distro -cne $script:fixtureIdentity.distro -or $InstallRoot -cne $script:fixtureIdentity.installRoot){throw 'Wrong fixture identity'}
    $script:fixtureIdentity
}
function Get-ScheduledTask { param($TaskName,$ErrorAction)
    if($TaskName -ceq ($identity.taskName+'-Startup')){return $script:startupTask}
    if($TaskName -cne $identity.taskName){throw 'Unexpected task lookup'}
    [pscustomobject]@{
        Actions=@([pscustomobject]@{Execute=(Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe');Arguments=(Get-ODSWslTaskArguments $identity)})
        Settings=[pscustomobject]@{ExecutionTimeLimit='PT0S';RestartCount=0};Triggers=@()
        Principal=[pscustomobject]@{UserId=$identity.ownerSid;RunLevel='Limited'};State='Ready'
    }
}
function New-ScheduledTaskAction { param($Execute,$Argument);[pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel);[pscustomobject]@{UserId=$UserId;LogonType=$LogonType;RunLevel=$RunLevel} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User)
    if(-not $AtLogOn){throw 'Only owner logon is allowed'}
    [pscustomobject]@{UserId=$User;Delay='';CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}}
}
function New-ScheduledTaskSettingsSet { param([switch]$Hidden,$ExecutionTimeLimit,$MultipleInstances,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries)
    Check ($ExecutionTimeLimit.TotalMinutes -eq 25 -and $MultipleInstances -eq 'IgnoreNew') 'startup task is bounded and cannot overlap itself'
    [pscustomobject]@{ExecutionTimeLimit='PT25M';RestartCount=0}
}
function Register-ScheduledTask { param($TaskName,$Action,$Principal,$Trigger,$Settings,$Description)
    if($TaskName -cne ($identity.taskName+'-Startup')){throw 'Wrong startup registration'}
    $script:registrations++
    $script:startupTask=[pscustomobject]@{Actions=@($Action);Principal=$Principal;Triggers=@($Trigger);Settings=$Settings}
}
function Start-ODSWslDockerDesktop {
    $script:events+='docker'
    if($script:scenario -eq 'cancel'){Set-ODSWslStartupIntent $identity $false}
}
function Invoke-ODSWslBoundedCommand { param($Identity,[string[]]$Arguments,[int]$Seconds,[switch]$AsRoot)
    if($Identity.id -cne $identity.id -or $AsRoot){throw 'Unexpected startup probe authority'}
    $script:events+=($Arguments -join ' ')
    if($script:scenario -eq 'programming'){throw [InvalidOperationException]::new('fixture programming error')}
    if($script:scenario -eq 'acl'){throw [UnauthorizedAccessException]::new('fixture ACL denied')}
    if($script:scenario -eq 'identity'){throw 'fixture identity mismatch'}
    if($script:scenario -eq 'timeout'){throw [IO.IOException]::new('fixture Docker offline')}
    if($script:scenario -in @('cold','probe-timeout')){
        $probes=@($script:events|Where-Object{$_ -eq '/usr/bin/env docker info'}).Count
        if($probes -eq 2){$script:sawProbeDiagnostic=(Read-ODSWslJson (Join-Path $Identity.directory 'startup-status.json')).error -like 'fixture Docker*'}
        if($probes -lt 3){
            if($script:scenario -eq 'probe-timeout'){throw [TimeoutException]::new('fixture Docker probe timed out')}
            throw [IO.IOException]::new('fixture Docker still starting')
        }
    }
    'fixture-ready'
}
function Start-ODSWslLifetime { param($Identity);$script:events+='hold';[pscustomobject]@{state='running'} }
function Invoke-ODSWslStack { param($Identity,$Action)
    if($Identity.id -cne $identity.id -or $Action -ne 'start'){throw 'Unexpected startup stack'}
    $script:events+='stack-start'
    if($script:scenario -eq 'stack-failed'){throw 'fixture plan verification failed'}
}
try {
    Initialize-ODSPrivateDirectory $fixture
    Write-ODSWslJson (Join-Path $fixture 'instance.json') $identity
    $desktop=Join-Path $fixture 'Docker Desktop.exe'
    Write-ODSPrivateBytes $desktop ([byte[]]@(0))
    Enable-ODSWslStartup $identity $desktop
    Check ((Get-ODSWslStartupConfig $identity).dockerDesktopPath -ceq $desktop) 'startup uses the installer-resolved executable instead of assuming the Windows drive or Docker location'
    Check ($script:registrations -eq 1) 'verified installation creates exactly one separate startup task'
    Check ((Get-ODSWslStartupIntent $identity).desiredRunning) 'new installation opts into sign-in recovery'
    # Public enable-startup repair path: must call owner registration without
    # invoking Resolve-ODSWslRegisteredDistro (no WSL query) and without
    # touching the holder task or running any WSL/Docker/service command.
    $script:registrations=0;$script:events=@()
    $script:startupTask=$null
    Set-ODSWslStartupIntent $identity $false
    $savedResolve=Get-Command Resolve-ODSWslRegisteredDistro -CommandType Function -ErrorAction SilentlyContinue
    function Resolve-ODSWslRegisteredDistro {param($Name);throw 'enable-startup must not query WSL'}
    $savedRunning=Get-Command Get-ODSWslRunningDistributions -CommandType Function -ErrorAction SilentlyContinue
    function Get-ODSWslRunningDistributions {throw 'enable-startup must not enumerate WSL distributions'}
    try {
        $repair=Invoke-ODSWslLifecycle enable-startup $identity.distro $identity.installRoot -DockerDesktopPath $desktop
        Check ($script:registrations -eq 1) 'enable-startup registers the owner-limited startup task without rerunning the installer'
        Check ($script:events.Count -eq 0) 'enable-startup performs no WSL Docker or service operation'
        Check (-not (Get-ODSWslStartupIntent $identity).desiredRunning) 'enable-startup preserves the existing desiredRunning=false preference'
        Check ($repair.identity.id -ceq $identity.id) 'enable-startup returns the lifetime status for the repaired installation'
        Check ($repair.state -eq 'registered' -and $null -eq $repair.distroRunning) 'startup registration returns Windows-only status without claiming runtime readiness'
    } finally {
        if ($savedResolve) { Set-Item -Path Function:\Resolve-ODSWslRegisteredDistro -Value $savedResolve.ScriptBlock }
        if ($savedRunning) { Set-Item -Path Function:\Get-ODSWslRunningDistributions -Value $savedRunning.ScriptBlock }
    }
    $script:registrations=0;$script:startupTask=$null
    Enable-ODSWslStartup $identity $desktop
    Check ($script:registrations -eq 1) 'fixture re-registers the startup task for subsequent checks'
    Check ((Read-ODSWslJson (Join-Path $fixture 'instance.json')).id -ceq $identity.id) 'registration preserves the installation manifest'
    Check ((Get-Content (Join-Path $fixture 'startup.ps1') -Raw).Contains('function Invoke-ODSWslStartup')) 'startup launcher is copied into durable private state'
    Set-ODSWslStartupIntent $identity $false
    Enable-ODSWslStartup $identity
    Check ($script:registrations -eq 1 -and -not (Get-ODSWslStartupIntent $identity).desiredRunning) 'rerun preserves explicit stop and never rewrites task registration'
    Invoke-ODSWslStartup $fixture
    Check ($script:events.Count -eq 0) 'disabled startup performs no Docker WSL or service operation'
    Check ((Read-ODSWslJson (Join-Path $fixture 'startup-status.json')).state -eq 'disabled') 'disabled startup is visible in status'
    $saved=$script:startupTask.Actions[0].Arguments
    $script:startupTask.Actions[0].Arguments+=' foreign'
    Reject {Enable-ODSWslStartup $identity} 'foreign task action is rejected instead of replaced'
    Check ($script:registrations -eq 1) 'foreign registration was not overwritten'
    $script:startupTask.Actions[0].Arguments=$saved
    $script:startupTask.Triggers[0].UserId='S-1-5-18'
    Reject {Assert-ODSWslStartupTask $identity} 'foreign logon trigger is rejected'
    $script:startupTask.Triggers[0].UserId=$identity.ownerSid
    $intent=Get-ODSWslStartupIntent $identity
    $intent.distro='Other-Ubuntu';Write-ODSWslJson (Join-Path $fixture 'startup-intent.json') $intent
    Reject {Get-ODSWslStartupIntent $identity} 'startup preference cannot cross distro ownership'
    $intent.distro=$identity.distro;Write-ODSWslJson (Join-Path $fixture 'startup-intent.json') $intent
    foreach($case in @('ready','cold','probe-timeout','timeout','cancel','stack-failed','programming','acl','identity')) {
        $script:events=@();$script:scenario=$case;$script:now=[datetime]'2026-01-01T00:00:00Z'
        $script:sawProbeDiagnostic=$false
        Set-ODSWslStartupIntent $identity $true
        $failure=''
        try{Invoke-ODSWslStartup $fixture}catch{$failure=$_.Exception.Message}
        $status=Read-ODSWslJson (Join-Path $fixture 'startup-status.json')
        if($case -in @('ready','cold','probe-timeout')){
            Check (-not $failure -and $status.state -eq 'started') "$case startup reports stack completion"
            Check (($script:events[-2..-1] -join ',') -eq 'hold,stack-start') "$case startup waits for Docker before holding and starting its stack"
            if($case -ne 'ready'){Check $script:sawProbeDiagnostic "$case publishes the last readiness failure while waiting"}
        } else {
            Check ($failure -and $status.state -eq 'failed' -and $status.error -ceq $failure) "$case startup propagates failure and leaves an actionable record"
            if($case -ne 'stack-failed'){Check (-not ($script:events -contains 'hold')) "$case cannot start the stack"}
            if($case -in @('programming','acl','identity')){
                Check (@($script:events|Where-Object{$_ -eq '/usr/bin/env docker info'}).Count -eq 1 -and $script:now -eq [datetime]'2026-01-01T00:00:00Z') "$case aborts immediately instead of being treated as Docker readiness"
            }
            if($case -eq 'timeout'){Check ($status.error -like '*fixture Docker offline*') 'Docker readiness deadline retains the last probe diagnostic'}
        }
    }
    $script:events=@()
    $generation='a'*32
    $controller=[pscustomobject]@{pid=123;startTicks='1';executable='fixture';commandLine='fixture'}
    $child=[pscustomobject]@{pid=124;startTicks='2';executable='fixture-wsl';commandLine='fixture-holder'}
    Write-ODSWslJson (Join-Path $fixture 'request.json') @{generation=$generation;action='run'}
    Write-ODSWslJson (Join-Path $fixture 'runtime.json') @{generation=$generation;state='running';controller=$controller;child=$child}
    function Get-ODSWslLifetimeStatus {param($Identity);[pscustomobject]@{state='running';distroRunning=$false}}
    function Get-ODSProcessIdentity {param($ProcessId);if($ProcessId -eq 123){return $controller};throw 'Unexpected fixture PID'}
    function Stop-ODSOwnedProcess {param($Expected);if($Expected.pid -ne 124 -or $Expected.startTicks -ne '2'){throw 'Wrong captured child'};$script:events+='stop-exact-child'}
    $null=Stop-ODSWslLifetime $identity
    Check (($script:events -join ',') -eq 'stop-task,stop-exact-child') 'wedged controller fallback stops only the verified task and captured child'
    Check ((Read-ODSWslJson (Join-Path $fixture 'runtime.json')).state -eq 'stopped') 'wedged controller confirms stopped after exact cleanup'
    $script:events=@()
    Write-ODSWslJson (Join-Path $fixture 'runtime.json') @{generation=('b'*32);state='running';controller=$controller;child=$child}
    Reject {Stop-ODSWslLifetime $identity} 'fallback refuses a different controller generation'
    Check ($script:events.Count -eq 0) 'different generation triggers no Scheduler or child stop'
    function Resolve-ODSWslRegisteredDistro {param($Name);'Ubuntu-24.04'}
    function Stop-ODSWslLifetime {param($Identity)
        Check (-not (Get-ODSWslStartupIntent $Identity).desiredRunning) 'explicit stop is persisted before holder release'
        [pscustomobject]@{state='stopped'}
    }
    Set-ODSWslStartupIntent $identity $true
    $null=Invoke-ODSWslLifecycle stop 'ubuntu-24.04' $identity.installRoot
    Check (-not (Get-ODSWslStartupIntent $identity).desiredRunning) 'explicit stop remains disabled across the next sign-in'
    # A deterministic late Linux completion: cancellation publishes stopped,
    # but command.lock must stay held through the simulated Compose completion.
    $script:events=@();Set-ODSWslStartupIntent $identity $true
    $script:ODSWslStartupIdentity=$identity
    $script:ODSWslStartupGeneration=(Get-ODSWslStartupIntent $identity).generation
    $script:ODSWslStartupDeadline=$script:now.AddMinutes(20)
    $drainingLock=Open-ODSPrivateLock (Join-Path $fixture 'command.lock')
    $fakeProcess=[pscustomobject]@{polls=0}
    $fakeProcess | Add-Member ScriptMethod WaitForExit {
        param($Milliseconds)
        $this.polls++
        if($this.polls -eq 1){Set-ODSWslStartupIntent $script:fixtureIdentity $false}
        $other=$null
        try{$other=Open-ODSPrivateLock (Join-Path $script:fixtureIdentity.directory 'command.lock')}catch [IO.IOException]{}
        if($other){$other.Dispose();throw 'Concurrent stop acquired command lock before Linux completion'}
        if($this.polls -eq 3){$script:events+='late-compose-completion';return $true}
        return $false
    }
    try{$cancelled=Wait-ODSWslCommandProcess $fakeProcess $script:now.AddMinutes(20) -Mutation}finally{$drainingLock.Dispose()}
    Check ($cancelled -match 'newer owner command' -and $fakeProcess.polls -eq 3 -and $script:events[-1] -eq 'late-compose-completion') 'stop during Compose retains the command lock until late Linux completion'
    Check (-not (Get-ODSWslStartupIntent $identity).desiredRunning) 'late Linux completion cannot rewrite the newer stopped preference'
    $script:ODSWslStartupIdentity=$null;$script:ODSWslStartupGeneration=$null;$script:ODSWslStartupDeadline=$null
    # Simulate a stop arriving after manual start obtains its generation but
    # while it waits for command.lock. No holder or stack start may follow.
    function Open-ODSWslCommandLock {param($Identity)
        Set-ODSWslStartupIntent $Identity $false
        Open-ODSPrivateLock (Join-Path $Identity.directory 'command.lock')
    }
    $script:events=@()
    Reject {Invoke-ODSWslLifecycle start $identity.distro $identity.installRoot} 'manual start cannot overwrite a stop arriving while it waits for the command lock'
    Check ($script:events.Count -eq 0 -and -not (Get-ODSWslStartupIntent $identity).desiredRunning) 'cancelled manual start performs no holder or stack start'
    function Open-ODSWslCommandLock {param($Identity);Open-ODSPrivateLock (Join-Path $Identity.directory 'command.lock')}
    foreach($code in @(0,1,124,137)){
        Write-ODSWslJson (Join-Path $fixture 'command-pending.json') @{schemaVersion=1;state='pending';id=$identity.id;bootId='1000'}
        $payload=Complete-ODSWslCommand $identity ('a'*32) ("progress`nODS_WSL_COMPLETED_"+('a'*32)+":${code}`n") $code
        $pending=Read-ODSWslJson (Join-Path $fixture 'command-pending.json')
        Check ($payload -ceq 'progress' -and ($pending.state -eq 'completed') -eq ($code -in @(0,1))) "Linux acknowledgement distinguishes retryable exit $code from ambiguous timeout"
    }
    Reject {Complete-ODSWslCommand $identity ('b'*32) ("progress`nODS_WSL_COMPLETED_"+('a'*32)+":0`n") 0} 'foreign completion token cannot clear an interrupted stack operation'
    Check ((Read-ODSWslJson (Join-Path $fixture 'command-pending.json')).state -eq 'pending') 'missing exact completion retains the fail-closed record'
    Write-ODSWslJson (Join-Path $fixture 'command-pending.json') @{schemaVersion=1;state='completed';id=$identity.id}
    function Resolve-ODSWslRegisteredDistro {param($Name);throw 'Uninstall must not query or enter WSL'}
    $script:events=@()
    Set-ODSWslStartupIntent $identity $true
    $before=Get-FileHash (Join-Path $fixture 'startup-intent.json')
    $validated=Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot -ValidateOnly
    Check ($validated.state -eq 'validated' -and $validated.identity.installRoot -ceq $identity.installRoot) 'uninstall precheck returns the validated installation identity'
    Check ($before.Hash -ceq (Get-FileHash (Join-Path $fixture 'startup-intent.json')).Hash -and $script:events.Count -eq 0) 'uninstall precheck writes nothing and never disables a task'
    $busy=Open-ODSPrivateLock (Join-Path $fixture 'command.lock')
    try{Reject {Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot -ValidateOnly} 'uninstall precheck refuses an in-flight stack command'}finally{$busy.Dispose()}
    Write-ODSWslJson (Join-Path $fixture 'command-pending.json') @{schemaVersion=1;state='pending';id=$identity.id;bootId='1000'}
    Reject {Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot -ValidateOnly} 'uninstall refuses an ambiguous Linux completion from the current Windows boot'
    $script:bootId='2000'
    $pendingHash=(Get-FileHash (Join-Path $fixture 'command-pending.json')).Hash
    $null=Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot -ValidateOnly
    Check ($pendingHash -ceq (Get-FileHash (Join-Path $fixture 'command-pending.json')).Hash) 'recovery precheck after full Windows restart remains read only'
    $disabled=Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot
    Check ($disabled.state -eq 'disabled' -and ($script:events -join ',') -eq 'disable-startup') 'uninstall disables only the exact Windows startup task without WSL'
    Check ((Read-ODSWslJson (Join-Path $fixture 'command-pending.json')).state -eq 'completed') 'a later Windows boot safely retires an interrupted command without deleting metadata'
    $script:startupTask=$null;$script:events=@()
    $disabled=Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot
    Check ($disabled.state -eq 'disabled' -and $script:events.Count -eq 0) 'missing startup task remains idempotently disabled without registration'
    Check (-not (Get-ODSWslStartupArguments $identity).Contains($PSScriptRoot)) 'startup action is independent of temporary installer sources'
    $listing=@('--list','--running','--quiet') | ForEach-Object { ConvertTo-ODSWindowsArgument $_ }
    Check (($listing -join ' ') -ceq '--list --running --quiet') 'WSL option prefix contains no redundant quotes'
    Check ((ConvertTo-ODSWindowsArgument 'Ubuntu-24.04') -ceq 'Ubuntu-24.04') 'simple distribution names remain unquoted'
    Check ((ConvertTo-ODSWindowsArgument '') -ceq '""') 'empty executable arguments remain explicit'
    Check ((ConvertTo-ODSWindowsArgument '/home/owner/ods') -ceq '/home/owner/ods') 'simple Linux paths remain unquoted'
    Check ((ConvertTo-ODSWindowsArgument 'C:\a b\') -ceq '"C:\a b\\"') 'Windows argv quoting preserves trailing separators'
    Check ((ConvertTo-ODSWindowsArgument 'a"b') -ceq '"a\"b"') 'Windows argv quoting preserves embedded quotes'
    $parameterNames=@([Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1'),[ref]$null,[ref]$null
    ).ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    Check (($parameterNames -join ',') -ceq 'Action,Distro,InstallRoot,InstanceDirectory,ValidateOnly,StateRoot,DockerDesktopPath') 'public script preserves all legacy positional parameters before the new optional Docker path'
    Write-Host "Passed $count startup contracts; no WSL Docker Scheduler or service action ran."
} finally {
    if(Test-Path -LiteralPath $fixture){
        $resolved=(Resolve-Path -LiteralPath $fixture).Path
        $prefix=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
        if($resolved -cne [IO.Path]::GetFullPath($fixture) -or -not $resolved.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe startup fixture cleanup path'}
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
