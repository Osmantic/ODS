$ErrorActionPreference='Stop'
$source=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
. (Join-Path $source 'installers\windows\lib\remote-provider-source-copy.ps1')
. (Join-Path $source 'installers\windows\lib\remote-provider-docker-transaction.ps1')
$root=Join-Path ([IO.Path]::GetTempPath()) ('ods-transaction-lifecycle-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path (Join-Path $root 'data') -Force
foreach($service in @('remote-provider-egress','remote-provider-ssh-tunnel')){
    $dir=Join-Path $root "extensions\services\$service"
    $null=New-Item -ItemType Directory -Path $dir -Force
    Copy-Item -LiteralPath (Join-Path $source "extensions\services\$service\manifest.yaml") -Destination $dir
}
foreach($file in @('docker-compose.base.yml','docker-compose.override.yml')){[IO.File]::WriteAllText((Join-Path $root $file),'services: {}')}
Sync-ODSWindowsRemoteProviderRecipes -InstallDir $root -SourceRoot $source
$flags=@('-f','docker-compose.base.yml','-f','docker-compose.override.yml')
$null=Invoke-ODSWindowsRemoteTransaction -InstallDir $root -SourceRoot $source -Request @{operation='flags';flags=$flags}
$dashboard=(docker image inspect ods-dashboard-api:latest --format '{{.Id}}').Trim()
$uidScript=Join-Path $PSScriptRoot 'remote-provider-transaction-uid-probe.py'
$uid = docker run --rm --user 1000:1000 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --mount "type=bind,source=$root,target=/ods" --mount "type=bind,source=$source,target=/candidate,readonly" --mount "type=bind,source=$uidScript,target=/probe.py,readonly" --entrypoint python3 $dashboard /probe.py
if($LASTEXITCODE -ne 0){throw 'Runtime UID selector proof failed'}
$uid=$uid|ConvertFrom-Json
if($uid.uid -ne 1000 -or -not $uid.sameLockInode){throw 'Wrong runtime identity proof'}
$holderScript=Join-Path $root 'holder.py'
[IO.File]::WriteAllText($holderScript,@'
import fcntl,pathlib,time
p=pathlib.Path('/ods')
with (p/'data/.extensions-lock').open('a+b') as f:
    fcntl.flock(f,fcntl.LOCK_EX)
    (p/'ready').write_text('ready')
    deadline=time.monotonic()+30
    while not (p/'release').exists() and time.monotonic()<deadline: time.sleep(.05)
'@)
$image='python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e'
$holder=(docker run -d --network none --mount "type=bind,source=$root,target=/ods" --entrypoint python3 $image /ods/holder.py).Trim()
if($LASTEXITCODE -ne 0 -or $holder -notmatch '^[a-f0-9]{64}$'){throw 'Holder failed to launch'}
try {
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while(-not(Test-Path -LiteralPath (Join-Path $root 'ready'))){if([DateTime]::UtcNow -ge $deadline){throw 'Holder did not start'};Start-Sleep -Milliseconds 50}
    $before=[IO.File]::ReadAllText((Join-Path $root '.compose-flags'))
    $rejected=$false
    try{$null=Invoke-ODSWindowsRemoteTransaction -InstallDir $root -SourceRoot $source -Request @{operation='flags';flags=$flags;lockTimeout=.2}}catch{$rejected=$true}
    if(-not $rejected -or [IO.File]::ReadAllText((Join-Path $root '.compose-flags')) -cne $before){throw 'Contending transaction did not fail without mutation'}
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$rootHash=-join($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToUpperInvariant()))|ForEach-Object{$_.ToString('x2')})}finally{$hash.Dispose()}
    $name='ods-windows-remote-'+$rootHash.Substring(0,24)
    $imageId=(docker image inspect $image --format '{{.Id}}').Trim()
    $previous=Get-ODSRemoteWriter -Name $name -RootHash $rootHash -ImageId $imageId
    if($null -eq $previous -or $previous.State.Running -or $previous.State.Status -ne 'exited'){throw 'Expected failed terminal writer absent'}
    $null=docker rm $previous.Id
    if($LASTEXITCODE -ne 0){throw 'Failed fixture writer cleanup'}
    $requestDir=Join-Path $root 'paused-request'
    $null=New-Item -ItemType Directory -Path $requestDir
    [IO.File]::WriteAllText((Join-Path $requestDir 'request.json'),(@{operation='flags';flags=$flags}|ConvertTo-Json -Compress))
    $pending=(docker run -d --name $name --label "com.osmantic.ods.remote-transaction-root=$rootHash" --network none --read-only --cap-drop ALL --security-opt no-new-privileges --mount "type=bind,source=$root,target=/ods" --mount "type=bind,source=$source,target=/candidate,readonly" --mount "type=bind,source=$requestDir,target=/transaction,readonly" --entrypoint python3 $image /candidate/installers/windows/lib/remote-provider-transaction.py --request-file).Trim()
    if($LASTEXITCODE -ne 0 -or $pending -notmatch '^[a-f0-9]{64}$'){throw 'Pending fixture writer failed'}
    try {
        $null=docker pause $pending
        if($LASTEXITCODE -ne 0){throw 'Could not pause fixture writer'}
        $pausedRefused=$false
        try{$null=Invoke-ODSWindowsRemoteTransaction -InstallDir $root -SourceRoot $source -Request @{operation='flags';flags=$flags}}catch{$pausedRefused=$_.Exception.Message -like '*state is unresolved*'}
        $observed=Get-ODSRemoteWriter -Name $name -RootHash $rootHash -ImageId $imageId
        if(-not $pausedRefused -or $observed.Id -cne $pending -or -not $observed.State.Paused){throw 'Paused writer was not retained and refused'}
        if([IO.File]::ReadAllText((Join-Path $root '.compose-flags')) -cne $before){throw 'Paused writer refusal changed flags'}
    } finally {
        $null=docker unpause $pending
        $null=docker kill $pending
        $null=docker wait $pending
    }
    [IO.File]::WriteAllText((Join-Path $root 'release'),'release')
    $holderExit=docker wait $holder
    if($holderExit -ne '0'){throw 'Holder exited abnormally'}
    $retried=Invoke-ODSWindowsRemoteTransaction -InstallDir $root -SourceRoot $source -Request @{operation='flags';flags=$flags}
    if(($retried.flags -join ' ') -cne $before){throw 'Retry failed to reconcile terminal writer'}
}finally{$null=docker rm -f $holder}

