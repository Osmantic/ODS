# Retire-only recognition of ODS's former Lemonade Portal runtime (Round F).
#
# Until Round F the Windows Portal ran the AMD model through Lemonade Server.
# This file only recognizes, stops and retires what ODS itself created: the
# task ODSLemonadeRuntime-<SID> (or this user's legacy ODSLemonadeRuntime when
# its action matches a launcher ODS wrote), its proven process tree and its
# launcher files. It never runs msiexec, never touches Lemonade's install
# folders, cache, registry or configuration, and never stops a Lemonade that
# ODS cannot prove it started. Tasks ODS never owned (for example
# another tool's Lemonade runtime task) are not examined. Remove one release after Round F.
#
# The durable Lemonade launcher used the same portal-runtime files as the
# llama.cpp runtime (launch.ps1, runtime.json, intent.json). Every Lemonade
# task is therefore validated and stopped BEFORE the new files are written,
# and afterwards it is only unregistered when its identity is unchanged.
# Requires wsl-portal-amd.ps1 in scope.

$script:ODSPortalLemonadeLegacyTaskName = 'ODSLemonadeRuntime'
$script:ODSPortalLemonadeExecutableNames = @('lemonade-server.exe', 'LemonadeServer.exe')

function Get-ODSPortalLemonadeTaskName {
    return ('ODSLemonadeRuntime-' + (Get-ODSPortalUserSid))
}

function Get-ODSPortalLemonadeTasks {
    # This user's ODS Lemonade tasks: the per-user name first, then the legacy
    # name. Another user's legacy task is never adopted or changed.
    $currentName = Get-ODSPortalLemonadeTaskName
    $found = [Collections.Generic.List[object]]::new()
    foreach ($name in @($currentName, $script:ODSPortalLemonadeLegacyTaskName)) {
        $lookupErrors = @()
        $tasks = @(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue -ErrorVariable lookupErrors)
        $unexpected = @($lookupErrors | Where-Object { $_.CategoryInfo.Category -ne 'ObjectNotFound' })
        if ($unexpected.Count) {
            if ($name -eq $script:ODSPortalLemonadeLegacyTaskName -and
                -not @($unexpected | Where-Object { $_.CategoryInfo.Category -notin @('PermissionDenied', 'SecurityError') }).Count) {
                continue # An unreadable legacy task is not ours to adopt.
            }
            throw 'Cannot inspect the Portal Lemonade task; no process was changed.'
        }
        if (-not $tasks.Count) { continue }
        if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
            [string]::IsNullOrWhiteSpace([string]$tasks[0].Principal.UserId)) {
            throw 'The Portal Lemonade task identity is ambiguous.'
        }
        if ((Get-ODSPortalUserSid $tasks[0].Principal.UserId) -ne (Get-ODSPortalUserSid)) {
            if ($name -eq $currentName) { throw 'The Portal Lemonade task belongs to a different Windows user.' }
            continue
        }
        $found.Add($tasks[0])
    }
    return @($found)
}

function Get-ODSPortalLemonadeTask {
    return @(Get-ODSPortalLemonadeTasks) | Select-Object -First 1
}

