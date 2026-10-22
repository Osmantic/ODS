# ============================================================================
# ODS Windows host-agent supervisor
# ============================================================================
# Foreground, per-user supervisor for ods-host-agent.py. Task Scheduler starts
# this script; a kill-on-close Job Object contains the Python process tree.
# State is diagnostic only. Intent and the strictly validated plan are the
# durable authority. No secret or environment value is written to state.
# ============================================================================

param([switch]$Run, [string]$PlanPath = '')

. (Join-Path $PSScriptRoot 'private-file.ps1')
. (Join-Path $PSScriptRoot 'native-process-job.ps1')

$script:ODSHostAgentSupervisorSchema = 1
$script:ODSHostAgentStableSeconds = 300
$script:ODSHostAgentStartupSeconds = 30
$script:ODSHostAgentRestartDelays = @(1, 2, 5, 10, 30)

function Get-ODSHostAgentSupervisorDirectory {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    return (Join-Path (Join-Path ([IO.Path]::GetFullPath($InstallDir)) 'data') 'host-agent-supervisor')
}

function Get-ODSHostAgentSupervisorPaths {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $directory = Get-ODSHostAgentSupervisorDirectory $InstallDir
    return [pscustomobject]@{
        Directory = $directory
        Plan = Join-Path $directory 'supervisor-plan.json'
        Intent = Join-Path $directory 'supervisor-intent.json'
        State = Join-Path $directory 'supervisor-state.json'
    }
}

function Get-ODSHostAgentTaskName {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $identity = $sid + "`n" + [IO.Path]::GetFullPath($InstallDir).TrimEnd('\').ToLowerInvariant()
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($identity))
    } finally { $sha.Dispose() }
    return 'ODSHostAgent-' + (($digest | Select-Object -First 6 | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Get-ODSHostAgentSupervisorArguments {
    param([Parameter(Mandatory = $true)][string]$PlanPath)
    $scriptPath = Join-Path $PSScriptRoot 'host-agent-supervisor.ps1'
    return '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
        $scriptPath.Replace('"', '""') + '" -Run -PlanPath "' + $PlanPath.Replace('"', '""') + '"'
}

function Get-ODSHostAgentSupervisorTask {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][string]$PlanPath,
        [switch]$MissingOk
    )
    $taskName = Get-ODSHostAgentTaskName $InstallDir
    $tasks = @(Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue)
    if (-not $tasks.Count -and $MissingOk) { return $null }
    if ($tasks.Count -ne 1 -or @($tasks[0].Actions).Count -ne 1) {
        throw 'The host-agent supervisor task is missing or ambiguous.'
    }
    $task = $tasks[0]
    $action = $task.Actions[0]
    $expectedArguments = Get-ODSHostAgentSupervisorArguments $PlanPath
    $shellName = [IO.Path]::GetFileName([string]$action.Execute)
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [string]$task.Principal.UserId
    if ($task.TaskPath -ne '\' -or $shellName -ine 'powershell.exe' -or
        [string]$action.Arguments -cne $expectedArguments -or
        -not ([IO.Path]::GetFullPath([string]$action.WorkingDirectory).Equals(
            [IO.Path]::GetFullPath($InstallDir), [StringComparison]::OrdinalIgnoreCase)) -or
        ($principal -ine $current.Name -and $principal -ine $current.User.Value)) {
        throw 'The host-agent supervisor task is not owned by this user and installation.'
    }
    return $task
}

function Test-ODSHostAgentPlainString {
    param($Value, [int]$Maximum = 32768)
    return $Value -is [string] -and $Value.Length -gt 0 -and $Value.Length -le $Maximum -and
        -not ($Value.ToCharArray() | Where-Object { [int]$_ -lt 32 -or [int]$_ -eq 127 })
}