# A local CLI timeout must leave the independent container observable.
$timeoutName='ods-fleet-client-timeout-'+[guid]::NewGuid().ToString('N')
$timedOut=$false
try { $null=Invoke-ODSRemoteDockerCommand -Arguments @('run','--name',$timeoutName,'--network','none','--entrypoint','python3',$image,'-c','import time; time.sleep(5)') -TimeoutSeconds 1 }
catch { $timedOut=$_.Exception.Message -like '*state remains unverified*' }
try {
    $status=(docker inspect $timeoutName --format '{{.State.Status}}').Trim()
    if(-not $timedOut -or $status -ne 'running'){throw 'CLI timeout incorrectly settled the writer'}
    $timeoutExit=docker wait $timeoutName
    if($timeoutExit -ne '0'){throw 'Timed-out client writer failed'}
}finally{$null=docker rm -f $timeoutName}
[pscustomobject]@{utc=[DateTime]::UtcNow.ToString('o');powershell=$PSVersionTable.PSVersion.ToString();root=$root;runtimeUid=$uid;lockTimeoutRefusedWithoutMutation=$rejected;pausedWriterRetainedAndRefused=$pausedRefused;terminalWriterRetryPassed=$true;cliTimeoutLeftWriterRunning=$timedOut;pass=$true}|ConvertTo-Json -Depth 7
