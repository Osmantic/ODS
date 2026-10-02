# Source-only transaction fixtures. No task, process, network, or product host is changed.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-amd.ps1')
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-lemonade-upgrade-' + [guid]::NewGuid().ToString('N'))
$oldLocal = $env:LOCALAPPDATA
$script:taskName = 'ODSLemonadeRuntime-S-1-5-21-fixture'
$script:oldXml = '<Task><Settings><Enabled>true</Enabled></Settings><Actions><Exec><Command>old</Command></Exec></Actions></Task>'
$script:currentXml = $script:oldXml
$script:enabled = $true
$script:stops = [Collections.Generic.List[string]]::new()
$script:calls = [Collections.Generic.List[string]]::new()
$script:failReadyOnce = $false
function Get-ODSPortalLemonadeTaskName { return $script:taskName }
function Get-ODSPortalLemonadeTask {
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    return [pscustomobject]@{ TaskName = $script:taskName
        Actions = @([pscustomobject]@{ Arguments = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $runtimeDir 'launch.ps1') + '"')
            WorkingDirectory = $runtimeDir })
        Settings = [pscustomobject]@{ Enabled = $script:enabled } }
}
function Export-ScheduledTask { [CmdletBinding()] param($TaskName, $TaskPath) return $script:currentXml }
function Disable-ScheduledTask { [CmdletBinding()] param($TaskName, $TaskPath) $script:enabled = $false; $script:calls.Add('disable') }
function Enable-ScheduledTask { [CmdletBinding()] param($TaskName, $TaskPath) $script:enabled = $true; $script:calls.Add('enable') }
function Register-ScheduledTask { [CmdletBinding()] param($TaskName, $TaskPath, $Xml, [switch]$Force) $script:currentXml = $Xml; $script:calls.Add('register') }
function Start-ScheduledTask { [CmdletBinding()] param($TaskName, $TaskPath) $script:calls.Add('start') }
function Stop-ODSPortalLemonade([string]$ExecutablePath) { $script:stops.Add($ExecutablePath); $script:calls.Add('stop') }
function Wait-ODSPortalLemonadeReady($Registration) {
    $script:calls.Add('ready')
    if ($script:failReadyOnce) { $script:failReadyOnce = $false; throw 'fixture readiness timeout' }
    return 'model'
}
function Write-ODSPrivateEnvFile([string]$Path, [string]$Content) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}
function Check([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message }; Write-Output "PASS $Message" }
try {
    New-Item -ItemType Directory -Path $fixture -ErrorAction Stop | Out-Null
    $env:LOCALAPPDATA = $fixture
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $oldExe = Join-Path $fixture 'old.exe'
    $newExe = Join-Path $fixture 'new.exe'
    [IO.File]::WriteAllText($oldExe, 'old')
    [IO.File]::WriteAllText($newExe, 'new')
    $oldPlan = @{ ExecutablePath = $oldExe; ModelsDir = (Join-Path (Get-ODSPortalStateDir) 'models'); Port = 8080; ContextSize = 65536 } | ConvertTo-Json -Compress
    Write-ODSPrivateEnvFile (Join-Path $runtimeDir 'runtime.json') $oldPlan
    Write-ODSPrivateEnvFile (Join-Path $runtimeDir 'launch.ps1') 'old launcher'
    Write-ODSPrivateEnvFile (Join-Path $runtimeDir 'intent.json') '{"State":"running"}'
    $task = Get-ODSPortalLemonadeTask
    Check ((Get-ODSPortalDurableTaskExecutable $task) -eq $oldExe) 'old durable plan resolves only its recorded executable'
    $null = Save-ODSPortalLemonadeUpgrade $task $oldExe $newExe
    $savedPlan = ((Get-Content (Get-ODSPortalLemonadeUpgradeJournalPath) -Raw | ConvertFrom-Json).Files | Where-Object { $_.Name -eq 'runtime.json' })[0].Content
    Check ($savedPlan -eq $oldPlan) 'journal preserves the old plan before any stop'
    Restore-ODSPortalLemonadeUpgrade
    Check ($script:calls.Count -eq 0 -and -not (Test-Path (Get-ODSPortalLemonadeUpgradeJournalPath))) 'untouched preflight journal clears without stopping or restarting old service'

    $null = Save-ODSPortalLemonadeUpgrade $task $oldExe $newExe
    $script:enabled = $false
    $script:currentXml = $script:oldXml.Replace('<Enabled>true</Enabled>', '<Enabled>false</Enabled>')
    Write-ODSPrivateEnvFile (Join-Path $runtimeDir 'intent.json') '{"State":"stopped"}'
    Restore-ODSPortalLemonadeUpgrade
    Check ($script:calls -join ',' -eq 'disable,register,enable,start,ready' -and
        (Get-Content (Join-Path $runtimeDir 'runtime.json') -Raw) -eq $oldPlan -and
        -not (Test-Path (Get-ODSPortalLemonadeUpgradeJournalPath))) 'interrupted stop restores old task, plan, and running model'

    $script:calls.Clear()
    $null = Save-ODSPortalLemonadeUpgrade (Get-ODSPortalLemonadeTask) $oldExe $newExe
    $script:currentXml = '<Task><Settings><Enabled>true</Enabled></Settings><Actions><Exec><Command>new</Command></Exec></Actions></Task>'
    Write-ODSPrivateEnvFile (Join-Path $runtimeDir 'runtime.json') (@{ ExecutablePath = $newExe } | ConvertTo-Json -Compress)
    Restore-ODSPortalLemonadeUpgrade
    Check ($script:stops.Count -eq 1 -and $script:stops[0] -eq $newExe -and
        $script:calls -join ',' -eq 'stop,register,enable,start,ready') 'failed new task is stopped by exact new executable before old task restoration'

    $script:calls.Clear()
    $null = Save-ODSPortalLemonadeUpgrade (Get-ODSPortalLemonadeTask) $oldExe $newExe
    $script:enabled = $false
    Write-ODSPrivateEnvFile (Join-Path $runtimeDir 'intent.json') '{"State":"stopped"}'
    $script:failReadyOnce = $true
    $message = ''
    try { Restore-ODSPortalLemonadeUpgrade } catch { $message = $_.Exception.Message }
    $pending = Get-Content (Get-ODSPortalLemonadeUpgradeJournalPath) -Raw | ConvertFrom-Json
    Check ($message -match 'readiness timeout' -and $pending.Phase -eq 'recovering' -and
        (Test-Path (Get-ODSPortalLemonadeUpgradeJournalPath))) 'failed readiness retains a recovering journal'
    $script:calls.Clear()
    Restore-ODSPortalLemonadeUpgrade
    Check ($script:calls -join ',' -eq 'disable,register,enable,start,ready' -and
        -not (Test-Path (Get-ODSPortalLemonadeUpgradeJournalPath))) 'retry proves old model readiness before clearing journal'

    $script:calls.Clear()
    $script:currentXml = '<Task><Actions><Exec><Command>foreign</Command></Exec></Actions></Task>'
    $message = ''
    try { $null = Register-ODSPortalLemonadeTask ([pscustomobject]@{ ExecutablePath = $newExe }) '' '' '' $true $script:oldXml }
    catch { $message = $_.Exception.Message }
    Check ($message -match 'changed after' -and $script:calls.Count -eq 0) 'concurrent task replacement is refused before overwrite'
    $script:currentXml = $script:oldXml

    $wrongTask = [pscustomobject]@{ TaskName = 'foreign'; Actions = $task.Actions; Settings = $task.Settings }
    $message = ''
    try { $null = Get-ODSPortalDurableTaskExecutable $wrongTask } catch { $message = $_.Exception.Message }
    Check ($message -match 'legacy or ambiguous' -and $script:calls.Count -eq 0) 'foreign task identity is refused before mutation'
} finally {
    $env:LOCALAPPDATA = $oldLocal
    $actual = [IO.Path]::GetFullPath($fixture)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $actual.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($actual) -notmatch '^ods-lemonade-upgrade-[0-9a-f]{32}$') {
        throw 'Unexpected upgrade fixture cleanup path.'
    }
    if (Test-Path -LiteralPath $actual) { Remove-Item -LiteralPath $actual -Recurse -Force }
}
