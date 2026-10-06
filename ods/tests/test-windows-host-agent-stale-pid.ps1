param([string]$SourceWindowsDir = '')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$windowsDir = Join-Path $PSScriptRoot '../installers/windows'
if ($SourceWindowsDir) { $windowsDir = $SourceWindowsDir }
$cliPath = Join-Path $windowsDir 'ods.ps1'
$phasePath = Join-Path $windowsDir 'phases/07-devtools.ps1'
$helperPath = Join-Path $windowsDir 'lib/host-agent-process.ps1'
if (Test-Path -LiteralPath $helperPath) { . $helperPath }

function Get-TestAst {
    param([string]$Path)
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw "Parse failed: $Path" }
    return $ast
}
$cliAst = Get-TestAst $cliPath
$agent = $cliAst.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Agent'
}, $true)
Invoke-Expression $agent.Extent.Text
$phaseAst = Get-TestAst $phasePath
$reinstall = $phaseAst.Find({ param($node)
    $node -is [Management.Automation.Language.IfStatementAst] -and
        $node.Extent.Text.StartsWith('if (Test-Path $script:ODS_AGENT_PID_FILE)')
}, $true)
if (-not $reinstall) { throw 'Host-agent reinstall cleanup not found' }

$testRoot = Join-Path ([IO.Path]::GetTempPath()) "ods agent stale pid $([guid]::NewGuid())"
$InstallDir = Join-Path $testRoot 'owned install'
$foreignDir = Join-Path $testRoot 'other install'
$agentScript = Join-Path (Join-Path $InstallDir 'bin') 'ods-host-agent.py'
$foreignScript = Join-Path (Join-Path $foreignDir 'bin') 'ods-host-agent.py'
$script:ODS_AGENT_PID_FILE = Join-Path $testRoot 'agent.pid'
$script:ODS_AGENT_LOG_FILE = Join-Path $testRoot 'agent.log'
$script:ODS_AGENT_PORT = 3003
$script:ODS_AGENT_HEALTH_URL = 'http://127.0.0.1:3003/health'
$script:ODS_AGENT_TASK_NAME = 'ODSHostAgentFixture'
$script:python = (& python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $script:python)) { throw 'Runnable Python required' }
$fixtures = New-Object 'System.Collections.Generic.List[System.Diagnostics.Process]'
$script:passes = 0

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    $script:passes++
    Write-Host "PASS $Message"
}
function New-AgentFixture {
    param([string]$ScriptPath, [switch]$CommandString)
    if ($CommandString) {
        $argument = '-c "import time; p = r' + "'" + $ScriptPath + "'" + '; time.sleep(120)"'
    } else {
        $argument = '"' + $ScriptPath + '"'
    }
    $process = Microsoft.PowerShell.Management\Start-Process -FilePath $script:python -ArgumentList $argument -WindowStyle Hidden -PassThru
    $fixtures.Add($process)
    # Wait until CIM can inspect the actual launched Python command line.
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
        if ($record -and $record.CommandLine) { return $process }
        Microsoft.PowerShell.Utility\Start-Sleep -Milliseconds 20
    }
    throw 'Python fixture did not start'
}
function Test-FixtureAlive {
    param([Diagnostics.Process]$Process)
    $Process.Refresh()
    return -not $Process.HasExited
}

# Exercise the production CLI body while isolating Task Scheduler, Startup and
# health probes. Process inspection and termination use real Windows processes.
function Write-AI { param($Message) }
function Write-AISuccess { param($Message) }
function Write-AIWarn { param($Message) }
function Write-AIError { param($Message) throw $Message }
function Start-Sleep { param($Seconds) }
function Invoke-WebRequest {
    param($Uri, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
    if (-not $script:healthy) { throw 'Fixture agent offline' }
    return [pscustomobject]@{StatusCode=200}
}
function Resolve-ODSHostAgentPython { return [pscustomobject]@{FilePath=$script:python;PrefixArgs=@()} }
function Stop-ScheduledTask { param($TaskName, $ErrorAction) }
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory) return @{} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn) return @{} }
function New-ScheduledTaskSettingsSet {
    param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$StartWhenAvailable, $ExecutionTimeLimit)
    return @{}
}
function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel) return @{} }
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force, $ErrorAction)
    $script:registrations++
}
function Start-ScheduledTask { param($TaskName) $script:healthy = $true }
function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) return $script:listeners }
function Test-Path {
    param($Path, $LiteralPath, $PathType, $ErrorAction)
    $target = if ($LiteralPath) { $LiteralPath } else { $Path }
    if ($target -eq (Join-Path ([Environment]::GetFolderPath('Startup')) 'ods-host-agent.vbs')) { return $false }
    return Microsoft.PowerShell.Management\Test-Path -LiteralPath $target
}

