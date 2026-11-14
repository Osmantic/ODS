# ============================================================================
# ODS Windows Desktop -- dashboard-api launcher
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Start, prove and stop the Windows Desktop dashboard-api. The
#          sign-in task ODSDesktopApi-<SID> runs launch-api.ps1, which calls
#          Invoke-ODSDesktopApi: it stops any process it can prove a previous
#          launch left, starts the pinned pythonw.exe on 127.0.0.1, records
#          the process, waits for /health and then for the process to exit.
# Expects: native-llama-runtime.ps1 (Invoke-ODSNativeLlamaHttp,
#          Get-ODSPortalOwnedProcessTree, Stop-ODSPortalOwnedProcesses,
#          Write-ODSPortalProcessOwnership, Read-ODSNativeLlamaJson,
#          Test-ODSNativeLlamaWanted), private-file.ps1, desktop-python.ps1
#          (Test-ODSDesktopPythonInstall), desktop-runtime.ps1
#          (ConvertFrom-ODSDesktopEnvText). setup copies all of them beside
#          launch-api.ps1, so the task never runs from a checkout.
# Provides: Get-ODSDesktopApiOwnedTree, Stop-ODSDesktopApi,
#           New-ODSDesktopApiArguments, Invoke-ODSDesktopApi
# Modder notes: a recorded PID is trusted only while its executable path and
#   start time still match; Windows reuses PIDs, and a reused one belongs to
#   someone else. pythonw.exe needs stdout and stderr redirected: without a
#   console uvicorn otherwise exits silently.
# ============================================================================

$script:ODSDesktopApiStartupSeconds = 90

function Get-ODSDesktopApiOwnedTree([string]$OwnershipPath) {
    <#
    .SYNOPSIS
        The live process tree a previous launch recorded, or $null when the
        record is absent or every recorded process is gone or was reused.
    #>
    if (-not (Test-Path -LiteralPath $OwnershipPath -PathType Leaf)) { return $null }
    $record = Read-ODSNativeLlamaJson $OwnershipPath
    $roots = @(foreach ($entry in @($record.Processes)) {
        if ($entry.ExitedAt) { continue }
        # As main reads it: PowerShell 7 already parsed it, 5.1 casts the text.
        $started = [datetime]$entry.StartedAt
        $process = Get-Process -Id ([int]$entry.ProcessId) -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        try {
            $same = ([string]$process.Path).Equals([string]$entry.ExecutablePath, [StringComparison]::OrdinalIgnoreCase) -and
                [math]::Abs(($process.StartTime.ToUniversalTime() - $started.ToUniversalTime()).TotalMilliseconds) -lt 1
        } finally { $process.Dispose() }
        if (-not $same) { continue }
        [pscustomobject]@{ ProcessId = [int]$entry.ProcessId; CreationDate = $started
            ExecutablePath = [string]$entry.ExecutablePath; ExitedAt = $null }
    })
    if (-not $roots.Count) { return $null }
    return (Get-ODSPortalOwnedProcessTree $roots @(Get-CimInstance Win32_Process -ErrorAction Stop))
}

function Stop-ODSDesktopApi([string]$OwnershipPath) {
    # Stops only what the record proves; returns how many processes it stopped.
    $owned = Get-ODSDesktopApiOwnedTree $OwnershipPath
    $count = 0
    if ($owned) {
        try {
            Stop-ODSPortalOwnedProcesses $owned.Handles
            $count = $owned.Handles.Count
        } finally {
            foreach ($process in $owned.Handles) { $process.Dispose() }
        }
    }
    if (Test-Path -LiteralPath $OwnershipPath) { Remove-Item -LiteralPath $OwnershipPath -Force }
    return $count
}

