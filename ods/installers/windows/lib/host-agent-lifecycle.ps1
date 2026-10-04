function Test-ODSUninstallPathOwned {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    try {
        if (-not [IO.Path]::IsPathRooted($Path)) { return $false }
        $root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
        $actual = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
        return $actual.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
            $actual.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Resolve-ODSUninstallLiteral {
    param($Node, $Assignments, [int]$Before, [int]$Depth=0)
    if ($Depth -gt 12) { throw 'Unknown launcher expression' }
    $next=$Depth+1
    if ($Node -is [Management.Automation.Language.StringConstantExpressionAst]) { return [string]$Node.Value }
    if ($Node -is [Management.Automation.Language.VariableExpressionAst]) {
        $name=$Node.VariablePath.UserPath
        if (-not $Assignments.ContainsKey($name)) { throw 'Unknown launcher variable' }
        $assignment=$Assignments[$name]
        if ($assignment.Right.Extent.EndOffset -ge $Before) { throw 'Ambiguous launcher assignment' }
        return Resolve-ODSUninstallLiteral $assignment.Right $Assignments $assignment.Extent.StartOffset $next
    }
    if ($Node -is [Management.Automation.Language.CommandExpressionAst]) { return Resolve-ODSUninstallLiteral $Node.Expression $Assignments $Before $next }
    if ($Node -is [Management.Automation.Language.ParenExpressionAst]) { return Resolve-ODSUninstallLiteral $Node.Pipeline $Assignments $Before $next }
    if ($Node -is [Management.Automation.Language.ArrayExpressionAst]) { return Resolve-ODSUninstallLiteral $Node.SubExpression $Assignments $Before $next }
    if ($Node -is [Management.Automation.Language.PipelineAst] -and $Node.PipelineElements.Count -eq 1) { return Resolve-ODSUninstallLiteral $Node.PipelineElements[0] $Assignments $Before $next }
    if ($Node -is [Management.Automation.Language.StatementBlockAst]) {
        foreach ($statement in $Node.Statements) { Resolve-ODSUninstallLiteral $statement $Assignments $Before $next }
        return
    }
    if ($Node -is [Management.Automation.Language.ArrayLiteralAst]) {
        foreach ($element in $Node.Elements) { Resolve-ODSUninstallLiteral $element $Assignments $Before $next }
        return
    }
    if ($Node -is [Management.Automation.Language.BinaryExpressionAst] -and $Node.Operator -eq 'Plus' -and
        $Node.Left -is [Management.Automation.Language.ArrayExpressionAst] -and
        $Node.Right -is [Management.Automation.Language.ArrayExpressionAst]) {
        Resolve-ODSUninstallLiteral $Node.Left $Assignments $Before $next
        Resolve-ODSUninstallLiteral $Node.Right $Assignments $Before $next
        return
    }
    throw 'Unknown launcher expression'
}

function Test-ODSUninstallCommandOwned {
    param([string]$CommandLine, [string]$Executable='', [switch]$ArgumentsOnly, [int]$Depth=0)
    if (-not $CommandLine -or $Depth -gt 2) { return $false }
    $argv=@([regex]::Matches($CommandLine, '"([^"\r\n]*)"|[^\s"]+') | ForEach-Object { $_.Value.Trim('"') })
    if (-not $ArgumentsOnly) {
        if (-not $argv.Count) { return $false }
        if (-not $Executable) { $Executable=$argv[0] }
        $argv=@($argv | Select-Object -Skip 1)
    }
    $program=($Executable -split '[\\/]')[-1]
    if ($program -match '^(powershell|pwsh)(\.exe)?$') {
        for ($index=0; $index -lt $argv.Count; $index++) {
            $arg=$argv[$index]
            if ($arg -in @('-File','-f')) {
                return ($index+1 -lt $argv.Count -and (Test-ODSUninstallPathOwned $argv[$index+1]))
            }
            if ($arg -in @('-EncodedCommand','-enc','-e')) {
                if ($index+1 -ge $argv.Count) { return $false }
                try {
                    $text=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($argv[$index+1]))
                    $errors=$null; $tokens=$null
                    $ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
                    if ($errors.Count) { return $false }
                    if ($ast.ParamBlock -or $ast.BeginBlock -or $ast.ProcessBlock -or $ast.EndBlock.Traps.Count) { return $false }
                    # Recognize the generated launcher without evaluating it.
                    # Nested/deferred commands, aliases and mixed launchers do
                    # not establish ownership of the scheduled task.
                    $assignments=@{}; $launchers=@()
                    foreach ($statement in $ast.EndBlock.Statements) {
                        if ($statement -is [Management.Automation.Language.AssignmentStatementAst]) {
                            if ($statement.Operator -ne 'Equals' -or $statement.Left -isnot [Management.Automation.Language.VariableExpressionAst]) { return $false }
                            if (@($statement.Right.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst]},$true)).Count) { return $false }
                            $name=$statement.Left.VariablePath.UserPath
                            if ($assignments.ContainsKey($name)) { return $false }
                            $assignments[$name]=$statement
                            continue
                        }
                        if ($statement -isnot [Management.Automation.Language.PipelineAst] -or $statement.PipelineElements.Count -ne 1 -or
                            $statement.PipelineElements[0] -isnot [Management.Automation.Language.CommandAst]) { return $false }
                        $command=$statement.PipelineElements[0]
                        if ($command.GetCommandName() -eq 'Set-Location') { continue }
                        if ($command.GetCommandName() -ne 'Start-Process') { return $false }
                        $launchers+=,$command
                    }
                    if ($launchers.Count -ne 1) { return $false }
                    foreach ($command in $launchers) {
                        $file=$null; $arguments=$null
                        $elements=$command.CommandElements
                        for ($i=1; $i -lt $elements.Count; $i++) {
                            $element=$elements[$i]
                            if ($element -is [Management.Automation.Language.CommandParameterAst]) {
                                $value=$element.Argument
                                if (-not $value -and $i+1 -lt $elements.Count -and $elements[$i+1] -isnot [Management.Automation.Language.CommandParameterAst]) { $value=$elements[$i+1] }
                                if ($element.ParameterName -eq 'FilePath') { $file=$value }
                                if ($element.ParameterName -eq 'ArgumentList') { $arguments=$value }
                            } elseif ($i -eq 1) { $file=$element }
                        }
                        if (-not $file -or -not $arguments) { continue }
                        $exe=@(Resolve-ODSUninstallLiteral $file $assignments $command.Extent.StartOffset)
                        $values=@(Resolve-ODSUninstallLiteral $arguments $assignments $command.Extent.StartOffset)
                        if ($exe.Count -ne 1) { continue }
                        # Start-Process joins ArgumentList verbatim. Adding
                        # quotes here would invent execution proof for paths
                        # that the real launcher splits at spaces.
                        $serialized=$values -join ' '
                        if (Test-ODSUninstallCommandOwned $serialized $exe[0] -ArgumentsOnly -Depth ($Depth+1)) { return $true }
                    }
                } catch { return $false }
                return $false
            }
            if ($arg -in @('-NoProfile','-NoLogo','-NonInteractive','-Sta','-Mta')) { continue }
            if ($arg -in @('-ExecutionPolicy','-WindowStyle')) { $index++; continue }
            # Inline commands and unknown switches cannot establish script execution.
            return $false
        }
    } elseif ($program -match '^bash(\.exe)?$') {
        # Native model upgrades use Git Bash with exactly one generated ODS
        # wrapper. Do not infer ownership from -c, another script, or a shared
        # bash.exe location.
        if ($argv.Count -ne 1 -or -not (Test-ODSUninstallPathOwned $argv[0])) { return $false }
        try {
            $expected=[IO.Path]::GetFullPath((Join-Path $InstallDir 'logs\bootstrap-run.sh'))
            $actual=[IO.Path]::GetFullPath($argv[0])
            return $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)
        } catch { return $false }
    } elseif ($program -match '^(python(?:3(?:\.\d+)?)?|pythonw|py)(\.exe)?$') {
        foreach ($arg in $argv) {
            if ($arg -in @('-u','-B','-E','-s','-S') -or $arg -match '^-[23](?:\.\d+)?$') { continue }
            if ($arg.StartsWith('-')) { return $false }
            return (Test-ODSUninstallPathOwned $arg)
        }
    } elseif ($program -match '^(wscript|cscript)(\.exe)?$') {
        foreach ($arg in $argv) {
            if ($arg.StartsWith('//')) { continue }
            return (Test-ODSUninstallPathOwned $arg)
        }
    }
    return $false
}

