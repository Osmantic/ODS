$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'installers/windows/lib/devtools-selection.ps1')

function Assert-ODSDevTools {
    param([bool]$Condition, [string]$Label)
    if (-not $Condition) { throw "FAIL: $Label" }
}

$script:mockTask = $null
$script:disableCalls = 0
function Get-ScheduledTask {
    param([string]$TaskName, [string]$ErrorAction)
    if ($null -eq $script:mockTask) { throw 'Task not found' }
    return $script:mockTask
}
function Disable-ScheduledTask {
    param([string]$TaskName, [string]$ErrorAction)
    $script:disableCalls++
    $script:mockTask.State = 'Disabled'
    return $script:mockTask
}

$launcher = 'C:\Users\test\.opencode\start-opencode.ps1'
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\test\.opencode\start-opencode.ps1"'
$base = @{TaskName='ODSOpenCodeWeb'; ExpectedLauncher=$launcher}

Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'fresh install skips developer tools'
$script:mockTask = [pscustomobject]@{State='Ready'; Actions=@([pscustomobject]@{Execute='powershell.exe'; Arguments=$arguments; WorkingDirectory='C:\Users\test\.opencode'})}
Assert-ODSDevTools (Resolve-ODSWindowsDevToolsSelection @base) 'enabled ODS task retains developer tools'
$script:mockTask.Actions[0].Arguments = $arguments + ' -Command "foreign"'
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'task with extra command does not retain developer tools'
Assert-ODSDevTools (-not (Disable-ODSWindowsOpenCodeLoginTask @base)) 'task with extra command is not disabled'
$script:mockTask.Actions[0].Arguments = $arguments
$script:mockTask.Actions += [pscustomobject]@{Execute='cmd.exe'; Arguments='/c foreign'; WorkingDirectory='C:\Users\test\.opencode'}
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'task with extra action does not retain developer tools'
$script:mockTask.Actions = @($script:mockTask.Actions[0])
$script:mockTask.Actions[0].Execute = 'C:\other\powershell.exe'
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'foreign executable named powershell.exe is rejected'
$script:mockTask.Actions[0].Execute = 'powershell.exe'
$script:mockTask.Actions[0].WorkingDirectory = 'C:\other'
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'foreign working directory is rejected'
$script:mockTask.Actions[0].WorkingDirectory = 'C:\Users\test\.opencode'
$script:mockTask.State = 'Disabled'
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'disabled ODS task stays disabled'
$script:mockTask.State = 'Unknown'
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'unknown task state does not opt in'
$script:mockTask.State = 'Queued'
Assert-ODSDevTools (Resolve-ODSWindowsDevToolsSelection @base) 'queued ODS task retains selection'
$script:mockTask.State = 'Ready'
$script:mockTask.Actions[0].Arguments = '-NoProfile -File "C:\other\launcher.ps1"'
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base)) 'foreign task does not select developer tools'
Assert-ODSDevTools (-not (Disable-ODSWindowsOpenCodeLoginTask @base)) 'foreign task is not disabled'
Assert-ODSDevTools ($script:disableCalls -eq 0) 'foreign task is untouched'
$foreignRejected = $false
try { Resolve-ODSWindowsDevToolsSelection @base -ExplicitEnable $true | Out-Null } catch { $foreignRejected = $true }
Assert-ODSDevTools $foreignRejected 'explicit opt-in refuses a foreign task'
$script:mockTask.Actions[0].Arguments = $arguments
Assert-ODSDevTools (Disable-ODSWindowsOpenCodeLoginTask @base) 'explicit opt-out disables ODS task'
Assert-ODSDevTools ($script:disableCalls -eq 1 -and $script:mockTask.State -eq 'Disabled') 'ODS task became disabled'
Assert-ODSDevTools (Resolve-ODSWindowsDevToolsSelection @base -ExplicitEnable $true) 'explicit opt-in wins'
Assert-ODSDevTools (Resolve-ODSWindowsDevToolsSelection @base -All $true) 'All enables developer tools'
Assert-ODSDevTools (-not (Resolve-ODSWindowsDevToolsSelection @base -All $true -ExplicitDisable $true)) 'explicit opt-out wins over All'
$conflictRejected = $false
try {
    Resolve-ODSWindowsDevToolsSelection @base -ExplicitEnable $true -ExplicitDisable $true | Out-Null
} catch { $conflictRejected = $true }
Assert-ODSDevTools $conflictRejected 'contradictory flags fail before install phases'

$global:odsDevToolsTestMessages = New-Object 'System.Collections.Generic.List[string]'
function Write-Phase { param($Phase, $Total, $Name, $Estimate) }
function Write-AI { param([string]$Message) [void]$global:odsDevToolsTestMessages.Add($Message) }
$dryRun = $true
$cloudMode = $false
$tierConfig = @{LlmModel='fixture-model'}
$script:OPENCODE_VERSION = 'fixture-version'
$script:OPENCODE_EXE = 'C:\fixture\opencode.exe'
$script:OPENCODE_TASK_NAME = 'ODSOpenCodeWeb'
$script:ODS_AGENT_PORT = 7710
$script:ODS_AGENT_TASK_NAME = 'ODSHostAgent'
$phase = Join-Path $repoRoot 'installers/windows/phases/07-devtools.ps1'

$enableDevTools = $false
& $phase
Assert-ODSDevTools (@($global:odsDevToolsTestMessages | Where-Object { $_ -match 'skip OpenCode, Node.js, Claude Code, and Codex CLI' }).Count -eq 1) 'dry run omits developer tools'
Assert-ODSDevTools (@($global:odsDevToolsTestMessages | Where-Object { $_ -match 'Would install OpenCode' }).Count -eq 0) 'dry run does not promise OpenCode'
Assert-ODSDevTools (@($global:odsDevToolsTestMessages | Where-Object { $_ -match 'Would start ODS Host Agent' }).Count -eq 1) 'host agent remains selected'

$global:odsDevToolsTestMessages.Clear()
$enableDevTools = $true
& $phase
Assert-ODSDevTools (@($global:odsDevToolsTestMessages | Where-Object { $_ -match 'Would install OpenCode' }).Count -eq 1) 'dry run includes explicit OpenCode'
Assert-ODSDevTools (@($global:odsDevToolsTestMessages | Where-Object { $_ -match 'Claude Code \+ Codex CLI' }).Count -eq 1) 'dry run includes explicit npm tools'
Assert-ODSDevTools (@($global:odsDevToolsTestMessages | Where-Object { $_ -match 'Would start ODS Host Agent' }).Count -eq 1) 'host agent remains selected with tools'

$global:odsDevToolsTestMessages = $null
Write-Output 'PASS: Windows developer tools are opt-in and retained without affecting the host agent'
