param([string]$Scenario = '')
$ErrorActionPreference = 'Stop'

if ($Scenario) {
    . (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -Distro 'Receipt-Fixture'
    # Only external authority boundaries are replaced. The real stack planner,
    # lifecycle ordering, and public PowerShell result pipeline remain in use.
    $script:fixtureRunning = $true
    function Get-ODSWslIdentity { param($Distro, $InstallRoot)
        [pscustomobject]@{distro=$Distro; installRoot=$InstallRoot; directory=$env:TEMP}
    }
    function Initialize-ODSPrivateDirectory { param($Path) }
    function Open-ODSPrivateLock { param($Path); [IO.MemoryStream]::new() }
    function Get-ODSWslLifetimeStatus { param($Identity)
        [pscustomobject]@{schemaVersion=1;scope='wsl-lifetime';state=$(if($script:fixtureRunning){'running'}else{'stopped'});distroRunning=$script:fixtureRunning}
    }
    function Start-ODSWslLifetime { param($Identity)
        $script:fixtureRunning=$true
        Get-ODSWslLifetimeStatus $Identity
    }
    function Stop-ODSWslLifetime { param($Identity)
        $script:fixtureRunning=$false
        Get-ODSWslLifetimeStatus $Identity
    }
    function Invoke-ODSWslCommand { param($Identity,[string[]]$Arguments,[switch]$AsRoot)
        if ($AsRoot -or $Arguments[0] -ne 'python3') { throw 'Unexpected external command' }
        if ($Arguments[2] -like 'plan-*') {
            return ([pscustomobject]@{schemaVersion=1;action=$Arguments[2].Substring(5);installRoot=$Identity.installRoot;ownerUid=1000;nativeUnits=@()} | ConvertTo-Json -Compress)
        }
        if ($Arguments[2] -notin @('compose-start','compose-stop')) { throw 'Unexpected adapter action' }
        # The real adapter inherits ods-cli stdout and then prints its receipt.
        'Starting ODS fixture service...'
        '{"state":"started","installRoot":"/home/fixture/ods"}'
        if ($Scenario -eq 'failure') { throw 'fixture compose failure' }
    }
    try {
        $requested = if ($Scenario -eq 'failure') { 'start' } else { $Scenario }
        Invoke-ODSWslLifecycle $requested 'Receipt-Fixture' '/home/fixture/ods' | ConvertTo-Json -Depth 8
        exit 0
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 1
    }
}

$failures = @()
foreach ($case in @('start','stop','restart','status','release','failure')) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $start.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Scenario ' + $case
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw 'Could not start receipt fixture' }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'Receipt fixture timed out'
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($case -eq 'failure') {
            if ($process.ExitCode -eq 0 -or $stdout.Trim() -ne '' -or $stderr -notmatch 'fixture compose failure') {
                throw 'failed Compose must return nonzero without a success JSON receipt'
            }
        } else {
            if ($process.ExitCode -ne 0) { throw $stderr }
            $result = $stdout | ConvertFrom-Json -ErrorAction Stop
            if ($result -is [Array] -or $result.scope -cne 'wsl-lifetime' -or $result.schemaVersion -ne 1) {
                throw ('expected one lifecycle object, received: ' + $stdout.Trim())
            }
            $expectedState = if ($case -in @('stop','release')) { 'stopped' } else { 'running' }
            if ($result.state -cne $expectedState) { throw 'wrong final lifecycle state' }
            if ($case -in @('start','stop','restart') -and $stderr -notmatch 'Starting ODS fixture service') {
                throw 'stack diagnostics were lost instead of being sent to stderr'
            }
        }
        Write-Host "PASS $case has an unambiguous process result"
    } catch {
        $failures += "$case : $($_.Exception.Message)"
    } finally {
        $process.Dispose()
    }
}
if ($failures.Count) { throw ($failures -join [Environment]::NewLine) }
Write-Host 'Passed 6 native PowerShell process checks; no WSL, Scheduler, or service mutation was performed.'