function Test-ODSUninstallTaskOwned {
    param($Task)
    if (-not $Task -or -not @($Task.Actions).Count) { return $false }
    foreach ($action in @($Task.Actions)) {
        if (-not (Test-ODSUninstallPathOwned ([string]$action.Execute)) -and
            -not (Test-ODSUninstallCommandOwned ([string]$action.Arguments) ([string]$action.Execute) -ArgumentsOnly)) { return $false }
    }
    return $true
}

function Test-ODSUninstallStartupLauncherOwned {
    param([string]$Content)
    if ([string]::IsNullOrWhiteSpace($Content)) { return $false }
    # Both native installer paths write the exact comment before this VBS
    # launcher. Older generated files omitted it. Reject any extra commands.
    $launcher=[regex]::Match($Content.Trim(), '(?i)^(?:'' ODS Host Agent login startup launcher\r?\n)?Set WshShell = CreateObject\("WScript\.Shell"\)\r?\nWshShell\.Run "([^"\r\n]+)", 0, False$')
    return ($launcher.Success -and (Test-ODSUninstallCommandOwned $launcher.Groups[1].Value))
}

# Native host-agent lifecycle. All ownership inspections precede mutations.
# The shared launcher recognizers below also serve uninstall; they inspect
# literal launcher syntax without executing encoded PowerShell or VBScript.

