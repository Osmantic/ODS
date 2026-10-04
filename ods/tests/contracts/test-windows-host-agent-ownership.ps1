param([string]$CliPath = '')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$cli = Join-Path $root 'installers/windows/ods.ps1'
if ($CliPath) { $cli = $CliPath }
$library = Join-Path $root 'installers/windows/lib/host-agent-lifecycle.ps1'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-agent-ownership-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture -Force | Out-Null
$script:processesToDispose = @()
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}
function Write-AI { param([string]$Message) }
function Write-AISuccess { param([string]$Message) }
function Write-AIWarn { param([string]$Message) }
function Write-AIError { param([string]$Message) }
function Get-ScheduledTask { param($TaskName, $ErrorAction) if ($script:taskLookupFailure) { throw 'Task inventory unavailable' }; return $script:tasks }
function Stop-ScheduledTask { param($TaskName, $ErrorAction) if ($script:taskStopFailure) { throw 'Task stop failed' }; $script:stoppedTasks += $TaskName }
function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) if ($script:listenerLookupFailure) { throw 'Listener inventory unavailable' }; return $script:listeners }
function Invoke-WebRequest { param($Uri, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction) throw 'No agent health' }
function Resolve-ODSHostAgentPython { $script:startAttempted = $true; return $null }

