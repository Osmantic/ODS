# Windows Desktop launch, end to end, with no Python, Node or Git on PATH:
# the pinned python.org embeddable Python, the hash-locked dashboard-api
# wheels, the published app and dashboard build, and the real sign-in
# launcher. Proves the app listens on 127.0.0.1 only, serves the dashboard
# through the loopback gate, injects the key for local requests, refuses a
# foreign Host, starts no child process, and is stopped through its record.
# No model is downloaded and no task is registered (CI runners are elevated;
# setup refuses that). Network: www.python.org, files.pythonhosted.org.
# Runs under Windows PowerShell 5.1 and PowerShell 7.
param([string]$Root = (Join-Path ([IO.Path]::GetTempPath()) ('ODS Desktop Jos' + [char]0x00E9 + ' ' + [guid]::NewGuid().ToString('N').Substring(0, 8))))
$ErrorActionPreference = 'Stop'
$ods = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$lib = Join-Path $ods 'installers/windows/lib'
foreach ($name in 'native-llama-runtime.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1') { . (Join-Path $lib $name) }
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Invoke-Get([int]$Port, [string]$Path, [string]$HostHeader = '') {
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$Port$Path")
    $request.Proxy = $null
    $request.Timeout = 10000
    if ($HostHeader) { $request.Host = $HostHeader }
    try { $response = $request.GetResponse() } catch [Net.WebException] { $response = $_.Exception.Response; if (-not $response) { throw } }
    try {
        $reader = [IO.StreamReader]::new($response.GetResponseStream(), [Text.UTF8Encoding]::new($false))
        try { return [pscustomobject]@{ Status = [int]$response.StatusCode; Body = $reader.ReadToEnd(); SignIn = $response.Headers['X-ODS-Sign-In'] } }
        finally { $reader.Dispose() }
    } finally { $response.Close() }
}
function Get-FreePort {
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $probe.Start()
    try { return ([Net.IPEndPoint]$probe.LocalEndpoint).Port } finally { $probe.Stop() }
}