function New-ODSDesktopApiArguments([string]$AppSource, [int]$Port) {
    # --app-dir: the ._pth file puts Python in isolated mode, so the working
    # directory is not on sys.path.
    if ($AppSource -match '["\r\n]' -or $AppSource.EndsWith('\')) { throw 'The ODS app folder path cannot be passed to Python.' }
    return ('-m uvicorn --app-dir "' + $AppSource + '" main:app --host 127.0.0.1 --port ' + $Port +
        ' --log-level warning --no-access-log')
}

function Get-ODSDesktopLogTail([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $lines = @(Get-Content -LiteralPath $Path -Tail 12 -Encoding UTF8 | Where-Object { $_ -and $_ -notmatch '(?i)api[_-]?key|authorization|bearer' })
    if (-not $lines.Count) { return '' }
    return ' Last lines of ' + (Split-Path -Leaf $Path) + ': ' + ($lines -join ' | ')
}

function Wait-ODSDesktopApiHealth([int]$Port, $Process, [string]$ErrorLog, [int]$Seconds = $script:ODSDesktopApiStartupSeconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            throw ("The ODS app stopped during startup (exit code $($Process.ExitCode))." + (Get-ODSDesktopLogTail $ErrorLog))
        }
        if ((Invoke-ODSNativeLlamaHttp -Port $Port -Path '/health' -TimeoutMilliseconds 2000).StatusCode -eq 200) { return }
        Start-Sleep -Milliseconds 500
    }
    throw ("The ODS app did not answer within $Seconds seconds." + (Get-ODSDesktopLogTail $ErrorLog))
}

function Invoke-ODSDesktopApi($Paths) {
    <#
    .SYNOPSIS
        Durable launcher body. Returns the app's exit code; throws with a
        plain reason, which launch-api.ps1 writes to api-ready.json.
    #>
    if (-not (Test-ODSNativeLlamaWanted $Paths.Intent)) { return 0 }
    $environment = ConvertFrom-ODSDesktopEnvText ([IO.File]::ReadAllText($Paths.Environment, [Text.UTF8Encoding]::new($false, $true)))
    $port = [int]$environment['DASHBOARD_API_PORT']
    # Sizes only at each start; setup's -Repair compares every hash.
    $problem = Test-ODSDesktopPythonInstall -Directory $Paths.Python -Requirements (Join-Path $Paths.AppSource 'requirements.lock')
    if ($problem) { throw "$problem Run ODS setup again to repair it." }
    if (Test-Path -LiteralPath $Paths.Ready) { Remove-Item -LiteralPath $Paths.Ready -Force }
    $null = Stop-ODSDesktopApi $Paths.Ownership
    $listener = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listener.Count) {
        $owner = Get-Process -Id $listener[0].OwningProcess -ErrorAction SilentlyContinue
        throw "Port $port is used by $(if ($owner) { $owner.ProcessName } else { 'another program' }); nothing was stopped. Close it, or run ODS setup again with -Port and a free port."
    }
    $null = New-Item -ItemType Directory -Force -Path $Paths.Logs
    $out = Join-Path $Paths.Logs 'dashboard-api.out.log'
    $err = Join-Path $Paths.Logs 'dashboard-api.err.log'
    foreach ($log in @($out, $err)) { if (Test-Path -LiteralPath $log) { Move-Item -LiteralPath $log -Destination "$log.1" -Force } }
    foreach ($key in $environment.Keys) { [Environment]::SetEnvironmentVariable($key, [string]$environment[$key], 'Process') }
    $pythonw = Join-Path $Paths.Python 'pythonw.exe'
    $child = Start-Process -FilePath $pythonw -ArgumentList (New-ODSDesktopApiArguments $Paths.AppSource $port) `
        -WorkingDirectory $Paths.AppSource -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    try {
        $null = $child.Handle
        $identity = [pscustomobject]@{ ProcessId = $child.Id; CreationDate = $child.StartTime; ExecutablePath = $pythonw; ExitedAt = $null }
        Write-ODSPortalProcessOwnership $Paths.Ownership ([pscustomobject]@{ ExecutablePath = $pythonw; Port = $port }) @($identity)
        try {
            Wait-ODSDesktopApiHealth $port $child $err
        } catch {
            if (-not $child.HasExited) { $child.Kill(); $null = $child.WaitForExit(30000) }
            throw
        }
        $ready = [ordered]@{ ProcessId = $child.Id; Port = $port; StartedAt = $child.StartTime.ToUniversalTime().ToString('o') }
        Write-ODSPrivateEnvFile -Path $Paths.Ready -Content ($ready | ConvertTo-Json -Compress)
        $child.WaitForExit()
        return $child.ExitCode
    } finally {
        $child.Dispose()
    }
}