function Test-ODSHostAgentProcessOwned {
    param($ProcessInfo, [string]$AgentScript)
    if (-not $ProcessInfo.ExecutablePath -or -not $ProcessInfo.CommandLine) { return $false }
    $program = [IO.Path]::GetFileName([string]$ProcessInfo.ExecutablePath)
    if ($program -notmatch '^(python(?:3(?:\.\d+)?)?|pythonw|py)\.exe$') { return $false }
    $arguments = @([regex]::Matches([string]$ProcessInfo.CommandLine, '"([^"\r\n]*)"|[^\s"]+') | ForEach-Object { $_.Value.Trim('"') })
    for ($index = 1; $index -lt $arguments.Count; $index++) {
        $argument = $arguments[$index]
        if ($argument -in @('-u', '-B', '-E', '-s', '-S') -or $argument -match '^-[23](?:\.\d+)?$') { continue }
        if ($argument.StartsWith('-')) { return $false }
        try {
            return [IO.Path]::IsPathRooted($argument) -and
                [IO.Path]::GetFullPath($argument).Equals([IO.Path]::GetFullPath($AgentScript), [StringComparison]::OrdinalIgnoreCase)
        } catch { return $false }
    }
    return $false
}

function Close-ODSHostAgentStopPlan {
    param($Plan)
    if ($Plan) { foreach ($process in @($Plan.Processes)) { $process.Dispose() } }
}

