# Real private files/ACLs; all WSL, Docker, Scheduler and service boundaries are mocked.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1')
# The pending-record contract runs the real transport only up to its first
# private write; two guards stop it before any wsl.exe launch.
$realBoundedCommand=${function:Invoke-ODSWslBoundedCommand}
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
function Get-ODSWslRunningDistributions { throw 'Unexpected WSL distribution listing in startup fixture' }
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
    if($TaskName -ceq ($identity.taskName+'-Relay')){return $null} # no relay in this startup fixture
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
    # Uninstall retirement leaves desiredRunning=false and a disabled task in
    # this per-root directory; only a new installation re-arms both.
    $script:startupTask | Add-Member -NotePropertyName State -NotePropertyValue 'Disabled' -Force
    $script:enabledTasks=@()
    function Enable-ScheduledTask { param($TaskName); $script:enabledTasks+=$TaskName; $script:startupTask.State='Ready' }
    Enable-ODSWslStartup $identity
    Check (-not (Get-ODSWslStartupIntent $identity).desiredRunning -and $script:enabledTasks.Count -eq 0) 'installer rerun keeps the stop and leaves the disabled startup task alone'
    Enable-ODSWslStartup $identity -NewInstallation
    Check ((Get-ODSWslStartupIntent $identity).desiredRunning) 'new installation at a retired root re-arms sign-in recovery'
    Check ($script:enabledTasks.Count -eq 1 -and $script:enabledTasks[0] -ceq ($identity.taskName+'-Startup') -and $script:registrations -eq 1) 'new installation re-enables the retired startup task without re-registering it'
    Enable-ODSWslStartup $identity -NewInstallation
    Check ($script:enabledTasks.Count -eq 1) 'an enabled startup task is not re-enabled'
    Set-ODSWslStartupIntent $identity $false
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
    $nativeKind=Get-ODSWslMutationKind $identity @('/usr/bin/systemctl','stop','pixel-extension-manager.service') -AsRoot
    $stackKind=Get-ODSWslMutationKind $identity @('python3',"$($identity.installRoot)/installers/lib/wsl_stack.py",'compose-stop',$identity.installRoot)
    Check ($nativeKind -ceq 'native:stop:pixel-extension-manager.service' -and $stackKind -ceq 'stack:compose-stop') 'mutation observations identify fixed native and owner Compose commands without raw argv'
    Check ((Get-ODSWslMutationKind $identity @('python3',"$($identity.installRoot)/installers/lib/wsl_stack.py",'compose-stop','/other/install')) -ceq 'other') 'Compose diagnostic classification requires the exact selected installation root'
    $token='c'*32
    $observation=New-ODSWslExitObservation $identity $token $nativeKind '2026-01-01T00:00:00Z' 12345 '67890' 1 'native output' ('x'*1200)
    Write-ODSWslJson (Join-Path $fixture 'command-pending.json') @{schemaVersion=1;state='pending';id=$identity.id;bootId='1000';token=$token}
    Reject {Complete-ODSWslCommand $identity $token 'native output' 1 $observation} 'missing Linux acknowledgement still fails closed after recording the client exit'
    $failure=Read-ODSWslJson (Join-Path $fixture 'command-failure.json')
    $pending=Read-ODSWslJson (Join-Path $fixture 'command-pending.json')
    Check ($failure.token -ceq $token -and $failure.kind -ceq $nativeKind -and $failure.clientPid -eq 12345 -and
        $failure.clientStartTicks -ceq '67890' -and $failure.clientExitCode -eq 1 -and
        -not $failure.completionSuffixPresent -and $failure.stderrChars -eq 1200 -and $failure.stderrTail.Length -eq 1024 -and
        $pending.state -ceq 'pending') 'bounded private failure receipt survives missing acknowledgement without clearing pending custody'
    $failureHash=(Get-FileHash (Join-Path $fixture 'command-failure.json')).Hash
    $successOutput="ordinary output`nODS_WSL_COMPLETED_${token}:0`n"
    $successObservation=New-ODSWslExitObservation $identity $token $nativeKind '2026-01-01T00:00:00Z' 12345 '67890' 0 $successOutput 'private native text'
    $null=Complete-ODSWslCommand $identity $token $successOutput 0 $successObservation
    Check ((Get-FileHash (Join-Path $fixture 'command-failure.json')).Hash -ceq $failureHash -and
        -not (Test-Path -LiteralPath (Join-Path $fixture 'command-observation.json'))) 'acknowledged success does not persist diagnostic output or overwrite the last failure'
    Write-ODSWslJson (Join-Path $fixture 'command-pending.json') @{schemaVersion=1;state='pending';id=$identity.id;bootId='1000';token=$token}
    $privateObservation=New-ODSWslExitObservation $identity $token $stackKind '2026-01-01T00:00:00Z' 12345 '67890' 1 'user output' 'secret fixture'
    Check (-not $privateObservation.ContainsKey('stdoutTail') -and -not $privateObservation.ContainsKey('stderrTail')) 'owner Compose observations never persist application output'
    $reservedToken='d'*32
    $reservedOutput="user output`nODS_WSL_COMPLETED_${reservedToken}:124`n"
    $reservedObservation=New-ODSWslExitObservation $identity $reservedToken $stackKind '2026-01-01T00:00:00Z' 12345 '67890' 124 $reservedOutput 'secret fixture'
    Write-ODSWslJson (Join-Path $fixture 'command-pending.json') @{schemaVersion=1;state='pending';id=$identity.id;bootId='1000';token=$reservedToken}
    $null=Complete-ODSWslCommand $identity $reservedToken $reservedOutput 124 $reservedObservation
    $reservedFailure=Read-ODSWslJson (Join-Path $fixture 'command-failure.json')
    Check ($reservedFailure.token -ceq $reservedToken -and $reservedFailure.completionSuffixPresent -and
        -not ($reservedFailure.PSObject.Properties.Name -contains 'stdoutTail') -and
        -not ($reservedFailure.PSObject.Properties.Name -contains 'stderrTail') -and
        (Read-ODSWslJson (Join-Path $fixture 'command-pending.json')).state -ceq 'pending') 'reserved timeout retains private metadata and pending custody without application output'
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
    # A later WSL VM boot (wsl --shutdown) also retires a lost acknowledgement
    # when the record holds the kernel boot id of the VM the command ran in.
    # WSL stays mocked: the fixture fakes only that read and the running list.
    & {
        $pendingPath=Join-Path $fixture 'command-pending.json'
        $oldVm='11111111-2222-4333-8444-555555555555'
        $newVm='66666666-7777-4888-9999-aaaaaaaaaaaa'
        $script:vmBoot=$oldVm;$script:vmReads=0;$script:runningDistros=@();$script:bootId='3000'
        function Get-ODSWslRunningDistributions { $script:runningDistros }
        function Invoke-ODSWslBoundedCommand { param($Identity,[string[]]$Arguments,[int]$Seconds,[switch]$AsRoot,[switch]$ListRunning,[switch]$Mutation)
            if($Identity.id -cne $script:fixtureIdentity.id -or $AsRoot -or $ListRunning -or $Mutation){throw 'Unexpected WSL command authority'}
            $command=$Arguments -join ' '
            if($command -like '/usr/bin/env docker *'){$script:events+=$command;return 'fixture-ready'}
            if($command -cne '/bin/cat /proc/sys/kernel/random/boot_id' -or $Seconds -le 0){throw 'Unexpected WSL command'}
            $script:vmReads++
            if($script:vmBoot -is [Exception]){throw $script:vmBoot}
            $script:vmBoot+"`n"
        }
        function Resolve-ODSWslRegisteredDistro {param($Name);$Name}
        function Stop-ODSWslLifetime {param($Identity);$script:events+='release';[pscustomobject]@{state='stopped'}}
        function Write-PendingFixture([string]$LinuxBootId) {
            $record=@{schemaVersion=1;state='pending';id=$identity.id;token=('d'*32);bootId=$script:bootId}
            if($LinuxBootId){$record.linuxBootId=$LinuxBootId}
            Write-ODSWslJson $pendingPath $record
            $script:events=@();$script:vmReads=0
            (Get-FileHash $pendingPath).Hash
        }
        function Invoke-Refused([string]$Action) {
            $message='';try{$null=Invoke-ODSWslLifecycle $Action $identity.distro $identity.installRoot -ValidateOnly:($Action -eq 'disable-startup')}catch{$message=$_.Exception.Message}
            $message
        }
        foreach($read in @($newVm,[IO.IOException]::new('fixture WSL relay lost'))){
            Write-ODSWslJson $pendingPath @{schemaVersion=1;state='completed';id=$identity.id}
            $script:vmBoot=$read
            & {
                $realWrite=${function:Write-ODSWslJson}
                function Write-ODSWslJson {param($Path,$Value);& $realWrite $Path $Value;if($Value.state -ceq 'pending'){throw 'fixture stopped before wsl.exe'}}
                function ConvertTo-ODSWindowsArgument {throw 'fixture guard: wsl.exe must not start'}
                $stopped='';try{& $realBoundedCommand $identity @('/usr/bin/true') 15 -Mutation}catch{$stopped=$_.Exception.Message}
                Check ($stopped -ceq 'fixture stopped before wsl.exe') 'the mutation fixture stops at its pending record, before wsl.exe'
            }
            $record=Read-ODSWslJson $pendingPath
            if($read -is [string]){
                Check ($record.state -eq 'pending' -and $record.bootId -ceq '3000' -and $record.linuxBootId -ceq $newVm) 'a stack mutation records the WSL VM boot id with its pending state'
            } else {
                Check ($record.state -eq 'pending' -and $record.bootId -ceq '3000' -and @($record.PSObject.Properties.Name) -notcontains 'linuxBootId') 'an unreadable WSL VM boot id leaves the Windows-boot-only pending record'
            }
        }
        # Start and restart may boot the distribution to read the id; after
        # wsl --shutdown nothing is running yet.
        $hash=Write-PendingFixture $oldVm
        $script:vmBoot=$oldVm
        $message=Invoke-Refused restart
        Check ($message.Contains('Run `wsl --shutdown` (or restart Windows), then retry') -and $message.Contains('do not delete command-pending.json') -and
            $script:vmReads -eq 1 -and $script:events.Count -eq 0 -and (Get-FileHash $pendingPath).Hash -ceq $hash) 'restart in the same WSL VM still refuses the unconfirmed command and names wsl --shutdown'
        foreach($read in @([IO.IOException]::new('fixture WSL relay lost'),[TimeoutException]::new('fixture read timed out'),'','fixture-ready',$newVm.ToUpperInvariant(),"$newVm`n$oldVm")){
            $hash=Write-PendingFixture $oldVm
            $script:vmBoot=$read
            $message=Invoke-Refused restart
            $label=if($read -is [Exception]){$read.GetType().Name}else{$read -replace "`n",'\n'}
            Check ($message.Contains('Run `wsl --shutdown` (or restart Windows)') -and $script:vmReads -eq 1 -and $script:events.Count -eq 0 -and
                (Get-FileHash $pendingPath).Hash -ceq $hash) "an unreadable WSL VM boot id keeps the command blocking [$label]"
        }
        $hash=Write-PendingFixture $oldVm
        $script:vmBoot=[InvalidOperationException]::new('fixture programming error')
        Check ((Invoke-Refused restart) -ceq 'fixture programming error' -and (Get-FileHash $pendingPath).Hash -ceq $hash) 'only WSL I/O failures count as an unreadable boot id; other errors still stop the action'
        $null=Write-PendingFixture $oldVm
        $script:vmBoot=$newVm
        $null=Invoke-ODSWslLifecycle restart $identity.distro $identity.installRoot
        $record=Read-ODSWslJson $pendingPath
        Check ($record.state -eq 'completed' -and $record.reason -ceq 'WSL VM restarted' -and $script:vmReads -eq 1 -and ($script:events -join ',') -ceq 'release,hold,stack-start') 'restart after a WSL VM restart retires the unconfirmed command and proceeds'
        $null=Write-PendingFixture $oldVm
        Set-ODSWslStartupIntent $identity $true
        Invoke-ODSWslStartup $fixture
        Check ((Read-ODSWslJson (Join-Path $fixture 'startup-status.json')).state -eq 'started' -and (Read-ODSWslJson $pendingPath).reason -ceq 'WSL VM restarted' -and
            (($script:events[-2..-1]) -join ',') -ceq 'hold,stack-start') 'sign-in startup after a WSL VM restart retires the unconfirmed command and starts the stack'
        # Records written before linuxBootId existed keep the Windows boot rule.
        $hash=Write-PendingFixture ''
        $message=Invoke-Refused restart
        Check ($message.Contains('Restart Windows (not just sign out), then retry') -and -not $message.Contains('wsl --shutdown') -and $script:vmReads -eq 0 -and
            $script:events.Count -eq 0 -and (Get-FileHash $pendingPath).Hash -ceq $hash) 'a record without a WSL VM boot id is not retired by a VM restart'
        $script:bootId='4000'
        $null=Invoke-ODSWslLifecycle restart $identity.distro $identity.installRoot
        Check ((Read-ODSWslJson $pendingPath).reason -ceq 'previous Windows boot ended' -and ($script:events -join ',') -ceq 'release,hold,stack-start') 'a record without a WSL VM boot id is still retired by a later Windows boot'
        $script:bootId='3000'
        # Booting a stopped distribution starts its enabled ODS units, so stop,
        # release and uninstall read the id only from a running distribution.
        $script:runningDistros=@('docker-desktop')
        foreach($action in @('stop','release','disable-startup')){
            $hash=Write-PendingFixture $oldVm
            $message=Invoke-Refused $action
            Check ($message.Contains('Run `wsl --shutdown` and open '+$identity.distro+' again (or restart Windows)') -and $script:vmReads -eq 0 -and
                $script:events.Count -eq 0 -and (Get-FileHash $pendingPath).Hash -ceq $hash) "$action never starts a stopped distribution to read its WSL VM boot id"
        }
        $script:runningDistros=@('docker-desktop',$identity.distro)
        $hash=Write-PendingFixture $oldVm
        $validated=Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot -ValidateOnly
        Check ($validated.state -eq 'validated' -and $script:vmReads -eq 1 -and $script:events.Count -eq 0 -and (Get-FileHash $pendingPath).Hash -ceq $hash) 'uninstall precheck accepts a restarted WSL VM without writing'
        $disabled=Invoke-ODSWslLifecycle disable-startup $identity.distro $identity.installRoot
        Check ($disabled.state -eq 'disabled' -and (Read-ODSWslJson $pendingPath).reason -ceq 'WSL VM restarted') 'uninstall then retires the command that the restarted WSL VM proved ended'
        $null=Write-PendingFixture $oldVm
        $null=Invoke-ODSWslLifecycle stop $identity.distro $identity.installRoot
        Check ((Read-ODSWslJson $pendingPath).reason -ceq 'WSL VM restarted' -and ($script:events -join ',') -ceq 'release') 'stop in a running distribution accepts a WSL VM restart and proceeds'
        $script:bootId='2000'
    }
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
    Check (($parameterNames -join ',') -ceq 'Action,Distro,InstallRoot,InstanceDirectory,ValidateOnly,StateRoot,DockerDesktopPath,RetireRelay') 'public script preserves all existing positional parameters before the optional relay retirement flag'
    Write-Host "Passed $count startup contracts; no WSL Docker Scheduler or service action ran."
} finally {
    if(Test-Path -LiteralPath $fixture){
        $resolved=(Resolve-Path -LiteralPath $fixture).Path
        $prefix=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
        if($resolved -cne [IO.Path]::GetFullPath($fixture) -or -not $resolved.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe startup fixture cleanup path'}
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
