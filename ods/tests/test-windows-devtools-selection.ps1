$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'installers/windows/lib/devtools-selection.ps1')

function Assert-ODSDevTools {
    param([bool]$Condition, [string]$Label)
    if (-not $Condition) { throw "FAIL: $Label" }
}

$script:mockTask = $null
$script:disableCalls = 0
$script:taskLookupFailure = $false
function Get-ScheduledTask {
    param([string]$TaskName, [string]$ErrorAction)
    if ($script:taskLookupFailure) { throw 'Scheduler access failed' }
    if ($null -eq $script:mockTask) {
        Write-Error 'Task not found' -ErrorId 'CmdletizationQuery_NotFound_TaskName' `
            -Category ObjectNotFound -ErrorAction Stop
    }
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
$script:taskLookupFailure = $true
$lookupRejected = $false
try { Resolve-ODSWindowsDevToolsSelection @base -ExplicitEnable $true | Out-Null } catch { $lookupRejected = $true }
Assert-ODSDevTools $lookupRejected 'scheduler lookup failure is not treated as task absence'
$script:taskLookupFailure = $false
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

# A fresh Core install must not rewrite an already installed OpenCode profile.
# Execute the installer's actual post-launch block with a mocked config writer.
$installerPath = Join-Path $repoRoot 'installers/windows/install-windows.ps1'
$installerText = Get-Content -LiteralPath $installerPath -Raw
$syncCall = $installerText.IndexOf('$opencodeSync = Sync-WindowsOpenCodeConfigFromEnv -InstallDir $installDir')
$syncStart = $installerText.LastIndexOf('if ($enableDevTools) {', $syncCall)
$syncEnd = $installerText.IndexOf('$windowsEnvMap = Get-WindowsODSEnvMap -InstallDir $installDir', $syncStart)
Assert-ODSDevTools ($syncStart -ge 0 -and $syncCall -ge $syncStart -and
    ($syncCall - $syncStart) -lt 120 -and $syncEnd -gt $syncCall) 'post-launch config guard is present'
$syncBlock = $installerText.Substring($syncStart, $syncEnd - $syncStart)
$script:syncCalls = 0
function Sync-WindowsOpenCodeConfigFromEnv {
    param($InstallDir, $GpuBackend, [switch]$UseLemonade, [switch]$CloudMode,
          $DefaultModelId, $DefaultModelName, $DefaultContextLimit, [switch]$SkipIfUnavailable)
    $script:syncCalls++
    return @{ Status = 'updated'; ModelName = 'fixture-model' }
}
function Write-AISuccess { param([string]$Message) }
$installDir = 'C:\fixture'
$gpuInfo = @{ Backend = 'nvidia' }
$useLemonade = $false
$tierConfig = @{ GgufFile = 'fixture.gguf'; LlmModel = 'fixture-model'; MaxContext = 8192 }
$enableDevTools = $false
Invoke-Expression $syncBlock
Assert-ODSDevTools ($script:syncCalls -eq 0) 'Core-only post-launch path leaves existing OpenCode config alone'
$enableDevTools = $true
Invoke-Expression $syncBlock
Assert-ODSDevTools ($script:syncCalls -eq 1) 'selected developer tools still refresh OpenCode config'
Assert-ODSDevTools ($installerText.Contains('export ODS_WINDOWS_DEVTOOLS_SELECTED="$($enableDevTools.ToString().ToLowerInvariant())"')) `
    'background upgrade wrapper receives selected DevTools choice'
$upgradeText = Get-Content -LiteralPath (Join-Path $repoRoot 'scripts/bootstrap-upgrade.sh') -Raw
Assert-ODSDevTools ($upgradeText.Contains('case "${ODS_WINDOWS_DEVTOOLS_SELECTED:-}" in')) `
    'background upgrade checks selected DevTools choice before config sync'

# The asynchronous model upgrade runs in a separate PowerShell process. Its
# explicit opt-out signal must also preserve an existing user config verbatim.
$updateScript = Join-Path $repoRoot 'scripts/update-windows-opencode-config.ps1'
$testProfile = Join-Path ([System.IO.Path]::GetTempPath()) ('ods-devtools-profile-' + [guid]::NewGuid().ToString('N'))
$configDir = Join-Path $testProfile '.config\opencode'
$previousProfile = $env:USERPROFILE
$previousSelection = $env:ODS_WINDOWS_DEVTOOLS_SELECTED
try {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    $primary = Join-Path $configDir 'opencode.json'
    $compat = Join-Path $configDir 'config.json'
    [System.IO.File]::WriteAllText($primary, '{"custom":"retain-primary"}')
    [System.IO.File]::WriteAllText($compat, '{"custom":"retain-compat"}')
    $beforePrimary = (Get-FileHash -LiteralPath $primary -Algorithm SHA256).Hash
    $beforeCompat = (Get-FileHash -LiteralPath $compat -Algorithm SHA256).Hash
    $env:USERPROFILE = $testProfile
    $env:ODS_WINDOWS_DEVTOOLS_SELECTED = 'false'
    & $updateScript -InstallDir $testProfile | Out-Null
    Assert-ODSDevTools ((Get-FileHash -LiteralPath $primary -Algorithm SHA256).Hash -eq $beforePrimary) `
        'asynchronous opt-out leaves OpenCode primary config unchanged'
    Assert-ODSDevTools ((Get-FileHash -LiteralPath $compat -Algorithm SHA256).Hash -eq $beforeCompat) `
        'asynchronous opt-out leaves OpenCode compatibility config unchanged'
} finally {
    $env:USERPROFILE = $previousProfile
    $env:ODS_WINDOWS_DEVTOOLS_SELECTED = $previousSelection
    Remove-Item -LiteralPath $testProfile -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output 'PASS: Windows developer tools are opt-in and retained without affecting the host agent'
