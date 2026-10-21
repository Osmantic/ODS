param([string]$SourceWindowsDir = '')
$ErrorActionPreference = 'Stop'
$windows = Join-Path $PSScriptRoot '../installers/windows'
if ($SourceWindowsDir) { $windows = $SourceWindowsDir }
. (Join-Path $windows 'lib/llm-endpoint.ps1')
. (Join-Path $windows 'lib/private-file.ps1')
function Read-Ast([string]$Path) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "Parse failed: $Path" }
    return $ast
}
$phaseAst = Read-Ast (Join-Path $windows 'phases/07-devtools.ps1')
$phasePort = @($phaseAst.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq '$script:OPENCODE_PORT'
})
$body = $phaseAst.Find({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($_ocReady -and')}, $true)
if (-not $body) { throw 'OpenCode installer block missing' }
$phase = [scriptblock]::Create($body.Extent.Text)
$cliAst = Read-Ast (Join-Path $windows 'ods.ps1')
$cliPort = @($cliAst.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq '$script:OPENCODE_PORT'
})
foreach ($name in @('Get-ODSOpenCodePortState', 'Start-ODSOpenCodeRuntime', 'Stop-ODSOpenCodeRuntime')) {
    $node = $cliAst.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    if (-not $node) { throw "CLI function missing: $name" }
    . ([scriptblock]::Create($node.Extent.Text))
}
$constantAst = Read-Ast (Join-Path $windows 'lib/constants.ps1')
$constant = $constantAst.Find({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$script:OPENCODE_PORT'}, $true)
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods-opencode-port-' + [guid]::NewGuid().ToString('N'))
$installDir = Join-Path $scratch ("owner's " + [char]0x00e9 + ' install')
$script:ODS_INSTALL_DIR = $installDir
$script:OPENCODE_DIR = Join-Path $scratch 'opencode'
$script:OPENCODE_EXE = Join-Path $script:OPENCODE_DIR 'opencode-fixture.ps1'
$script:OPENCODE_TASK_NAME = 'ODSOpenCodePortFixture'
$record = Join-Path $scratch 'arguments.json'
$saved = @{}
foreach ($key in @('OPENCODE_PORT', 'LOCALAPPDATA', 'ODS_HOME', 'ODS_OPENCODE_PORT_FIXTURE')) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key)
}
$gpuInfo = @{Backend='cpu'}; $tierConfig = @{GgufFile='fixture.gguf';LlmModel='fixture';MaxContext=4096}
$cloudMode = $false; $_ocReady = $true
$script:messages = [Collections.Generic.List[string]]::new()
$script:probes = [Collections.Generic.List[int]]::new()
$script:checks = 0; $script:failures = @()
$script:stopped = [Collections.Generic.List[int]]::new()
function Check($condition, [string]$message) {
    $script:checks++
    if (-not $condition) { $script:failures += $message; Write-Host "FAIL $message" }
}
function Write-AI { param($Message) $script:messages.Add([string]$Message) }
function Write-AIWarn { param($Message) $script:messages.Add([string]$Message) }
function Write-AIError { param($Message) $script:messages.Add([string]$Message) }
function Write-AISuccess { param($Message) $script:messages.Add([string]$Message) }
function Sync-WindowsOpenCodeConfigFromEnv { param($InstallDir,$GpuBackend,$CloudMode,$DefaultModelId,$DefaultModelName,$DefaultContextLimit) return @{Status='created';ModelName='fixture'} }
function Stop-ScheduledTask { param($TaskName,$ErrorAction) }
function Unregister-ScheduledTask { param($TaskName,$Confirm,$ErrorAction) }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) return @{} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn) return @{} }
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$ExecutionTimeLimit) return @{} }
function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel) return @{} }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Principal,$Description,[switch]$Force,$ErrorAction) }
function Start-ScheduledTask { param($TaskName,$ErrorAction) }
function Get-ScheduledTask { param($TaskName,$ErrorAction) return @{TaskName=$TaskName} }
function Start-Process { throw 'No native process may be launched by the fixture block' }
function Stop-Process { throw 'No process may be stopped by the fixture' }
function Stop-ODSNativeProcessId { param($ProcessId) $script:stopped.Add([int]$ProcessId) }
function Start-Sleep { param($Seconds) }
function Get-NetTCPConnection {
    param($LocalPort,$State,$ErrorAction)
    $script:probes.Add([int]$LocalPort)
    if ([int]$LocalPort -eq $script:expectedPort) { return [pscustomobject]@{OwningProcess=123} }
}
function Get-CimInstance {
    param($ClassName,$Filter,$ErrorAction)
    return [pscustomobject]@{ExecutablePath=$script:listenerExe; ProcessId=123; ParentProcessId=0; CommandLine="opencode web --port $script:expectedPort --hostname 127.0.0.1"}
}
function Read-CliPort {
    . ([scriptblock]::Create($constant.Extent.Text))
    foreach ($statement in $cliPort) { . ([scriptblock]::Create($statement.Extent.Text)) }
}
function Invoke-Launcher {
    $shell = (Get-Process -Id $PID).Path
    & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $script:OPENCODE_DIR 'start-opencode.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Generated launcher failed: $LASTEXITCODE" }
    return @(Get-Content -LiteralPath $record -Raw | ConvertFrom-Json)
}
try {
    New-Item -ItemType Directory -Path (Join-Path $installDir 'lib'),$script:OPENCODE_DIR | Out-Null
    Copy-Item -LiteralPath (Join-Path $windows 'lib/llm-endpoint.ps1') -Destination (Join-Path $installDir 'lib/llm-endpoint.ps1')
    Write-Utf8NoBom -Path $script:OPENCODE_EXE -Content '[IO.File]::WriteAllText($env:ODS_OPENCODE_PORT_FIXTURE, (ConvertTo-Json -InputObject @($args) -Compress)); exit 0'
    $env:LOCALAPPDATA = Join-Path $scratch 'local'
    $env:ODS_HOME = Join-Path $scratch 'unrelated installation'
    $env:ODS_OPENCODE_PORT_FIXTURE = $record
    $script:listenerExe = $script:OPENCODE_EXE
    foreach ($case in @(
        @{name='default'; value=$null; process=$null; port=3003},
        @{name='missing saved'; value=$null; process='33003'; port=3003},
        @{name='retained'; value='23003'; process=$null; port=23003},
        @{name='quoted'; value='"23004"'; process='33003'; port=23004},
        @{name='invalid ambient'; value='23005'; process='not-a-port'; port=23005},
        @{name='zero saved'; value='0'; process='33003'; port=3003},
        @{name='negative'; value='-1'; process=$null; port=3003},
        @{name='out of range'; value='65536'; process=$null; port=3003},
        @{name='noninteger'; value='23.5'; process=$null; port=3003},
        @{name='overflow'; value='999999999999999999999'; process=$null; port=3003},
        @{name='minimum'; value='1'; process=$null; port=1},
        @{name='maximum'; value='65535'; process=$null; port=65535}
    )) {
        [Environment]::SetEnvironmentVariable('OPENCODE_PORT', $(if ($null -eq $case.process) { [NullString]::Value } else { $case.process }))
        $line = if ($null -eq $case.value) { '# No OpenCode port saved' } else { "OPENCODE_PORT=$($case.value)" }
        Write-Utf8NoBom -Path (Join-Path $installDir '.env') -Content $line
        $script:expectedPort = $case.port
        $script:messages.Clear(); $script:probes.Clear()
        . ([scriptblock]::Create($constant.Extent.Text))
        foreach ($statement in $phasePort) { . ([scriptblock]::Create($statement.Extent.Text)) }
        . $phase
        Check ($script:OPENCODE_PORT -eq $case.port) "$($case.name): installer uses saved/default port"
        Check ($script:probes.Count -gt 0 -and @($script:probes | Where-Object { $_ -ne $case.port }).Count -eq 0) "$($case.name): installer probes only selected port"
        Check (@($script:messages | Where-Object { $_ -eq "OpenCode web app started (http://localhost:$($case.port))" }).Count -eq 1) "$($case.name): installer reports selected URL"
        $arguments = Invoke-Launcher
        Check (($arguments -join ' ') -eq "web --port $($case.port) --hostname 127.0.0.1") "$($case.name): generated login/manual launcher uses selected port and loopback"
        Read-CliPort
        Check ($script:OPENCODE_PORT -eq $case.port) "$($case.name): CLI reloads same installed port"
        $script:probes.Clear(); $script:messages.Clear()
        $started = Start-ODSOpenCodeRuntime
        Check ($started -and $script:probes.Count -gt 0 -and @($script:probes | Where-Object { $_ -ne $case.port }).Count -eq 0) "$($case.name): CLI recognizes owned listener on selected port"
    }
    # A retained launcher must follow a later saved configuration, not the
    # install-time value or a different login/CLI process environment.
    Write-Utf8NoBom -Path (Join-Path $installDir '.env') -Content 'OPENCODE_PORT=23009'
    $env:OPENCODE_PORT = '33009'
    $arguments = Invoke-Launcher
    Check (($arguments -join ' ') -eq 'web --port 23009 --hostname 127.0.0.1') 'existing launcher rereads its pinned install configuration'
    Read-CliPort
    $script:expectedPort = 23009
    $script:listenerExe = Join-Path $scratch 'foreign.exe'
    $script:messages.Clear()
    Check (-not (Start-ODSOpenCodeRuntime)) 'CLI refuses a foreign listener on the configured port'
    Check (@($script:messages | Where-Object { $_ -like 'Port 23009 is used by another process*' }).Count -eq 1) 'foreign-listener refusal names the configured port'
    Stop-ODSOpenCodeRuntime
    Check ($script:stopped.Count -eq 0) 'CLI stop preserves a foreign listener'
    $script:listenerExe = $script:OPENCODE_EXE
    Stop-ODSOpenCodeRuntime
    Check ($script:stopped.Count -eq 1 -and $script:stopped[0] -eq 123) 'CLI stop selects the owned process on the configured port'
    if ($script:failures.Count) { throw "$($script:failures.Count)/$($script:checks) checks failed" }
    Write-Host "PASS $script:checks native OpenCode port checks"
} finally {
    foreach ($key in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($key, $(if ($null -eq $saved[$key]) { [NullString]::Value } else { $saved[$key] }))
    }
    $resolved = [IO.Path]::GetFullPath($scratch)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path $resolved -Leaf) -notlike 'ods-opencode-port-*') { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
