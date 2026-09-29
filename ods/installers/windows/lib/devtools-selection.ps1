# Developer tools are optional on a fresh native Windows install. An enabled
# ODS-owned OpenCode login task records the legacy installer's prior selection.
# A disabled task is an owner decision and must not be re-enabled by a rerun.
function Test-ODSWindowsOpenCodeTaskOwned {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][string]$ExpectedLauncher
    )
    foreach ($action in @($Task.Actions)) {
        if ($null -eq $action -or [string]::IsNullOrWhiteSpace([string]$action.Execute)) {
            continue
        }
        if ((Split-Path -Leaf ([string]$action.Execute)) -ieq 'powershell.exe' -and
            [string]$action.Arguments -and
            ([string]$action.Arguments).IndexOf(
                $ExpectedLauncher,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -ge 0) {
            return $true
        }
    }
    return $false
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

    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } catch {
        $task = $null
    }
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
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } catch {
        return $false
    }
    if ($null -eq $task -or
        -not (Test-ODSWindowsOpenCodeTaskOwned -Task $task -ExpectedLauncher $ExpectedLauncher)) {
        return $false
    }
    if ([string]$task.State -eq 'Disabled') { return $true }
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
    return $true
}
