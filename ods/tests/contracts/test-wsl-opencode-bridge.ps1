$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1')
$fixture=Join-Path $PSScriptRoot ('.wsl-bridge-test-'+[guid]::NewGuid().ToString('N'))
Initialize-ODSPrivateDirectory $fixture
$identity=[pscustomobject]@{id='bridge-fixture';directory=$fixture;installRoot='/fixture/ods';distro='Fixture-Only'}
$script:fixtureOwner=[pscustomobject]@{schemaVersion=1;manager='ods';installRoot='/fixture/ods';unit='opencode-web.service';
    ownerUid=1000;ownerUser='michael';mainPid=123;startTicks=456;port=49193}
$script:externalCalls=@()
function Invoke-ODSWslBoundedCommand { param($Identity,[string[]]$Arguments,[int]$Seconds)
    $script:externalCalls+=,@($Arguments)
    if ($Arguments[0] -eq '/usr/bin/wslpath') { return ('/fixture/'+[IO.Path]::GetFileName($Arguments[3])) }
    if ($Arguments[0] -eq '/usr/bin/python3' -and $Arguments[2] -eq 'verify' -and $Arguments[3] -eq '/fixture/ods') {
        return ($script:fixtureOwner | ConvertTo-Json -Compress)
    }
    throw 'Unexpected external command'
}
$count=0
function Check([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message }; $script:count++ }
function Reject([scriptblock]$Action,[string]$Message) { $rejected=$false;try { & $Action | Out-Null } catch {$rejected=$true};Check $rejected $Message }
$bridge=$null; $foreign=$null
try {
    Save-ODSOpenCodeBridgeDependencies $identity
    $context=Get-ODSOpenCodeBridgeContext $identity
    Check ($context.guestPath -eq '/fixture/wsl-loopback-helper.py') 'fixed guest path'
    Check ($context.ownerPath -eq '/fixture/opencode-ownership.py') 'fixed owner verifier path'
    $owner=Get-ODSOpenCodeBridgeOwner $identity $context
    Check ($owner.ownerUser -eq 'michael' -and $owner.ownerUid -eq 1000) 'verified user name instead of unsupported numeric WSL username'
    $script:fixtureOwner.installRoot='/foreign/root'
    Reject {Get-ODSOpenCodeBridgeOwner $identity $context} 'foreign root metadata rejected'
    $script:fixtureOwner.installRoot='/fixture/ods'
    $helper=Join-Path $fixture 'lib/wsl-loopback-helper.py'
    $original=[IO.File]::ReadAllBytes($helper)
    Write-ODSPrivateBytes $helper ([Text.Encoding]::UTF8.GetBytes('# changed'))
    Reject {Get-ODSOpenCodeBridgeContext $identity} 'changed snapshot rejected before importing code'
    Write-ODSPrivateBytes $helper $original
    Write-ODSWslJson (Join-Path $fixture 'request.json') @{generation='generation-a';action='stop'}
    Reject {Start-ODSOpenCodeOwnedBridge $identity $context $owner 'generation-a'} 'stop cancels before binding'
    Write-ODSWslJson (Join-Path $fixture 'request.json') @{generation='generation-b';action='run'}
    Reject {Start-ODSOpenCodeOwnedBridge $identity $context $owner 'generation-a'} 'different generation cancels before binding'
    $foreign=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,49193)
    $foreign.ExclusiveAddressUse=$true;$foreign.Start()
    Reject {Start-ODSOpenCodeOwnedBridge $identity $context $owner 'generation-b'} 'foreign Windows listener preserved'
    Check ($foreign.LocalEndpoint.Port -eq 49193) 'foreign listener remains owned by the fixture'
    $foreign.Stop();$foreign=$null
    $bridge=Start-ODSOpenCodeOwnedBridge $identity $context $owner 'generation-b'
    Check ($bridge.Ready -and $bridge.ListenPort -eq 49193) 'owned bridge listens only on disposable fixture port'
    $bridge.Dispose();$bridge=$null
    Check (@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object {$_.Port -eq 49193}).Count -eq 0) 'owned bridge disposal releases the listener'
    Check (@($script:externalCalls | Where-Object {$_ -contains '--user' -or $_ -contains 'record'}).Count -eq 0) 'probes never mutate ownership or use root'
    Write-Output "Passed $count bridge integration contracts; private copies/ACL/compiled transport/listeners are real, WSL identity probes are fixture-only."
} finally {
    if ($bridge) {$bridge.Dispose()}
    if ($foreign) {$foreign.Stop()}
    # Keep the unique private fixture for audit; no real installation or task was changed.
}