function Get-ODSHostAgentStopPlan {
    param(
        [string]$InstallDir,
        [string]$PidFile,
        [int]$Port,
        [string]$TaskName,
        [string]$StartupFolder = [Environment]::GetFolderPath('Startup')
    )
    $agentScript = Join-Path $InstallDir 'bin/ods-host-agent.py'
    $plan = [pscustomobject]@{Processes = @(); Task = $null; StartupFile = (Join-Path $StartupFolder 'ods-host-agent.vbs'); StartupContent = $null; PidContent = $null}
    try {
        # Enumerate with terminating errors so access/provider failures cannot
        # masquerade as an absent task or an unused port.
        $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq $TaskName -and $_.TaskPath -eq '\' })
        if ($tasks.Count -gt 1) { throw 'Ambiguous host-agent task ownership' }
        if ($tasks.Count) {
            if (-not (Test-ODSUninstallTaskOwned $tasks[0])) { throw "Refusing foreign scheduled task: $TaskName" }
            $plan.Task = $tasks[0]
        }
        if (Test-Path -LiteralPath $plan.StartupFile) {
            $plan.StartupContent = Get-Content -LiteralPath $plan.StartupFile -Raw -ErrorAction Stop
            if (-not (Test-ODSUninstallStartupLauncherOwned $plan.StartupContent)) { throw "Refusing foreign Startup launcher: $($plan.StartupFile)" }
        }
        $processIds = @()
        if (Test-Path -LiteralPath $PidFile) {
            $plan.PidContent = Get-Content -LiteralPath $PidFile -Raw -ErrorAction Stop
            $agentProcessId = 0
            if (-not [int]::TryParse($plan.PidContent.Trim(), [ref]$agentProcessId) -or $agentProcessId -le 0) { throw "Invalid host-agent PID receipt: $PidFile" }
            $processIds += $agentProcessId
        }
        $listeners = @(Get-NetTCPConnection -ErrorAction Stop | Where-Object { $_.LocalPort -eq $Port -and $_.State -eq 'Listen' })
        foreach ($listener in $listeners) {
            if ($listener.OwningProcess -le 0) { throw "Unknown owner of host-agent port $Port" }
            $processIds += [int]$listener.OwningProcess
        }
        foreach ($agentProcessId in @($processIds | Sort-Object -Unique)) {
            $process = $null
            try {
                $process = Get-Process -Id $agentProcessId -ErrorAction Stop
            } catch {
                if ($_.FullyQualifiedErrorId -like 'NoProcessFoundForGivenId*') { continue }
                throw
            }
            # Retain the process handle from BEFORE the CIM query through Kill.
            # A recycled numeric PID must never select a replacement process.
            $plan.Processes += $process
            $null = $process.Handle
            if ($process.HasExited) { continue }
            $native = Get-CimInstance Win32_Process -Filter "ProcessId=$agentProcessId" -ErrorAction Stop
            if ($process.HasExited) { continue }
            if (-not $native -or -not (Test-ODSHostAgentProcessOwned -ProcessInfo $native -AgentScript $agentScript)) {
                throw "Refusing unverified host-agent process PID $agentProcessId; keep the PID receipt and resolve the collision before retrying"
            }
        }
        return $plan
    } catch {
        Close-ODSHostAgentStopPlan $plan
        throw
    }
}

function Assert-ODSHostAgentResourcesOwned {
    param([string]$InstallDir, [string]$PidFile, [int]$Port, [string]$TaskName, [string]$StartupFolder = [Environment]::GetFolderPath('Startup'))
    $plan = Get-ODSHostAgentStopPlan @PSBoundParameters
    Close-ODSHostAgentStopPlan $plan
}

function Stop-ODSHostAgentOwnedResources {
    param([string]$InstallDir, [string]$PidFile, [int]$Port, [string]$TaskName, [string]$StartupFolder = [Environment]::GetFolderPath('Startup'))
    $plan = Get-ODSHostAgentStopPlan @PSBoundParameters
    try {
        if ($plan.Task) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
        foreach ($process in @($plan.Processes)) {
            if ($process.HasExited) { continue }
            $process.Kill()
            if (-not $process.WaitForExit(10000)) { throw "Host-agent process PID $($process.Id) did not exit; preserving cleanup receipts" }
        }
        if ($null -ne $plan.StartupContent -and (Test-Path -LiteralPath $plan.StartupFile)) {
            if ((Get-Content -LiteralPath $plan.StartupFile -Raw -ErrorAction Stop) -cne $plan.StartupContent) { throw 'Host-agent Startup launcher changed during stop; preserving it' }
            Remove-Item -LiteralPath $plan.StartupFile -Force -ErrorAction Stop
        }
        if ($null -ne $plan.PidContent -and (Test-Path -LiteralPath $PidFile)) {
            if ((Get-Content -LiteralPath $PidFile -Raw -ErrorAction Stop) -cne $plan.PidContent) { throw 'Host-agent PID receipt changed during stop; preserving it' }
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction Stop
        }
    } finally { Close-ODSHostAgentStopPlan $plan }
}