try {
    $InstallDir = Join-Path $fixture 'install'
    $startupFixture = Join-Path $fixture 'startup'
    New-Item -ItemType Directory -Path (Join-Path $InstallDir 'bin') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $InstallDir 'data') -Force | Out-Null
    New-Item -ItemType Directory -Path $startupFixture -Force | Out-Null
    $script:ODS_AGENT_PID_FILE = Join-Path $InstallDir 'data/ods-host-agent.pid'
    $script:ODS_AGENT_LOG_FILE = Join-Path $InstallDir 'data/ods-host-agent.log'
    $script:ODS_AGENT_PORT = 57993
    $script:ODS_AGENT_HEALTH_URL = 'http://127.0.0.1:57993/health'
    $script:ODS_AGENT_TASK_NAME = 'ODSHostAgent'
    $script:ODSAgentStartupFolder = $startupFixture
    $script:tasks = @()
    $script:stoppedTasks = @()
    $script:taskStopFailure = $false
    $script:listeners = @()
    $script:taskLookupFailure = $false

    $script:listenerLookupFailure = $false
    $script:startAttempted = $false

    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($cli, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw $errors[0] }
    if (Test-Path -LiteralPath $library) { . $library }
    $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Agent' }, $true)
    # Redirect only the host Startup directory, never touch the user's launchers.
    $functionText = $definition.Extent.Text.Replace('[Environment]::GetFolderPath("Startup")', '$script:ODSAgentStartupFolder')
    . ([scriptblock]::Create($functionText))

    $foreign = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile -Command "Start-Sleep -Seconds 120"' -WindowStyle Hidden -PassThru
    $script:processesToDispose += $foreign
    [IO.File]::WriteAllText($script:ODS_AGENT_PID_FILE, [string]$foreign.Id)
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check (-not $foreign.HasExited) 'agent stop preserves a live foreign process referenced by a stale PID file'
    Check ($refused -and (Test-Path -LiteralPath $script:ODS_AGENT_PID_FILE)) 'foreign PID refuses cleanup and preserves the PID receipt'

    foreach ($action in @('start', 'restart')) {
        $script:startAttempted = $false
        $refused = $false
        try { Invoke-Agent -Action $action } catch { $refused = $true }
        Check ($refused -and -not $script:startAttempted -and -not $foreign.HasExited) "$action refuses the foreign PID before attempting a replacement"
    }
    Remove-Item -LiteralPath $script:ODS_AGENT_PID_FILE -Force
    $script:listeners = @([pscustomobject]@{ LocalPort = $script:ODS_AGENT_PORT; State = 'Listen'; OwningProcess = $foreign.Id })
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check ($refused -and -not $foreign.HasExited) 'port collision preserves the foreign listener without a PID file'
    $script:listeners = @()

    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $agentScript = Join-Path $InstallDir 'bin/ods-host-agent.py'
    [IO.File]::WriteAllText($agentScript, 'import time; time.sleep(120)')
    $agent = Start-Process -FilePath $python -ArgumentList ('"{0}" --port {1} --pid-file "{2}" --install-dir "{3}"' -f $agentScript, $script:ODS_AGENT_PORT, $script:ODS_AGENT_PID_FILE, $InstallDir) -WindowStyle Hidden -PassThru
    $script:processesToDispose += $agent
    [IO.File]::WriteAllText($script:ODS_AGENT_PID_FILE, [string]$agent.Id)
    $script:listenerLookupFailure = $true
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check ($refused -and -not $agent.HasExited -and (Test-Path -LiteralPath $script:ODS_AGENT_PID_FILE)) 'failed listener inspection preserves the owned process and PID for retry'
    $script:listenerLookupFailure = $false
    $script:taskLookupFailure = $true
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check ($refused -and -not $agent.HasExited) 'failed task inspection refuses every stop mutation'
    $script:taskLookupFailure = $false

    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) throw 'Process ownership inventory unavailable' }
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check ($refused -and -not $agent.HasExited -and (Test-Path -LiteralPath $script:ODS_AGENT_PID_FILE)) 'failed CIM ownership inspection preserves the process and PID receipt'
    Remove-Item Function:Get-CimInstance

    $script:tasks = @([pscustomobject]@{TaskName = 'ODSHostAgent'; TaskPath = '\'; Actions = @([pscustomobject]@{Execute = 'python.exe'; Arguments = 'C:\foreign\agent.py'})})
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check ($refused -and -not $agent.HasExited -and -not $script:stoppedTasks.Count) 'foreign same-name task is preserved before stopping the owned agent'
    $script:tasks = @()
    $startupFile = Join-Path $startupFixture 'ods-host-agent.vbs'
    [IO.File]::WriteAllText($startupFile, 'WScript.Echo "foreign launcher"')
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check ($refused -and -not $agent.HasExited -and (Test-Path -LiteralPath $startupFile)) 'foreign same-name Startup launcher is preserved before stopping the owned agent'
    Remove-Item -LiteralPath $startupFile -Force

    # Inspect the actual generated launcher shape without executing it.
    $launcherCommand = "`$agentArgs = @('$agentScript', '--port', '$($script:ODS_AGENT_PORT)', '--pid-file', '$($script:ODS_AGENT_PID_FILE)', '--install-dir', '$InstallDir')`r`nSet-Location '$InstallDir'`r`nStart-Process -FilePath '$python' -ArgumentList `$agentArgs -WorkingDirectory '$InstallDir' -WindowStyle Hidden -RedirectStandardError '$($script:ODS_AGENT_LOG_FILE)' -Wait"
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launcherCommand))
    $script:tasks = @([pscustomobject]@{TaskName = 'ODSHostAgent'; TaskPath = '\'; Actions = @([pscustomobject]@{Execute = 'powershell.exe'; Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $encodedCommand"})})
    $launcherContent = "' ODS Host Agent login startup launcher`r`nSet WshShell = CreateObject(`"WScript.Shell`")`r`nWshShell.Run `"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $encodedCommand`", 0, False"
    [IO.File]::WriteAllText($startupFile, $launcherContent)
    $script:taskStopFailure = $true
    $refused = $false
    try { Invoke-Agent -Action stop } catch { $refused = $true }
    Check ($refused -and -not $agent.HasExited -and (Test-Path -LiteralPath $startupFile) -and (Test-Path -LiteralPath $script:ODS_AGENT_PID_FILE)) 'failed owned-task stop preserves the process and both persistence receipts for retry'
    $script:taskStopFailure = $false

    # Recover after the collision is removed. Duplicate listener rows must not
    # reopen a numeric PID after terminating the owned process.
    $script:listeners = @(
        [pscustomobject]@{LocalPort = $script:ODS_AGENT_PORT; State = 'Listen'; OwningProcess = $agent.Id},
        [pscustomobject]@{LocalPort = $script:ODS_AGENT_PORT; State = 'Listen'; OwningProcess = $agent.Id}
    )
    Invoke-Agent -Action stop
    Check ($agent.WaitForExit(5000) -and -not (Test-Path -LiteralPath $script:ODS_AGENT_PID_FILE)) 'retry stops the owned process once and removes its PID receipt'
    Check ($script:stoppedTasks.Count -eq 1 -and -not (Test-Path -LiteralPath $startupFile)) 'owned encoded task and Startup persistence are cleaned together'
    Check (-not $foreign.HasExited) 'successful owned cleanup continues to preserve the unrelated process'
    $script:tasks = @()
    $script:listeners = @()
    [IO.File]::WriteAllText($script:ODS_AGENT_PID_FILE, [string]$agent.Id)
    Invoke-Agent -Action stop
    Check (-not (Test-Path -LiteralPath $script:ODS_AGENT_PID_FILE)) 'expired owned PID is cleaned without terminating a different process'

    # Native process and listener inspection, not a synthetic PID model.
    Remove-Item Function:Get-NetTCPConnection
    $socketScript = Join-Path $InstallDir 'bin/ods-host-agent.py'
    $portFile = Join-Path $fixture 'port.txt'
    [IO.File]::WriteAllText($socketScript, 'import socket,sys,time; s=socket.socket(); s.bind(("127.0.0.1",0)); s.listen(); open(sys.argv[1],"w").write(str(s.getsockname()[1])); time.sleep(120)')
    $socketProcess = Start-Process -FilePath $python -ArgumentList ('"{0}" "{1}"' -f $socketScript, $portFile) -WindowStyle Hidden -PassThru
    $script:processesToDispose += $socketProcess
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $portFile) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    $script:ODS_AGENT_PORT = [int](Get-Content -LiteralPath $portFile -Raw)
    Check (@(Get-NetTCPConnection -LocalPort $script:ODS_AGENT_PORT -State Listen -ErrorAction Stop).Count -gt 0) 'fixture exposes a real native Windows listener'
    Invoke-Agent -Action stop
    Check ($socketProcess.WaitForExit(5000)) 'owned agent without a PID receipt stops through the real Windows listener and CIM path'
    Invoke-Agent -Action stop
    Check (-not $foreign.HasExited) 'empty native listener inventory permits idempotent stop and preserves unrelated process'

    # Run the actual installer's host-agent conditional with dependency and
    # scheduler boundaries stubbed, so refusal cannot fall into launch fallback.
    $phase = Join-Path $root 'installers/windows/phases/07-devtools.ps1'
    $phaseAst = [Management.Automation.Language.Parser]::ParseFile($phase, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw $errors[0] }
    $hostConditional = $phaseAst.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.IfStatementAst] -and $_.Extent.Text.StartsWith('if (Test-Path $_agentScript)') } | Select-Object -First 1
    if (-not $hostConditional) { throw 'Installer host-agent conditional missing' }
    function Resolve-ODSHostAgentPython { return [pscustomobject]@{FilePath = $python; PrefixArgs = @()} }
    function Invoke-ODSNativeQuiet { param($FilePath, $Arguments, $LogPath) return 0 }
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory) $script:launchAttempted = $true; throw 'Unexpected launch' }
    $_agentScript = $agentScript
    $script:launchAttempted = $false
    [IO.File]::WriteAllText($script:ODS_AGENT_PID_FILE, [string]$foreign.Id)
    $refused = $false
    try { . ([scriptblock]::Create($hostConditional.Extent.Text.Replace('[Environment]::GetFolderPath("Startup")', '$script:ODSAgentStartupFolder'))) } catch { $refused = $true }
    Check ($refused -and -not $foreign.HasExited -and -not $script:launchAttempted -and (Test-Path -LiteralPath $script:ODS_AGENT_PID_FILE)) 'installer reinstall refuses a foreign PID before task/VBS/direct-launch fallback'
    Write-Host "Result: $script:checks ownership checks passed"
} finally {
    foreach ($process in $script:processesToDispose) {
        try { if (-not $process.HasExited) { $process.Kill(); $null = $process.WaitForExit(5000) } } finally { $process.Dispose() }
    }
    $absoluteFixture = [IO.Path]::GetFullPath($fixture)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\ods-agent-ownership-'
    if (-not $absoluteFixture.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing cleanup outside the ownership test fixture' }
    Remove-Item -LiteralPath $absoluteFixture -Recurse -Force
}
