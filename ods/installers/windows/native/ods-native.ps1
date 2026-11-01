# ============================================================================
# ODS Windows-native app -- control script
# ============================================================================
# Part of: installers/windows/native/ (shipped at the root of the app bundle)
# Purpose: Set up, start, stop, open and remove the per-user ODS app. Used by
#          the Inno Setup installer, the Start menu entry (launch-ods.vbs) and
#          CI. No administrator rights, no Docker, no WSL.
#
# Expects: The app bundle layout written by build-bundle.ps1 around this file:
#          python\, native\supervisor\, dashboard-api\, dashboard\dist\,
#          llama\, lib\ods-native-lib.ps1, lib\private-file.ps1,
#          lib\backend-contract.ps1. Windows PowerShell 5.1 or PowerShell 7.
# Provides: ods-native.ps1 <command>
#   install-state [-ModelPath <file.gguf>] [-CtxSize N]
#       Create %LOCALAPPDATA%\ODS\Native\{secrets,run,logs,data,models},
#       the two API-key files (32 random bytes, hex, owner-only ACL, kept on
#       re-run), supervisor.json, and the sign-in entry: scheduled task "ODS"
#       (AtLogOn, restart on failure) or, if Task Scheduler refuses, the
#       Startup-folder script ods-native.vbs. Re-runnable. Without
#       -ModelPath the model of the existing config is kept; with no model
#       llama-server stays disabled and only the dashboard runs.
#   start            Start the supervisor through its sign-in entry.
#   stop             Ask the supervisor to stop (run\supervisor.stop), wait,
#                    then end the task and kill the verified process.
#   open [-NoBrowser] [-ShowErrors]
#                    Start if needed, wait for the dashboard, open
#                    http://127.0.0.1:3002/ in the default browser.
#   status           JSON: setup, sign-in entry, supervisor PIDs, run record,
#                    service health.
#   uninstall-state [-RemoveData]
#       Stop, remove the task / Startup script, and delete the generated
#       state (config, secrets, run, logs). models\ and data\ are kept unless
#       -RemoveData, which deletes all of %LOCALAPPDATA%\ODS\Native.
#
# Modder notes:
#   Pure helpers live in lib\ods-native-lib.ps1 (Pester-tested). Secrets are
#   only ever written to owner-only files; they never appear on a command
#   line, in the scheduled task, in the config or in output. Errors are
#   appended to logs\ods-native.log and re-thrown (non-zero exit).
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('install-state', 'start', 'stop', 'open', 'status', 'uninstall-state')]
    [string]$Command,
    [string]$ModelPath = '',
    [ValidateRange(512, 1048576)][int]$CtxSize = 8192,
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 300,
    [switch]$RemoveData,
    [switch]$NoBrowser,
    [switch]$ShowErrors
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

. (Join-Path $PSScriptRoot 'lib\ods-native-lib.ps1')
. (Join-Path $PSScriptRoot 'lib\private-file.ps1')
. (Join-Path $PSScriptRoot 'lib\backend-contract.ps1')

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'ods-native.ps1 runs on Windows only.' }
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'Cannot locate %LOCALAPPDATA% for the ODS state folder.' }

$script:Paths = Get-ODSNativePaths -InstallRoot $PSScriptRoot -LocalAppData $env:LOCALAPPDATA `
    -StartupFolder ([Environment]::GetFolderPath('Startup'))
# uninstall-state deletes logs\, so it must not recreate the log file.
$script:FileLog = ($Command -ne 'uninstall-state')

# ---------------------------------------------------------------- output

function Write-ODSNativeLog {
    param([string]$Message)
    $line = '{0} {1} {2}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $Command, $Message
    if ($script:FileLog) {
        New-Item -ItemType Directory -Force -Path $script:Paths.LogsDir | Out-Null
        [IO.File]::AppendAllText($script:Paths.ControlLog, $line + "`r`n", [Text.UTF8Encoding]::new($false))
    }
    Write-Host $Message
}

function Show-ODSNativeError {
    # The Start menu entry runs hidden; tell the user instead of failing silently.
    param([string]$Message)
    $shell = New-Object -ComObject WScript.Shell
    try {
        $null = $shell.Popup("ODS could not start.`n`n$Message`n`nDetails: $($script:Paths.LogsDir)", 0, 'ODS', 16)
    } finally {
        $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}

# ---------------------------------------------------------------- probes

function Test-ODSNativeHealth {
    param([Parameter(Mandatory = $true)][int]$Port)
    $request = [Net.WebRequest]::Create((Get-ODSNativeHealthUrl -Port $Port))
    $request.Timeout = 2000
    $request.Proxy = $null  # loopback only; never route through a configured proxy
    try {
        $response = $request.GetResponse()
    } catch [Net.WebException] {
        # Refused, timed out or non-2xx: not healthy (yet).
        return $false
    }
    try { return ([int]$response.StatusCode -eq 200) } finally { $response.Dispose() }
}

function Get-ODSNativeSupervisorProcessId {
    # PIDs of this installation's supervisor, verified by executable path and
    # exact arguments; the run record's PID alone is never trusted.
    $ids = @()
    foreach ($name in @('pythonw.exe', 'python.exe')) {
        foreach ($process in @(Get-CimInstance -ClassName Win32_Process -Filter "Name = '$name'")) {
            if (Test-ODSNativeSupervisorCommandLine -ExecutablePath ([string]$process.ExecutablePath) `
                    -CommandLine ([string]$process.CommandLine) -Paths $script:Paths) {
                $ids += [int]$process.ProcessId
            }
        }
    }
    return $ids
}

