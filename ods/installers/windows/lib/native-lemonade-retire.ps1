# ============================================================================
# ODS Windows Installer -- retire the legacy native Lemonade runtime (Round F)
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Migration-only. Until Round F the native (non-Portal) installer ran
#          Lemonade Server through the task ODSLemonadeRuntime. This file
#          recognizes that task only when its action is a launcher ODS wrote
#          (the direct "serve" contract on this install's port and models
#          folder, or the <install>\logs\lemonade-launch.task.ps1 wrapper),
#          stops only processes proven to belong to it, and unregisters it
#          after llama-server proved its model.
#
# Never: msiexec, Lemonade's install folder, cache, configuration or
# registry, a kill by executable folder or name, or a task ODS did not write
# (another tool's Lemonade task included). Remove one release after Round F.
# Requires native-llama-runtime.ps1 (process-tree helpers) in scope.
# ============================================================================

$script:ODSLegacyLemonadeTaskName = 'ODSLemonadeRuntime'
$script:ODSLegacyLemonadeExecutableNames = @('lemonade-server.exe', 'LemonadeServer.exe')

function Get-ODSLegacyCurrentUserSid {
    return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}

function ConvertTo-ODSLegacyPrincipalSid([string]$UserId) {
    if ([string]::IsNullOrWhiteSpace($UserId)) { return '' }
    try {
        if ($UserId -match '^S-1-') { return (New-Object Security.Principal.SecurityIdentifier($UserId)).Value }
        return (New-Object Security.Principal.NTAccount($UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch { return '' }
}

function Read-ODSLegacyLemonadeWrapper([string]$Path) {
    # Constant assignments only; the wrapper is never dot-sourced or evaluated.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { return $null }
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
            return $null
        }
        $values[$name] = $assignment.Right.Expression.Value
    }
    if ($values.Count -ne 3) { return $null }
    return [pscustomobject]$values
}

function Get-ODSLegacyLemonadeRuntime {
    <#
    .SYNOPSIS
        The ODS-owned legacy Lemonade task, or a reason it is not ODS's.
    .OUTPUTS
        $null when no ODSLemonadeRuntime task exists. Otherwise Owned (bool)
        and Reason; when owned also Task, Action, ActionExe, ExecutablePath,
        Generation and the exact command lines its processes run.
    #>
    param([Parameter(Mandatory = $true)][string]$InstallDir, [Parameter(Mandatory = $true)][int]$Port)
    $lookupErrors = @()
    $tasks = @(Get-ScheduledTask -TaskName $script:ODSLegacyLemonadeTaskName -TaskPath '\' -ErrorAction SilentlyContinue -ErrorVariable lookupErrors)
    if (@($lookupErrors | Where-Object { $_.CategoryInfo.Category -ne 'ObjectNotFound' }).Count) {
        return [pscustomobject]@{ Owned = $false; Reason = 'the task cannot be read' }
    }
    if (-not $tasks.Count) { return $null }
    $notOurs = { param([string]$Why) [pscustomobject]@{ Owned = $false; Reason = $Why } }
    if ($tasks.Count -ne 1 -or (ConvertTo-ODSLegacyPrincipalSid ([string]$tasks[0].Principal.UserId)) -ne (Get-ODSLegacyCurrentUserSid)) {
        return (& $notOurs 'it belongs to another Windows user or is ambiguous')
    }
    $task = $tasks[0]
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { return (& $notOurs 'its action is not one ODS wrote') }
    $action = $actions[0]
    $modelsDir = Join-Path (Join-Path $InstallDir 'data') 'models'
    $actionExe = [string]$action.Execute
    $serve = '^serve --port (\d+) --host 127\.0\.0\.1 --no-tray --llamacpp vulkan --extra-models-dir "([^"]+)"(?: --ctx-size \d+)?$'
    if ([IO.Path]::IsPathRooted($actionExe) -and [IO.Path]::GetFileName($actionExe) -in $script:ODSLegacyLemonadeExecutableNames) {
        if ([string]$action.Arguments -notmatch $serve -or [int]$Matches[1] -ne $Port -or $Matches[2] -ine $modelsDir -or
            [string]$action.WorkingDirectory -ine (Split-Path -Parent $actionExe)) {
            return (& $notOurs 'its Lemonade command does not match the ODS launch contract for this installation')
        }
        return [pscustomobject]@{ Owned = $true; Reason = ''; Task = $task; Action = $action; ActionExe = $actionExe
            ExecutablePath = $actionExe; Generation = 'direct'
            CommandLines = @(('"' + $actionExe + '" ' + $action.Arguments), ($actionExe + ' ' + $action.Arguments)) }
    }
    $wrapperPath = Join-Path (Join-Path $InstallDir 'logs') 'lemonade-launch.task.ps1'
    $wrapperArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $wrapperPath + '"'
    $shells = @(Get-Command pwsh.exe, powershell.exe -CommandType Application -ErrorAction SilentlyContinue)
    $shell = @($shells | Where-Object { $_.Source -ieq $actionExe -or $_.Name -ieq $actionExe })
    if ($shell.Count -ne 1 -or [string]$action.Arguments -cne $wrapperArgs) {
        return (& $notOurs 'its action is not one ODS wrote')
    }
    $wrapper = Read-ODSLegacyLemonadeWrapper $wrapperPath
    if (-not $wrapper -or -not [IO.Path]::IsPathRooted([string]$wrapper.exe) -or
        [IO.Path]::GetFileName([string]$wrapper.exe) -notin $script:ODSLegacyLemonadeExecutableNames -or
        [string]$wrapper.workingDirectory -ine (Split-Path -Parent ([string]$wrapper.exe)) -or
        [string]$action.WorkingDirectory -ine [string]$wrapper.workingDirectory) {
        return (& $notOurs 'its launcher script was changed or is not one ODS wrote')
    }
    $loopback = '^--port (\d+) --host 127\.0\.0\.1$'
    $argumentString = [string]$wrapper.argumentString
    if (($argumentString -match $loopback -and [int]$Matches[1] -eq $Port) -or
        ($argumentString -match $serve -and [int]$Matches[1] -eq $Port -and $Matches[2] -ieq $modelsDir)) {
        return [pscustomobject]@{ Owned = $true; Reason = ''; Task = $task; Action = $action; ActionExe = $shell[0].Source
            ExecutablePath = [string]$wrapper.exe; Generation = 'wrapper'; WrapperPath = $wrapperPath
            CommandLines = @(('"' + [string]$wrapper.exe + '" ' + $argumentString), ([string]$wrapper.exe + ' ' + $argumentString)) }
    }
    return (& $notOurs 'its launcher does not serve this installation''s port on 127.0.0.1')
}

function Get-ODSLegacyTaskEngineId([string]$TaskName) {
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $instances = @($scheduler.GetFolder('\').GetTask($TaskName).GetInstances(0))
    if ($instances.Count -gt 1) { throw 'More than one ODSLemonadeRuntime instance is running; ownership is ambiguous.' }
    if ($instances.Count -eq 1) { return [int]$instances[0].EnginePID }
    return 0
}

function Stop-ODSLegacyLemonadeRuntime {
    <#
    .SYNOPSIS
        Disable the recognized ODS Lemonade task and stop only the process tree
        it proves: its running task instance, or the PID-file process whose
        executable and exact command line are the task's own.
    .OUTPUTS
        Stopped (process count) and Left (a message when a process on the port
        could not be proven ODS's; it is never stopped).
    #>
    param([Parameter(Mandatory = $true)]$Runtime, [string]$PidFile, [int]$Port)
    Disable-ScheduledTask -TaskName $script:ODSLegacyLemonadeTaskName -TaskPath '\' -ErrorAction Stop | Out-Null
    $nodes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $engineId = Get-ODSLegacyTaskEngineId $script:ODSLegacyLemonadeTaskName
    $root = $null
    if ($engineId -gt 0) {
        $taskLines = @(('"' + $Runtime.ActionExe + '" ' + $Runtime.Action.Arguments), ($Runtime.ActionExe + ' ' + $Runtime.Action.Arguments))
        $candidates = @($nodes | Where-Object {
            $_.ExecutablePath -ieq $Runtime.ActionExe -and $_.CommandLine -cin $taskLines -and
            ($_.ProcessId -eq $engineId -or $_.ParentProcessId -eq $engineId)
        })
        if ($candidates.Count -ne 1) { throw 'Cannot prove the running ODSLemonadeRuntime process; no process was stopped.' }
        $root = $candidates[0]
    } elseif ($PidFile -and (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        $raw = ([string](Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue)).Trim()
        if ($raw -match '^\d+$') {
            $saved = @($nodes | Where-Object { $_.ProcessId -eq [int]$raw })
            if ($saved.Count -eq 1 -and $saved[0].ExecutablePath -ieq $Runtime.ExecutablePath -and $saved[0].CommandLine -cin $Runtime.CommandLines) {
                $root = $saved[0]
            }
        }
    }
    $stopped = 0
    if ($root) {
        $owned = Get-ODSPortalOwnedProcessTree @($root) $nodes
        try {
            if ($engineId -gt 0) { Stop-ScheduledTask -TaskName $script:ODSLegacyLemonadeTaskName -TaskPath '\' -ErrorAction Stop }
            Stop-ODSPortalOwnedProcesses $owned.Handles
            $stopped = @($owned.Handles).Count
        } finally {
            foreach ($process in $owned.Handles) { $process.Dispose() }
        }
    }
    $left = ''
    $listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) | Select-Object -First 1
    if ($listener) {
        $owner = @($nodes | Where-Object { $_.ProcessId -eq [int]$listener.OwningProcess })
        $name = if ($owner.Count) { [string]$owner[0].Name } else { "process $($listener.OwningProcess)" }
        $left = "Port $Port is still used by $name, which ODS cannot prove it started, so ODS left it running. Close it (or set AMD_INFERENCE_PORT to a free port), then rerun the installer."
    }
    return [pscustomobject]@{ Stopped = $stopped; Left = $left }
}

function Complete-ODSLegacyLemonadeRetirement {
    <#
    .SYNOPSIS
        After llama-server proved its model: unregister the recognized task
        while it is still that exact idle task, remove the launcher script ODS
        wrote, and show a one-time notice. Logs are kept.
    #>
    param([Parameter(Mandatory = $true)]$Runtime, [Parameter(Mandatory = $true)][string]$InstallDir)
    $current = @(Get-ScheduledTask -TaskName $script:ODSLegacyLemonadeTaskName -TaskPath '\' -ErrorAction SilentlyContinue)
    if ($current.Count -eq 1) {
        $action = @($current[0].Actions)
        if ($action.Count -eq 1 -and $action[0].Execute -eq $Runtime.Action.Execute -and $action[0].Arguments -ceq $Runtime.Action.Arguments -and
            $action[0].WorkingDirectory -eq $Runtime.Action.WorkingDirectory -and (Get-ODSLegacyTaskEngineId $script:ODSLegacyLemonadeTaskName) -eq 0) {
            Unregister-ScheduledTask -TaskName $script:ODSLegacyLemonadeTaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
            if ($Runtime.Generation -eq 'wrapper' -and (Test-Path -LiteralPath $Runtime.WrapperPath -PathType Leaf)) {
                Remove-Item -LiteralPath $Runtime.WrapperPath -Force
            }
        } else {
            Write-AIWarn "The former ODSLemonadeRuntime task changed or restarted after ODS stopped it, so ODS left it registered (disabled)."
        }
    }
    $marker = Join-Path (Join-Path $InstallDir 'data') 'lemonade-retired.json'
    if (Test-Path -LiteralPath $marker -PathType Leaf) { return }
    $msiLog = Join-Path (Join-Path $InstallDir 'logs') 'lemonade-msi-install.log'
    if (Test-Path -LiteralPath $msiLog -PathType Leaf) {
        $version = ''
        try {
            $match = [regex]::Match([string](Get-Content -LiteralPath $msiLog -Raw -ErrorAction Stop), 'ProductVersion\s*=\s*([0-9]+(?:\.[0-9]+){1,3})')
            if ($match.Success) { $version = ' ' + $match.Groups[1].Value }
        } catch { }
        $date = (Get-Item -LiteralPath $msiLog).LastWriteTime.ToString('yyyy-MM-dd')
        Write-AI "ODS installed Lemonade Server$version on $date and no longer uses it. If you do not use it yourself, uninstall it from Settings > Apps. ODS left it unchanged."
    } else {
        Write-AI 'ODS stopped using Lemonade Server; your Lemonade installation was left unchanged.'
    }
    Write-ODSPrivateEnvFile -Path $marker -Content (@{ RetiredAt = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json -Compress)
}
