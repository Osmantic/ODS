[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve production command signatures.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidOverwritingBuiltInCmdlets', '', Justification='Test-process listener and process mocks exercise the real conflict probe without modifying native applications.')]
[CmdletBinding()]
param()
# tests/test-whisper-port-conflict.ps1  (standalone, PS 5.1, no Pester)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $script:repoRoot 'installers\windows\lib\env-generator.ps1')

$script:passCount = 0
$script:caseCount = 0
function Assert-Equal {
    param([string]$Name, $Actual, $Expected)
    $script:caseCount++
    if ($Actual -ceq $Expected) {
        $script:passCount++
        Write-Host ("PASS {0}" -f $Name)
    } else {
        Write-Host ("FAIL {0}: expected '{1}' got '{2}'" -f $Name, $Expected, $Actual)
    }
}

function New-StubConn { param([int]$P) [pscustomobject]@{ OwningProcess = $P; LocalPort = 9000; State = 'Listen' } }
function New-StubProc { param([int]$P, [string]$N) [pscustomobject]@{ Id = $P; ProcessName = $N } }

function Set-ListenerStub {
    param([object[]]$Conns, [object[]]$Procs, [switch]$Throw)
    $script:probeCalls = 0
    $script:stubConnections = $Conns
    $script:stubProcesses = $Procs
    $script:stubThrow = [bool]$Throw
}
function Get-NetTCPConnection {
    param($LocalPort, $State, $ErrorAction)
    $script:probeCalls++
    if ($script:stubThrow) { throw 'probe unavailable' }
    return $script:stubConnections
}
function Get-Process {
    param($Id, $ErrorAction)
    foreach ($process in $script:stubProcesses) {
        if ($process.Id -eq $Id) { return $process }
    }
    return $null
}

# 1. Free port
Set-ListenerStub -Conns @() -Procs @()
Assert-Equal 'free-port-retains-9000' (Test-WindowsLemonadeWhisperPortConflict) $false

# 2. Docker-only listener (non-Lemonade name)
Set-ListenerStub -Conns @(New-StubConn 100) -Procs @(New-StubProc 100 'com.docker.backend')
Assert-Equal 'docker-only-no-conflict' (Test-WindowsLemonadeWhisperPortConflict) $false

# 3. Unrelated LemonadeServer behind Docker: Docker listed first, Lemonade second
Set-ListenerStub -Conns @((New-StubConn 100), (New-StubConn 200)) `
    -Procs @((New-StubProc 100 'com.docker.backend'), (New-StubProc 200 'LemonadeServer'))
Assert-Equal 'lemonade-behind-docker-detected' (Test-WindowsLemonadeWhisperPortConflict) $true

# 4. Multiple Lemonade names / case variants
Set-ListenerStub -Conns @((New-StubConn 1), (New-StubConn 2), (New-StubConn 3)) `
    -Procs @((New-StubProc 1 'LEMONADE-SERVER'), (New-StubProc 2 'lemonade-router'), (New-StubProc 3 'LemonadeServer'))
Assert-Equal 'lemonade-name-case-variants' (Test-WindowsLemonadeWhisperPortConflict) $true

# 5. Other app only
Set-ListenerStub -Conns @(New-StubConn 300) -Procs @(New-StubProc 300 'nginx')
Assert-Equal 'other-app-no-conflict' (Test-WindowsLemonadeWhisperPortConflict) $false

# 6. Unavailable probe returns false
Set-ListenerStub -Conns @() -Procs @() -Throw
Assert-Equal 'unavailable-probe-false' (Test-WindowsLemonadeWhisperPortConflict) $false

# 7. Persisted/default 9000 migrates under conflict
Set-ListenerStub -Conns @(New-StubConn 200) -Procs @(New-StubProc 200 'lemonade-server')
Assert-Equal 'default9000-migrates-on-conflict' `
    (Resolve-WindowsWhisperHostPort -ConfiguredPort '9000') '9100'

# 8. Custom 9500 with conflict: zero probe calls
Set-ListenerStub -Conns @(New-StubConn 200) -Procs @(New-StubProc 200 'lemonade-server')
Assert-Equal 'custom9500-preserved' (Resolve-WindowsWhisperHostPort -ConfiguredPort '9500') '9500'
Assert-Equal 'custom9500-zero-probe-calls' $script:probeCalls 0

# 9. Managed AMD default
Set-ListenerStub -Conns @() -Procs @()
Assert-Equal 'managed-amd-default-9100' `
    (Resolve-WindowsWhisperHostPort -ConfiguredPort '9000' -GpuBackend 'amd' -AmdInferenceRuntime 'lemonade' -AmdInferenceLocation 'host') '9100'

# The resolver retains default 9000 when no Lemonade listener exists.
Set-ListenerStub -Conns @() -Procs @()
Assert-Equal 'nvidia-free-default9000' (Resolve-WindowsWhisperHostPort -GpuBackend 'nvidia') '9000'
Assert-Equal 'amd-container-default9000' (Resolve-WindowsWhisperHostPort -GpuBackend 'amd' -AmdInferenceRuntime 'lemonade' -AmdInferenceLocation 'container') '9000'
foreach ($name in @('LemonadeServer','LEMONADE-SERVER','lemonade-router')) {
    Set-ListenerStub -Conns @(New-StubConn 200) -Procs @(New-StubProc 200 $name)
    Assert-Equal ('name-'+$name) (Test-WindowsLemonadeWhisperPortConflict) $true
}

Write-Host ("{0}/{1} cases passed" -f $script:passCount, $script:caseCount)
if ($script:passCount -ne $script:caseCount) { exit 1 }
