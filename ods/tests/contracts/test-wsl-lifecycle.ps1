$ErrorActionPreference='Stop'
$Distro='Ubuntu-Scope-Test'
. (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -Distro $Distro
function Resolve-ODSWslRegisteredDistro { param($Name); $Name }
$count=0
function Check([bool]$Condition,[string]$Message) { if(-not $Condition){throw $Message}; $script:count++; Write-Host "PASS $Message" }
function Reject([scriptblock]$Operation,[string]$Message) { $threw=$false; try { & $Operation } catch { $threw=$true }; Check $threw $Message }
$fixture=Join-Path $PSScriptRoot ('.wsl-lifetime-test-'+[guid]::NewGuid().ToString('N'))
$ownedProcess=$null
try {
    Check ($Distro -ceq 'Ubuntu-Scope-Test') 'dot-sourcing preserves the selected distribution'
    $parseTokens=$null;$parseErrors=$null
    $installerAst=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path (Join-Path $PSScriptRoot '../../installers/windows.ps1')),[ref]$parseTokens,[ref]$parseErrors)
    $previewAssignment=$installerAst.Find({param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$lifetimeRequired'},$true)
    foreach($preview in @('--dry-run','--help','-h')){
        $PassthroughArgs=@($preview)
        . ([scriptblock]::Create($previewAssignment.Extent.Text))
        Check (-not $lifetimeRequired) "installer $preview does not request a persistent lifetime task"
    }
    $a=Get-ODSWslIdentity 'Ubuntu-24.04' '/home/ods/ods'
    $b=Get-ODSWslIdentity 'Other-Ubuntu' '/home/ods/ods'
    $c=Get-ODSWslIdentity 'Ubuntu-24.04' '/home/ods/ods-other'
    Check ($a.id -ne $b.id -and $a.id -ne $c.id) 'identity separates distributions and install roots'
    Reject { Get-ODSWslIdentity 'Ubuntu' '/home/ods/../other' } 'reject traversal in Linux root'
    Reject { Get-ODSWslIdentity 'Ubuntu' '/' } 'reject whole-filesystem root'
    Reject { Get-ODSWslIdentity 'Ubuntu' '//' } 'reject slash-only normalization to empty root'
    Reject { Get-ODSWslIdentity 'Ubuntu' '' } 'reject empty root'
    Check ((Get-ODSWslIdentity 'Ubuntu-24.04' '/home/ods/ods/').id -ceq $a.id) 'trailing slash normalizes to the same nonempty installation identity'
    Reject { Get-ODSWslIdentity 'Ubuntu" --exec cmd' '/home/ods/ods' } 'reject distribution argument injection'
    Check ((Get-ODSWslHolderArguments $a).StartsWith('--distribution Ubuntu-24.04 --exec ')) 'simple distribution name avoids WSL quote retention'
    $spaceDistro=Get-ODSWslIdentity 'Ubuntu Custom' '/home/ods/ods'
    Check ((Get-ODSWslHolderArguments $spaceDistro).StartsWith('--distribution "Ubuntu Custom" --exec ')) 'distribution whitespace remains within one argument'
    $defaultTaskArguments=Get-ODSWslTaskArguments $a
    $defaultStartupArguments=Get-ODSWslStartupArguments $a
    $script:ODSWslStateRoot=Join-Path $PSScriptRoot 'fixture state root'
    $explicitStateIdentity=Get-ODSWslIdentity 'Ubuntu-24.04' '/home/ods/ods'
    Check ($explicitStateIdentity.id -ceq $a.id) 'state location does not change the owner and Linux installation identity'
    Check ($explicitStateIdentity.directory -ceq (Join-Path $script:ODSWslStateRoot $a.id)) 'explicit state location selects the private instance directory'
    Check ((Get-ODSWslTaskArguments $explicitStateIdentity).EndsWith((' -StateRoot "{0}"' -f $script:ODSWslStateRoot))) 'scheduled controller receives the same explicit state location'
    Check ((Get-ODSWslStartupArguments $explicitStateIdentity).EndsWith((' -StateRoot "{0}"' -f $script:ODSWslStateRoot))) 'scheduled startup receives the same explicit state location'
    $script:ODSWslStateRoot=''
    Check ((Get-ODSWslTaskArguments $a) -ceq $defaultTaskArguments) 'default scheduler arguments remain compatible'
    Check ((Get-ODSWslStartupArguments $a) -ceq $defaultStartupArguments) 'default startup arguments remain compatible'
    $defaultInstallerCommand=New-ODSWslInstallerCommand '/mnt/c/source' @('--pixel') '/home/ods/ods'
    Check ($defaultInstallerCommand -ceq "cd -- '/mnt/c/source' && env INSTALL_DIR='/home/ods/ods' bash install-core.sh '--pixel'") 'default installer command keeps the existing INSTALL_DIR contract without StateRoot'
    $script:ODSWslStateRoot="C:\Owner's state root"
    $expectedStateAssignment="ODS_WSL_STATE_ROOT='C:\Owner'\''s state root'"
    Check ((New-ODSWslInstallerCommand '/mnt/c/source' @('--pixel') '/home/ods/ods') -ceq ("cd -- '/mnt/c/source' && env INSTALL_DIR='/home/ods/ods' " + $expectedStateAssignment + " bash install-core.sh '--pixel'")) 'installer preserves INSTALL_DIR while safely quoting StateRoot spaces and apostrophe'
    Check ((New-ODSWslInstallerCommand '/mnt/c/source' @('--pixel') '') -ceq ("cd -- '/mnt/c/source' && env " + $expectedStateAssignment + " bash install-core.sh '--pixel'")) 'installer forwards StateRoot even before INSTALL_DIR is resolved'
    $script:ODSWslStateRoot=''
    Check ((New-ODSWslInstallerCommand '/mnt/c/source' @('--pixel') '') -ceq "cd -- '/mnt/c/source' && bash install-core.sh '--pixel'") 'installer without explicit roots adds no environment override'
    foreach ($unsafeRoot in @('C:relative', '\relative', 'C:\', '\\server\share', 'C:\state"injected', ("C:\state"+[char]10+'injected'))) {
        $validationError=$null
        try { . (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -StateRoot $unsafeRoot } catch { $validationError=$_.Exception.Message }
        Check ($validationError -in @('An absolute Windows state directory is required','State directory cannot be a filesystem root')) "state root rejects unsafe path $($unsafeRoot.Replace([string][char]10,'<newline>')) before dispatch"
    }
    . (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -Distro $Distro
    Initialize-ODSPrivateDirectory $fixture
    Assert-ODSPrivatePath $fixture -Directory
    Check $true 'actual Windows directory ACL is private'
    & {
        $customStateRoot=Join-Path $fixture 'custom state root'
        . (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -StateRoot $customStateRoot
        Initialize-ODSPrivateDirectory $customStateRoot
        $customIdentity=Get-ODSWslIdentity 'Ubuntu-24.04' '/home/ods/ods'
        Initialize-ODSPrivateDirectory $customIdentity.directory
        Write-ODSWslJson (Join-Path $customIdentity.directory 'instance.json') $customIdentity
        Set-ODSWslStartupIntent $customIdentity $false
        $uncreatedRoot=Join-Path $fixture 'cancelled state root'
        $script:ODSWslStateRoot=$uncreatedRoot
        $script:ODSWslStartupIdentity=$customIdentity
        $script:ODSWslStartupGeneration=(Get-ODSWslStartupIntent $customIdentity).generation
        $script:ODSWslStartupDeadline=[DateTime]::UtcNow.AddMinutes(1)
        Reject {Start-ODSWslLifetime $customIdentity} 'cancelled startup refuses lifetime creation before initializing StateRoot'
        Check (-not (Test-Path -LiteralPath $uncreatedRoot)) 'cancelled startup leaves its uninitialized StateRoot untouched'
        $script:ODSWslStateRoot=$customStateRoot
        $script:ODSWslStartupIdentity=$null;$script:ODSWslStartupGeneration=$null;$script:ODSWslStartupDeadline=$null
        function Get-ScheduledTask { param($TaskName,$ErrorAction)
            if($TaskName -cne ($customIdentity.taskName+'-Startup')){throw 'Unexpected custom-root task lookup'}
            [pscustomobject]@{
                Actions=@([pscustomobject]@{Execute=(Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe');Arguments=(Get-ODSWslStartupArguments $customIdentity)})
                Principal=[pscustomobject]@{UserId=$customIdentity.ownerSid;RunLevel='Limited';LogonType='Interactive'}
                Triggers=@([pscustomobject]@{UserId=$customIdentity.ownerSid;Delay='PT30S';CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}})
                Settings=[pscustomobject]@{ExecutionTimeLimit='PT25M';RestartCount=0}
            }
        }
        function Start-ODSWslDockerDesktop { throw 'Custom-root stopped fixture must not start Docker' }
        function Start-ODSWslLifetime { throw 'Custom-root stopped fixture must not start WSL' }
        Invoke-ODSWslStartup $customIdentity.directory
        Check ((Read-ODSWslJson (Join-Path $customIdentity.directory 'startup-status.json')).state -eq 'disabled') 'custom-root startup recomputes the real identity and reads its stopped preference'
        Check ($customIdentity.directory -ceq (Join-Path $customStateRoot $a.id)) 'custom-root startup retains the owner and installation hash'
    }
    . (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -Distro $Distro
    # Re-sourcing the real identity/StateRoot code also restores this boundary;
    # keep all later public lifecycle calls inside the registered-distro fixture.
    function Resolve-ODSWslRegisteredDistro { param($Name); $Name }
    $a.directory=$fixture
    Write-ODSWslJson (Join-Path $fixture 'instance.json') $a
    $null=Assert-ODSWslManifest $a
    Check $true 'atomic private manifest round trip'
    $unicodePath=Join-Path $fixture 'unicode.json'
    $unicodeValue=[pscustomobject]@{ root=('/home/'+[char]0x00E9+'/ods') }
    Write-ODSWslJson $unicodePath $unicodeValue
    Check ((Read-ODSWslJson $unicodePath).root -ceq $unicodeValue.root) 'UTF-8 paths survive Windows PowerShell metadata readback'
    Check ((Get-Acl -LiteralPath $unicodePath).GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) 'new metadata explicitly belongs to the current user'
    Reject { Write-ODSPrivateBytes $unicodePath ([byte[]]@(1)) -CreateOnly } 'create-only initialization never replaces existing state'
    $originalAcl=Get-Acl -LiteralPath $unicodePath
    $leakyAcl=Get-Acl -LiteralPath $unicodePath
    $leakyAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'))
    Set-Acl -LiteralPath $unicodePath -AclObject $leakyAcl
    Reject { Read-ODSWslJson $unicodePath } 'actual ACL drift rejects readable lifecycle metadata'
    Reject { Write-ODSWslJson $unicodePath @{changed=$true} } 'writer refuses to reclaim existing unsafe metadata'
    Set-Acl -LiteralPath $unicodePath -AclObject $originalAcl
    $wrong=$a.PSObject.Copy(); $wrong.distro='Other'
    Reject { Assert-ODSWslManifest $wrong } 'reject manifest distribution drift'
    $lockPath=Join-Path $fixture 'command.lock'
    $lock=Open-ODSPrivateLock $lockPath
    try {
        Check ((Get-Acl -LiteralPath $lockPath).GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) 'new lock explicitly belongs to the current user'
        Reject { Open-ODSPrivateLock $lockPath } 'actual Windows file lock prevents a concurrent command'
        Reject { Write-ODSPrivateBytes $lockPath ([byte[]]@()) -CreateOnly } 'racing initialization cannot replace the held lock'
    } finally { $lock.Dispose() }
    $script:testTask=[pscustomobject]@{
        Actions=@([pscustomobject]@{Execute=(Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe');Arguments=(Get-ODSWslTaskArguments $a)})
        Settings=[pscustomobject]@{ExecutionTimeLimit='PT0S';RestartCount=0};Triggers=@();Principal=[pscustomobject]@{UserId=$a.ownerSid;RunLevel='Limited'}; State='Ready'
    }
    function Get-ScheduledTask { param($TaskName,$ErrorAction); if($TaskName -cne $a.taskName){throw 'unrelated task lookup'}; $script:testTask }
    $null=Assert-ODSWslTask $a
    Check $true 'exact on-demand scheduled-task identity accepted'
    $script:testTask.Triggers=@('logon')
    Reject { Assert-ODSWslTask $a } 'reject a recurring or logon resurrection trigger'
    $script:testTask.Triggers=@();$script:testTask.Actions[0].Arguments+=' injected'
    Reject { Assert-ODSWslTask $a } 'reject changed task command'
    $script:testTask.Actions[0].Arguments=Get-ODSWslTaskArguments $a
    $script:testTask.Principal.UserId='S-1-5-18'
    Reject { Assert-ODSWslTask $a } 'reject changed task owner'
    $script:testTask.Principal.UserId=$a.ownerSid
    $script:testTask.Settings.ExecutionTimeLimit='PT6H'
    Reject { Assert-ODSWslTask $a } 'reject reintroduced finite holder lifetime'
    $script:testTask.Settings.ExecutionTimeLimit='PT0S';$script:testTask.Settings.RestartCount=1
    Reject { Assert-ODSWslTask $a } 'reject automatic failure restart policy'
    $script:testTask.Settings.RestartCount=0

    $ownedProcess=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList '-NoProfile -NonInteractive -WindowStyle Hidden -Command "Start-Sleep -Seconds 60"' -WindowStyle Hidden -PassThru
    $null=$ownedProcess.Handle
    $identity=Get-ODSProcessIdentity $ownedProcess.Id
    Check (Test-ODSProcessIdentity $identity (Get-ODSProcessIdentity $ownedProcess.Id)) 'actual Windows child identity captured'
    $stale=$identity.PSObject.Copy();$stale.startTicks='0'
    Reject { Stop-ODSOwnedProcess $stale } 'stale start time cannot kill a reused PID'
    $ownedProcess.Refresh();Check (-not $ownedProcess.HasExited) 'wrong identity leaves actual child alive'
    $stale=$identity.PSObject.Copy();$stale.commandLine+=' --different'
    Reject { Stop-ODSOwnedProcess $stale } 'changed command cannot kill the process'
    Stop-ODSOwnedProcess $identity
    $ownedProcess.Refresh();Check $ownedProcess.HasExited 'exact owned process handle is released'
    $ownedProcess.Dispose();$ownedProcess=$null

    $script:running=@('docker-desktop','Unrelated-Ubuntu')
    $script:listCalls=0
    function Get-ODSWslRunningDistributions { $script:listCalls++; $script:running }
    $status=Get-ODSWslLifetimeStatus $a
    Check (-not $status.distroRunning -and $status.state -eq 'inactive') 'status recognizes a stopped target alongside unrelated running distributions'
    Check ($script:listCalls -eq 1) 'status uses only the read-only Windows distribution list'

    $script:running=@('Ubuntu-24.04','docker-desktop')
    $script:testTask=$null;$script:registered=0;$script:startCount=0
    function New-ScheduledTaskAction { param($Execute,$Argument); [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
    function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel); Check ($LogonType -eq 'Interactive' -and $RunLevel -eq 'Limited') 'registration uses credential-free limited owner'; [pscustomobject]@{UserId=$UserId;RunLevel=$RunLevel} }
    function New-ScheduledTaskSettingsSet { param([switch]$Hidden,$ExecutionTimeLimit,$MultipleInstances,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries); Check ($ExecutionTimeLimit -eq [TimeSpan]::Zero -and $Hidden -and $MultipleInstances -eq 'IgnoreNew') 'registration requests hidden unlimited on-demand lifetime'; [pscustomobject]@{ExecutionTimeLimit='PT0S';RestartCount=0} }
    function Register-ScheduledTask { param($TaskName,$Action,$Principal,$Settings,$Description); $script:registered++;$script:testTask=[pscustomobject]@{Actions=@($Action);Principal=$Principal;Settings=$Settings;Triggers=$null;State='Ready'} }
    function Start-ScheduledTask { param($TaskName); $script:startCount++;$r=Read-ODSWslJson (Join-Path $a.directory 'request.json');Write-ODSWslJson (Join-Path $a.directory 'runtime.json') @{generation=$r.generation;state='running';child=(Get-ODSProcessIdentity $PID)} }
    $startResult=Start-ODSWslLifetime $a
    Check ($startResult.state -eq 'running' -and $script:registered -eq 1 -and $script:startCount -eq 1) 'start creates exact task and waits for matching controller generation'
    $again=Start-ODSWslLifetime $a
    Check ($again.state -eq 'running' -and $script:registered -eq 1 -and $script:startCount -eq 1) 'repeated start reuses the live holder'
    # This simulated record intentionally references this test process; retire
    # the record before exercising stop dispatch, never attempt to stop it.
    Write-ODSWslJson (Join-Path $a.directory 'runtime.json') @{state='stopped';generation='fixture'}

    $script:plan=[pscustomobject]@{schemaVersion=1;action='stop';installRoot=$a.installRoot;ownerUid=1000;nativeUnits=@('pixel-ingress.service','openclaw-gateway.service','pixel-extension-manager.service','pixel-artifact-promoter.service','pixel-workspace-preview.service')}
    Assert-ODSWslStackPlan $a stop $script:plan
    Check $true 'strict ordinary-owner legacy five-unit plan accepted'
    $complete=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json
    $complete.nativeUnits+=@('pixel-preview-inspection.service')
    Assert-ODSWslStackPlan $a stop $complete
    Check $true 'strict complete six-unit inspection plan accepted'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.nativeUnits=@($bad.nativeUnits[0..3])
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject incomplete four-unit legacy plan'
    $bad=$complete|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.nativeUnits=@($bad.nativeUnits[0..3])+@($bad.nativeUnits[5])
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject five-unit plan replacing publisher with inspector'
    $bad=$complete|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.nativeUnits[5]=$bad.nativeUnits[4]
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject duplicate service in six-unit plan'
    $bad=$complete|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.nativeUnits[5]='Pixel-preview-inspection.service'
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject case-altered inspection unit'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.nativeUnits+=@('docker.service')
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject extra native unit'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.nativeUnits[0]='unknown.service'
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject unknown native unit'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.nativeUnits[0]='pixel-ingress.service; id'
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject injected unit text'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.installRoot='/home/other/ods'
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject plan for a different install root'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.ownerUid=0
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject root-produced owner plan'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.ownerUid=$true
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject coerced boolean owner UID'
    $bad=$script:plan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad|Add-Member executable '/bin/bash'
    Reject { Assert-ODSWslStackPlan $a stop $bad } 'reject extra executable metadata'
    $managedPlan=$complete|ConvertTo-Json -Depth 5|ConvertFrom-Json
    $managedPlan|Add-Member hostAgentRestart $false
    Assert-ODSWslStackPlan $a stop $managedPlan
    Check $true 'optional host agent restart false remains valid on stop'
    $managedPlan.action='start';$managedPlan.hostAgentRestart=$true
    Assert-ODSWslStackPlan $a start $managedPlan
    Check $true 'strict boolean host agent restart is accepted only on start'
    foreach($invalid in @('true',1,$null)){
        $bad=$managedPlan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.hostAgentRestart=$invalid
        Reject {Assert-ODSWslStackPlan $a start $bad} 'host agent restart rejects non-boolean values'
    }
    $bad=$managedPlan|ConvertTo-Json -Depth 5|ConvertFrom-Json;$bad.action='stop'
    Reject {Assert-ODSWslStackPlan $a stop $bad} 'stop cannot request a host agent restart'
    Assert-ODSWslRootArguments @('/usr/bin/systemctl','restart','ods-host-agent.service')
    Check $true 'root allowlist accepts only the fixed host agent restart tuple'
    foreach($rootArguments in @(
        @('/usr/bin/systemctl','start','ods-host-agent.service'),
        @('/usr/bin/systemctl','stop','ods-host-agent.service'),
        @('/usr/bin/systemctl','restart','pixel-ingress.service'),
        @('/usr/bin/systemctl','restart','docker.service'),
        @('/usr/bin/systemctl','Restart','ods-host-agent.service'),
        @('/usr/bin/systemctl','restart','ods-host-agent.service; id'),
        @('/usr/bin/systemctl','restart','ods-host-agent.service','docker.service'),
        @('python3','restart','ods-host-agent.service')
    )){
        Reject {Assert-ODSWslRootArguments $rootArguments} ('root rejects non-allowlisted tuple: '+($rootArguments -join ' '))
    }
    Reject { Invoke-ODSWslCommand $a @('python3','owner.py') -AsRoot } 'root transport rejects owner Python before execution'
    Reject { Invoke-ODSWslCommand $a @('/bin/bash','-c','anything') -AsRoot } 'root transport rejects shell execution'
    Reject { Invoke-ODSWslCommand $a @('/usr/bin/systemctl','stop','docker.service') -AsRoot } 'root transport rejects unrelated services'
    Reject { Invoke-ODSWslCommand $a @('/usr/bin/systemctl','stop','pixel-ingress.service','docker.service') -AsRoot } 'root transport rejects extra argv'
    $script:transport=@();$script:unitState='inactive';$script:nativeFail=$false
    $script:agentState='active';$script:agentFail=$false;$script:composeFail=$false
    function Invoke-ODSWslCommand { param($Identity,[string[]]$Arguments,[switch]$AsRoot)
        $script:transport+=[pscustomobject]@{distro=$Identity.distro;arguments=$Arguments;asRoot=[bool]$AsRoot}
        if($AsRoot -and $script:nativeFail){throw 'native stop failed'}
        if($AsRoot -and $Arguments[2] -ceq 'ods-host-agent.service' -and $script:agentFail){throw 'host agent restart failed'}
        if($Arguments[0] -eq 'python3' -and $Arguments[2] -like 'plan-*'){return ($script:plan|ConvertTo-Json -Depth 5)}
        if($Arguments[0] -eq 'python3' -and $Arguments[2] -eq 'compose-start' -and $script:composeFail){throw 'owner Compose failed'}
        if($Arguments[0] -eq '/usr/bin/systemctl' -and $Arguments[1] -eq 'show' -and $Arguments[2] -ceq 'ods-host-agent.service'){return $script:agentState}
        if($Arguments[0] -eq '/usr/bin/systemctl' -and $Arguments[1] -eq 'show'){return $script:unitState}
    }
    $null=Invoke-ODSWslStack $a stop
    $rootCalls=@($script:transport|Where-Object asRoot)
    Check (($rootCalls.arguments|Where-Object {$_ -eq 'sudo'}).Count -eq 0 -and $rootCalls.Count -eq 5) 'native lifecycle uses five fixed root commands without sudo'
    Check (@($rootCalls|Where-Object {$_.arguments[0] -cne '/usr/bin/systemctl' -or $_.arguments.Count -ne 3}).Count -eq 0) 'root execution is only fixed systemctl argv'
    Check (($rootCalls|ForEach-Object {$_.arguments[2]}) -join ',' -ceq ($script:plan.nativeUnits -join ',')) 'native stop order is ingress then gateway then auxiliaries'
    Check ($script:transport[-1].arguments[2] -eq 'compose-stop' -and -not $script:transport[-1].asRoot) 'Compose stop follows native drain as ordinary owner'
    Check (@($script:transport|Where-Object {$_.distro -cne $a.distro}).Count -eq 0) 'every command remains tied to the bound distribution'
    $script:transport=@();$script:plan.action='start';$script:unitState='active'
    $null=Invoke-ODSWslStack $a start
    Check ($script:transport[1].arguments[2] -eq 'compose-start' -and -not $script:transport[1].asRoot) 'Compose starts before native services as ordinary owner'
    $script:transport=@();$script:plan=$complete;$script:unitState='inactive'
    $null=Invoke-ODSWslStack $a stop
    $rootCalls=@($script:transport|Where-Object asRoot)
    Check ($rootCalls.Count -eq 6 -and $rootCalls[-1].arguments[2] -ceq 'pixel-preview-inspection.service') 'complete inspection stop dispatches exactly six fixed native units'
    $script:transport=@();$script:plan.action='start';$script:unitState='active'
    $null=Invoke-ODSWslStack $a start
    $rootCalls=@($script:transport|Where-Object asRoot)
    Check ($rootCalls.Count -eq 6 -and @($rootCalls|Where-Object {$_.arguments[2] -ceq 'pixel-preview-inspection.service'}).Count -eq 1) 'complete inspection start dispatches its inspector exactly once'
    $script:transport=@();$script:plan.action='stop';$script:unitState='inactive';$script:nativeFail=$true
    Reject { Invoke-ODSWslStack $a stop } 'native stop failure is propagated'
    Check (@($script:transport|Where-Object {$_.arguments[2] -eq 'compose-stop'}).Count -eq 0) 'native stop failure prevents Compose stop'

    $script:nativeFail=$false;$script:unitState='active';$script:plan=$managedPlan;$script:transport=@()
    $null=Invoke-ODSWslStack $a start
    Check ($script:transport[1].arguments[2] -ceq 'compose-start' -and -not $script:transport[1].asRoot -and
        ($script:transport[2].arguments -join ' ') -ceq '/usr/bin/systemctl restart ods-host-agent.service' -and $script:transport[2].asRoot) 'owned Compose completes before the fixed root host agent restart'
    Check (($script:transport[3].arguments -join ' ') -ceq '/usr/bin/systemctl show ods-host-agent.service --property=ActiveState --value' -and
        -not $script:transport[3].asRoot -and $script:transport[4].arguments[1] -ceq 'start') 'host agent active state is confirmed as owner before any Pixel unit starts'
    foreach($failure in @('compose','restart','inactive')){
        $script:transport=@();$script:composeFail=$failure -eq 'compose';$script:agentFail=$failure -eq 'restart'
        $script:agentState=if($failure -eq 'inactive'){'inactive'}else{'active'}
        Reject {Invoke-ODSWslStack $a start} "$failure failure propagates from managed startup"
        Check (@($script:transport|Where-Object {$_.asRoot -and $_.arguments[1] -ceq 'start'}).Count -eq 0) "$failure failure prevents Pixel units starting"
        if($failure -eq 'compose'){Check (@($script:transport|Where-Object asRoot).Count -eq 0) 'failed Compose performs no root mutation'}
    }
    $script:composeFail=$false;$script:agentFail=$false;$script:agentState='active'
    $script:plan.hostAgentRestart=$false;$script:transport=@()
    $null=Invoke-ODSWslStack $a start
    Check (@($script:transport|Where-Object {$_.arguments[2] -ceq 'ods-host-agent.service'}).Count -eq 0) 'false host agent flag preserves the existing start path'
    $script:plan.action='stop';$script:unitState='inactive';$script:transport=@()
    $null=Invoke-ODSWslStack $a stop
    Check (@($script:transport|Where-Object {$_.arguments[2] -ceq 'ods-host-agent.service'}).Count -eq 0) 'stop never restarts or stops the host agent'
    $script:plan.hostAgentRestart=$true;$script:transport=@()
    Reject {Invoke-ODSWslStack $a stop} 'invalid stop restart flag fails before dispatch'
    Check ($script:transport.Count -eq 1 -and -not $script:transport[0].asRoot) 'invalid restart plan dispatches no Compose or root command'

    $script:events=@();$script:stopFail=$false
    function Get-ODSWslIdentity { param($Distro,$InstallRoot); $a }
    function Get-ODSWslLifetimeStatus { param($Identity); [pscustomobject]@{state='stopped';distroRunning=$script:targetRunning} }
    function Stop-ODSWslLifetime { param($Identity); $script:events+='release'; [pscustomobject]@{state='stopped'} }
    function Start-ODSWslLifetime { param($Identity); $script:events+='hold'; [pscustomobject]@{state='running'} }
    function Enable-ODSWslStartup { param($Identity) }
    function Invoke-ODSWslStack { param($Identity,$Action); $script:events+=$Action; if($script:stopFail){throw 'drain failed'} }
    $script:targetRunning=$false
    $null=Invoke-ODSWslLifecycle stop 'Ubuntu-24.04' '/home/ods/ods'
    Check (($script:events -join ',') -eq 'release') 'stop of a stopped distribution never enters WSL'
    $script:targetRunning=$true;$script:events=@();$script:stopFail=$true
    Reject { Invoke-ODSWslLifecycle stop 'Ubuntu-24.04' '/home/ods/ods' } 'native drain failure is visible'
    Check (($script:events -join ',') -eq 'stop') 'native drain failure keeps holder alive'
    $script:stopFail=$false;$script:events=@()
    $null=Invoke-ODSWslLifecycle restart 'Ubuntu-24.04' '/home/ods/ods'
    Check (($script:events -join ',') -eq 'stop,release,hold,start') 'restart enforces drain, release, hold, then start order'
    Write-Host "Passed $count Windows lifecycle checks; scheduler/WSL execution is mocked, ACL/locks/owned-process checks are real."
} finally {
    if($ownedProcess -and -not $ownedProcess.HasExited){$ownedProcess.Kill();$ownedProcess.WaitForExit();$ownedProcess.Dispose()}
    # Only the unique fixture under this checked test directory is removed.
    if(Test-Path -LiteralPath $fixture){
        $resolved=(Resolve-Path -LiteralPath $fixture).Path
        $expected=[IO.Path]::GetFullPath($fixture)
        if($resolved -cne $expected -or -not $resolved.StartsWith([IO.Path]::GetFullPath($PSScriptRoot)+[IO.Path]::DirectorySeparatorChar)){throw 'Unexpected fixture cleanup path'}
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