function Get-ODSPortalLemonadeOwnedLaunch($Task) {
    <#
    .SYNOPSIS
        Validate one of the three launchers ODS wrote for Lemonade and return
        its executable, port and (for the durable launcher) saved model.
    .DESCRIPTION
        The executable comes from the task's own action or private plan, never
        from a search: a user-installed Lemonade elsewhere is not ODS's.
    #>
    $taskName = if ($Task.TaskName) { $Task.TaskName } else { Get-ODSPortalLemonadeTaskName }
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1) { throw 'The Portal Lemonade task has an unrecognized action; no process was stopped.' }
    $action = $actions[0]
    $runtimeDir = Get-ODSPortalRuntimeDir
    $actionExe = [string]$action.Execute
    $result = [ordered]@{ TaskName = $taskName; Action = $action; ActionExe = $actionExe; ExecutablePath = ''
        Port = 0; Durable = $false; Generation = ''; GgufFile = ''; ContextSize = 0
        UserId = [string]$Task.Principal.UserId; WasEnabled = ([string]$Task.State -ne 'Disabled') }
    if ([IO.Path]::IsPathRooted($actionExe) -and [IO.Path]::GetFileName($actionExe) -in $script:ODSPortalLemonadeExecutableNames) {
        # Lemonade before 10.7, started directly by the task.
        $pattern = '^serve --port (\d+) --host 127\.0\.0\.1 --no-tray --llamacpp vulkan --extra-models-dir "([^"]+)"(?: --ctx-size (\d+))?$'
        if ($action.Arguments -notmatch $pattern -or
            $Matches[2] -ine (Get-ODSPortalModelsDir) -or
            $action.WorkingDirectory -ine (Split-Path -Parent $actionExe)) {
            throw 'The existing Lemonade task does not match the ODS launch contract; no process was stopped.'
        }
        $result.ExecutablePath = $actionExe
        $result.Port = [int]$Matches[1]
        if ($Matches[3]) { $result.ContextSize = [int]$Matches[3] }
        $result.Generation = 'direct'
        return [pscustomobject]$result
    }
    $shells = @(Get-Command pwsh.exe, powershell.exe -CommandType Application -ErrorAction SilentlyContinue)
    $shell = @($shells | Where-Object { $_.Source -ieq $actionExe -or $_.Name -ieq $actionExe })
    $expectedArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $runtimeDir 'launch.ps1') + '"'
    $legacyPath = Join-Path (Get-ODSPortalStateDir) 'lemonade-launch.task.ps1'
    $legacyArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $legacyPath + '"'
    if ($shell.Count -ne 1 -or $action.Arguments -cnotin @($expectedArgs, $legacyArgs)) {
        throw 'The existing Lemonade task launcher is not recognized; no process was stopped.'
    }
    $result.ActionExe = $shell[0].Source
    if ($action.Arguments -ceq $expectedArgs) {
        # The durable launcher path is shared with the llama.cpp runtime; a
        # leftover task with this exact ODS action runs whichever plan is
        # saved there, so either plan executable proves the same ownership.
        $plan = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
        if ($action.WorkingDirectory -ine $runtimeDir -or -not [IO.Path]::IsPathRooted([string]$plan.ExecutablePath) -or
            [IO.Path]::GetFileName([string]$plan.ExecutablePath) -notin @($script:ODSPortalLemonadeExecutableNames + 'llama-server.exe') -or
            $plan.ModelsDir -ine (Get-ODSPortalModelsDir)) {
            throw 'The saved Portal Lemonade plan belongs to a different runtime; no process was stopped.'
        }
        $result.ExecutablePath = [string]$plan.ExecutablePath
        $result.Port = [int]$plan.Port
        $result.Durable = $true
        $result.Generation = 'durable'
        $result.GgufFile = [string]$plan.GgufFile
        if ($plan.ContextSize -is [int] -or $plan.ContextSize -is [long]) { $result.ContextSize = [int]$plan.ContextSize }
        return [pscustomobject]$result
    }
    # Migrate the former 10.7 wrapper by reading constant assignments,
    # never by dot-sourcing or evaluating its PowerShell contents.
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($legacyPath, [ref]$tokens, [ref]$parseErrors)
    $values = @{}
    foreach ($assignment in $ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -in @('exe', 'argumentString', 'workingDirectory')
    }, $true)) {
        $name = $assignment.Left.VariablePath.UserPath
        if ($assignment.Parent -ne $ast.EndBlock -or $values.ContainsKey($name) -or
            $assignment.Operator -ne [Management.Automation.Language.TokenKind]::Equals -or
            $assignment.Right -isnot [Management.Automation.Language.CommandExpressionAst] -or
            $assignment.Right.Expression -isnot [Management.Automation.Language.StringConstantExpressionAst]) {
            throw 'The former Lemonade launcher has nonconstant or ambiguous settings; no process was stopped.'
        }
        $values[$name] = $assignment.Right.Expression.Value
    }
    if ($parseErrors.Count -or $values.Count -ne 3 -or -not [IO.Path]::IsPathRooted([string]$values.exe) -or
        [IO.Path]::GetFileName([string]$values.exe) -notin $script:ODSPortalLemonadeExecutableNames -or
        $values.workingDirectory -ine (Split-Path -Parent $values.exe) -or
        $action.WorkingDirectory -ine $values.workingDirectory -or
        $values.argumentString -notmatch '^--port (\d+) --host 127\.0\.0\.1$') {
        throw 'The former Lemonade launcher does not match the ODS loopback contract; no process was stopped.'
    }
    $result.ExecutablePath = [string]$values.exe
    $result.Port = [int]$Matches[1]
    $result.Generation = 'wrapper'
    return [pscustomobject]$result
}

