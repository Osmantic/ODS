# Actual scheduler boundary: no WSL process, service, container or network use.
[CmdletBinding()]
param([switch]$AllowUnavailableInteractiveSession)
$ErrorActionPreference='Stop'
$source=(Resolve-Path (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1')).Path
. $source
$count=0
function Check([bool]$Condition,[string]$Message) { if(-not $Condition){throw $Message}; $script:count++; Write-Host "PASS $Message" }
$nonce=[guid]::NewGuid().ToString('N')
$identity=Get-ODSWslIdentity 'ODS-Fixture-No-WSL' "/home/ods/scheduler-fixture-$nonce"
$fixture=$identity.directory
$profileRoot=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)) '.ods\wsl'
$registered=$false
$oldLocalAppData=$env:LOCALAPPDATA
try {
    Check ($fixture -ceq (Join-Path $profileRoot $identity.id)) 'new state uses the user profile outside virtualized AppData'
    Initialize-ODSPrivateDirectory $fixture
    Write-ODSWslJson (Join-Path $fixture 'instance.json') $identity
    Check ((Get-ODSPhysicalFilePath (Join-Path $fixture 'instance.json')) -ieq (Join-Path $fixture 'instance.json')) 'new metadata handle resolves to its shared physical path'

    # A pre-existing nonredirected private legacy installation stays in place.
    $env:LOCALAPPDATA=Join-Path $fixture 'legacy-appdata'
    $legacyId=Get-ODSWslIdentity 'ODS-Fixture-Legacy' "/home/ods/legacy-fixture-$nonce"
    $legacy=Join-Path $env:LOCALAPPDATA "ODS\wsl\$($legacyId.id)"
    Initialize-ODSPrivateDirectory $legacy
    $legacyId.directory=$legacy
    Write-ODSWslJson (Join-Path $legacy 'instance.json') $legacyId
    $preserved=Get-ODSWslIdentity $legacyId.distro $legacyId.installRoot
    Check ($preserved.directory -ceq $legacy) 'existing physical legacy state remains discoverable'
    $acl=Get-Acl -LiteralPath (Join-Path $legacy 'instance.json')
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'))
    Set-Acl -LiteralPath (Join-Path $legacy 'instance.json') -AclObject $acl
    $rejected=$false
    try { $null=Get-ODSWslIdentity $legacyId.distro $legacyId.installRoot } catch { $rejected=$true }
    Check $rejected 'unsafe legacy permissions are rejected, never migrated silently'
    $env:LOCALAPPDATA=$oldLocalAppData

    # This script runs outside a packaged caller's filesystem view. It uses the
    # real identity and ACL functions, but intentionally never calls the holder.
    $quotedSource="'"+$source.Replace("'","''")+"'"
    $controller=@'
param([string]$Action,[string]$InstanceDirectory)
$ErrorActionPreference='Stop'
$probeDirectory=$InstanceDirectory
. __SOURCE__
$manifest=Read-ODSWslJson (Join-Path $probeDirectory 'instance.json')
$expected=Get-ODSWslIdentity $manifest.distro $manifest.installRoot
if ($expected.directory -cne $probeDirectory) { throw 'Scheduled task selected a different state directory' }
$null=Assert-ODSWslManifest $expected
Write-ODSWslJson (Join-Path $probeDirectory 'scheduler-proof.json') @{
    ownerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    directory=$expected.directory
    controllerReadable=[IO.File]::Exists((Join-Path $probeDirectory 'controller.ps1'))
}
'@
    $controller=$controller.Replace('__SOURCE__',$quotedSource)
    $encoding=[Text.UTF8Encoding]::new($true)
    Write-ODSPrivateBytes (Join-Path $fixture 'controller.ps1') ($encoding.GetPreamble()+$encoding.GetBytes($controller))
    Check (-not (Get-ScheduledTask -TaskName $identity.taskName -ErrorAction SilentlyContinue)) 'unique fixture does not replace an existing task'
    $scheduledAction=New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument (Get-ODSWslTaskArguments $identity)
    $principal=New-ScheduledTaskPrincipal -UserId $identity.ownerSid -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $identity.taskName -Action $scheduledAction -Principal $principal -Settings $settings | Out-Null
    $registered=$true
    $null=Assert-ODSWslTask $identity
    Start-ScheduledTask -TaskName $identity.taskName
    $proofPath=Join-Path $fixture 'scheduler-proof.json'
    for($attempt=0;$attempt -lt 60 -and -not (Test-Path -LiteralPath $proofPath);$attempt++){Start-Sleep -Milliseconds 250}
    $proof=Read-ODSWslJson $proofPath
    $info=Get-ScheduledTaskInfo -TaskName $identity.taskName
    # Some hosted runners have no signed-in owner. This is a documented
    # prerequisite of InteractiveToken, not permission to run as SYSTEM or to
    # ignore missing scripts, denied ACLs, module errors or other task failures.
    if(-not $proof -and $AllowUnavailableInteractiveSession -and [uint32]$info.LastTaskResult -eq [uint32]2147943712){
        Write-Host 'SKIP actual scheduler boundary: ERROR_NO_SUCH_LOGON_SESSION (0x80070520); no interactive owner is signed in. Filesystem checks passed; no scheduler success claim.'
        return
    }
    Check ([bool]$proof) "Limited task can read controller and write private metadata (LastTaskResult=$($info.LastTaskResult))"
    Check ($proof.ownerSid -ceq $identity.ownerSid -and $proof.directory -ceq $fixture -and $proof.controllerReadable) 'scheduler identity and physical state match the packaged caller'
    Assert-ODSPrivatePath $proofPath
    Check $true 'scheduler proof retains owner-only metadata permissions'
    Write-Host "PASS $count scheduler boundary checks; no WSL or services started"
} finally {
    $env:LOCALAPPDATA=$oldLocalAppData
    if($registered){
        $task=Assert-ODSWslTask $identity
        if($task.State -in @('Running','Queued')){Stop-ScheduledTask -TaskName $identity.taskName}
        Unregister-ScheduledTask -TaskName $identity.taskName -Confirm:$false
    }
    if(Test-Path -LiteralPath $fixture){
        $resolved=[IO.Path]::GetFullPath($fixture)
        if($resolved -cne (Join-Path $profileRoot $identity.id)){throw 'Refusing cleanup outside the exact fixture state directory'}
        Assert-ODSPrivatePath $fixture -Directory
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