# Only System32 on PATH (the Python launcher py.exe installs into C:\Windows
# itself), so nothing here can lean on a developer's tools.
$env:PATH = @((Join-Path $env:SystemRoot 'System32'), (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0')) -join ';'
$visible = @('python', 'python3', 'py', 'pip', 'node', 'npm', 'git' | Where-Object { Get-Command $_ -CommandType Application -ErrorAction SilentlyContinue })
Check ($visible.Count -eq 0) 'no Python, Node or Git is reachable on PATH'

$paths = Get-ODSDesktopPaths (Join-Path $Root 'desktop')
$launcher = $null
try {
    $lock = Join-Path $ods 'extensions/services/dashboard-api/requirements.lock'
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $null = Install-ODSDesktopPython -SourceRoot $ods -Destination $paths.Python -Requirements $lock
    Microsoft.PowerShell.Utility\Write-Host ("         Python and the locked packages installed in {0:N0} s" -f $clock.Elapsed.TotalSeconds)
    Check ($null -eq (Test-ODSDesktopPythonInstall -Directory $paths.Python -Requirements $lock -Full)) 'the installed Python matches its manifest hash for hash'

    Publish-ODSDesktopApp $ods $paths
    $dist = Join-Path $Root 'dist-fixture'
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $dist 'assets')
    [IO.File]::WriteAllText((Join-Path $dist 'index.html'), '<!doctype html><title>ODS Desktop fixture</title>')
    [IO.File]::WriteAllText((Join-Path $dist 'assets/app-1.js'), 'console.log(1)')
    Publish-ODSDesktopDist $dist $paths
    $runtimeDir = Join-Path $Root 'portal-runtime'
    $null = New-Item -ItemType Directory -Force -Path $runtimeDir, $paths.Data
    Write-ODSNativeLlamaApiKeyFile (Join-Path $runtimeDir 'api-key') (New-ODSNativeLlamaApiKey)
    $port = Get-FreePort
    $environment = New-ODSDesktopApiEnvironment $paths $port (Get-FreePort) $runtimeDir (New-ODSNativeLlamaApiKey)
    Write-ODSPrivateEnvFile -Path $paths.Environment -Content (ConvertTo-ODSDesktopEnvText $environment)

    # The launcher exactly as setup writes it, with the libraries it copies.
    $tokens = $null; $errors = $null
    $entry = [Management.Automation.Language.Parser]::ParseFile((Join-Path $ods 'installers/windows-desktop.ps1'), [ref]$tokens, [ref]$errors)
    $assignment = $entry.FindAll({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        [string]$node.Left -ceq '$script:ODSDesktopLaunchScript' }, $true) | Select-Object -First 1
    $null = New-Item -ItemType Directory -Force -Path $paths.Launcher
    foreach ($name in 'private-file.ps1', 'native-llama-runtime.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-api-launcher.ps1') {
        Copy-Item -LiteralPath (Join-Path $lib $name) -Destination (Join-Path $paths.Launcher $name)
    }
    Write-ODSPrivateEnvFile -Path $paths.LauncherScript -Content ($assignment.Right.Expression.Value)
    Write-ODSPrivateEnvFile -Path $paths.Intent -Content '{"State":"running"}'

    $clock.Restart()
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $launcherOut = Join-Path $Root 'launcher.out.log'; $launcherErr = Join-Path $Root 'launcher.err.log'
    # Task Scheduler starts the launcher with a fresh environment. From
    # PowerShell 7, Windows PowerShell would inherit 7's module path and fail
    # to load Microsoft.PowerShell.Security, so start it the way the task does.
    $modulePath = $env:PSModulePath
    $env:PSModulePath = ''
    try {
        $launcher = Start-Process -FilePath $shell -ArgumentList (Get-ODSDesktopApiLauncherArguments $paths) -WorkingDirectory $paths.Launcher `
            -RedirectStandardOutput $launcherOut -RedirectStandardError $launcherErr -PassThru
    } finally { $env:PSModulePath = $modulePath }
    $deadline = (Get-Date).AddSeconds(120)
    while (-not (Test-Path -LiteralPath $paths.Ready) -and (Get-Date) -lt $deadline -and -not $launcher.HasExited) { Start-Sleep -Milliseconds 250 }
    if (-not (Test-Path -LiteralPath $paths.Ready)) {
        $logs = foreach ($log in (Join-Path $paths.Logs 'launch-api.log'), (Join-Path $paths.Logs 'dashboard-api.err.log'), $launcherOut, $launcherErr) {
            if (Test-Path -LiteralPath $log) { (Split-Path -Leaf $log) + ': ' + [IO.File]::ReadAllText($log) }
        }
        throw "The launcher published no api-ready.json (exited: $($launcher.HasExited)). $($logs -join ' ')"
    }
    Check $true 'the launcher published api-ready.json'
    $ready = Read-ODSNativeLlamaJson $paths.Ready
    if ($ready.Error) { throw "$($ready.Error) $(Get-Content -LiteralPath (Join-Path $paths.Logs 'dashboard-api.err.log') -Raw -ErrorAction SilentlyContinue)" }
    Microsoft.PowerShell.Utility\Write-Host ("         The app answered /health {0:N1} s after the launcher started" -f $clock.Elapsed.TotalSeconds)

    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen)
    $app = Get-Process -Id ([int]$ready.ProcessId)
    Check ($listeners.Count -ge 1 -and -not ($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' -or $_.OwningProcess -ne $ready.ProcessId })) 'the app listens on 127.0.0.1 only'
    Check (([string]$app.Path).Equals((Join-Path $paths.Python 'pythonw.exe'), [StringComparison]::OrdinalIgnoreCase)) 'the listener is the pinned pythonw.exe'

    $page = Invoke-Get $port '/' "localhost:$port"
    Check ($page.Status -eq 200 -and $page.Body.Contains('ODS Desktop fixture')) 'the dashboard is served at / for this PC'
    $status = Invoke-Get $port '/api/status' "localhost:$port"
    Check ($status.Status -eq 200 -and ($status.Body | ConvertFrom-Json).runtimeProfile -ceq 'windows-desktop') 'a local request reaches /api/status with the key injected; the profile is windows-desktop'
    $chat = Invoke-Get $port '/api/local-chat/status' "127.0.0.1:$port"
    Check ($chat.Status -eq 200 -and ($chat.Body | ConvertFrom-Json).state -ne 'ready') 'local chat reports the stopped model plainly'
    $foreign = Invoke-Get $port '/api/status' "evil.example:$port"
    Check ($foreign.Status -eq 401 -and $foreign.SignIn) 'a foreign Host (DNS rebinding) gets 401 and the sign-in marker'
    Start-Sleep -Seconds 6
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($ready.ProcessId)")
    Check ($children.Count -eq 0) 'the app starts no child process (no curl, no nvidia-smi, no console windows)'

    Check ((Stop-ODSDesktopApi $paths.Ownership) -ge 1 -and $app.WaitForExit(30000)) 'the app is stopped through its ownership record'
    Check ($launcher.WaitForExit(30000)) 'the launcher ends with the app it supervised'
    Check (-not @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count) 'the port is free again'
} finally {
    if ($launcher -and -not $launcher.HasExited) { $launcher.Kill() }
    $null = Stop-ODSDesktopApi $paths.Ownership
    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop launch checks passed."
