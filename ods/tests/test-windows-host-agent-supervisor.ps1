param([switch]$SkipLiveProcesses)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$supervisorScript = Join-Path $root 'installers/windows/lib/host-agent-supervisor.ps1'
. $supervisorScript

$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
function Wait-ForState([string]$Path, [string]$Actual, [int]$Seconds) {
    $deadline = [datetime]::UtcNow.AddSeconds($Seconds)
    while ([datetime]::UtcNow -lt $deadline) {
        try {
            $state = Read-ODSHostAgentSupervisorState $Path
            if ($state.actualState -ceq $Actual) { return $state }
        } catch { }
        Start-Sleep -Milliseconds 100
    }
    return $null
}
function Start-TestProcess([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $FilePath
    $info.Arguments = ConvertTo-ODSNativeJobArgumentString $Arguments
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw 'Could not start the supervisor fixture.' }
    $null = $process.Handle
    return $process
}
function New-TestInstall([string]$Base, [string]$Name, [int]$CrashBefore) {
    $install = Join-Path $Base $Name
    $bin = Join-Path $install 'bin'
    $data = Join-Path $install 'data'
    [void](New-Item -ItemType Directory -Path $bin, $data)
    [IO.File]::WriteAllText((Join-Path $data 'crash-before.txt'), [string]$CrashBefore)
    $fixtureAgent = @'
import argparse, http.server, json, os, pathlib, sys
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--pid-file", required=True)
parser.add_argument("--install-dir", required=True)
parser.add_argument("--log-file", required=True)
args = parser.parse_args()
data = pathlib.Path(args.install_dir) / "data"
count_path = data / "run-count.txt"
count = int(count_path.read_text() if count_path.exists() else "0") + 1
count_path.write_text(str(count))
pathlib.Path(args.pid_file).write_text(str(os.getpid()))
with open(args.log_file, "a", encoding="utf-8") as log:
    log.write("fixture run %d\n" % count)
crash_before = int((data / "crash-before.txt").read_text())
if count <= crash_before:
    sys.exit(23)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404); self.end_headers(); return
        body = json.dumps({"status": "ok", "version": "fixture"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
'@
    $agent = Join-Path $bin 'ods-host-agent.py'
    [IO.File]::WriteAllText($agent, $fixtureAgent, [Text.UTF8Encoding]::new($false))
    return $install
}
function Get-FreePort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}
function Publish-TestPlan([string]$Install, [int]$Port, [string]$Python) {
    $data = Join-Path $Install 'data'
    $paths = Write-ODSHostAgentSupervisorPlan -PythonPath $Python -AgentScript (Join-Path $Install 'bin/ods-host-agent.py') `
        -InstallDir $Install -Port $Port -PidFile (Join-Path $data 'agent.pid') `
        -LogFile (Join-Path $data 'agent.log') -DockerBin (Join-Path $Install 'docker-bin')
    Write-ODSHostAgentSupervisorIntent -InstallDir $Install -State running
    return $paths
}

$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile($supervisorScript, [ref]$tokens, [ref]$parseErrors)
Check ($parseErrors.Count -eq 0) 'the host-agent supervisor parses'

