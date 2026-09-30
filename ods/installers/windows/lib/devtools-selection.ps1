# Developer tools are optional on a fresh native Windows install. An enabled
# ODS-owned OpenCode login task records the legacy installer's prior selection.
# A disabled task is an owner decision and must not be re-enabled by a rerun.
function Get-ODSWindowsOpenCodeTask {
    param([Parameter(Mandatory = $true)][string]$TaskName)
    try {
        return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } catch {
        # Missing is a normal first-install state. Permission and scheduler
        # errors are not proof of absence; refuse to overwrite an unknown task.
        if ($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::ObjectNotFound -and
            $_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound_TaskName,*') {
            return $null
        }
        throw
    }
}

function Test-ODSWindowsOpenCodeTaskOwned {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][string]$ExpectedLauncher
    )
    # The installer owns exactly one fixed action. A substring match would
    # accept a foreign task that mentions this launcher while executing a
    # different command, and -NoDevTools could then disable that task.
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1 -or $null -eq $actions[0]) { return $false }
    $action = $actions[0]
    $expectedArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ExpectedLauncher + '"'
    $expectedDirectory = Split-Path -Parent $ExpectedLauncher
    return (([string]$action.Execute) -ieq 'powershell.exe' -and
        ([string]$action.Arguments) -ieq $expectedArguments -and
        ([string]$action.WorkingDirectory) -ieq $expectedDirectory)
}

function Resolve-ODSWindowsDevToolsSelection {
    param(
        [bool]$ExplicitEnable,
        [bool]$ExplicitDisable,
        [bool]$All,
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][string]$ExpectedLauncher
    )

    if ($ExplicitEnable -and $ExplicitDisable) {
        throw '-DevTools and -NoDevTools cannot be used together.'
    }
    if ($ExplicitDisable) { return $false }

    $task = Get-ODSWindowsOpenCodeTask -TaskName $TaskName
    if ($ExplicitEnable -or $All) {
        if ($null -ne $task -and
            -not (Test-ODSWindowsOpenCodeTaskOwned -Task $task -ExpectedLauncher $ExpectedLauncher)) {
            throw 'An existing non-ODS scheduled task uses the OpenCode task name.'
        }
        return $true
    }
    if ($null -eq $task -or [string]$task.State -notin @('Ready', 'Running', 'Queued')) {
        return $false
    }
    return (Test-ODSWindowsOpenCodeTaskOwned -Task $task -ExpectedLauncher $ExpectedLauncher)
}

# Explicit -NoDevTools also turns off the ODS login task. Leave binaries and
# an already running user session untouched; the owner can opt in again later.
function Disable-ODSWindowsOpenCodeLoginTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][string]$ExpectedLauncher
    )
    $task = Get-ODSWindowsOpenCodeTask -TaskName $TaskName
    if ($null -eq $task -or
        -not (Test-ODSWindowsOpenCodeTaskOwned -Task $task -ExpectedLauncher $ExpectedLauncher)) {
        return $false
    }
    if ([string]$task.State -eq 'Disabled') { return $true }
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
    return $true
}