function Stop-ODSPortalLemonadeLaunch($Task, $Launch) {
    if ($Task.TaskPath -ne '\' -or [string]::IsNullOrWhiteSpace([string]$Task.Principal.UserId) -or
        (Get-ODSPortalUserSid $Task.Principal.UserId) -ne (Get-ODSPortalUserSid)) {
        throw 'The existing Portal Lemonade task is not uniquely owned by the current Windows user.'
    }
    Stop-ODSPortalTaskTree -Task $Task -TaskName $Launch.TaskName -Action $Launch.Action -ActionExe $Launch.ActionExe `
        -ExecutablePath $Launch.ExecutablePath -Port $Launch.Port -Durable $Launch.Durable
}

function Stop-ODSPortalLemonade([string]$ExecutablePath = '') {
    # Capture the task's actual process tree before Task Scheduler removes its
    # root. Matching an installation directory does not prove process ownership.
    $tasks = @(Get-ODSPortalLemonadeTask | Where-Object { $null -ne $_ })
    if ($tasks.Count -eq 0) { return } # First install preserves user-started servers.
    if ($tasks.Count -ne 1) { throw 'The existing Portal Lemonade task is not uniquely owned by the current Windows user.' }
    $launch = Get-ODSPortalLemonadeOwnedLaunch $tasks[0]
    if ($ExecutablePath -and $launch.ExecutablePath -ine $ExecutablePath) {
        throw 'The existing Lemonade task does not match the ODS launch contract; no process was stopped.'
    }
    Stop-ODSPortalLemonadeLaunch $tasks[0] $launch
}

function Get-ODSPortalLemonadeLaunches {
    # Validate every ODS Lemonade task before anything changes. One that does
    # not match a launcher ODS wrote stops setup with nothing changed.
    $launches = [Collections.Generic.List[object]]::new()
    foreach ($task in @(Get-ODSPortalLemonadeTasks)) {
        $launches.Add([pscustomobject]@{ Task = $task; Launch = (Get-ODSPortalLemonadeOwnedLaunch $task) })
    }
    return @($launches)
}

function Stop-ODSPortalLemonadeLaunches($Launches) {
    foreach ($entry in @($Launches)) { Stop-ODSPortalLemonadeLaunch $entry.Task $entry.Launch }
}

function Restore-ODSPortalLemonadeLaunches($Launches) {
    # Rollback: re-arm only the ODS tasks that were enabled before the cutover.
    # No Lemonade launch code runs here; the restored task starts its own.
    $primary = $null
    foreach ($entry in @($Launches)) {
        if (-not $entry.Launch.WasEnabled) { continue }
        Enable-ScheduledTask -TaskName $entry.Launch.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
        if (-not $primary) { $primary = $entry.Launch.TaskName }
    }
    if ($primary) {
        Set-ODSPortalRuntimeIntent 'running'
        Start-ScheduledTask -TaskName $primary -TaskPath '\' -ErrorAction Stop
    }
}

function Unregister-ODSPortalLemonadeLaunches($Launches) {
    # The tasks were validated and stopped before the cutover. Unregister each
    # only while it is still that exact, idle task. Returns how many remain.
    $remaining = 0
    foreach ($entry in @($Launches)) {
        $launch = $entry.Launch
        $current = @(Get-ScheduledTask -TaskName $launch.TaskName -TaskPath '\' -ErrorAction SilentlyContinue)
        if (-not $current.Count) { continue }
        $action = @($current[0].Actions)
        if ($current.Count -ne 1 -or $action.Count -ne 1 -or $current[0].Principal.UserId -ne $launch.UserId -or
            $action[0].Execute -ne $launch.Action.Execute -or $action[0].Arguments -cne $launch.Action.Arguments -or
            $action[0].WorkingDirectory -ne $launch.Action.WorkingDirectory -or
            (Get-ODSPortalTaskEngineId $launch.TaskName) -gt 0) {
            Write-Host "         The former task $($launch.TaskName) changed or restarted after ODS stopped it, so ODS left it registered."
            $remaining++
            continue
        }
        Unregister-ScheduledTask -TaskName $launch.TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
    }
    return $remaining
}

function Get-ODSPortalLemonadeInstallRecord {
    # Evidence that ODS itself ran the Lemonade MSI (for the one-time notice).
    # It cannot show whether the user also uses that Lemonade, so ODS never
    # uninstalls it.
    $log = Join-Path (Get-ODSPortalStateDir) 'lemonade-msi-install.log'
    if (-not (Test-Path -LiteralPath $log -PathType Leaf)) { return $null }
    $version = ''
    try {
        $text = Get-Content -LiteralPath $log -Raw -ErrorAction Stop
        $match = [regex]::Match([string]$text, 'ProductVersion\s*=\s*([0-9]+(?:\.[0-9]+){1,3})')
        if ($match.Success) { $version = $match.Groups[1].Value }
    } catch { }
    return [pscustomobject]@{ Version = $version; Date = (Get-Item -LiteralPath $log).LastWriteTime.ToString('yyyy-MM-dd') }
}

function Show-ODSPortalLemonadeNotice {
    # Once per Windows user: a later rerun stays quiet.
    $marker = Join-Path (Get-ODSPortalStateDir) 'lemonade-retired.json'
    if (Test-Path -LiteralPath $marker -PathType Leaf) { return }
    $record = Get-ODSPortalLemonadeInstallRecord
    if ($record) {
        $version = if ($record.Version) { " $($record.Version)" } else { '' }
        Write-Host "         ODS installed Lemonade Server$version on $($record.Date) and no longer uses it. If you do not use it yourself, uninstall it from Settings > Apps. ODS left it unchanged."
    } else {
        Write-Host '         ODS stopped using your Lemonade Server; it was left unchanged.'
    }
    Write-ODSPrivateEnvFile -Path $marker -Content (@{ RetiredAt = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json -Compress)
}

function Complete-ODSPortalLemonadeRetirement($Launches) {
    <#
    .SYNOPSIS
        After llama.cpp proved its model: retire the ODS-owned Lemonade tasks,
        remove launcher files only ODS wrote, and show the one-time notice.
        Logs and the portal-runtime backup are kept.
    #>
    $remaining = Unregister-ODSPortalLemonadeLaunches $Launches
    if ($remaining -eq 0) {
        # A task left registered keeps the files it would run.
        $runtimeDir = Get-ODSPortalRuntimeDir
        foreach ($leftover in @((Join-Path (Get-ODSPortalStateDir) 'lemonade-launch.task.ps1'),
                (Join-Path $runtimeDir 'backend-contract.ps1'), (Join-Path $runtimeDir 'env-generator.ps1'))) {
            if (Test-Path -LiteralPath $leftover -PathType Leaf) { Remove-Item -LiteralPath $leftover -Force }
        }
    }
    Show-ODSPortalLemonadeNotice
}