try {
    New-Item -ItemType Directory -Path (Split-Path $agentScript), (Split-Path $foreignScript) -Force | Out-Null
    Set-Content -LiteralPath $agentScript -Value 'import time; time.sleep(120)' -Encoding ascii
    Set-Content -LiteralPath $foreignScript -Value 'import time; time.sleep(120)' -Encoding ascii
    $foreign = New-AgentFixture $foreignScript
    $script:healthy = $false; $script:registrations = 0; $script:listeners = @()

    Set-Content -LiteralPath $script:ODS_AGENT_PID_FILE -Value $foreign.Id
    Invoke-Agent stop
    Assert-Test (Test-FixtureAlive $foreign) 'stop preserves a foreign process referenced by the PID receipt'
    Assert-Test (-not (Test-Path $script:ODS_AGENT_PID_FILE)) 'stop discards the stale PID receipt'

    $script:listeners = @([pscustomobject]@{OwningProcess=$foreign.Id})
    Invoke-Agent stop
    Assert-Test (Test-FixtureAlive $foreign) 'stop preserves a foreign listener without a PID receipt'
    $script:listeners = @()

    foreach ($action in @('start', 'restart')) {
        Set-Content -LiteralPath $script:ODS_AGENT_PID_FILE -Value $foreign.Id
        $script:healthy = $false; $script:registrations = 0
        Invoke-Agent $action
        Assert-Test (Test-FixtureAlive $foreign) "$action preserves the foreign process"
        Assert-Test ($script:registrations -eq 1) "$action continues to register the replacement agent"
        Assert-Test (-not (Test-Path $script:ODS_AGENT_PID_FILE)) "$action clears the stale PID receipt"
    }

    Set-Content -LiteralPath $script:ODS_AGENT_PID_FILE -Value $foreign.Id
    $_agentScript = $agentScript
    Invoke-Expression $reinstall.Extent.Text
    Assert-Test (Test-FixtureAlive $foreign) 'reinstall preserves the foreign process'
    Assert-Test (-not (Test-Path $script:ODS_AGENT_PID_FILE)) 'reinstall clears the stale receipt without throwing'

    $owned = New-AgentFixture $agentScript
    Set-Content -LiteralPath $script:ODS_AGENT_PID_FILE -Value $owned.Id
    Invoke-Agent stop
    Assert-Test (-not (Test-FixtureAlive $owned)) 'stop terminates Python running the exact installed agent script'

    $ownedListener = New-AgentFixture $agentScript
    $script:listeners = @([pscustomobject]@{OwningProcess=$ownedListener.Id})
    Invoke-Agent stop
    Assert-Test (-not (Test-FixtureAlive $ownedListener)) 'stop still terminates an owned listener without a PID receipt'
    $script:listeners = @()

    $lookalike = New-AgentFixture $agentScript -CommandString
    Set-Content -LiteralPath $script:ODS_AGENT_PID_FILE -Value $lookalike.Id
    Invoke-Agent stop
    Assert-Test (Test-FixtureAlive $lookalike) 'a python -c command merely containing the installed path is preserved'

    foreach ($invalid in @('not-a-pid', '-1', '2147483648')) {
        Set-Content -LiteralPath $script:ODS_AGENT_PID_FILE -Value $invalid
        Invoke-Agent stop
        Assert-Test (-not (Test-Path $script:ODS_AGENT_PID_FILE)) "invalid PID receipt $invalid is discarded"
    }
    Set-Content -LiteralPath $script:ODS_AGENT_PID_FILE -Value $foreign.Id
    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) throw 'Fixture metadata access denied' }
    $script:healthy = $false; $script:registrations = 0
    Invoke-Agent start
    Assert-Test (Test-FixtureAlive $foreign) 'failed ownership inspection preserves the process'
    Assert-Test ($script:registrations -eq 1) 'failed ownership inspection still permits stale-receipt recovery'

    Write-Host "[PASS] $script:passes native Windows host-agent stale PID checks"
} finally {
    foreach ($fixture in $fixtures) {
        if (Test-FixtureAlive $fixture) { Microsoft.PowerShell.Management\Stop-Process -InputObject $fixture -Force -ErrorAction SilentlyContinue }
        $fixture.Dispose()
    }
    # Only this test's verified temporary directory is removed.
    if ([IO.Path]::GetFullPath($testRoot).StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) {
        Microsoft.PowerShell.Management\Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
