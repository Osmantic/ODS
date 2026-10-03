param([string]$LifecycleScript=(Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1'))
$ErrorActionPreference='Stop'
. $LifecycleScript
$fixture=Join-Path $PSScriptRoot ('.wsl-json-sharing-'+[guid]::NewGuid().ToString('N'))
$count=0
function Check([bool]$Condition,[string]$Message) { if(-not $Condition){throw $Message}; $script:count++; Write-Host "PASS $Message" }
function Reject([scriptblock]$Operation,[string]$Message) { $failed=$false; try { & $Operation } catch { $failed=$true }; Check $failed $Message }
if(-not ('OdsLifecycleJsonSharingContract' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
public static class OdsLifecycleJsonSharingContract {
    public static Task Hold(string path, int milliseconds, byte[] replacement) {
        var stream = File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        return Task.Run(() => {
            using(stream) {
                Thread.Sleep(milliseconds);
                if(replacement != null) {
                    stream.SetLength(0);
                    stream.Write(replacement, 0, replacement.Length);
                }
            }
        });
    }
    public static Task Publish(string path, int rounds) {
        return Task.Run(() => {
            for(int i=1; i<=rounds; i++) {
                var temporary=path+"."+Guid.NewGuid().ToString("N")+".tmp";
                File.WriteAllText(temporary, "{\"generation\":"+i+"}", new UTF8Encoding(false));
                File.Replace(temporary,path,null);
                Thread.Sleep(2);
            }
        });
    }
    public static Task Gap(string path, int milliseconds) {
        var previous=path+".previous";
        File.Move(path,previous);
        return Task.Run(() => {
            Thread.Sleep(milliseconds);
            File.Move(previous,path);
        });
    }
    public static Task LockThenRemove(string path, int milliseconds) {
        var stream=File.Open(path,FileMode.Open,FileAccess.ReadWrite,FileShare.None);
        return Task.Run(() => {
            Thread.Sleep(milliseconds);
            stream.Dispose();
            File.Delete(path);
        });
    }
}
'@
}
$pending=$null
$exclusive=$null
try {
    Initialize-ODSPrivateDirectory $fixture
    $path=Join-Path $fixture 'runtime.json'
    Write-ODSWslJson $path @{generation=0;state='running'}
    $pending=[OdsLifecycleJsonSharingContract]::Hold($path,250,$null)
    $value=Read-ODSWslJson $path
    $pending.GetAwaiter().GetResult();$pending=$null
    Check ($value.generation -eq 0 -and $value.state -ceq 'running') 'temporary exclusive lock is retried on real Windows'

    $exclusive=[IO.File]::Open($path,'Open','ReadWrite','None')
    $watch=[Diagnostics.Stopwatch]::StartNew()
    try { Reject { Read-ODSWslJson $path } 'persistent sharing violation remains a failure' }
    finally { $exclusive.Dispose();$exclusive=$null }
    Check ($watch.Elapsed.TotalMilliseconds -ge 1000 -and $watch.Elapsed.TotalSeconds -lt 6) 'persistent lock failure has a bounded retry budget'

    $pending=[OdsLifecycleJsonSharingContract]::LockThenRemove($path,150)
    Reject { Read-ODSWslJson $path } 'observed metadata disappearing during retries is a failure, not absence'
    $pending.GetAwaiter().GetResult();$pending=$null
    Write-ODSWslJson $path @{generation=0}

    $oversize=[Text.UTF8Encoding]::new($false).GetBytes('x'*65537)
    $pending=[OdsLifecycleJsonSharingContract]::Hold($path,150,$oversize)
    Reject { Read-ODSWslJson $path } 'size validation repeats after a locked file changes'
    $pending.GetAwaiter().GetResult();$pending=$null
    Write-ODSPrivateBytes $path ([Text.UTF8Encoding]::new($false).GetBytes('{broken'))
    $watch.Restart()
    Reject { Read-ODSWslJson $path } 'malformed JSON is never treated as transient locking'
    Check ($watch.Elapsed.TotalSeconds -lt 1) 'malformed JSON fails without a retry delay'

    Write-ODSWslJson $path @{generation=0}
    $acl=Get-Acl -LiteralPath $path
    $leaky=Get-Acl -LiteralPath $path
    $leaky.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'))
    Set-Acl -LiteralPath $path -AclObject $leaky
    try { Reject { Read-ODSWslJson $path } 'private ACL validation remains mandatory' }
    finally { Set-Acl -LiteralPath $path -AclObject $acl }
    $watch.Restart()
    Check ($null -eq (Read-ODSWslJson (Join-Path $fixture 'missing.json'))) 'absent metadata keeps its existing null behavior'
    Check ($watch.Elapsed.TotalSeconds -lt 6) 'absence confirmation has a bounded retry budget'
    $pending=[OdsLifecycleJsonSharingContract]::Gap($path,250)
    $value=Read-ODSWslJson $path
    $pending.GetAwaiter().GetResult();$pending=$null
    Check ($null -ne $value -and $value.generation -eq 0) 'a temporary publication gap is not reported as absent metadata'

    $pending=[OdsLifecycleJsonSharingContract]::Publish($path,150)
    $reads=0
    do {
        $watch.Restart()
        try { $value=Read-ODSWslJson $path } catch {
            $cause=$_.Exception.GetBaseException()
            # This deliberately saturated publisher can exceed the production
            # budget on a busy runner. A bounded sharing failure while it is
            # still running is valid; an early or unrelated failure is not.
            if ($cause -is [IO.IOException] -and ($cause.HResult -band 0xFFFF) -in @(32,33) -and
                $watch.ElapsedMilliseconds -ge 1900 -and $watch.Elapsed.TotalSeconds -lt 6 -and -not $pending.IsCompleted) {
                Check $true 'continuous publication fails closed when the sharing budget expires'
                break
            }
            Write-Warning ("Concurrent read failed: elapsedMs={0}; type={1}; code={2}; publisherState={3}; reads={4}" -f
                $watch.ElapsedMilliseconds,$cause.GetType().FullName,($cause.HResult -band 0xFFFF),$pending.Status,$reads)
            throw
        }
        Check ($null -ne $value.generation -and $value.generation -ge 0 -and $value.generation -le 150) 'concurrent publication yields a complete JSON snapshot'
        $reads++
    } while(-not $pending.IsCompleted)
    $pending.GetAwaiter().GetResult();$pending=$null
    Check ((Read-ODSWslJson $path).generation -eq 150) 'the complete final snapshot is readable after the publisher finishes'
    Write-Host "Passed $count physical Windows JSON sharing checks."
} finally {
    if($exclusive){$exclusive.Dispose()}
    if($pending){try{$pending.GetAwaiter().GetResult()}catch{Write-Warning 'Fixture background operation failed'}}
    if(Test-Path -LiteralPath $fixture){
        $resolved=(Resolve-Path -LiteralPath $fixture).Path
        $expected=[IO.Path]::GetFullPath($fixture)
        if($resolved -cne $expected -or -not $resolved.StartsWith([IO.Path]::GetFullPath($PSScriptRoot)+[IO.Path]::DirectorySeparatorChar)){throw 'Unexpected fixture cleanup path'}
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
