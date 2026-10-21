param([string]$Scenario = '', [string]$ControllerRoot = '', [switch]$ModulePathOnly)
$ErrorActionPreference = 'Stop'
if (-not $ControllerRoot) { $ControllerRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../installers')) }
if ($Scenario -eq 'dot-source-module-path') {
    $before = $env:PSModulePath
    . (Join-Path $ControllerRoot 'wsl-lifecycle.ps1')
    . (Join-Path $ControllerRoot 'windows/lib/portal-model-control.ps1')
    if ($env:PSModulePath -cne $before) { throw 'Dot-sourcing changed the caller module path' }
    'DOT_SOURCE_MODULE_PATH_UNCHANGED'
    exit 0
}

if ($Scenario) {
    . (Join-Path $PSScriptRoot '../../installers/wsl-lifecycle.ps1') -Distro 'Receipt-Fixture'
    function Resolve-ODSWslRegisteredDistro { param($Name); $Name }
    function Enable-ODSWslStartup { param($Identity) }
    function Enable-ScheduledTask { param($TaskName) }
    function Set-ODSWslStartupIntent { param($Identity,[bool]$Running);$script:fixtureIntent=[pscustomobject]@{desiredRunning=$Running;generation=('a'*32)} }
    function Get-ODSWslStartupIntent { param($Identity);$script:fixtureIntent }
    function Write-ODSPrivateBytes { param($Path,$Bytes,[switch]$CreateOnly) }
    function Assert-ODSWslCommandSettled { param($Identity,[switch]$MayStartDistribution) }
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
    function Update-ODSWslAgentAddress { param($Identity); [pscustomobject]@{mode='unmanaged';changed=$false} }
    function Stop-ODSWslAgentRelay { param($Identity) }
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
$cases = if ($ModulePathOnly) { @() } else { @('start','stop','restart','status','release','failure') }
foreach ($case in $cases) {
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
# Real controller entrypoints under a conflicting parent module search path.
# The holder fixture has an invalid manifest: it must pass the actual ACL read,
# then reject the distro before acquiring a lock, starting WSL or writing state.
. (Join-Path $ControllerRoot 'wsl-lifecycle.ps1')
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-wsl-module-path-' + [guid]::NewGuid().ToString('N'))
$parentModulePath = $env:PSModulePath
try {
    Initialize-ODSPrivateDirectory $fixture
    $private = Join-Path $fixture 'private'; Initialize-ODSPrivateDirectory $private
    # Elevated Windows runners can give a raw new file the Administrators owner.
    # Match production's explicit owner custody before testing the child ACL read.
    $manifestPath = Join-Path $private 'instance.json'
    Write-ODSWslJson $manifestPath @{distro='';installRoot=''}
    if ((Get-Acl -LiteralPath $manifestPath).GetOwner([Security.Principal.SecurityIdentifier]).Value -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) { throw 'Fixture manifest owner does not match the controller user' }
    $exposed = Join-Path $fixture 'exposed'
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User); $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'), 'Read', 'Allow'))
    if ($PSVersionTable.PSEdition -eq 'Desktop') { [void][IO.Directory]::CreateDirectory($exposed, $acl) }
    else { [void][IO.FileSystemAclExtensions]::Create([IO.DirectoryInfo]::new($exposed), $acl) }
    $poison = Join-Path $fixture 'modules'
    foreach ($module in @('Microsoft.PowerShell.Security','Microsoft.PowerShell.Utility')) {
        $directory = Join-Path $poison $module; [void][IO.Directory]::CreateDirectory($directory)
        $functions = if ($module -like '*.Security') { @('Get-Acl') } else { @('ConvertFrom-Json','ConvertTo-Json') }
        $body = ($functions | ForEach-Object { "function $_ { throw 'CONTAMINATED_PARENT_MODULE' }" }) -join "`n"
        [IO.File]::WriteAllText((Join-Path $directory 'conflict.psm1'), ($body + "`nExport-ModuleMember -Function *"))
        $exports = ($functions | ForEach-Object { "'$_'" }) -join ','
        [IO.File]::WriteAllText((Join-Path $directory ($module + '.psd1')), ("@{RootModule='conflict.psm1';ModuleVersion='99.0.0';FunctionsToExport=@($exports)}"))
    }
    $nativeModules = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\Modules'
    $manifestBytes = [IO.File]::ReadAllText((Join-Path $private 'instance.json'))
    foreach ($case in @('private-acl','exposed-acl','model-json','dot-source-module-path')) {
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        $start.UseShellExecute = $false; $start.CreateNoWindow = $true
        $start.RedirectStandardInput = $true; $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
        $start.EnvironmentVariables['PSModulePath'] = $poison + ';' + $nativeModules
        if ($case -eq 'model-json') {
            $start.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $ControllerRoot 'windows/portal-model-control.ps1') + '"'
        } elseif ($case -eq 'dot-source-module-path') {
            $start.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Scenario dot-source-module-path -ControllerRoot "' + $ControllerRoot + '"'
        } else {
            $directory = if ($case -eq 'private-acl') { $private } else { $exposed }
            $start.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $ControllerRoot 'wsl-lifecycle.ps1') + '" -Action hold -InstanceDirectory "' + $directory + '"'
        }
        $process = [Diagnostics.Process]::new(); $process.StartInfo = $start; $stdout = ''; $stderr = ''; $exitCode = $null
        try {
            # Match the Python bridge's BOM-free UTF-8 bytes. .NET Framework's
            # redirected writer inherits Console.InputEncoding when Start runs.
            $inputEncoding = [Console]::InputEncoding
            try {
                if ($case -eq 'model-json') { [Console]::InputEncoding = [Text.UTF8Encoding]::new($false) }
                if (-not $process.Start()) { throw 'Could not start module-path fixture' }
            } finally { [Console]::InputEncoding = $inputEncoding }
            $stdoutTask = $process.StandardOutput.ReadToEndAsync(); $stderrTask = $process.StandardError.ReadToEndAsync()
            if ($case -eq 'model-json') { $process.StandardInput.Write('{"action":"unknown","distro":"fixture","installDir":"/fixture"}') }
            $process.StandardInput.Close()
            if (-not $process.WaitForExit(15000)) { $process.Kill(); $process.WaitForExit(); throw 'Module-path fixture timed out' }
            $stdout = $stdoutTask.GetAwaiter().GetResult(); $stderr = $stderrTask.GetAwaiter().GetResult()
            $exitCode = $process.ExitCode
            if ($case -eq 'model-json') {
                $receipt = $stdout | ConvertFrom-Json -ErrorAction Stop
                if ($process.ExitCode -ne 1 -or $receipt.code -cne 'invalid_request' -or $receipt.ok -ne $false -or $stderr.Trim()) { throw 'Contaminated module path broke the finite JSON error boundary' }
            } elseif ($case -eq 'dot-source-module-path') {
                if ($process.ExitCode -ne 0 -or $stdout.Trim() -cne 'DOT_SOURCE_MODULE_PATH_UNCHANGED' -or $stderr.Trim()) { throw 'Dot-sourcing did not preserve the caller module path' }
            } else {
                $expected = if ($case -eq 'private-acl') { 'Invalid WSL distribution name' } else { 'Lifecycle path is accessible to another identity' }
                if ($process.ExitCode -eq 0 -or $stdout.Trim() -or $stderr -notmatch [regex]::Escape($expected) -or $stderr -match 'CONTAMINATED_PARENT_MODULE|CouldNotAutoloadMatchingModule') { throw "Controller did not enforce the expected native ACL/identity guard: $case" }
            }
            Write-Host "PASS module-path $case uses the real PS5 process boundary"
        } catch { $failures += "$case : $($_.Exception.Message); child exit: $exitCode; stdout: $($stdout.Substring(0, [Math]::Min(2048, $stdout.Length))); stderr: $($stderr.Substring(0, [Math]::Min(2048, $stderr.Length)))" }
        finally { if ($process.Id -and -not $process.HasExited) { $process.Kill(); $process.WaitForExit() }; $process.Dispose() }
    }
    if ($env:PSModulePath -cne $parentModulePath) { throw 'Controller tests changed the parent module path' }
    if ([IO.File]::ReadAllText((Join-Path $private 'instance.json')) -cne $manifestBytes -or @(Get-ChildItem -LiteralPath $private).Count -ne 1 -or @(Get-ChildItem -LiteralPath $exposed).Count -ne 0) { throw 'Read-only controller rejection changed fixture state' }
} finally {
    $resolved = [IO.Path]::GetFullPath($fixture); $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Module fixture escaped temporary directory' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
if ($failures.Count) { throw ($failures -join [Environment]::NewLine) }
Write-Host ('Passed ' + ($cases.Count + 4) + ' native PowerShell process checks; no WSL, Scheduler, or service mutation was performed.')
