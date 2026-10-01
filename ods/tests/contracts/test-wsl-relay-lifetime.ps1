$ErrorActionPreference='Stop'
# Load boundary modules before defining fakes; lazy imports must not replace them.
Import-Module Microsoft.PowerShell.Utility,Microsoft.PowerShell.Management
$PSModuleAutoLoadingPreference='None'
. (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1')
$script:checks=0
function Check([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message }; $script:checks++; Write-Host "PASS $Message" }
function Reject([scriptblock]$Operation,[string]$Pattern) {
    $message=''; try { & $Operation } catch { $message=$_.Exception.Message }
    Check ($message -like $Pattern) "rejects $Pattern"
}
$script:identity=[pscustomobject]@{directory=(Join-Path $PSScriptRoot '.relay-memory-fixture');taskName='ODS-Relay-Memory-Fixture';ownerSid='S-1-5-21-1-2-3-1001';distro='Fixture';installRoot='/home/fixture/ods'}
function Record([int]$Number) { [pscustomobject]@{pid=$Number;startTicks=('ticks'+$Number);executable='fixture.exe';commandLine=('fixture '+$Number)} }
function Key([string]$Name) { Join-Path $script:identity.directory $Name }
function Reset {
    $script:files=@{}; $script:task=$null; $script:processes=@{}; $script:started=0; $script:spawned=0; $script:stopped=@(); $script:mode='running'; $script:ticks=0; $script:holder=$false
    $script:directoryExists=$false; $script:intentWrites=0; $script:lockBusy=$false; $script:pending=$false
    $script:processes[101]=Record 101; $script:processes[102]=Record 102; $script:processes[$PID]=Record $PID
}
# Every execution boundary is mocked. No directory is created and no WSL,
# Scheduler or real process operation is permitted by this fixture.
function Get-ScheduledTask { param($TaskName,$ErrorAction); if ($TaskName -ceq ($script:identity.taskName+'-Startup')) { return $null }; if ($TaskName -cne ($script:identity.taskName+'-Relay')) { throw 'Unexpected task' }; $script:task }
function New-ScheduledTaskAction { param($Execute,$Argument); [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel); [pscustomobject]@{UserId=$UserId;LogonType=$LogonType;RunLevel=$RunLevel} }
function New-ScheduledTaskSettingsSet { param([switch]$Hidden,$ExecutionTimeLimit,$MultipleInstances,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries); [pscustomobject]@{ExecutionTimeLimit='PT0S';RestartCount=0;MultipleInstances=$MultipleInstances} }
function Register-ScheduledTask { param($TaskName,$Action,$Principal,$Settings,$Description); $script:task=[pscustomobject]@{Actions=@($Action);Principal=$Principal;Settings=$Settings;Triggers=@();State='Ready'} }
function NewTask {
    Register-ScheduledTask -TaskName ($script:identity.taskName+'-Relay') -Action (New-ScheduledTaskAction (Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe') (Get-ODSWslRelayTaskArguments $script:identity)) -Principal (New-ScheduledTaskPrincipal $script:identity.ownerSid 'Interactive' 'Limited') -Settings (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew)
}
function Start-ScheduledTask { param($TaskName)
    $script:started++; $script:task.State='Running'; $request=$script:files[(Key 'relay-request.json')]
    $generation=if ($script:mode -eq 'stale') { 'stale' } else { $request.generation }
    $script:files[(Key 'relay-runtime.json')]=@{generation=$generation;state=$script:mode;controller=(Record 101);child=(Record 102);error='fixture failure'}
    $script:files[(Key 'agent-relay-process.json')]=Record 102
}
function Stop-ScheduledTask { param($TaskName); $script:task.State='Ready' }
function Assert-ODSWslManifest { param($Identity) }
function Assert-ODSPrivatePath { param($Path,[switch]$Directory) }
function Assert-ODSWslStartupStillWanted { }
function Read-ODSWslJson { param($Path); $script:files[$Path] }
function Write-ODSWslJson { param($Path,$Value); $script:files[$Path]=$Value }
function Write-ODSPrivateBytes { param($Path,$Bytes); $script:files[$Path]=$Bytes }
function Open-ODSPrivateLock { param($Path); [IO.MemoryStream]::new() }
function Get-ODSProcessIdentity { param([int]$ProcessId); $script:processes[$ProcessId] }
function Stop-ODSOwnedProcess { param($Expected)
    if ($script:processes.ContainsKey([int]$Expected.pid)) {
        if (-not (Test-ODSProcessIdentity $Expected $script:processes[[int]$Expected.pid])) { throw 'fixture process identity changed' }
        $script:processes.Remove([int]$Expected.pid)
    }
    $script:stopped+=@($Expected.pid)
    if ($script:holder) { $script:child.HasExited=$true }
}
function Remove-Item { param($LiteralPath,[switch]$Force)
    if ($LiteralPath -cne (Key 'agent-relay-process.json')) { throw 'Unexpected fixture removal' }
    $script:files.Remove($LiteralPath)
}
function Test-Path { param($LiteralPath,$PathType)
    if ($LiteralPath -ceq $script:identity.directory) { return $script:directoryExists }
    if ($script:files.ContainsKey($LiteralPath)) { return $true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
}
function Get-FileHash { param($LiteralPath,$Algorithm); [pscustomobject]@{Hash='same-source'} }
function Start-Sleep { param($Milliseconds,$Seconds)
    $script:ticks++
    if ($script:holder) { $script:files[(Key 'relay-request.json')].action='stop' }
    elseif ($script:task -and $script:files.ContainsKey((Key 'relay-request.json')) -and $script:files[(Key 'relay-request.json')].action -eq 'stop') { $script:task.State='Ready' }
}
function Start-Process { param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
    if (-not $script:holder) { throw 'Caller must never launch a relay child' }
    $script:spawned++
    $script:child=[pscustomobject]@{Id=102;Handle=1;HasExited=$false;ExitCode=0}
    foreach ($method in @('Refresh','WaitForExit','Dispose')) { $script:child | Add-Member ScriptMethod $method {} }
    $script:child
}

Reset; NewTask
Check ($null -ne (Assert-ODSWslRelayTask $script:identity)) 'valid on-demand owner task accepted'
foreach ($mutation in @(
    { $script:task.Actions[0].Execute='C:\foreign.exe' },
    { $script:task.Actions[0].Arguments+=' extra' },
    { $script:task.Principal.UserId='S-1-5-18' },
    { $script:task.Principal.LogonType='ServiceAccount' },
    { $script:task.Principal.RunLevel='Highest' },
    { $script:task.Triggers=@([pscustomobject]@{Enabled=$true}) },
    { $script:task.Settings.ExecutionTimeLimit='PT25M' },
    { $script:task.Settings.RestartCount=1 },
    { $script:task.Settings.MultipleInstances='Parallel' }
)) {
    Reset; NewTask; & $mutation
    Reject { Start-ODSWslAgentRelay $script:identity } '*relay task identity changed*'
    Check ($script:started -eq 0 -and $script:stopped.Count -eq 0 -and $script:files.Count -eq 0) 'foreign task rejected before mutation'
}
Reset
$script:processes[103]=Record 103
$script:files[(Key 'agent-relay-process.json')]=Record 103
Start-ODSWslAgentRelay $script:identity
Check ($script:stopped -contains 103 -and $script:started -eq 1 -and $script:spawned -eq 0) 'legacy caller child migrates to scheduler without caller spawn'
$previous=$script:files[(Key 'relay-request.json')].generation
Start-ODSWslAgentRelay $script:identity
Check ($script:started -eq 1 -and $script:files[(Key 'relay-request.json')].generation -ceq $previous) 'matching durable relay is reused'
Stop-ODSWslAgentRelay $script:identity
Check ($script:files[(Key 'relay-request.json')].action -eq 'stop' -and $script:stopped -contains 102) 'stop requests controller shutdown and releases exact child'
Check (-not $script:files.ContainsKey((Key 'agent-relay-process.json'))) 'stop removes the owned process record'
Reset; $script:mode='stale'
Reject { Start-ODSWslAgentRelay $script:identity } '*startup timed out*'
Check ($script:ticks -eq 60 -and $script:files[(Key 'relay-request.json')].action -eq 'stop') 'stale generation cannot succeed; timeout cancels delayed launch'
Reset; $script:mode='failed'
Reject { Start-ODSWslAgentRelay $script:identity } '*fixture failure*'
Check ($script:files[(Key 'relay-request.json')].action -eq 'stop') 'early controller failure cancels start request'

# Run the actual holder against process fakes, including the cancelled launch.
function Get-ODSWslIdentity { param($Distro,$InstallRoot); $script:identity }
Reset; NewTask; $script:holder=$true
$script:files[(Key 'instance.json')]=@{distro='Fixture';installRoot='/home/fixture/ods'}
$script:files[(Key 'relay-request.json')]=@{generation='holder';action='stop'}
Invoke-ODSWslRelayHolder $script:identity.directory
Check ($script:spawned -eq 0) 'cancelled queued holder launches no child'
$script:files[(Key 'relay-request.json')].action='run'
Invoke-ODSWslRelayHolder $script:identity.directory
Check ($script:spawned -eq 1 -and $script:stopped -contains 102) 'scheduler holder owns and stops its child'
Check ((Test-ODSProcessIdentity $script:files[(Key 'agent-relay-process.json')] (Record 102))) 'holder publishes compatible full process identity'
Check ($script:files[(Key 'relay-runtime.json')].state -eq 'stopped') 'holder records completed requested stop'

# Exercise the uninstall caller as well as the relay helper. No real scheduler,
# interop, lock file, process or startup preference may escape these fakes.
function Get-ODSWslStartupIntent { param($Identity); $null }
function Set-ODSWslStartupIntent { param($Identity,$DesiredRunning); $script:intentWrites++ }
function Open-ODSWslCommandLock { param($Identity); if ($script:lockBusy) { throw 'fixture command lock busy' }; [IO.MemoryStream]::new() }
function Assert-ODSWslCommandSettled { param($Identity,[switch]$ValidateOnly); if ($script:pending) { throw 'fixture command pending' } }
function Disable-ScheduledTask { param($TaskName); throw 'Fixture has no startup task to disable' }
Reset; NewTask; $script:directoryExists=$true
$result=Disable-ODSWslStartup $script:identity -RetireRelay -ValidateOnly
Check ($result.relayRetirement -eq 'validated' -and $script:intentWrites -eq 0 -and $script:stopped.Count -eq 0 -and $script:files.Count -eq 0) 'uninstall relay precheck is read only'
foreach ($readonly in @($true,$false)) {
    Reset; NewTask; $script:directoryExists=$true; $script:task.Principal.UserId='S-1-5-18'
    Reject { Disable-ODSWslStartup $script:identity -RetireRelay -ValidateOnly:$readonly } '*relay task identity changed*'
    Check ($script:intentWrites -eq 0 -and $script:stopped.Count -eq 0) 'foreign uninstall relay fails before startup mutation'
}
Reset; NewTask; $script:directoryExists=$true
Start-ODSWslAgentRelay $script:identity
$result=Disable-ODSWslStartup $script:identity
Check ($result.relayRetirement -eq 'not-requested' -and $script:task.State -eq 'Running' -and $script:stopped.Count -eq 0) 'ordinary login opt-out preserves manually running relay'
$result=Disable-ODSWslStartup $script:identity -RetireRelay
Check ($result.relayRetirement -eq 'stopped' -and $script:task.State -eq 'Ready' -and $script:stopped -contains 102 -and -not $script:files.ContainsKey((Key 'agent-relay-process.json'))) 'uninstall settles holder and removes exact owned relay'
Reset; $script:directoryExists=$true; $script:files[(Key 'agent-relay-process.json')]=Record 102
$null=Disable-ODSWslStartup $script:identity -RetireRelay
Check ($script:stopped -contains 102) 'uninstall also retires legacy caller-owned relay without a task'
foreach ($failure in @('lockBusy','pending')) {
    Reset; NewTask; $script:directoryExists=$true; Start-ODSWslAgentRelay $script:identity
    Set-Variable -Name $failure -Value $true -Scope Script
    Reject { Disable-ODSWslStartup $script:identity -RetireRelay } '*fixture command*'
    Check ($script:task.State -eq 'Running' -and $script:stopped.Count -eq 0) 'unsettled command prevents relay mutation'
}
Reset; NewTask
Reject { Disable-ODSWslStartup $script:identity -RetireRelay -ValidateOnly } '*without its owner manifest*'
Check ($script:intentWrites -eq 0 -and $script:stopped.Count -eq 0) 'missing manifest never authorizes orphan task mutation'
Reset
Check ((Disable-ODSWslStartup $script:identity -RetireRelay).relayRetirement -eq 'unmanaged') 'absent installation and relay remain unmanaged'
Reject { Invoke-ODSWslLifecycle 'start' 'Fixture' '/home/fixture/ods' -RetireRelay } '*only for disable-startup*'
Write-Host "Passed $script:checks relay checks; all execution and filesystem mutation boundaries mocked."