function Assert-ODSHostAgentSupervisorPlan {
    param($Plan)
    $names = @($Plan.PSObject.Properties.Name)
    $expected = @('schemaVersion', 'pythonPath', 'pythonPrefixArgs', 'agentScript', 'installDir',
        'port', 'pidFile', 'logFile', 'dockerBin')
    if (($names -join "`n") -cne ($expected -join "`n") -or
        $Plan.schemaVersion -isnot [int] -or $Plan.schemaVersion -ne $script:ODSHostAgentSupervisorSchema) {
        throw 'The host-agent supervisor plan schema is invalid.'
    }
    foreach ($name in @('pythonPath', 'agentScript', 'installDir', 'pidFile', 'logFile', 'dockerBin')) {
        if (-not (Test-ODSHostAgentPlainString $Plan.$name) -or -not [IO.Path]::IsPathRooted([string]$Plan.$name)) {
            throw 'The host-agent supervisor plan contains an invalid absolute path.'
        }
    }
    if (-not (Test-Path -LiteralPath $Plan.pythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Plan.agentScript -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Plan.installDir -PathType Container)) {
        throw 'The host-agent supervisor plan names a missing executable, script or installation.'
    }
    $install = [IO.Path]::GetFullPath([string]$Plan.installDir).TrimEnd('\')
    $agent = [IO.Path]::GetFullPath([string]$Plan.agentScript)
    if (-not $agent.Equals((Join-Path (Join-Path $install 'bin') 'ods-host-agent.py'),
            [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The host-agent supervisor plan does not name this installation agent.'
    }
    foreach ($pathName in @('pidFile', 'logFile')) {
        $path = [IO.Path]::GetFullPath([string]$Plan.$pathName)
        if (-not $path.StartsWith($install + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The host-agent supervisor state paths must stay inside this installation.'
        }
    }
    if ($Plan.port -isnot [int] -or $Plan.port -lt 1 -or $Plan.port -gt 65535) {
        throw 'The host-agent supervisor port is invalid.'
    }
    $prefix = @($Plan.pythonPrefixArgs)
    if ($prefix.Count -gt 8) { throw 'The host-agent Python prefix is too large.' }
    foreach ($argument in $prefix) {
        if (-not (Test-ODSHostAgentPlainString $argument 256)) {
            throw 'The host-agent Python prefix contains an invalid argument.'
        }
    }
}

function Assert-ODSHostAgentPrivateFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { return }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The host-agent supervisor file must be a regular private file.'
    }
    $acl = Get-Acl -LiteralPath $Path
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 1 -or
        $rules[0].IdentityReference -ne $owner -or $rules[0].IsInherited -or
        $rules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rules[0].FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {
        throw 'The host-agent supervisor file is not owner-private.'
    }
}

function Read-ODSHostAgentSupervisorJson {
    param([Parameter(Mandatory = $true)][string]$Path, [int]$MaximumBytes = 65536)
    Assert-ODSHostAgentPrivateFile $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -lt 2 -or $item.Length -gt $MaximumBytes) {
        throw 'The host-agent supervisor file is invalid.'
    }
    $bytes = [IO.File]::ReadAllBytes($Path)
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    return ($text | ConvertFrom-Json -ErrorAction Stop)
}

function Get-ODSHostAgentPlanDigest {
    param([Parameter(Mandatory = $true)][string]$PlanPath)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash([IO.File]::ReadAllBytes($PlanPath)) }
    finally { $sha.Dispose() }
    return (($digest | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Write-ODSHostAgentSupervisorPlan {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [string[]]$PythonPrefixArgs = @(),
        [Parameter(Mandatory = $true)][string]$AgentScript,
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$PidFile,
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$DockerBin
    )
    $paths = Get-ODSHostAgentSupervisorPaths $InstallDir
    $plan = [ordered]@{
        schemaVersion = $script:ODSHostAgentSupervisorSchema
        pythonPath = [IO.Path]::GetFullPath($PythonPath)
        pythonPrefixArgs = @($PythonPrefixArgs)
        agentScript = [IO.Path]::GetFullPath($AgentScript)
        installDir = [IO.Path]::GetFullPath($InstallDir)
        port = $Port
        pidFile = [IO.Path]::GetFullPath($PidFile)
        logFile = [IO.Path]::GetFullPath($LogFile)
        dockerBin = [IO.Path]::GetFullPath($DockerBin)
    }
    Assert-ODSHostAgentSupervisorPlan ([pscustomobject]$plan)
    Write-ODSPrivateEnvFile -Path $paths.Plan -Content ($plan | ConvertTo-Json -Depth 3 -Compress)
    return $paths
}

function Write-ODSHostAgentSupervisorIntent {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [ValidateSet('running', 'stopped')][string]$State
    )
    $paths = Get-ODSHostAgentSupervisorPaths $InstallDir
    $record = [ordered]@{ schemaVersion = 1; desiredState = $State }
    Write-ODSPrivateEnvFile -Path $paths.Intent -Content ($record | ConvertTo-Json -Compress)
}

function Read-ODSHostAgentSupervisorIntent {
    param([Parameter(Mandatory = $true)][string]$Path)
    $intent = Read-ODSHostAgentSupervisorJson $Path 4096
    $names = @($intent.PSObject.Properties.Name)
    if (($names -join ',') -cne 'schemaVersion,desiredState' -or
        $intent.schemaVersion -isnot [int] -or $intent.schemaVersion -ne 1 -or
        [string]$intent.desiredState -cnotin @('running', 'stopped')) {
        throw 'The host-agent supervisor intent is invalid.'
    }
    return [string]$intent.desiredState
}

function Write-ODSHostAgentSupervisorState {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$DesiredState,
        [Parameter(Mandatory = $true)][string]$ActualState,
        [Parameter(Mandatory = $true)][string]$PlanDigest,
        [int]$ProcessId = 0,
        [datetime]$StartedAt = [datetime]::MinValue,
        $ExitCode = $null,
        [int]$RestartCount = 0,
        [datetime]$NextRetryAt = [datetime]::MinValue,
        [string]$Health = ''
    )
    $record = [ordered]@{
        schemaVersion = 1
        desiredState = $DesiredState
        actualState = $ActualState
        processId = $(if ($ProcessId -gt 0) { $ProcessId } else { $null })
        startedAt = $(if ($StartedAt -ne [datetime]::MinValue) { $StartedAt.ToUniversalTime().ToString('o') } else { $null })
        lastExitCode = $ExitCode
        restartCount = $RestartCount
        nextRetryAt = $(if ($NextRetryAt -ne [datetime]::MinValue) { $NextRetryAt.ToUniversalTime().ToString('o') } else { $null })
        lastHealth = $Health
        transitionedAt = [datetime]::UtcNow.ToString('o')
        planDigest = $PlanDigest
    }
    Write-ODSPrivateEnvFile -Path $Path -Content ($record | ConvertTo-Json -Compress)
}