Check ((Get-ODSHostAgentNextRestartCount 4 299) -eq 5) 'a short run consumes the next restart budget slot'
Check ((Get-ODSHostAgentNextRestartCount 4 300) -eq 0) 'a stable run resets the restart budget'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $SkipLiveProcesses) {
    Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks host-agent supervisor contracts (live cases skipped)."
    exit 0
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-supervisor-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $fixture)
$supervisors = [Collections.Generic.List[Diagnostics.Process]]::new()
$managed = [Collections.Generic.List[Diagnostics.Process]]::new()
try {
    $python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $install = New-TestInstall $fixture 'healthy' 2
    [void](New-Item -ItemType Directory -Path (Join-Path $install 'docker-bin'))
    $port = Get-FreePort
    $paths = Publish-TestPlan $install $port $python

    $plan = Read-ODSHostAgentSupervisorJson $paths.Plan
    Assert-ODSHostAgentSupervisorPlan $plan
    Check ((Get-Acl -LiteralPath $paths.Plan).AreAccessRulesProtected -and
        (Get-Acl -LiteralPath $paths.Intent).AreAccessRulesProtected) 'plan and intent files have protected owner-private DACLs'
    $plan | Add-Member -NotePropertyName extra -NotePropertyValue unexpected
    Check ((Failure { Assert-ODSHostAgentSupervisorPlan $plan }) -match 'schema') 'an extra plan field is rejected'

    $corruptState = Join-Path $paths.Directory 'corrupt-state.json'
    Write-ODSPrivateEnvFile $corruptState '{"schemaVersion":1}'
    Check ((Failure { Read-ODSHostAgentSupervisorState $corruptState }) -match 'schema') 'malformed state is rejected'

    $log = Join-Path $install 'data/rotation.log'
    Write-ODSPrivateEnvFile $log ('x' * 1025)
    Rotate-ODSHostAgentLog $log 1024
    Check ((Get-Item $log).Length -eq 0 -and (Get-Item ($log + '.1')).Length -eq 1025) 'oversized logs rotate to one capped predecessor'

    $shell = (Get-Process -Id $PID).Path
    $supervisor = Start-TestProcess $shell @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $supervisorScript, '-Run', '-PlanPath', $paths.Plan) $install
    $supervisors.Add($supervisor)
    $running = Wait-ForState $paths.State running 25
    Check ($null -ne $running -and $running.restartCount -eq 2) 'crashes restart with bounded backoff before healthy publication'
    $agentProcess = Get-Process -Id ([int]$running.processId) -ErrorAction Stop
    $null = $agentProcess.Handle
    $managed.Add($agentProcess)
    $duplicate = Start-TestProcess $shell @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $supervisorScript, '-Run', '-PlanPath', $paths.Plan) $install
    $supervisors.Add($duplicate)
    Check ($duplicate.WaitForExit(5000) -and $duplicate.ExitCode -eq 2 -and -not $supervisor.HasExited) 'a second supervisor for the same user and installation is refused'
    Check ((Test-ODSHostAgentManagedHealth $port $agentProcess.Id) -and
        -not (Test-ODSHostAgentManagedHealth $port $PID)) 'health accepts only valid JSON from the managed listener PID'
    Check ((Get-Acl -LiteralPath $paths.State).AreAccessRulesProtected) 'atomic supervisor state is owner-private'

    Write-ODSHostAgentSupervisorIntent -InstallDir $install -State stopped
    Check ($supervisor.WaitForExit(15000)) 'stopped intent exits the foreground supervisor'
    $stopped = Wait-ForState $paths.State stopped 3
    Check ($null -ne $stopped -and $stopped.desiredState -ceq 'stopped') 'stopped intent publishes a final stopped receipt'
    Check ($agentProcess.WaitForExit(10000)) 'stopping the supervisor terminates its owned host-agent job'

    $loopInstall = New-TestInstall $fixture 'crash-loop' 99
    [void](New-Item -ItemType Directory -Path (Join-Path $loopInstall 'docker-bin'))
    $loopPaths = Publish-TestPlan $loopInstall (Get-FreePort) $python
    $loopSupervisor = Start-TestProcess $shell @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $supervisorScript, '-Run', '-PlanPath', $loopPaths.Plan) $loopInstall
    $supervisors.Add($loopSupervisor)
    $crashLoop = Wait-ForState $loopPaths.State crash-loop 35
    Check ($null -ne $crashLoop -and $crashLoop.restartCount -eq 5 -and
        $loopSupervisor.WaitForExit(5000)) 'the restart budget ends in a durable crash-loop state'
} finally {
    foreach ($process in $supervisors) {
        try {
            if (-not $process.HasExited) { $process.Kill(); [void]$process.WaitForExit(10000) }
        } catch { }
        $process.Dispose()
    }
    foreach ($process in $managed) {
        try {
            if (-not $process.HasExited) { $process.Kill(); [void]$process.WaitForExit(10000) }
        } catch { }
        $process.Dispose()
    }
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to clean a fixture outside the temporary directory.'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks host-agent supervisor contracts."
