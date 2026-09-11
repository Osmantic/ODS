$ErrorActionPreference='Stop'
$Distro='Ubuntu-Scope-Test'
. (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -Distro $Distro
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
    Initialize-ODSPrivateDirectory $fixture
    Assert-ODSPrivatePath $fixture -Directory
    Check $true 'actual Windows directory ACL is private'
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
    Check $true 'strict ordinary-owner plan accepted'
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
    Reject { Invoke-ODSWslCommand $a @('python3','owner.py') -AsRoot } 'root transport rejects owner Python before execution'
    Reject { Invoke-ODSWslCommand $a @('/bin/bash','-c','anything') -AsRoot } 'root transport rejects shell execution'
    Reject { Invoke-ODSWslCommand $a @('/usr/bin/systemctl','stop','docker.service') -AsRoot } 'root transport rejects unrelated services'
    Reject { Invoke-ODSWslCommand $a @('/usr/bin/systemctl','stop','pixel-ingress.service','docker.service') -AsRoot } 'root transport rejects extra argv'
    $script:transport=@();$script:unitState='inactive';$script:nativeFail=$false
    function Invoke-ODSWslCommand { param($Identity,[string[]]$Arguments,[switch]$AsRoot)
        $script:transport+=[pscustomobject]@{distro=$Identity.distro;arguments=$Arguments;asRoot=[bool]$AsRoot}
        if($AsRoot -and $script:nativeFail){throw 'native stop failed'}
        if($Arguments[0] -eq 'python3' -and $Arguments[2] -like 'plan-*'){return ($script:plan|ConvertTo-Json -Depth 5)}
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
    $script:transport=@();$script:plan.action='stop';$script:unitState='inactive';$script:nativeFail=$true
    Reject { Invoke-ODSWslStack $a stop } 'native stop failure is propagated'
    Check (@($script:transport|Where-Object {$_.arguments[2] -eq 'compose-stop'}).Count -eq 0) 'native stop failure prevents Compose stop'

    $script:events=@();$script:stopFail=$false
    function Get-ODSWslIdentity { param($Distro,$InstallRoot); $a }
    function Get-ODSWslLifetimeStatus { param($Identity); [pscustomobject]@{state='stopped';distroRunning=$script:targetRunning} }
    function Stop-ODSWslLifetime { param($Identity); $script:events+='release'; [pscustomobject]@{state='stopped'} }
    function Start-ODSWslLifetime { param($Identity); $script:events+='hold'; [pscustomobject]@{state='running'} }
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
