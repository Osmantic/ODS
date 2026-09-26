# Durable Lemonade task: filesystem fixtures and mocked processes/APIs.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-amd.ps1')
$script:restartChecks = 0
function Assert-Restart([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:restartChecks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ("ods-modern-task-' " + [guid]::NewGuid().ToString('N'))
$previousLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $fixture
New-Item -ItemType Directory -Path $fixture -Force | Out-Null
try {
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory)
        return [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
    }
    $contract = [pscustomobject]@{
        Modern = $true; Version = [Version]'10.7.0'; ExecutablePath = (Join-Path (Join-Path $fixture 'Lemonade') 'LemonadeServer.exe')
        Port = 13305; ModelsDir = (Join-Path $fixture 'my models'); ContextSize = 65536
        AdminApiKey = 'secret-must-not-enter-launcher'
    }
    $registration = New-ODSPortalLemonadeRuntimeAction $contract 'Model-9B.gguf'
    $runtimeDir = Split-Path -Parent $registration.ReadyPath
    $saved = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json
    Assert-Restart ($saved.ModelsDir -eq $contract.ModelsDir -and $saved.GgufFile -eq 'Model-9B.gguf' -and $saved.ContextSize -eq 65536) 'durable JSON preserves selected model, context and paths without command interpolation'
    Assert-Restart ($registration.Action.Arguments -match '-NoProfile.+-WindowStyle Hidden.+-File' -and
        $registration.Action.Arguments.Contains((Join-Path $runtimeDir 'launch.ps1'))) 'Portal task launches its durable script hidden'
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    if ($onWindows) { $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User }
    foreach ($name in @('runtime.json', 'launch.ps1', 'backend-contract.ps1', 'env-generator.ps1')) {
        $path = Join-Path $runtimeDir $name
        if ($onWindows) {
            $acl = Get-Acl -LiteralPath $path
            $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
            Assert-Restart ($acl.AreAccessRulesProtected -and $rules.Count -eq 1 -and $rules[0].IdentityReference -eq $sid) "$name is private to the Windows user"
        } else {
            Assert-Restart (Test-Path -LiteralPath $path -PathType Leaf) "$name is materialized (Windows ACL checks run on Windows)"
        }
        Assert-Restart (-not (Get-Content -LiteralPath $path -Raw).Contains($contract.AdminApiKey)) "$name contains no launcher credential"
    }
    $tokens = $null; $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $runtimeDir 'launch.ps1'), [ref]$tokens, [ref]$parseErrors)
    Assert-Restart ($parseErrors.Count -eq 0) 'generated durable launcher parses in this PowerShell version'
    $launcher = Get-Content -LiteralPath (Join-Path $runtimeDir 'launch.ps1') -Raw
    Assert-Restart ($launcher.Contains("Join-Path `$PSScriptRoot 'backend-contract.ps1'") -and
        $launcher.Contains('Invoke-ODSPortalLemonadeRuntime $plan') -and -not $launcher.Contains($PSScriptRoot)) 'task dependencies resolve beside the durable launcher, independently of the checkout'

    # Simulate sign-in with a fresh process and empty server configuration.
    $script:runtimePlan = $registration.Plan
    $script:calls = [Collections.Generic.List[string]]::new()
    $script:launched = $false; $script:occupied = $false; $script:foreignAfter = $false
    $script:descendant = $false; $script:foreignParent = $false; $script:lookalikeDir = $false; $script:oldListener = $false
    $script:healthy = $true; $script:failConfig = $false; $script:failLoad = $false
    $script:modernMode = $true
    $script:buildLaunchContract = ${function:Get-ODSLemonadeLaunchContract}
    function New-FakeLemonadeChild {
        $child = [pscustomobject]@{ Id = 4242; StartTime = [datetime]'2026-01-01T00:00:00'; HasExited = $false; ExitCode = 0; Killed = $false }
        $child | Add-Member ScriptMethod WaitForExit { $script:calls.Add('wait'); $this.HasExited = $true }
        $child | Add-Member ScriptMethod Kill { $script:calls.Add('kill'); $this.Killed = $true; $this.HasExited = $true }
        return $child
    }
    function Get-ODSLemonadeLaunchContract { param($ExecutablePath, $Port, $ModelsDir, $ContextSize)
        $version = if ($script:modernMode) { '10.7.0' } else { '10.0.0' }
        return & $script:buildLaunchContract -ExecutablePath $ExecutablePath -Port $Port -ModelsDir $ModelsDir `
            -ContextSize $ContextSize -VersionOverride $version
    }
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction)
        if ($script:occupied -or ($script:launched -and $script:foreignAfter)) {
            return [pscustomobject]@{ LocalAddress = '127.0.0.1'; OwningProcess = 9999 }
        }
        if ($script:launched) {
            $owner = if ($script:descendant) { 4243 } else { 4242 }
            return [pscustomobject]@{ LocalAddress = '127.0.0.1'; OwningProcess = $owner }
        }
    }
    function Get-Process { param($Id, $ErrorAction)
        return [pscustomobject]@{ Path = $script:runtimePlan.ExecutablePath; StartTime = [datetime]'2026-01-01T00:00:00' }
    }
    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction)
        $parent = if ($script:foreignParent -or $Filter -ne 'ProcessId=4243') { 99 } else { 4242 }
        $directory = Split-Path -Parent $script:runtimePlan.ExecutablePath
        if ($script:lookalikeDir) { $directory += '-other' }
        $created = if ($script:oldListener) { [datetime]'2025-01-01T00:00:00' } else { [datetime]'2026-01-01T00:00:01' }
        return [pscustomobject]@{ ExecutablePath = (Join-Path $directory 'lemonade-router.exe'); ParentProcessId = $parent; CreationDate = $created }
    }
    function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
        $expected = if ($script:modernMode) { '--port 13305 --host 127.0.0.1' } else {
            'serve --port 13305 --host 127.0.0.1 --no-tray --llamacpp vulkan --extra-models-dir "' + $script:runtimePlan.ModelsDir + '" --ctx-size 65536'
        }
        Assert-Restart ($WindowStyle -eq 'Hidden' -and $ArgumentList -eq $expected) 'restarted server retains its version-specific flags, hidden window and loopback binding'
        $script:calls.Add('start'); $script:launched = $true
        $script:configured = $false; $script:loaded = ''
        $script:child = New-FakeLemonadeChild
        return $script:child
    }
    function Wait-ODSPortalLemonadeHealth($Port, $Seconds) { $script:calls.Add('health'); return $script:healthy }
    function Set-ODSLemonadeModernRuntimeConfig { param($Port, $ModelsDir, $ContextSize)
        $script:calls.Add('config')
        if ($script:failConfig) { throw 'configuration verification failed' }
        Assert-Restart ($Port -eq 13305 -and $ModelsDir -eq $script:runtimePlan.ModelsDir -and $ContextSize -eq 65536) 'restart restores the planned models directory and context through the shared verifier'
        $script:configured = $true
    }
    function Resolve-ODSLemonadeModelId { param($Port, $GgufFile, $VersionOverride)
        $script:calls.Add('resolve')
        if ($script:modernMode) { return 'Model-9B' }
        return 'extra.Model-9B.gguf'
    }
    function Set-ODSLemonadeLoadedModel { param($Port, $ModelId, $ContextSize, $TimeoutSec)
        $script:calls.Add('load')
        if ($script:failLoad) { throw 'loaded context verification failed' }
        $expectedModel = if ($script:modernMode) { 'Model-9B' } else { 'extra.Model-9B.gguf' }
        Assert-Restart (($script:configured -or -not $script:modernMode) -and $ModelId -eq $expectedModel -and $ContextSize -eq 65536 -and $TimeoutSec -eq 900) 'restart loads the exact selected model even when health is already green but nothing is loaded'
        $script:loaded = $ModelId
    }
    foreach ($mode in @($false, $true)) {
        $script:modernMode = $mode
        foreach ($boot in @(1, 2)) {
            $script:launched = $false; $script:calls.Clear()
            $script:descendant = $boot -eq 2
            $code = Invoke-ODSPortalLemonadeRuntime $registration.Plan $registration.ReadyPath
            $ready = Get-Content -LiteralPath $registration.ReadyPath -Raw | ConvertFrom-Json
            $expectedModel = if ($mode) { 'Model-9B' } else { 'extra.Model-9B.gguf' }
            $expectedCalls = if ($mode) { 'start,health,config,resolve,load,wait' } else { 'start,health,resolve,load,wait' }
            Assert-Restart ($code -eq 0 -and $ready.ModelId -eq $expectedModel -and $ready.ProcessId -eq 4242 -and
                ($script:calls -join ',') -eq $expectedCalls) "sign-in $boot (modern=$mode) verifies the restored model before publishing readiness"
        }
    }
    Assert-Restart ((Wait-ODSPortalLemonadeReady $registration 1) -eq 'Model-9B') 'setup accepts only readiness tied to its owned listener'
    foreach ($failure in @('failConfig', 'failLoad', 'foreignAfter', 'foreignParent', 'lookalikeDir', 'oldListener')) {
        $script:launched = $false; $script:calls.Clear()
        Set-Variable -Name $failure -Value $true -Scope Script
        $message = ''
        try { $null = Invoke-ODSPortalLemonadeRuntime $registration.Plan $registration.ReadyPath } catch { $message = $_.Exception.Message }
        Assert-Restart ($message -and -not (Test-Path -LiteralPath $registration.ReadyPath) -and $script:child.Killed) "$failure publishes no readiness and stops only the child it launched"
        Set-Variable -Name $failure -Value $false -Scope Script
    }
    $script:occupied = $true; $script:launched = $false; $script:calls.Clear()
    $message = ''
    try { $null = Invoke-ODSPortalLemonadeRuntime $registration.Plan $registration.ReadyPath } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'already occupied' -and $script:calls.Count -eq 0) 'occupied port is refused without launching or configuring another process'
    $message = ''
    try { $null = Wait-ODSPortalLemonadeReady $registration 0 } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'did not finish restoring') 'setup readiness wait is bounded and names the launcher log'
    Write-ODSPrivateEnvFile -Path $registration.ReadyPath -Content '{"Error":"Lemonade startup failed; check launcher log."}'
    $message = ''
    try { $null = Wait-ODSPortalLemonadeReady $registration 1 } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'startup failed') 'launcher failure is reported immediately instead of waiting for the load deadline'

    # Teardown uses mocked Task Scheduler/CIM/process handles. No real task or
    # process API is invoked, including for malformed or missing ownership.
    function Get-ODSPortalUserSid([string]$UserId) {
        if (-not $UserId -or $UserId -eq 'fixture-user') { return 'S-1-5-21-1' }
        return 'S-1-5-21-2'
    }
    function Get-ScheduledTask { param($TaskName, $ErrorAction, $ErrorVariable)
        $script:taskReads++
        if ($null -eq $script:stopTask) { return }
        if ($script:changedTask -and $script:taskReads -gt 1) {
            return [pscustomobject]@{ TaskPath = '\'; Principal = $script:stopTask.Principal; Actions = @(); State = 'Running' }
        }
        return $script:stopTask
    }
    function Get-ODSPortalTaskEngineId { return $script:engineId }
    function Get-CimInstance { param($ClassName, $ErrorAction) return $script:processNodes }
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction)
        if ($script:portTaken) { return [pscustomobject]@{ LocalAddress = '127.0.0.1'; OwningProcess = 9999 } }
    }
    function Stop-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:stopCalls.Add('task') }
    function Get-Process { param($Id, $ErrorAction)
        $node = @($script:processNodes | Where-Object { $_.ProcessId -eq $Id })[0]
        if (-not $node) { throw 'Mock process no longer exists.' }
        $started = $node.CreationDate
        if ($script:reusedPid -eq $Id) { $started = $started.AddMinutes(1) }
        $handle = [pscustomobject]@{ Id = $Id; Handle = $Id; Path = $node.ExecutablePath; StartTime = $started; HasExited = $false }
        $handle | Add-Member ScriptMethod Kill { $script:stopCalls.Add("kill:$($this.Id)"); $this.HasExited = $true }
        $handle | Add-Member ScriptMethod WaitForExit { param($Milliseconds) return $this.HasExited }
        $handle | Add-Member ScriptMethod Dispose { $script:disposed.Add([int]$this.Id) }
        return $handle
    }
    function Reset-StopFixture {
        $script:stopCalls = [Collections.Generic.List[string]]::new()
        $script:disposed = [Collections.Generic.List[int]]::new()
        $script:engineId = 101; $script:reusedPid = 0; $script:portTaken = $false
        $script:changedTask = $false; $script:taskReads = 0
        $script:stopArgs = 'serve --port 13305 --host 127.0.0.1 --no-tray --llamacpp vulkan --extra-models-dir "' + (Join-Path (Get-ODSPortalStateDir) 'models') + '" --ctx-size 65536'
        $script:stopTask = [pscustomobject]@{
            TaskPath = '\'; State = 'Running'; Principal = [pscustomobject]@{ UserId = 'fixture-user' }
            Actions = @([pscustomobject]@{ Execute = $contract.ExecutablePath; Arguments = $script:stopArgs; WorkingDirectory = (Split-Path -Parent $contract.ExecutablePath) })
        }
        $script:processNodes = @(
            [pscustomobject]@{ ProcessId = 101; ParentProcessId = 5; ExecutablePath = $contract.ExecutablePath; CommandLine = ('"' + $contract.ExecutablePath + '" ' + $script:stopArgs); CreationDate = [datetime]'2026-01-01T00:00:00' }
            [pscustomobject]@{ ProcessId = 102; ParentProcessId = 101; ExecutablePath = (Join-Path (Split-Path -Parent $contract.ExecutablePath) 'lemonade-router.exe'); CreationDate = [datetime]'2026-01-01T00:00:01' }
            [pscustomobject]@{ ProcessId = 103; ParentProcessId = 102; ExecutablePath = (Join-Path $fixture 'runtime-cache/llama-server.exe'); CreationDate = [datetime]'2026-01-01T00:00:02' }
            [pscustomobject]@{ ProcessId = 999; ParentProcessId = 5; ExecutablePath = $contract.ExecutablePath; CommandLine = 'user-started server'; CreationDate = [datetime]'2025-01-01T00:00:00' }
            [pscustomobject]@{ ProcessId = 998; ParentProcessId = 999; ExecutablePath = (Join-Path $fixture 'runtime-cache/llama-server.exe'); CreationDate = [datetime]'2025-01-01T00:00:01' }
        )
    }
    Reset-StopFixture
    Stop-ODSPortalLemonade $contract.ExecutablePath
    Assert-Restart (($script:stopCalls -join ',') -eq 'task,kill:101,kill:102,kill:103' -and $script:disposed.Count -eq 3) 'legacy task stops its proven descendants outside bin and preserves a separate Lemonade and llama instance'
    Reset-StopFixture
    $script:stopTask = $null; $script:portTaken = $true
    Stop-ODSPortalLemonade $contract.ExecutablePath
    Assert-Restart ($script:stopCalls.Count -eq 0 -and $script:disposed.Count -eq 0) 'first install with no ODS task never stops user processes'
    $oldPort = $env:AMD_INFERENCE_PORT; $env:AMD_INFERENCE_PORT = $null
    $script:portTaken = $false
    function Get-ODSPortalPortOwner([int]$Port) { if ($Port -eq 8080) { return 'user-lemonade' }; return $null }
    try { Assert-Restart ((Select-ODSPortalLemonadePort) -eq 13305) 'first install selects a free port beside an existing user server' }
    finally { $env:AMD_INFERENCE_PORT = $oldPort }

    foreach ($case in @('foreign-user', 'foreign-action', 'foreign-models', 'duplicate-task', 'duplicate-root', 'unrelated-root', 'old-child', 'recycled-pid', 'changed-task', 'queued-task', 'unknown-orphan')) {
        Reset-StopFixture
        switch ($case) {
            'foreign-user' { $script:stopTask.Principal.UserId = 'other-user' }
            'foreign-action' { $script:stopTask.Actions[0].Execute += '-other' }
            'foreign-models' { $script:stopTask.Actions[0].Arguments = $script:stopArgs.Replace('models', 'other-models') }
            'duplicate-task' { $script:stopTask = @($script:stopTask, $script:stopTask) }
            'duplicate-root' { $script:processNodes += [pscustomobject]@{ ProcessId = 104; ParentProcessId = 101; ExecutablePath = $contract.ExecutablePath; CommandLine = $script:processNodes[0].CommandLine; CreationDate = [datetime]'2026-01-01T00:00:02' } }
            'unrelated-root' { $script:engineId = 777 }
            'old-child' { $script:processNodes[1].CreationDate = [datetime]'2025-01-01T00:00:00' }
            'recycled-pid' { $script:reusedPid = 102 }
            'changed-task' { $script:changedTask = $true }
            'queued-task' { $script:engineId = 0; $script:stopTask.State = 'Queued' }
            'unknown-orphan' { $script:engineId = 0; $script:stopTask.State = 'Ready'; $script:portTaken = $true }
        }
        $message = ''
        try { Stop-ODSPortalLemonade $contract.ExecutablePath } catch { $message = $_.Exception.Message }
        Assert-Restart ($message -and $script:stopCalls.Count -eq 0) "$case refuses teardown before stopping any task or process"
    }
    Reset-StopFixture
    $script:stopTask.State = 'Ready'; $script:engineId = 0
    Stop-ODSPortalLemonade $contract.ExecutablePath
    Assert-Restart ($script:stopCalls.Count -eq 0) 'stopped legacy task with no listener preserves all unrelated processes'

    # A durable task may have stopped its wrapper while its child still runs.
    # Only its saved PID AND creation timestamp may recover that child.
    $stopPlan = $registration.Plan | ConvertTo-Json | ConvertFrom-Json
    $stopPlan.ModelsDir = Join-Path (Get-ODSPortalStateDir) 'models'
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($stopPlan | ConvertTo-Json -Compress)
    # Task Scheduler and its Windows shell are mocked on every platform.
    $script:fixtureShell = $registration.Action.Execute
    function Get-Command { param($Name, $CommandType, $ErrorAction)
        return [pscustomobject]@{ Source = $script:fixtureShell; Name = 'powershell.exe' }
    }
    foreach ($case in @('active-wrapper', 'saved-child', 'stale-ready', 'orphan-ready')) {
        Reset-StopFixture
        $script:stopTask.Actions = @($registration.Action)
        $script:processNodes = @($script:processNodes | Where-Object { $_.ProcessId -ne 101 })
        $script:processNodes += [pscustomobject]@{ ProcessId = 101; ParentProcessId = 100; ExecutablePath = $contract.ExecutablePath; CommandLine = 'runtime child'; CreationDate = [datetime]'2026-01-01T00:00:00' }
        $identity = @{ ProcessId = 101; StartedAt = '2026-01-01T00:00:00'; Port = 13305 }
        if ($case -eq 'active-wrapper') {
            $script:engineId = 100
            $script:processNodes += [pscustomobject]@{ ProcessId = 100; ParentProcessId = 5; ExecutablePath = $registration.Action.Execute; CommandLine = ('"' + $registration.Action.Execute + '" ' + $registration.Action.Arguments); CreationDate = [datetime]'2025-12-31T23:59:59' }
        } else { $script:engineId = 0; $script:stopTask.State = 'Ready' }
        if ($case -eq 'stale-ready') { $identity.StartedAt = '2025-12-31T00:00:00' }
        if ($case -eq 'orphan-ready') { $script:processNodes = @($script:processNodes | Where-Object { $_.ProcessId -ne 101 }) }
        Write-ODSPrivateEnvFile -Path $registration.ReadyPath -Content ($identity | ConvertTo-Json -Compress)
        $message = ''
        try { Stop-ODSPortalLemonade $contract.ExecutablePath } catch { $message = $_.Exception.Message }
        if ($case -eq 'active-wrapper') {
            Assert-Restart (-not $message -and ($script:stopCalls -join ',') -eq 'task,kill:100,kill:101,kill:102,kill:103') 'durable task stops its exact wrapper and child tree'
        } elseif ($case -eq 'saved-child') {
            Assert-Restart (-not $message -and ($script:stopCalls -join ',') -eq 'kill:101,kill:102,kill:103') 'stopped durable wrapper recovers only its child with matching saved process identity'
        } else { Assert-Restart ($message -and $script:stopCalls.Count -eq 0) "$case never treats stale PID evidence as process ownership" }
    }

    $legacyPath = Join-Path (Get-ODSPortalStateDir) 'lemonade-launch.task.ps1'
    $legacyText = '$exe = ' + (ConvertTo-ODSPowerShellSingleQuotedLiteral $contract.ExecutablePath) + "`n" +
        '$argumentString = ''--port 13305 --host 127.0.0.1''' + "`n" +
        '$workingDirectory = ' + (ConvertTo-ODSPowerShellSingleQuotedLiteral (Split-Path -Parent $contract.ExecutablePath))
    foreach ($case in @('former-modern', 'dynamic-setting', 'compound-setting', 'duplicate-setting', 'foreign-legacy-exe', 'nonloopback-setting')) {
        Reset-StopFixture
        $script:engineId = 100
        $script:stopTask.Actions[0].Execute = $script:fixtureShell
        $script:stopTask.Actions[0].Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $legacyPath + '"'
        $script:processNodes[0].ParentProcessId = 100
        $script:processNodes += [pscustomobject]@{ ProcessId = 100; ParentProcessId = 5; ExecutablePath = $script:fixtureShell; CommandLine = ('"' + $script:fixtureShell + '" ' + $script:stopTask.Actions[0].Arguments); CreationDate = [datetime]'2025-12-31T23:59:59' }
        $contents = $legacyText
        switch ($case) {
            'dynamic-setting' { $contents = $contents.Replace('$argumentString = ''--port 13305 --host 127.0.0.1''', '$argumentString = $(throw ''must never execute'')') }
            'compound-setting' { $contents = $contents.Replace('$exe = ', '$exe += ') }
            'duplicate-setting' { $contents += "`n" + '$exe = ''different.exe''' }
            'foreign-legacy-exe' { $contents = $contents.Replace('LemonadeServer.exe', 'UnrelatedServer.exe') }
            'nonloopback-setting' { $contents = $contents.Replace('127.0.0.1', '0.0.0.0') }
        }
        Write-ODSPrivateEnvFile -Path $legacyPath -Content $contents
        $message = ''
        try { Stop-ODSPortalLemonade $contract.ExecutablePath } catch { $message = $_.Exception.Message }
        if ($case -eq 'former-modern') {
            Assert-Restart (-not $message -and ($script:stopCalls -join ',') -eq 'task,kill:100,kill:101,kill:102,kill:103') 'former ODS 10.7 launcher migrates using literal AST settings and its exact task process tree'
        } else {
            Assert-Restart ($message -and $message -notmatch 'must never execute' -and $script:stopCalls.Count -eq 0) "$case is rejected without evaluating the former launcher or stopping processes"
        }
    }
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    $resolvedFixture = [IO.Path]::GetFullPath($fixture)
    if (-not $resolvedFixture.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to clean a fixture outside the temporary directory.'
    }
    Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:restartChecks Portal Lemonade restart contracts."