function Get-ODSNativeTask {
    # The "ODS" task when it is the one install-state registered, else $null.
    $task = @(Get-ScheduledTask -TaskPath '\' | Where-Object { $_.TaskName -ceq $script:ODSNativeTaskName })
    if (-not $task.Count) { return $null }
    $actions = @($task[0].Actions)
    if ($actions.Count -ne 1 -or -not (Test-ODSNativeTaskAction -Execute ([string]$actions[0].Execute) `
            -Arguments ([string]$actions[0].Arguments) -Paths $script:Paths)) {
        return $null
    }
    return $task[0]
}

function Test-ODSNativeTaskNameTaken {
    return [bool](@(Get-ScheduledTask -TaskPath '\' | Where-Object { $_.TaskName -ceq $script:ODSNativeTaskName }).Count)
}

function Get-ODSNativeStartupScriptState {
    # 'none', 'ours' or 'foreign' for the Startup-folder fallback entry.
    $path = $script:Paths.StartupScript
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { return 'none' }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return 'foreign' }
    if (Test-ODSNativeStartupScriptContent -Content ([IO.File]::ReadAllText($path))) { return 'ours' }
    return 'foreign'
}

# ---------------------------------------------------------------- install-state

function New-ODSNativeSecretFile {
    # Keep a non-empty existing secret (re-run, upgrade); always re-verify
    # its owner-only ACL. Never prints or returns the value.
    param([Parameter(Mandatory = $true)][string]$Path)
    if ((Test-Path -LiteralPath $Path -PathType Leaf) -and (Get-Item -LiteralPath $Path).Length -gt 0) {
        Protect-ODSPrivateEnvFile -Path $Path
        return
    }
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    Write-ODSPrivateEnvFile -Path $Path -Content (ConvertTo-ODSNativeSecretText -Bytes $bytes)
    Protect-ODSPrivateEnvFile -Path $Path
}

function Resolve-ODSNativeModelPath {
    if ($ModelPath) {
        $full = [IO.Path]::GetFullPath($ModelPath)
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "Model file not found: $full" }
        return $full
    }
    if (-not (Test-Path -LiteralPath $script:Paths.ConfigPath -PathType Leaf)) { return '' }
    $previous = Get-Content -LiteralPath $script:Paths.ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $kept = Get-ODSNativeConfigModelPath -Config $previous
    if ($kept -and -not (Test-Path -LiteralPath $kept -PathType Leaf)) {
        Write-ODSNativeLog "The configured model $kept is missing; llama-server is disabled until a model is set."
        return ''
    }
    return $kept
}

function Set-ODSNativeStartupScript {
    $path = $script:Paths.StartupScript
    if (-not $path) { throw 'The Startup folder is unavailable, so ODS cannot start at sign-in.' }
    if ((Get-ODSNativeStartupScriptState) -eq 'foreign') { throw "$path exists and was not written by ODS; it was left unchanged." }
    Write-ODSPrivateEnvFile -Path $path -Content (Get-ODSNativeStartupScriptContent -Paths $script:Paths)
}

function Remove-ODSNativeStartupScript {
    if ((Get-ODSNativeStartupScriptState) -ne 'ours') { return }
    Remove-Item -LiteralPath $script:Paths.StartupScript -Force
}

function Register-ODSNativeSignIn {
    # Scheduled task first (restarts the supervisor after a fatal exit);
    # Startup-folder script when Task Scheduler refuses this user.
    if ((Test-ODSNativeTaskNameTaken) -and -not (Get-ODSNativeTask)) {
        throw "A scheduled task named '$($script:ODSNativeTaskName)' exists that this ODS installation did not create; it was left unchanged."
    }
    $action = New-ScheduledTaskAction -Execute $script:Paths.Pythonw `
        -Argument (Get-ODSNativeSupervisorArguments -Paths $script:Paths) -WorkingDirectory $script:Paths.InstallRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Resolve-ODSInteractiveScheduledTaskUser)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $script:ODSNativeTaskName -TaskPath '\' -Action $action -Trigger $trigger `
            -Settings $settings -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
            -Description 'ODS: starts the local AI services at sign-in (127.0.0.1 only).' -Force -ErrorAction Stop | Out-Null
    } catch [Microsoft.Management.Infrastructure.CimException], [UnauthorizedAccessException] {
        Write-ODSNativeLog "Task Scheduler refused the ODS task ($($_.Exception.Message)); using the Startup folder instead."
        Set-ODSNativeStartupScript
        return 'startup-folder'
    }
    Remove-ODSNativeStartupScript
    return 'task'
}

function Invoke-ODSNativeInstallState {
    foreach ($required in @($script:Paths.Python, $script:Paths.Pythonw)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "The ODS app bundle is incomplete: $required is missing." }
    }
    foreach ($dir in @($script:Paths.StateRoot, $script:Paths.SecretsDir, $script:Paths.RunDir, $script:Paths.LogsDir,
            $script:Paths.DataDir, $script:Paths.ModelsDir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    New-ODSNativeSecretFile -Path $script:Paths.LlamaKey
    New-ODSNativeSecretFile -Path $script:Paths.DashboardKey
    $model = Resolve-ODSNativeModelPath
    $config = New-ODSNativeSupervisorConfig -Paths $script:Paths -ModelPath $model -CtxSize $CtxSize
    Write-ODSPrivateEnvFile -Path $script:Paths.ConfigPath -Content (ConvertTo-ODSNativeConfigJson -Config $config)
    $signIn = Register-ODSNativeSignIn
    if ($model) { $modelNote = "model $model" } else { $modelNote = 'no model yet (dashboard only)' }
    Write-ODSNativeLog "ODS is set up in $($script:Paths.StateRoot): $modelNote, starts at sign-in via $signIn."
}

# ---------------------------------------------------------------- start / stop / open

function Start-ODSNativeSupervisor {
    if (-not (Test-Path -LiteralPath $script:Paths.ConfigPath -PathType Leaf)) {
        throw 'ODS is not set up for this Windows user. Reinstall ODS, or run: ods-native.ps1 install-state'
    }
    if (@(Get-ODSNativeSupervisorProcessId).Count) {
        Write-ODSNativeLog 'ODS is already running.'
        return
    }
    if (Get-ODSNativeTask) {
        Start-ScheduledTask -TaskName $script:ODSNativeTaskName -TaskPath '\'
        Write-ODSNativeLog 'Started ODS through its scheduled task.'
        return
    }
    if ((Get-ODSNativeStartupScriptState) -eq 'ours') {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wscript.exe') -ArgumentList ('"{0}"' -f $script:Paths.StartupScript)
        Write-ODSNativeLog 'Started ODS through its Startup-folder script.'
        return
    }
    throw 'ODS has no sign-in entry. Reinstall ODS, or run: ods-native.ps1 install-state'
}

function Stop-ODSNativeOwnedProcess {
    # Last resort. Hold the process handle before re-checking ownership so a
    # reused PID can never be killed.
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $candidate = @(Get-Process | Where-Object { $_.Id -eq $ProcessId })
    if (-not $candidate.Count) { return }
    $process = $candidate[0]
    try {
        $null = $process.Handle
        $record = @(Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId")
        if (-not $record.Count) { return }
        if (-not (Test-ODSNativeSupervisorCommandLine -ExecutablePath ([string]$record[0].ExecutablePath) `
                -CommandLine ([string]$record[0].CommandLine) -Paths $script:Paths)) { return }
        Stop-Process -InputObject $process -Force
        Write-ODSNativeLog "Ended the ODS supervisor (pid $ProcessId) after it did not stop in time."
    } finally {
        $process.Dispose()
    }
}

function Stop-ODSNativeSupervisor {
    $ids = @(Get-ODSNativeSupervisorProcessId)
    if (-not $ids.Count) {
        Write-ODSNativeLog 'ODS is not running.'
        return
    }
    New-Item -ItemType Directory -Force -Path $script:Paths.RunDir | Out-Null
    [IO.File]::WriteAllText($script:Paths.StopFile, 'stop')
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline -and @(Get-ODSNativeSupervisorProcessId).Count) { Start-Sleep -Milliseconds 500 }
    $left = @(Get-ODSNativeSupervisorProcessId)
    if ($left.Count) {
        # A killed supervisor counts as a task failure; end the task first so
        # Task Scheduler does not restart it.
        if (Get-ODSNativeTask) { Stop-ScheduledTask -TaskName $script:ODSNativeTaskName -TaskPath '\' }
        foreach ($id in @(Get-ODSNativeSupervisorProcessId)) { Stop-ODSNativeOwnedProcess -ProcessId $id }
    }
    if (Test-Path -LiteralPath $script:Paths.StopFile) { Remove-Item -LiteralPath $script:Paths.StopFile -Force }
    if (-not $left.Count) { Write-ODSNativeLog 'ODS stopped.' }
}

function Open-ODSNative {
    Start-ODSNativeSupervisor
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not (Test-ODSNativeHealth -Port $script:ODSNativeDashboardPort)) {
        if ((Get-Date) -ge $deadline) {
            throw "The ODS dashboard did not answer within $TimeoutSeconds seconds. See $($script:Paths.LogsDir)\supervisor.log."
        }
        Start-Sleep -Seconds 1
    }
    $url = 'http://127.0.0.1:{0}/' -f $script:ODSNativeDashboardPort
    if (-not $NoBrowser) { Start-Process -FilePath $url }
    Write-ODSNativeLog "ODS is ready at $url"
}

# ---------------------------------------------------------------- status / uninstall-state

function Get-ODSNativeStatus {
    $record = $null
    if (Test-Path -LiteralPath $script:Paths.RecordPath -PathType Leaf) {
        $record = Get-Content -LiteralPath $script:Paths.RecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    $signIn = 'none'
    if (Get-ODSNativeTask) { $signIn = 'task' } elseif ((Get-ODSNativeStartupScriptState) -eq 'ours') { $signIn = 'startup-folder' }
    $ids = @(Get-ODSNativeSupervisorProcessId)
    return [ordered]@{
        installed = (Test-Path -LiteralPath $script:Paths.ConfigPath -PathType Leaf)
        state_root = $script:Paths.StateRoot
        sign_in = $signIn
        running = [bool]$ids.Count
        supervisor_pids = $ids
        record = $record
        health = [ordered]@{
            'llama-server' = (Test-ODSNativeHealth -Port $script:ODSNativeLlamaPort)
            'dashboard-api' = (Test-ODSNativeHealth -Port $script:ODSNativeDashboardPort)
        }
    }
}

function Remove-ODSNativeEmptyFolder {
    # Remove a folder only when no file is left anywhere inside it.
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return }
    if (@(Get-ChildItem -LiteralPath $Path -Recurse -Force -File).Count) { return }
    Remove-Item -LiteralPath $Path -Recurse -Force
}

function Invoke-ODSNativeUninstallState {
    Stop-ODSNativeSupervisor
    if (Get-ODSNativeTask) {
        Unregister-ScheduledTask -TaskName $script:ODSNativeTaskName -TaskPath '\' -Confirm:$false
    }
    Remove-ODSNativeStartupScript
    foreach ($path in @(Get-ODSNativeRemovalPlan -Paths $script:Paths -RemoveData:$RemoveData)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    Remove-ODSNativeEmptyFolder -Path $script:Paths.StateRoot
    # %LOCALAPPDATA%\ODS may also hold the WSL install's records; only an
    # empty folder is removed.
    if ((Test-Path -LiteralPath $script:Paths.OdsRoot -PathType Container) -and
        -not @(Get-ChildItem -LiteralPath $script:Paths.OdsRoot -Force).Count) {
        Remove-Item -LiteralPath $script:Paths.OdsRoot -Force
    }
    if (Test-Path -LiteralPath $script:Paths.StateRoot) {
        Write-Host "ODS was removed. Your models and data were kept in $($script:Paths.StateRoot)."
    } else {
        Write-Host 'ODS was removed.'
    }
}

# ---------------------------------------------------------------- dispatch

try {
    switch ($Command) {
        'install-state' { Invoke-ODSNativeInstallState }
        'start' { Start-ODSNativeSupervisor }
        'stop' { Stop-ODSNativeSupervisor }
        'open' { Open-ODSNative }
        'status' { ConvertTo-Json -InputObject (Get-ODSNativeStatus) -Depth 8 }
        'uninstall-state' { Invoke-ODSNativeUninstallState }
    }
} catch {
    if ($script:FileLog) {
        New-Item -ItemType Directory -Force -Path $script:Paths.LogsDir | Out-Null
        [IO.File]::AppendAllText($script:Paths.ControlLog, ('{0} {1} ERROR {2}{3}{4}{3}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'),
            $Command, $_.Exception.Message, "`r`n", $_.ScriptStackTrace), [Text.UTF8Encoding]::new($false))
    }
    if ($ShowErrors) { Show-ODSNativeError -Message $_.Exception.Message }
    throw
}
