# Windows Desktop dashboard-api launcher with real Windows processes: it
# stops only processes its record proves (path and start time), including
# their children, never a reused PID; it refuses an occupied port without
# stopping its owner; a stopped intent starts nothing; a damaged Python
# runtime is reported before anything runs. Only temporary files and
# short-lived ping.exe/cmd.exe processes are used. Runs under Windows
# PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$lib = Join-Path $root 'installers/windows/lib'
foreach ($name in 'native-llama-runtime.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1') { . (Join-Path $lib $name) }
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
$ping = Join-Path ([Environment]::SystemDirectory) 'PING.EXE'
$cmd = Join-Path ([Environment]::SystemDirectory) 'cmd.exe'
function Start-Fixture([string]$Path, [string]$Arguments) {
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    $null = $process.Handle
    return $process
}
# The known executable, never $Process.Path: Windows can report that empty
# for a process that has only just started.
function Write-Record([string]$Path, $Process, [string]$ExecutablePath) {
    $identity = [pscustomobject]@{ ProcessId = $Process.Id; CreationDate = $Process.StartTime; ExecutablePath = $ExecutablePath; ExitedAt = $null }
    Write-ODSPortalProcessOwnership $Path ([pscustomobject]@{ ExecutablePath = $ExecutablePath; Port = 3101 }) @($identity)
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-launcher-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$started = @()
try {
    $record = Join-Path $fixture 'api-ownership.json'

    # --- A recorded process is stopped, and the record removed -----------------
    $owned = Start-Fixture $ping '-n 120 127.0.0.1'; $started += $owned
    Write-Record $record $owned $ping
    Check ((Stop-ODSDesktopApi $record) -ge 1 -and $owned.WaitForExit(10000) -and -not (Test-Path -LiteralPath $record)) 'a process the record proves is stopped and the record removed'

    # --- Its children go with it (console programs also get a conhost.exe) ----
    $parent = Start-Fixture $cmd ('/c ""' + $ping + '" -n 120 127.0.0.1"'); $started += $parent
    $deadline = (Get-Date).AddSeconds(10)
    do { $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($parent.Id)" | Where-Object { $_.Name -ieq 'PING.EXE' }); Start-Sleep -Milliseconds 100 }
    while (-not $children.Count -and (Get-Date) -lt $deadline)
    $child = Get-Process -Id $children[0].ProcessId; $started += $child
    Write-Record $record $parent $cmd
    Check ((Stop-ODSDesktopApi $record) -ge 2 -and $parent.WaitForExit(10000) -and $child.WaitForExit(10000)) 'a recorded parent is stopped together with the child it started'

    # --- A reused PID or a different program is never touched ------------------
    $self = Get-Process -Id $PID
    $identity = [pscustomobject]@{ ProcessId = $PID; CreationDate = $self.StartTime.AddMinutes(-5); ExecutablePath = $self.Path; ExitedAt = $null }
    Write-ODSPortalProcessOwnership $record ([pscustomobject]@{ ExecutablePath = $self.Path; Port = 3101 }) @($identity)
    Check ($null -eq (Get-ODSDesktopApiOwnedTree $record) -and (Stop-ODSDesktopApi $record) -eq 0 -and -not $self.HasExited) 'a PID Windows gave to another process (different start time) is left running'
    $other = Start-Fixture $ping '-n 120 127.0.0.1'; $started += $other
    Write-Record $record $other 'C:\ODS\desktop\python\pythonw.exe'
    Check ((Stop-ODSDesktopApi $record) -eq 0 -and -not $other.HasExited) 'a live process at the recorded PID but from another executable is left running'
    $other.Kill(); $null = $other.WaitForExit(10000)
    Write-Record $record $other $ping
    Check ((Stop-ODSDesktopApi $record) -eq 0 -and -not (Test-Path -LiteralPath $record)) 'a record whose process already exited is simply cleared'
    Check ((Stop-ODSDesktopApi $record) -eq 0) 'no record, nothing to stop'

    # --- A process that dies at startup explains itself ------------------------
    $errLog = Join-Path $fixture 'dashboard-api.err.log'
    [IO.File]::WriteAllText($errLog, "ModuleNotFoundError: No module named 'uvicorn'`nDASHBOARD_API_KEY=secret-value`n")
    $quick = Start-Fixture $cmd '/c exit 3'; $started += $quick
    $null = $quick.WaitForExit(10000)
    $failure = Get-Failure { Wait-ODSDesktopApiHealth 1 $quick $errLog 10 }
    Check ($failure -match 'exit code 3' -and $failure -match "No module named 'uvicorn'" -and $failure -notmatch 'secret-value') 'an app that exits during startup reports its error log, never a key line'

    # --- The launcher body ------------------------------------------------------
    $paths = Get-ODSDesktopPaths (Join-Path $fixture ('Jos' + [char]0x00E9 + '\desktop'))
    $null = New-Item -ItemType Directory -Force -Path $paths.AppSource, $paths.Python
    [IO.File]::WriteAllText($paths.Intent, '{"State":"stopped"}')
    Check ((Invoke-ODSDesktopApi $paths) -eq 0 -and -not (Test-Path -LiteralPath $paths.Ready)) 'a stopped intent starts nothing (an Unload or uninstall in progress)'
    [IO.File]::WriteAllText($paths.Intent, '{"State":"running"}')
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
        $environment = New-ODSDesktopApiEnvironment $paths $port 18080 'C:\ODS\portal-runtime' ('k' * 64)
        [IO.File]::WriteAllText($paths.Environment, (ConvertTo-ODSDesktopEnvText $environment), [Text.UTF8Encoding]::new($false))
        Check ((Get-Failure { Invoke-ODSDesktopApi $paths }) -match 'not installed\. Run ODS setup again') 'a missing Python runtime is reported before anything starts'
        # A runtime whose manifest matches its files, as Install-ODSDesktopPython writes it.
        [IO.File]::WriteAllText((Join-Path $paths.Python 'pythonw.exe'), 'fixture')
        $lock = Join-Path $paths.AppSource 'requirements.lock'
        [IO.File]::WriteAllText($lock, 'fastapi==1.0')
        $manifest = [ordered]@{ schemaVersion = 1; python = '3.13.16'; pip = '26.2.1'; requirementsSha256 = (Get-ODSNativeLlamaFileSha256 $lock)
            files = @(Get-ODSDesktopPythonFiles $paths.Python) }
        [IO.File]::WriteAllText((Join-Path $paths.Python 'ods-python.json'), ($manifest | ConvertTo-Json -Depth 4 -Compress))
        $failure = Get-Failure { Invoke-ODSDesktopApi $paths }
        Check ($failure -match "Port $port is used by" -and $failure -match 'nothing was stopped' -and $failure -match 'with -Port' -and $listener.Server.IsBound) 'an occupied port is named and its owner is left alone'
        [IO.File]::WriteAllText($lock, 'fastapi==2.0')
        Check ((Get-Failure { Invoke-ODSDesktopApi $paths }) -match 'older ODS version') 'packages from an older lock are caught at startup'
    } finally {
        $listener.Stop()
    }
} finally {
    foreach ($process in $started) { if (-not $process.HasExited) { $process.Kill() }; $process.Dispose() }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop launcher checks passed."