function Read-ODSHostAgentSupervisorState {
    param([Parameter(Mandatory = $true)][string]$Path)
    $state = Read-ODSHostAgentSupervisorJson $Path
    $names = @($state.PSObject.Properties.Name)
    $expected = @('schemaVersion', 'desiredState', 'actualState', 'processId', 'startedAt',
        'lastExitCode', 'restartCount', 'nextRetryAt', 'lastHealth', 'transitionedAt', 'planDigest')
    if (($names -join "`n") -cne ($expected -join "`n") -or
        $state.schemaVersion -isnot [int] -or $state.schemaVersion -ne 1 -or
        [string]$state.desiredState -cnotin @('running', 'stopped') -or
        [string]$state.actualState -cnotin @('starting', 'running', 'backoff', 'crash-loop', 'stopped') -or
        $state.restartCount -isnot [int] -or $state.restartCount -lt 0 -or
        [string]$state.planDigest -notmatch '^[0-9a-f]{64}$') {
        throw 'The host-agent supervisor state schema is invalid.'
    }
    if ($null -ne $state.processId -and ($state.processId -isnot [int] -or $state.processId -lt 1)) {
        throw 'The host-agent supervisor process identity is invalid.'
    }
    foreach ($dateName in @('startedAt', 'nextRetryAt')) {
        if ($null -ne $state.$dateName) {
            $parsed = [datetime]::MinValue
            if (-not [datetime]::TryParseExact([string]$state.$dateName, 'o',
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::RoundtripKind, [ref]$parsed)) {
                throw 'The host-agent supervisor state timestamp is invalid.'
            }
        }
    }
    return $state
}

