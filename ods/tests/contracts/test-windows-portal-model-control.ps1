# No actual scheduled task, WSL command, network call or runtime is invoked.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-model-control.ps1')
$script:controlChecks = 0
function Assert-Control([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:controlChecks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Assert-ControlError($Request, [string]$Code) {
    $caught = $null
    try { $null = Invoke-ODSPortalModelControl $Request } catch { $caught = $_.Exception }
    Assert-Control ($null -ne $caught -and $caught.Data['code'] -eq $Code) "request fails visibly with $Code"
    return $caught
}
function New-ControlRequest([string]$Action, [string]$Digest = '') {
    [pscustomobject]@{ action = $Action; distro = 'Ubuntu-24.04'; installDir = "/home/some user/ods"
        expectedPlanDigest = $Digest; gguf = 'Small-0.6B.gguf'; contextSize = 65536; plan = $null }
}
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-model-control-' + [char]0x00E9 + '-' + [guid]::NewGuid().ToString('N'))
$testShell = (Get-Process -Id $PID).Path
$previousLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $fixture
try {
    $script:fixtureMutex = 'ODS-PortalModelControl-Test-' + [guid]::NewGuid().ToString('N')
    function Get-ODSPortalControlMutexName { return $script:fixtureMutex }
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory)
        [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
    }
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    if (-not $onWindows) {
        function Get-ODSPortalUserSid([string]$UserId) { if ($UserId) { return $UserId }; return 'fixture-user' }
    }
    $models = Join-Path (Get-ODSPortalStateDir) 'models'
    $null = New-Item -ItemType Directory -Path $models -Force
    $exe = Join-Path $fixture 'LemonadeServer.exe'
    [IO.File]::WriteAllText($exe, 'fixture, never executed')
    foreach ($name in @('Original-9B.gguf', 'Small-0.6B.gguf')) { [IO.File]::WriteAllText((Join-Path $models $name), 'fixture') }
    $unicodeModel = 'Model-' + [char]0x00E9 + '-' + [char]0x4E2D + '.gguf'
    [IO.File]::WriteAllText((Join-Path $models $unicodeModel), 'fixture')
    $contract = [pscustomobject]@{ ExecutablePath = $exe; Port = 13305; ModelsDir = $models; ContextSize = 65536 }
    $registration = New-ODSPortalLemonadeRuntimeAction $contract 'Original-9B.gguf' 'Ubuntu-24.04' '/home/some user/ods'
    $planPath = Join-Path (Split-Path -Parent $registration.ReadyPath) 'runtime.json'
    $script:fixtureTask = [pscustomobject]@{ TaskPath = '\'; State = 'Ready'
        Principal = [pscustomobject]@{ UserId = Get-ODSPortalUserSid }; Actions = @($registration.Action) }
    $script:taskMissing = $false; $script:occupied = $false; $script:running = $false
    $script:healthFailure = $false; $script:staleProcess = $false; $script:wrongModel = $false
    $script:failStart = $false; $script:failReady = $false; $script:mutateDuringStop = $false
    $script:probeMutexDuringHealth = $false; $script:replacePlanDuringHealth = $false
    $script:events = [Collections.Generic.List[string]]::new()
    function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction, $ErrorVariable)
        if (-not $script:taskMissing) { return $script:fixtureTask }
    }
    if (-not $onWindows) {
        function Get-Command { param($Name, $CommandType, $ErrorAction)
            [pscustomobject]@{ Source = $registration.Action.Execute; Name = 'pwsh.exe' }
        }
    }
    function Get-ODSPortalTaskEngineId { if ($script:running) { return 100 }; return 0 }
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction)
        if ($script:occupied) { return [pscustomobject]@{ OwningProcess = 999; LocalAddress = '127.0.0.1' } }
    }
    function Stop-ODSPortalLemonade([string]$ExecutablePath) {
        Assert-Control ($ExecutablePath -eq $exe) 'controller stops only the verified plan executable'
        $script:events.Add('stop'); $script:running = $false
        if ($script:mutateDuringStop) { [IO.File]::AppendAllText($planPath, ' ') }
    }
    function Start-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        Assert-Control ($TaskName -ceq (Get-ODSPortalLemonadeTaskName) -and $TaskPath -ceq '\') 'only the existing owned task is started'
        $script:events.Add('start')
        if ($script:failStart) { throw 'fixture task start failed' }
        $script:running = $true
    }
    function Disable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        Assert-Control (-not (Test-ODSPortalLemonadeWanted (Join-Path (Split-Path -Parent $planPath) 'intent.json'))) 'stop intent is durable before retries are disabled'
        $script:taskDisabled = $true
    }
    function Enable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        Assert-Control (Test-ODSPortalLemonadeWanted (Join-Path (Split-Path -Parent $planPath) 'intent.json')) 'explicit start publishes running intent before enabling the owned task'
        $script:taskDisabled = $false
    }
    function Wait-ODSPortalLemonadeReady($Registration, [int]$Seconds = 1020) {
        if ($script:failReady) { throw 'fixture load failure' }
        $script:events.Add('ready')
        $ready = @{ ProcessId = 4242; StartedAt = '2026-01-01T00:00:00Z'; Port = $Registration.Plan.Port
            ContextSize = $Registration.Plan.ContextSize; ModelId = [IO.Path]::GetFileNameWithoutExtension($Registration.Plan.GgufFile) }
        Write-ODSPrivateEnvFile -Path $Registration.ReadyPath -Content ($ready | ConvertTo-Json -Compress)
        return $ready.ModelId
    }
    function Get-Process { param($Id, $ErrorAction)
        if (-not $script:running) { throw 'fixture process absent' }
        $time = if ($script:staleProcess) { [datetime]'2026-01-02T00:00:00Z' } else { [datetime]'2026-01-01T00:00:00Z' }
        $process = [pscustomobject]@{ Handle = 4242; Path = $exe; HasExited = $false; StartTime = $time }
        $process | Add-Member ScriptMethod Dispose { }
        return $process
    }
    function Assert-ODSPortalLemonadeListener([int]$Port, [int]$ProcessId, [string]$ExecutablePath) {
        if ($script:occupied) { throw 'fixture foreign listener' }
        if ($Port -ne 13305 -or $ProcessId -ne 4242 -or $ExecutablePath -ne $exe) { throw 'wrong listener proof' }
    }
    function Invoke-RestMethod { param($Uri, $TimeoutSec, $ErrorAction)
        if ($script:healthFailure) { throw [Net.WebException]::new('fixture health timeout') }
        $current = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($script:probeMutexDuringHealth) {
            $worker = [PowerShell]::Create()
            try {
                $null = $worker.AddScript({ param($Name)
                    $mutex = [Threading.Mutex]::new($false, $Name)
                    $acquired = $false
                    try {
                        $acquired = $mutex.WaitOne(0)
                        [pscustomobject]@{ Acquired = $acquired; ThreadId = [Threading.Thread]::CurrentThread.ManagedThreadId }
                    } finally { if ($acquired) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
                }).AddArgument($script:fixtureMutex)
                $proof = @($worker.Invoke())
                Assert-Control ($proof.Count -eq 1 -and $proof[0].Acquired -and
                    $proof[0].ThreadId -ne [Threading.Thread]::CurrentThread.ManagedThreadId) 'an in-flight status probe leaves the mutation mutex available to another thread'
            } finally { $worker.Dispose() }
        }
        if ($script:replacePlanDuringHealth) {
            $changed = $current | ConvertTo-Json | ConvertFrom-Json
            $changed.GgufFile = 'Original-9B.gguf'
            Write-ODSPrivateEnvFile -Path $planPath -Content ($changed | ConvertTo-Json -Compress)
        }
        $id = if ($script:wrongModel) { 'Unrelated' } else { [IO.Path]::GetFileNameWithoutExtension($current.GgufFile) }
        [pscustomobject]@{ all_models_loaded = @([pscustomobject]@{ model_name = $id; recipe_options = @{ ctx_size = $current.ContextSize } }) }
    }
    $status = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    if (-not $status.managed) { throw ($status | ConvertTo-Json -Compress) }
    Assert-Control ($status.managed -and -not $status.running -and $null -eq $status.observation -and
        $status.modelStoreWindowsPath -eq $models -and $status.planPathWindows -eq $planPath) 'stopped bound runtime remains managed and reports its actual private plan and model store'
    Assert-Control ($status.planDigest -cmatch '^[0-9a-f]{64}$' -and $status.plan.WslInstallDir -ceq '/home/some user/ods') 'status returns a byte digest and exact binding, including spaces'
    $originalPlan = $status.plan | ConvertTo-Json | ConvertFrom-Json
    $originalDigest = $status.planDigest
    $bytesBefore = [IO.File]::ReadAllText($planPath)
    # Hold the real named mutex on a separate thread. Its name is fixture-only,
    # so this test cannot block an installed ODS operation running concurrently.
    $heldSignal = [Threading.ManualResetEventSlim]::new($false)
    $releaseSignal = [Threading.ManualResetEventSlim]::new($false)
    $holder = [PowerShell]::Create()
    $null = $holder.AddScript({ param($Name, $HeldSignal, $ReleaseSignal)
        $mutex = [Threading.Mutex]::new($false, $Name)
        $acquired = $false
        try {
            $acquired = $mutex.WaitOne(0)
            if (-not $acquired) { throw 'The isolated test mutex was unexpectedly busy.' }
            $HeldSignal.Set()
            if (-not $ReleaseSignal.Wait(20000)) { throw 'The fixture did not release its mutex in time.' }
        } finally { if ($acquired) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
    }).AddArgument($script:fixtureMutex).AddArgument($heldSignal).AddArgument($releaseSignal)
    $pendingHolder = $holder.BeginInvoke()
    try {
        Assert-Control ($heldSignal.Wait(10000)) 'a separate fixture thread holds the mutation mutex'
        foreach ($poll in 1..5) {
            $concurrent = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
            Assert-Control ($concurrent.managed -and $concurrent.planDigest -ceq $originalDigest) "status poll $poll succeeds while a mutation owns the mutex"
        }
        $null = Assert-ControlError (New-ControlRequest 'activate' $originalDigest) 'busy'
        Assert-Control ($script:events.Count -eq 0) 'a second mutation is rejected before any stop, write or start'
    } finally {
        $releaseSignal.Set()
        if (-not $pendingHolder.AsyncWaitHandle.WaitOne(5000)) { $holder.Stop() }
        try { $null = $holder.EndInvoke($pendingHolder) }
        finally { $holder.Dispose(); $heldSignal.Dispose(); $releaseSignal.Dispose() }
    }
    $script:taskMissing = $true
    Assert-Control (-not (Invoke-ODSPortalModelControl (New-ControlRequest 'status')).managed) 'missing task is unmanaged'
    $script:taskMissing = $false
    $request = New-ControlRequest 'status'; $request.distro = 'OtherUbuntu'
    Assert-Control (-not (Invoke-ODSPortalModelControl $request).managed) 'another distro cannot claim the Windows runtime'
    $request = New-ControlRequest 'status'; $request.installDir = '/home/another/ods'
    Assert-Control (-not (Invoke-ODSPortalModelControl $request).managed) 'another Linux install directory cannot claim the Windows runtime'
    $oldArgs = $registration.Action.Arguments; $registration.Action.Arguments += ' -Command bad'
    Assert-Control (-not (Invoke-ODSPortalModelControl (New-ControlRequest 'status')).managed) 'an altered task action is unmanaged'
    $registration.Action.Arguments = $oldArgs
    $oldSid = $script:fixtureTask.Principal.UserId; $script:fixtureTask.Principal.UserId = 'S-1-5-18'
    Assert-Control (-not (Invoke-ODSPortalModelControl (New-ControlRequest 'status')).managed) 'another Windows principal is unmanaged'
    $script:fixtureTask.Principal.UserId = $oldSid
    $legacy = $originalPlan | Select-Object ExecutablePath, Port, ModelsDir, ContextSize, GgufFile
    Write-ODSPrivateEnvFile -Path $planPath -Content ($legacy | ConvertTo-Json -Compress)
    Assert-Control (-not (Invoke-ODSPortalModelControl (New-ControlRequest 'status')).managed) 'legacy unbound plan is read-only, never implicitly adopted'
    Write-ODSPrivateEnvFile -Path $planPath -Content $bytesBefore
    Assert-Control ($script:events.Count -eq 0) 'status checks never stop or start a runtime'
    $null = Assert-ControlError (New-ControlRequest 'activate' ('0' * 64)) 'plan_conflict'
    foreach ($bad in @('../Small-0.6B.gguf', 'sub\Small-0.6B.gguf', 'C:Small.gguf', 'missing.gguf')) {
        $request = New-ControlRequest 'activate' $originalDigest; $request.gguf = $bad
        $code = if ($bad -eq 'missing.gguf') { 'model_missing' } else { 'invalid_model' }
        $null = Assert-ControlError $request $code
    }
    foreach ($context in @(4095, 262145, 1.5, '65536')) {
        $request = New-ControlRequest 'activate' $originalDigest; $request.contextSize = $context
        $null = Assert-ControlError $request 'invalid_model'
    }
    Assert-Control ($script:events.Count -eq 0 -and [IO.File]::ReadAllText($planPath) -ceq $bytesBefore) 'invalid requests preserve the plan and running processes'
    $activated = Invoke-ODSPortalModelControl (New-ControlRequest 'activate' $originalDigest)
    Assert-Control ($activated.running -and $activated.observation.status -ceq 'verified' -and
        $activated.observation.modelId -ceq 'Small-0.6B' -and $activated.plan.GgufFile -ceq 'Small-0.6B.gguf' -and
        $activated.planDigest -cne $originalDigest -and ($script:events -join ',') -ceq 'stop,start,ready') 'activation atomically persists its model before starting and proving it'
    Assert-ODSPortalPrivateControlFile $planPath
    Assert-Control ((Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json).GgufFile -ceq 'Small-0.6B.gguf') 'the next sign-in reads the newly selected model'
    $script:probeMutexDuringHealth = $true
    try { $concurrent = Invoke-ODSPortalModelControl (New-ControlRequest 'status') }
    finally { $script:probeMutexDuringHealth = $false }
    Assert-Control ($concurrent.running -and $concurrent.observation.modelId -ceq 'Small-0.6B') 'concurrent status still proves the actual loaded model'
    $activatedBytes = [IO.File]::ReadAllText($planPath)
    $script:replacePlanDuringHealth = $true
    try { $changed = Invoke-ODSPortalModelControl (New-ControlRequest 'status') }
    finally { $script:replacePlanDuringHealth = $false }
    Assert-Control ($changed.managed -and -not $changed.running -and $null -eq $changed.observation -and
        $changed.plan.GgufFile -ceq 'Original-9B.gguf' -and $changed.planDigest -ceq (Get-ODSPortalPlanDigest $planPath)) 'atomic plan replacement during a probe returns the new snapshot without stale model identity'
    Write-ODSPrivateEnvFile -Path $planPath -Content $activatedBytes
    foreach ($damage in @('healthFailure', 'staleProcess', 'wrongModel', 'occupied')) {
        Set-Variable -Name $damage -Value $true -Scope Script
        $observed = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
        Assert-Control ($observed.managed -and -not $observed.running -and $null -eq $observed.observation -and $observed.runtimeError) "$damage never reports stale model identity"
        Set-Variable -Name $damage -Value $false -Scope Script
    }
    $stopped = Invoke-ODSPortalModelControl (New-ControlRequest 'stop' $activated.planDigest)
    Assert-Control (-not $stopped.running -and $stopped.planDigest -ceq $activated.planDigest -and
        $stopped.plan.GgufFile -ceq 'Small-0.6B.gguf') 'stop keeps the selected startup model and plan digest'
    $started = Invoke-ODSPortalModelControl (New-ControlRequest 'start' $stopped.planDigest)
    Assert-Control ($started.running -and $started.planDigest -ceq $stopped.planDigest) 'start proves the selected model without rewriting the startup choice'
    $beforeStart = $script:events.Count
    $null = Invoke-ODSPortalModelControl (New-ControlRequest 'start' $started.planDigest)
    Assert-Control ($script:events.Count -eq $beforeStart) 'start is idempotent only after live model proof'
    $request = New-ControlRequest 'restore' $started.planDigest; $request.plan = $originalPlan | ConvertTo-Json | ConvertFrom-Json
    $request.plan.WslDistro = $originalPlan.WslDistro.ToLowerInvariant()
    $restored = Invoke-ODSPortalModelControl $request
    Assert-Control ($restored.running -and $restored.observation.modelId -ceq 'Original-9B' -and
        $restored.plan.GgufFile -ceq 'Original-9B.gguf') 'rollback restores and proves the exact previous startup model'
    Assert-Control ($restored.plan.WslDistro -ceq $originalPlan.WslDistro) 'rollback accepts equivalent distro casing while retaining the registered spelling'
    $request = New-ControlRequest 'restore' $restored.planDigest; $request.plan = $originalPlan | ConvertTo-Json | ConvertFrom-Json
    $request.plan.WslInstallDir = $originalPlan.WslInstallDir.ToUpperInvariant()
    $null = Assert-ControlError $request 'invalid_plan'
    $request = New-ControlRequest 'restore' $restored.planDigest; $request.plan = $originalPlan | ConvertTo-Json | ConvertFrom-Json
    $request.plan.ExecutablePath = 'C:\unrelated.exe'
    $null = Assert-ControlError $request 'invalid_plan'
    $script:failReady = $true
    $failed = $null
    try { $null = Invoke-ODSPortalModelControl (New-ControlRequest 'activate' $restored.planDigest) } catch { $failed = $_.Exception }
    Assert-Control ($failed.Message -eq 'fixture load failure' -and $failed.Data['newPlanDigest'] -ceq (Get-ODSPortalPlanDigest $planPath)) 'failure after publication returns the new digest for journal-owned rollback'
    $script:failReady = $false
    $request = New-ControlRequest 'restore' $failed.Data['newPlanDigest']; $request.plan = $originalPlan
    $recovered = Invoke-ODSPortalModelControl $request
    Assert-Control ($recovered.running -and $recovered.observation.modelId -ceq 'Original-9B') 'rollback can recover a failed activation without reusing its stale digest'
    $request = New-ControlRequest 'activate' $recovered.planDigest; $request.gguf = $unicodeModel
    $unicodeActive = Invoke-ODSPortalModelControl $request
    Assert-Control ($unicodeActive.plan.GgufFile -ceq $unicodeModel -and $unicodeActive.running -and
        $unicodeActive.observation.modelId -ceq [IO.Path]::GetFileNameWithoutExtension($unicodeModel) -and
        $unicodeActive.plan.ModelsDir -ceq $models) 'UTF-8 filename, model identity and Windows path survive activation and readiness on this PowerShell version'
    $request = New-ControlRequest 'restore' $unicodeActive.planDigest; $request.plan = $originalPlan
    $recovered = Invoke-ODSPortalModelControl $request
    Assert-Control ($recovered.running -and $recovered.observation.modelId -ceq 'Original-9B') 'a Unicode model can roll back through the same CAS and readiness proof'
    $script:mutateDuringStop = $true
    $null = Assert-ControlError (New-ControlRequest 'activate' $recovered.planDigest) 'plan_conflict'
    Assert-Control ((Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json).GgufFile -ceq 'Original-9B.gguf') 'concurrent plan edit during teardown is preserved'
    $script:mutateDuringStop = $false
    $script:occupied = $true
    $null = Assert-ControlError (New-ControlRequest 'stop' (Get-ODSPortalPlanDigest $planPath)) 'stop_unverified'
    $script:occupied = $false
    # The real entrypoint must return one JSON error and a nonzero process exit.
    # Invalid actions fail before any task/process/network inspection can occur.
    $entry = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../installers/windows/portal-model-control.ps1'))
    foreach ($json in @('{"action":"unknown","distro":"Ubuntu-24.04","installDir":"/home/test"}', '[]', '{invalid')) {
        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $testShell
        $startInfo.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $entry + '"'
        $startInfo.UseShellExecute = $false; $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true; $startInfo.RedirectStandardOutput = $true; $startInfo.RedirectStandardError = $true
        $startInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
        $child = [Diagnostics.Process]::Start($startInfo)
        try {
            $null = $child.Handle
            $child.StandardInput.Write($json); $child.StandardInput.Close()
            $stdout = $child.StandardOutput.ReadToEnd(); $stderr = $child.StandardError.ReadToEnd()
            if (-not $child.WaitForExit(15000)) { throw 'The finite JSON entrypoint did not finish.' }
            $receipt = $stdout | ConvertFrom-Json
            Assert-Control ($child.ExitCode -eq 1 -and $receipt.ok -eq $false -and $receipt.code -and $receipt.error -and
                [string]::IsNullOrWhiteSpace($stderr)) 'real stdin boundary returns JSON errors and nonzero exit without native operations'
        } finally {
            if (-not $child.HasExited) { $child.Kill(); $null = $child.WaitForExit(5000) }
            $child.Dispose()
        }
    }
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    $resolvedFixture = [IO.Path]::GetFullPath($fixture)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedFixture.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temporary directory.' }
    if (Test-Path -LiteralPath $resolvedFixture) { Remove-Item -LiteralPath $resolvedFixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:controlChecks Portal Windows model control contracts."
