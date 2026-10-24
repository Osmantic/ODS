param([switch]$SkipLiveProcesses)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/native-process-job.ps1')

$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
function Process-Alive([Diagnostics.Process]$Process) {
    try { $Process.Refresh(); return -not $Process.HasExited } catch { return $false }
}
function Wait-Exited([Diagnostics.Process]$Process, [int]$Milliseconds = 10000) {
    try { return $Process.HasExited -or $Process.WaitForExit($Milliseconds) } catch { return $true }
}

$quoted = ConvertTo-ODSNativeJobArgumentString @('C:\dir\', 'a"b', 'two words')
Check ($quoted -ceq '"C:\dir\\" "a\"b" "two words"') 'arguments use Windows CRT quoting rules'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Check ((Failure { Start-ODSNativeJobProcess -FilePath '/tmp/nope' -WorkingDirectory '/tmp' }) -match 'require Windows') 'non-Windows use fails closed before process creation'
    Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks native process job contracts (live Windows cases skipped)."
    exit 0
}

$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $root 'installers/windows/lib/native-process-job.ps1'),
    [ref]$tokens,
    [ref]$parseErrors)
Check ($parseErrors.Count -eq 0) 'the native process job helper parses'

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-native-job-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $fixture)
$sessions = [Collections.Generic.List[object]]::new()
$processes = [Collections.Generic.List[Diagnostics.Process]]::new()
try {
    $shell = (Get-Process -Id $PID).Path
    $missing = Join-Path $fixture 'secret-api-key-does-not-exist.exe'
    $message = Failure { Start-ODSNativeJobProcess -FilePath $missing -WorkingDirectory $fixture }
    Check ($message -ceq 'The managed executable must be an existing absolute file.' -and
        -not $message.Contains('secret-api-key')) 'invalid executable errors expose no supplied path or secret-like text'
    Check ((Failure { Start-ODSNativeJobProcess -FilePath $shell -WorkingDirectory (Join-Path $fixture 'missing') }) -ceq
        'The managed working directory must be an existing absolute directory.') 'invalid working directories fail before process creation'

    if (-not $SkipLiveProcesses) {
        $childScript = Join-Path $fixture 'child.ps1'
        $recordPath = Join-Path $fixture 'tree.json'
        $childSource = @'
param([string]$Shell, [string]$Record)
$grandchild = Start-Process -FilePath $Shell -ArgumentList @('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 120') -WindowStyle Hidden -PassThru
$null = $grandchild.Handle
$json = @{ Parent = $PID; Child = $grandchild.Id } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($Record + '.tmp', $json, [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath ($Record + '.tmp') -Destination $Record
Start-Sleep -Seconds 120
'@
        [IO.File]::WriteAllText($childScript, $childSource, [Text.UTF8Encoding]::new($true))

        $unrelated = Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 120') -WindowStyle Hidden -PassThru
        $null = $unrelated.Handle
        $processes.Add($unrelated)
        $session = Start-ODSNativeJobProcess -FilePath $shell -ArgumentList @(
            '-NoProfile', '-NonInteractive', '-File', $childScript, '-Shell', $shell, '-Record', $recordPath
        ) -WorkingDirectory $fixture
        $sessions.Add($session)
        $deadline = [datetime]::UtcNow.AddSeconds(15)
        while (-not (Test-Path -LiteralPath $recordPath) -and [datetime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 50
        }
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        $grandchild = Get-Process -Id ([int]$record.Child) -ErrorAction Stop
        $null = $grandchild.Handle
        $processes.Add($grandchild)
        Check ($record.Parent -eq $session.Process.Id -and (Process-Alive $session.Process) -and
            (Process-Alive $grandchild)) 'a suspended root resumes and creates its immediate descendant'
        Stop-ODSNativeJobProcess -Session $session -TimeoutMilliseconds 10000
        Check ((Wait-Exited $grandchild) -and (Process-Alive $unrelated)) 'closing the job terminates its tree and preserves an unrelated process'

        # Prove kernel cleanup when the PowerShell supervisor itself dies.
        $launcherScript = Join-Path $fixture 'launcher.ps1'
        $launcherRecord = Join-Path $fixture 'launcher-child.txt'
        $helperPath = Join-Path $root 'installers/windows/lib/native-process-job.ps1'
        $launcherSource = @'
param([string]$Helper, [string]$Shell, [string]$Record, [string]$Working)
. $Helper
$session = Start-ODSNativeJobProcess -FilePath $Shell -ArgumentList @('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 120') -WorkingDirectory $Working
[IO.File]::WriteAllText($Record, [string]$session.Process.Id, [Text.UTF8Encoding]::new($false))
Start-Sleep -Seconds 120
'@
        [IO.File]::WriteAllText($launcherScript, $launcherSource, [Text.UTF8Encoding]::new($true))
        $launcherArgs = @('-NoProfile', '-NonInteractive', '-File', $launcherScript,
            '-Helper', $helperPath, '-Shell', $shell, '-Record', $launcherRecord, '-Working', $fixture)
        $launcher = Start-Process -FilePath $shell -ArgumentList $launcherArgs -WindowStyle Hidden -PassThru
        $null = $launcher.Handle
        $processes.Add($launcher)
        $deadline = [datetime]::UtcNow.AddSeconds(15)
        while (-not (Test-Path -LiteralPath $launcherRecord) -and [datetime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 50
        }
        $launchedChild = Get-Process -Id ([int][IO.File]::ReadAllText($launcherRecord)) -ErrorAction Stop
        $null = $launchedChild.Handle
        $processes.Add($launchedChild)
        $launcher.Kill()
        Check ($launcher.WaitForExit(10000) -and (Wait-Exited $launchedChild 10000)) 'terminating the supervisor closes its job and prevents an orphan'
    }
} finally {
    foreach ($session in $sessions) {
        try { $session.Dispose() } catch { }
    }
    foreach ($process in $processes) {
        try {
            if (Process-Alive $process) {
                $process.Kill()
                [void]$process.WaitForExit(10000)
            }
        } catch { }
        $process.Dispose()
    }
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to clean a fixture outside the temporary directory.'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks native process job contracts."