function Test-ODSHostAgentManagedHealth {
    param([int]$Port, [int]$ProcessId)
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -ne 1 -or [int]$listeners[0].OwningProcess -ne $ProcessId) { return $false }
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 `
            -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -ne 200 -or ([string]$response.Content).Length -gt 65536) { return $false }
        $body = [string]$response.Content | ConvertFrom-Json -ErrorAction Stop
        $names = @($body.PSObject.Properties.Name)
        return $body.status -ceq 'ok' -and (Test-ODSHostAgentPlainString ([string]$body.version) 128) -and
            $names -contains 'status' -and $names -contains 'version'
    } catch { return $false }
}

function Wait-ODSHostAgentManagedHealth {
    param($Process, [int]$Port, [int]$TimeoutSeconds = $script:ODSHostAgentStartupSeconds)
    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([datetime]::UtcNow -lt $deadline) {
        if ($Process.HasExited) { return $false }
        if (Test-ODSHostAgentManagedHealth -Port $Port -ProcessId $Process.Id) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Rotate-ODSHostAgentLog {
    param([Parameter(Mandatory = $true)][string]$Path, [long]$MaximumBytes = 5242880)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-ODSPrivateEnvFile -Path $Path -Content ''
        return
    }
    Protect-ODSPrivateEnvFile $Path
    if ((Get-Item -LiteralPath $Path).Length -le $MaximumBytes) { return }
    $previous = $Path + '.1'
    if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous -Force }
    Move-Item -LiteralPath $Path -Destination $previous
    Write-ODSPrivateEnvFile -Path $Path -Content ''
}

function Get-ODSHostAgentNextRestartCount {
    param([int]$CurrentCount, [double]$RunSeconds)
    if ($RunSeconds -ge $script:ODSHostAgentStableSeconds) { return 0 }
    return $CurrentCount + 1
}

function Invoke-ODSHostAgentSupervisor {
    param([Parameter(Mandatory = $true)][string]$PlanPath)
    $plan = Read-ODSHostAgentSupervisorJson $PlanPath
    Assert-ODSHostAgentSupervisorPlan $plan
    $paths = Get-ODSHostAgentSupervisorPaths ([string]$plan.installDir)
    if (-not [IO.Path]::GetFullPath($PlanPath).Equals([IO.Path]::GetFullPath($paths.Plan),
            [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The host-agent supervisor was given a foreign plan path.'
    }
    $mutex = [Threading.Mutex]::new($false, ('Local\' + (Get-ODSHostAgentTaskName $plan.installDir)))
    $ownsMutex = $false
    try {
        try { $ownsMutex = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { return 2 }
        $digest = Get-ODSHostAgentPlanDigest $PlanPath
        $restartCount = 0
        while ($true) {
        $desired = Read-ODSHostAgentSupervisorIntent $paths.Intent
        if ($desired -ceq 'stopped') {
            Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState stopped `
                -ActualState stopped -PlanDigest $digest -RestartCount $restartCount
            return 0
        }
        if ($restartCount -ge $script:ODSHostAgentRestartDelays.Count) {
            Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState running `
                -ActualState crash-loop -PlanDigest $digest -RestartCount $restartCount -Health 'restart-budget-exhausted'
            return 1
        }

        Rotate-ODSHostAgentLog ([string]$plan.logFile)
        $env:PATH = [string]$plan.dockerBin + ';' + $env:PATH
        $arguments = @($plan.pythonPrefixArgs) + @(
            [string]$plan.agentScript,
            '--port', [string]$plan.port,
            '--pid-file', [string]$plan.pidFile,
            '--install-dir', [string]$plan.installDir,
            '--log-file', [string]$plan.logFile
        )
        Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState running `
            -ActualState starting -PlanDigest $digest -RestartCount $restartCount
        $session = $null
        $started = [datetime]::UtcNow
        try {
            $session = Start-ODSNativeJobProcess -FilePath ([string]$plan.pythonPath) `
                -ArgumentList $arguments -WorkingDirectory ([string]$plan.installDir)
            if (-not (Wait-ODSHostAgentManagedHealth -Process $session.Process -Port ([int]$plan.port))) {
                if (-not $session.Process.HasExited) { Stop-ODSNativeJobProcess $session 15000 }
                throw 'The managed host agent did not publish verified health before the startup deadline.'
            }
            Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState running `
                -ActualState running -PlanDigest $digest -ProcessId $session.Process.Id `
                -StartedAt $session.Process.StartTime -RestartCount $restartCount -Health verified
            while (-not $session.Process.WaitForExit(1000)) {
                if ((Read-ODSHostAgentSupervisorIntent $paths.Intent) -ceq 'stopped') {
                    Stop-ODSNativeJobProcess $session 15000
                    Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState stopped `
                        -ActualState stopped -PlanDigest $digest -RestartCount $restartCount
                    return 0
                }
            }
            $exitCode = $session.Process.ExitCode
            $restartCount = Get-ODSHostAgentNextRestartCount -CurrentCount $restartCount `
                -RunSeconds (([datetime]::UtcNow - $started).TotalSeconds)
            if ($restartCount -ge $script:ODSHostAgentRestartDelays.Count) {
                Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState running `
                    -ActualState crash-loop -PlanDigest $digest -ExitCode $exitCode `
                    -RestartCount $restartCount -Health 'restart-budget-exhausted'
                return 1
            }
            $delay = $script:ODSHostAgentRestartDelays[[math]::Min(
                $restartCount - 1, $script:ODSHostAgentRestartDelays.Count - 1)]
            $retry = [datetime]::UtcNow.AddSeconds($delay)
            Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState running `
                -ActualState backoff -PlanDigest $digest -ExitCode $exitCode `
                -RestartCount $restartCount -NextRetryAt $retry -Health 'process-exited'
        } catch {
            $restartCount++
            if ($restartCount -ge $script:ODSHostAgentRestartDelays.Count) {
                Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState running `
                    -ActualState crash-loop -PlanDigest $digest -RestartCount $restartCount `
                    -Health 'restart-budget-exhausted'
                return 1
            }
            $delay = $script:ODSHostAgentRestartDelays[[math]::Min(
                $restartCount - 1, $script:ODSHostAgentRestartDelays.Count - 1)]
            $retry = [datetime]::UtcNow.AddSeconds($delay)
            Write-ODSHostAgentSupervisorState -Path $paths.State -DesiredState running `
                -ActualState backoff -PlanDigest $digest -RestartCount $restartCount `
                -NextRetryAt $retry -Health 'startup-failed'
        } finally {
            if ($session) { $session.Dispose() }
        }
            while ([datetime]::UtcNow -lt $retry) {
                if ((Read-ODSHostAgentSupervisorIntent $paths.Intent) -ceq 'stopped') { break }
                Start-Sleep -Milliseconds 250
            }
        }
    } finally {
        if ($ownsMutex) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

if ($Run) {
    exit (Invoke-ODSHostAgentSupervisor -PlanPath $PlanPath)
}
