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
    # The profile path holds a non-ASCII character; llama.cpp gets 8.3 names.
    function Get-ODSNativeLlamaShortPath([string]$Path) { return 'C:\FIXTUR~1\' + [IO.Path]::GetFileName($Path) }
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    if (-not $onWindows) {
        function Get-ODSPortalUserSid([string]$UserId) { if ($UserId) { return $UserId }; return 'fixture-user' }
    }
    $models = Get-ODSPortalModelsDir
    $null = New-Item -ItemType Directory -Path $models -Force
    $exeDir = Join-Path (Join-Path (Join-Path $fixture 'ODS') 'llama.cpp') 'b9014-win-vulkan-x64'
    $null = New-Item -ItemType Directory -Path $exeDir -Force
    $exe = Join-Path $exeDir 'llama-server.exe'
    [IO.File]::WriteAllText($exe, 'fixture, never executed')
    foreach ($name in @('Original-9B.gguf', 'Small-0.6B.gguf')) { [IO.File]::WriteAllText((Join-Path $models $name), 'fixture') }
    $unicodeModel = 'Model-' + [char]0x00E9 + '-' + [char]0x4E2D + '.gguf'
    [IO.File]::WriteAllText((Join-Path $models $unicodeModel), 'fixture')
    $plan = [ordered]@{ ExecutablePath = $exe; Port = 18080; ModelsDir = $models; ContextSize = 65536; GgufFile = 'Original-9B.gguf' }
    $options = [ordered]@{ schemaVersion = 1; Device = 'Vulkan0'; NGpuLayers = 'auto'
        ApiKeyPath = (Join-Path (Get-ODSPortalRuntimeDir) 'api-key'); LogPath = (Join-Path (Get-ODSPortalRuntimeDir) 'llama-server.log')
        ReleaseTag = 'b9014'; ZipSha256 = ('a' * 64); ReasoningArguments = @('--reasoning', 'off'); ExtraArguments = @() }
    $script:apiKey = New-ODSNativeLlamaApiKey
    $registration = New-ODSPortalRuntimeAction $plan $options $script:apiKey 'Ubuntu-24.04' '/home/some user/ods'
    $planPath = Join-Path (Split-Path -Parent $registration.ReadyPath) 'runtime.json'
    $script:fixtureTask = [pscustomobject]@{ TaskPath = '\'; State = 'Ready'
        Principal = [pscustomobject]@{ UserId = Get-ODSPortalUserSid }; Actions = @($registration.Action) }
    $script:taskMissing = $false; $script:occupied = $false; $script:running = $false
    $script:healthFailure = $false; $script:staleProcess = $false; $script:wrongModel = $false
    $script:failStart = $false; $script:failReady = $false; $script:mutateDuringStop = $false; $script:failStop = $false
    $script:probeMutexDuringHealth = $false; $script:replacePlanDuringHealth = $false
    $script:contextOffset = 0; $script:wrongKey = $false; $script:visionLoaded = $true
    $script:events = [Collections.Generic.List[string]]::new()
    $script:queried = [Collections.Generic.List[string]]::new()
    function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction, $ErrorVariable)
        $script:queried.Add($TaskName)
        if (-not $script:taskMissing -and $TaskName -ceq (Get-ODSPortalRuntimeTaskName)) { return $script:fixtureTask }
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
    function Stop-ODSPortalRuntime([string]$ExecutablePath) {
        Assert-Control ($ExecutablePath -eq $exe) 'controller stops only the verified plan executable'
        if ($script:failStop) { throw 'fixture stop refused' }
        $script:events.Add('stop'); $script:running = $false
        if ($script:mutateDuringStop) { [IO.File]::AppendAllText($planPath, ' ') }
    }
    function Start-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        Assert-Control ($TaskName -ceq (Get-ODSPortalRuntimeTaskName) -and $TaskName -like 'ODSLlamaServerRuntime-*' -and $TaskPath -ceq '\') 'only the existing owned llama.cpp task is started'
        $script:events.Add('start')
        if ($script:failStart) { throw 'fixture task start failed' }
        $script:running = $true
    }
    # Stop-ODSPortalRuntime disarms sign-in startup itself (its order is pinned
    # in test-windows-portal-llama-restart.ps1) and is a fixture here, so the
    # controller never disables the task on its own.
    function Disable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        throw 'The controller must not disarm sign-in startup ahead of Stop-ODSPortalRuntime.'
    }
    function Enable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        Assert-Control (Test-ODSNativeLlamaWanted (Join-Path (Split-Path -Parent $planPath) 'intent.json')) 'explicit start publishes running intent before enabling the owned task'
    }
    function Wait-ODSPortalRuntimeReady($Registration, [int]$Seconds = 1020) {
        if ($script:failReady) { throw 'fixture load failure' }
        $script:events.Add('ready')
        $ready = @{ ProcessId = 4242; StartedAt = '2026-01-01T00:00:00Z'; Port = $Registration.Plan.Port
            ContextSize = $Registration.Plan.ContextSize; ModelId = $Registration.Plan.GgufFile; RuntimeContext = $Registration.Plan.ContextSize }
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
    function Assert-ODSNativeLlamaListener([int]$Port, [int]$ProcessId, [string]$ExecutablePath) {
        if ($script:occupied) { throw 'fixture foreign listener' }
        if ($Port -ne 18080 -or $ProcessId -ne 4242 -or $ExecutablePath -ne $exe) { throw 'wrong listener proof' }
    }
    function Invoke-ODSNativeLlamaHttp { param([int]$Port, [string]$Path, [string]$ApiKey = '', [int]$TimeoutMilliseconds = 5000)
        if ($script:healthFailure) { return [pscustomobject]@{ StatusCode = 0; Body = ''; Error = 'ConnectFailure' } }
        $current = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($Path -eq '/health') {
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
            return [pscustomobject]@{ StatusCode = 200; Body = '{"status":"ok"}'; Error = '' }
        }
        if ($Path -eq '/v1/models') {
            Assert-Control (-not $ApiKey) 'the public model list gets no credential'
            $id = if ($script:wrongModel) { 'Unrelated.gguf' } else { $current.GgufFile }
            return [pscustomobject]@{ StatusCode = 200; Error = ''; Body = (@{ data = @(@{ id = $id }) } | ConvertTo-Json -Depth 4) }
        }
        if ($Path -eq '/props') {
            if ($script:wrongKey -or $ApiKey -cne $script:apiKey) { return [pscustomobject]@{ StatusCode = 401; Body = '{}'; Error = '' } }
            $body = @{ model_path = ('C:\FIXTUR~1\' + $current.GgufFile); total_slots = 1
                default_generation_settings = @{ n_ctx = $current.ContextSize + $script:contextOffset }
                modalities = @{ vision = [bool]($script:visionLoaded -and $current.MmprojFile); audio = $false } } | ConvertTo-Json -Depth 4
            return [pscustomobject]@{ StatusCode = 200; Body = $body; Error = '' }
        }
        throw "unexpected path $Path"
    }
    $status = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    if (-not $status.managed) { throw ($status | ConvertTo-Json -Compress) }
    Assert-Control ($status.managed -and -not $status.running -and $null -eq $status.observation -and
        $status.modelStoreWindowsPath -eq $models -and $status.planPathWindows -eq $planPath) 'stopped bound runtime remains managed and reports its actual private plan and model store'
    Assert-Control ($status.planDigest -cmatch '^[0-9a-f]{64}$' -and $status.plan.WslInstallDir -ceq '/home/some user/ods') 'status returns a byte digest and exact binding, including spaces'
    Assert-Control ((@($status.plan.Keys) -join ',') -ceq 'ExecutablePath,Port,ModelsDir,ContextSize,GgufFile,WslDistro,WslInstallDir') 'status projects exactly the plan keys the WSL bridge accepts'
    Assert-Control (-not ($script:queried | Where-Object { $_ -like 'ODSLemonadeRuntime*' })) 'the controller manages only the llama.cpp task, never a Lemonade-era name'
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
    $lemonadeExe = Join-Path $fixture 'LemonadeServer.exe'
    [IO.File]::WriteAllText($lemonadeExe, 'fixture, never executed')
    $lemonadePlan = $originalPlan | ConvertTo-Json | ConvertFrom-Json
    $lemonadePlan.ExecutablePath = $lemonadeExe
    Write-ODSPrivateEnvFile -Path $planPath -Content ($lemonadePlan | ConvertTo-Json -Compress)
    Assert-Control (-not (Invoke-ODSPortalModelControl (New-ControlRequest 'status')).managed) 'a Lemonade plan is never managed by the llama.cpp controller'
    Write-ODSPrivateEnvFile -Path $planPath -Content $bytesBefore
    $keyFile = Join-Path (Split-Path -Parent $planPath) 'api-key'
    $keyBytes = [IO.File]::ReadAllBytes($keyFile)
    Remove-Item -LiteralPath $keyFile
    Assert-Control (-not (Invoke-ODSPortalModelControl (New-ControlRequest 'status')).managed) 'a runtime without its private API key file is unmanaged'
    Write-ODSPrivateFileBytes -Path $keyFile -Bytes $keyBytes
    Assert-Control ($script:events.Count -eq 0) 'status checks never stop or start a runtime'
    $null = Assert-ControlError (New-ControlRequest 'activate' ('0' * 64)) 'plan_conflict'
    foreach ($bad in @('../Small-0.6B.gguf', 'sub\Small-0.6B.gguf', 'C:Small.gguf', 'missing.gguf', $unicodeModel)) {
        $request = New-ControlRequest 'activate' $originalDigest; $request.gguf = $bad
        $code = if ($bad -eq 'missing.gguf') { 'model_missing' } else { 'invalid_model' }
        $failure = Assert-ControlError $request $code
        if ($bad -eq $unicodeModel) { Assert-Control ($failure.Message -match 'ASCII') 'a non-ASCII GGUF name is refused with the llama.cpp reason before any stop' }
    }
    foreach ($context in @(4095, 262145, 1.5, '65536')) {
        $request = New-ControlRequest 'activate' $originalDigest; $request.contextSize = $context
        $null = Assert-ControlError $request 'invalid_model'
    }
    Assert-Control ($script:events.Count -eq 0 -and [IO.File]::ReadAllText($planPath) -ceq $bytesBefore) 'invalid requests preserve the plan and running processes'
    $activated = Invoke-ODSPortalModelControl (New-ControlRequest 'activate' $originalDigest)
    Assert-Control ($activated.running -and $activated.observation.status -ceq 'verified' -and
        $activated.observation.modelId -ceq 'Small-0.6B.gguf' -and $activated.observation.contextLength -eq 65536 -and
        $activated.plan.GgufFile -ceq 'Small-0.6B.gguf' -and
        $activated.planDigest -cne $originalDigest -and ($script:events -join ',') -ceq 'stop,start,ready') 'activation persists its model, restarts llama-server and proves the GGUF alias and context'
    Assert-ODSPortalPrivateControlFile $planPath
    Assert-Control ((Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json).GgufFile -ceq 'Small-0.6B.gguf') 'the next sign-in reads the newly selected model'
    $script:contextOffset = 200
    $aligned = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control ($aligned.running -and $aligned.observation.contextLength -eq 65536) 'llama.cpp alignment under 256 cells reports the requested context'
    $script:contextOffset = -32768
    $capped = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control ($capped.running -and $capped.observation.contextLength -eq 32768) 'a capped runtime context is reported as observed, never as requested'
    $script:contextOffset = 256
    $drift = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control ($drift.observation.contextLength -eq 65792) 'a full 256-cell drift reports the runtime n_ctx'
    $script:contextOffset = 0
    $script:probeMutexDuringHealth = $true
    try { $concurrent = Invoke-ODSPortalModelControl (New-ControlRequest 'status') }
    finally { $script:probeMutexDuringHealth = $false }
    Assert-Control ($concurrent.running -and $concurrent.observation.modelId -ceq 'Small-0.6B.gguf') 'concurrent status still proves the actual loaded model'
    $activatedBytes = [IO.File]::ReadAllText($planPath)
    $script:replacePlanDuringHealth = $true
    try { $changed = Invoke-ODSPortalModelControl (New-ControlRequest 'status') }
    finally { $script:replacePlanDuringHealth = $false }
    Assert-Control ($changed.managed -and -not $changed.running -and $null -eq $changed.observation -and
        $changed.plan.GgufFile -ceq 'Original-9B.gguf' -and $changed.planDigest -ceq (Get-ODSPortalPlanDigest $planPath)) 'atomic plan replacement during a probe returns the new snapshot without stale model identity'
    Write-ODSPrivateEnvFile -Path $planPath -Content $activatedBytes
    foreach ($damage in @('healthFailure', 'staleProcess', 'wrongModel', 'occupied', 'wrongKey')) {
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
    Assert-Control ($restored.running -and $restored.observation.modelId -ceq 'Original-9B.gguf' -and
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
    Assert-Control ($recovered.running -and $recovered.observation.modelId -ceq 'Original-9B.gguf') 'rollback can recover a failed activation without reusing its stale digest'
    $script:mutateDuringStop = $true
    $null = Assert-ControlError (New-ControlRequest 'activate' $recovered.planDigest) 'plan_conflict'
    Assert-Control ((Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json).GgufFile -ceq 'Original-9B.gguf') 'concurrent plan edit during teardown is preserved'
    $script:mutateDuringStop = $false
    $script:occupied = $true
    $null = Assert-ControlError (New-ControlRequest 'stop' (Get-ODSPortalPlanDigest $planPath)) 'stop_unverified'
    $script:occupied = $false
    # The stop itself disarms and re-arms; the controller never disarms ahead of it.
    $script:failStop = $true
    $failed = $null
    try { $null = Invoke-ODSPortalModelControl (New-ControlRequest 'stop' (Get-ODSPortalPlanDigest $planPath)) } catch { $failed = $_.Exception }
    Assert-Control ($failed.Message -eq 'fixture stop refused' -and
        (Test-ODSNativeLlamaWanted (Join-Path (Split-Path -Parent $planPath) 'intent.json'))) 'a refused Unload leaves sign-in startup armed'
    $script:failStop = $false
    # --- Vision projector (WP2): planned only for a launcher that declares it ---
    [IO.File]::WriteAllText((Join-Path $models 'mmproj-F16.gguf'), 'fixture')
    function New-VisionRequest([string]$Digest, [string]$Projector = 'mmproj-F16.gguf') {
        $request = New-ControlRequest 'activate' $Digest
        $request | Add-Member -NotePropertyName mmproj -NotePropertyValue $Projector
        return $request
    }
    $digest = Get-ODSPortalPlanDigest $planPath
    Assert-Control (@((Invoke-ODSPortalModelControl (New-ControlRequest 'status')).planFeatures).Count -eq 0) 'a launcher from before vision support declares no plan features'
    $failure = Assert-ControlError (New-VisionRequest $digest) 'invalid_model'
    Assert-Control ($failure.Message -match 'before vision support' -and (Get-ODSPortalPlanDigest $planPath) -ceq $digest) 'an older launcher is never planned a projector, and its plan is kept'
    $optionsPath = Join-Path (Split-Path -Parent $planPath) 'runtime-options.json'
    $declared = Get-Content -LiteralPath $optionsPath -Raw | ConvertFrom-Json
    $declared | Add-Member -NotePropertyName PlanFeatures -NotePropertyValue @('MmprojFile', 'Unknown')
    Write-ODSPrivateEnvFile -Path $optionsPath -Content ($declared | ConvertTo-Json -Depth 4 -Compress)
    Assert-Control ((@((Invoke-ODSPortalModelControl (New-ControlRequest 'status')).planFeatures) -join ',') -ceq 'MmprojFile') 'status reports only the known plan features the launcher declares'
    foreach ($bad in @('../mmproj-F16.gguf', 'mmproj.bin', ' mmproj-F16.gguf', 'Small-0.6B.gguf', 'missing-mmproj.gguf')) {
        $code = if ($bad -eq 'missing-mmproj.gguf') { 'model_missing' } else { 'invalid_model' }
        $null = Assert-ControlError (New-VisionRequest $digest $bad) $code
    }
    Assert-Control ((Get-ODSPortalPlanDigest $planPath) -ceq $digest -and $script:events[-1] -ceq 'stop') 'invalid projector requests keep the plan and start nothing'
    $script:visionLoaded = $false
    $failed = $null
    try { $null = Invoke-ODSPortalModelControl (New-VisionRequest $digest) } catch { $failed = $_.Exception }
    Assert-Control ($failed.Data['code'] -ceq 'start_unverified' -and $failed.Message -match 'vision projector' -and
        $failed.Data['newPlanDigest'] -ceq (Get-ODSPortalPlanDigest $planPath)) 'a launch that did not load its projector is never verified'
    $script:visionLoaded = $true
    $vision = Invoke-ODSPortalModelControl (New-VisionRequest $failed.Data['newPlanDigest'])
    Assert-Control ($vision.running -and $vision.plan.MmprojFile -ceq 'mmproj-F16.gguf' -and
        (Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json).MmprojFile -ceq 'mmproj-F16.gguf') 'a vision activation persists and proves its projector'
    Assert-Control ((@($vision.plan.Keys) -join ',') -ceq 'ExecutablePath,Port,ModelsDir,ContextSize,GgufFile,WslDistro,WslInstallDir,MmprojFile') 'the projector is the one optional key, after the keys the WSL bridge requires'
    $visionPlan = $vision.plan | ConvertTo-Json | ConvertFrom-Json
    $text = Invoke-ODSPortalModelControl (New-ControlRequest 'activate' $vision.planDigest)
    Assert-Control ($text.running -and -not $text.plan.Contains('MmprojFile') -and
        $null -eq (Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json).MmprojFile) 'switching to a model without a projector clears it'
    $request = New-ControlRequest 'restore' $text.planDigest; $request.plan = $visionPlan
    $back = Invoke-ODSPortalModelControl $request
    Assert-Control ($back.running -and $back.plan.MmprojFile -ceq 'mmproj-F16.gguf' -and $back.observation.modelId -ceq 'Small-0.6B.gguf') 'rollback restores the previous projector with its model'

    # --- Launcher errors in status (every GPU) --------------------------------------
    $runtimeDir = Split-Path -Parent $planPath
    $readyFile = Join-Path $runtimeDir 'ready.json'
    $current = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
    $script:running = $false
    Write-ODSPrivateEnvFile -Path $readyFile -Content '{"Error":"llama-server startup failed: Port 18080 is already occupied; no existing process was changed."}'
    $amdError = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control ($amdError.managed -and -not $amdError.running -and $amdError.runtimeError -ceq 'llama-server startup failed: Port 18080 is already occupied; no existing process was changed.' -and
        $amdError.runtimeErrorSource -ceq 'launcher' -and -not $amdError.Contains('startRefusal')) 'the launcher''s own error reaches status as written (AMD launch failures become visible), with no start refusal'
    Write-ODSPrivateEnvFile -Path $readyFile -Content (@{ Error = ('The model was not started: ' + ([string][char]0x00E9 * 3) + "`n" + ('x' * 2000)) } | ConvertTo-Json -Compress)
    $long = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control ($long.runtimeError.Length -le 1024 -and $long.runtimeError -cmatch '^[\x20-\x7e]+$' -and $long.runtimeError.StartsWith('The model was not started: ???')) 'status text is bounded printable ASCII (at most 1024 characters)'
    foreach ($damaged in @([Text.Encoding]::ASCII.GetBytes('{not json'), [byte[]]@(0x7B, 0xFF, 0xFE, 0x7D))) {
        Write-ODSPrivateFileBytes -Path $readyFile -Bytes $damaged
        $broken = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
        Assert-Control ($broken.managed -and -not $broken.running -and $broken.runtimeErrorSource -ceq 'observation' -and -not $broken.Contains('startRefusal')) 'a damaged ready.json is an observation failure as before, never a failed status'
    }
    $script:running = $true
    $null = Invoke-ODSPortalModelControl (New-ControlRequest 'start' (Get-ODSPortalPlanDigest $planPath))
    $observed = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control ($observed.running -and -not $observed.Contains('runtimeError')) 'a started model clears the launcher error'
    Assert-Control ((@($observed.Keys) -join ',') -ceq 'ok,managed,running,observation,modelStoreWindowsPath,planPathWindows,planDigest,plan,planFeatures') 'a running AMD status keeps exactly its earlier shape'
    $script:staleProcess = $true
    $stale = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control (-not $stale.running -and $stale.runtimeErrorSource -ceq 'observation' -and -not $stale.Contains('startRefusal')) 'an observation failure is marked as such, so only launcher text reaches the Models page'
    $script:staleProcess = $false

    # --- CUDA route (RTX Spark): memory before a switch, Start anyway -----------------
    $cudaOptions = Get-Content -LiteralPath $optionsPath -Raw | ConvertFrom-Json
    $cudaOptions.Device = 'CUDA0'; $cudaOptions.NGpuLayers = 'all'; $cudaOptions.ReleaseTag = 'b11429'
    $cudaOptions | Add-Member -NotePropertyName Backend -NotePropertyValue 'cuda'
    $cudaOptions | Add-Member -NotePropertyName Architecture -NotePropertyValue 'arm64'
    $amdOptionsJson = [IO.File]::ReadAllText($optionsPath)
    Write-ODSPrivateEnvFile -Path $optionsPath -Content ($cudaOptions | ConvertTo-Json -Depth 4 -Compress)
    $script:commitState = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommittedGiB = 14 }
    $script:gpuState = [pscustomobject]@{ Name = 'NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU)'; TotalMiB = 98240; UsedMiB = 1200; DriverVersion = '616.62' }
    $script:memoryReads = 0
    function Get-ODSNativeCommitState { $script:memoryReads++; return $script:commitState }
    function Get-ODSNativeGpuMemoryState([string]$GpuName = '') { $script:memoryReads++; return $script:gpuState }
    function Get-ODSWslEffectiveMemory([double]$VisibleGiB) { return [pscustomobject]@{ GiB = 15.25; Source = 'default' } }
    $catalogModel = 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf'
    [IO.File]::WriteAllText((Join-Path $models $catalogModel), 'fixture')
    $memoryFile = Join-Path $runtimeDir 'memory-plan.json'
    $overrideFile = Join-Path $runtimeDir 'start-override.json'
    function New-SwitchRequest([string]$Gguf, $Context) {
        $request = New-ControlRequest 'activate' (Get-ODSPortalPlanDigest $planPath); $request.gguf = $Gguf; $request.contextSize = $Context
        return $request
    }
    $help = 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC'
    $script:commitState = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 30.49; CommittedGiB = 14 }
    $digest = Get-ODSPortalPlanDigest $planPath
    $before = $script:events.Count
    $refused = Assert-ControlError (New-SwitchRequest $catalogModel 131072) 'insufficient_memory'
    Assert-Control ($refused.Message -match "^$([regex]::Escape($catalogModel)) at 128K needs about 25\.8 GiB, but Windows can lend about 0\.0 GiB once the current model stops \(memory limit 30\.5 GiB\)\. Your current model keeps running\." -and
        $refused.Message.EndsWith($help)) 'a switch Windows cannot lend memory for is refused with the cause and the way out (M18)'
    Assert-Control ($script:events.Count -eq $before -and (Get-ODSPortalPlanDigest $planPath) -ceq $digest -and -not (Test-Path -LiteralPath $memoryFile) -and
        (Test-ODSNativeLlamaWanted (Join-Path $runtimeDir 'intent.json'))) 'the refusal comes before any disarm or stop: plan, intent and the running model are untouched'
    $script:commitState = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommittedGiB = 14 }
    $script:gpuState.UsedMiB = 90000
    $gpuRefused = Assert-ControlError (New-SwitchRequest $catalogModel 131072) 'insufficient_memory'
    Assert-Control ($gpuRefused.Message -match 'needs about 23\.8 GiB of GPU memory, but NVIDIA RTX Spark N1X .* has about 8\.0 GiB free once the current model stops' -and
        $script:events.Count -eq $before) 'a GPU another program fills refuses the switch the same way'
    $script:gpuState.UsedMiB = 1200
    $switched = Invoke-ODSPortalModelControl (New-SwitchRequest $catalogModel 131072)
    $memory = Get-Content -LiteralPath $memoryFile -Raw | ConvertFrom-Json
    Assert-Control ($switched.running -and $switched.plan.GgufFile -ceq $catalogModel -and -not $switched.plan.Contains('MmprojFile') -and
        $memory.GgufFile -ceq $catalogModel -and $memory.ContextSize -eq 131072 -and $memory.DeviceNeedGiB -eq 23.83 -and $memory.CommitNeedGiB -eq 25.8 -and
        $memory.WslGiB -eq 15.25 -and $memory.Source -ceq 'catalog') 'a switch that fits starts, and memory-plan.json records the catalog estimate for the launcher''s own check'
    Assert-ODSPortalPrivateControlFile $memoryFile
    $imported = Invoke-ODSPortalModelControl (New-SwitchRequest 'Original-9B.gguf' 65536)
    $memory = Get-Content -LiteralPath $memoryFile -Raw | ConvertFrom-Json
    Assert-Control ($imported.running -and $memory.GgufFile -ceq 'Original-9B.gguf' -and $memory.Source -ceq 'files' -and $memory.CommitNeedGiB -eq 2) 'a model outside the catalog is sized from its files plus 2 GiB'
    $previousPlan = $imported.plan | ConvertTo-Json | ConvertFrom-Json
    $null = Invoke-ODSPortalModelControl (New-SwitchRequest $catalogModel 131072)
    $script:commitState = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 30.49; CommittedGiB = 14 }
    $request = New-ControlRequest 'restore' (Get-ODSPortalPlanDigest $planPath); $request.plan = $previousPlan
    $restored = Invoke-ODSPortalModelControl $request
    Assert-Control ($restored.running -and $restored.plan.GgufFile -ceq 'Original-9B.gguf' -and
        (Get-Content -LiteralPath $memoryFile -Raw | ConvertFrom-Json).GgufFile -ceq 'Original-9B.gguf') 'restore is never refused for memory (it is the rollback), and writes the restored plan''s memory figures'
    $stopped = Invoke-ODSPortalModelControl (New-ControlRequest 'stop' (Get-ODSPortalPlanDigest $planPath))
    $resumed = Invoke-ODSPortalModelControl (New-ControlRequest 'start' $stopped.planDigest)
    Assert-Control ($resumed.running -and $resumed.planDigest -ceq $stopped.planDigest) 'start is never refused for memory either'
    $nineB = $resumed.plan | ConvertTo-Json | ConvertFrom-Json
    $script:commitState = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommittedGiB = 14 }
    $null = Invoke-ODSPortalModelControl (New-SwitchRequest $catalogModel 131072)
    & {
        function Get-ODSPortalControlMemoryNeed { throw 'fixture: the catalog cannot be read' }
        $before = $script:events.Count
        $unsized = $null
        try { $null = Invoke-ODSPortalModelControl (New-SwitchRequest 'Small-0.6B.gguf' 65536) } catch { $unsized = $_.Exception }
        Assert-Control ($unsized.Message -match 'catalog cannot be read' -and $script:events.Count -eq $before) 'a switch whose memory cannot be sized fails before anything is stopped'
        $request = New-ControlRequest 'restore' (Get-ODSPortalPlanDigest $planPath); $request.plan = $nineB
        $rolled = Invoke-ODSPortalModelControl $request
        Assert-Control ($rolled.running -and $rolled.plan.GgufFile -ceq 'Original-9B.gguf' -and
            (Get-Content -LiteralPath $memoryFile -Raw | ConvertFrom-Json).GgufFile -ceq $catalogModel) 'a rollback whose memory cannot be sized still runs (the launcher then skips its check for the other plan''s figures)'
    }
    $script:commitState = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommittedGiB = 14 }

    # The launcher refused the saved plan: status says so, and Resume starts it anyway.
    $script:running = $false
    $plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
    $refusal = [ordered]@{ Error = "The model was not started: Windows can lend it only 1.0 GiB right now. $help"; Code = 'memory_refused'
        GgufFile = [string]$plan.GgufFile; ContextSize = [long]$plan.ContextSize }
    Write-ODSPrivateEnvFile -Path $readyFile -Content ($refusal | ConvertTo-Json -Compress)
    $status = Invoke-ODSPortalModelControl (New-ControlRequest 'status')
    Assert-Control (-not $status.running -and $status.startRefusal -ceq 'memory_refused' -and $status.runtimeErrorSource -ceq 'launcher' -and
        $status.runtimeError.StartsWith('The model was not started:')) 'a memory refusal for the saved plan reaches status as startRefusal with the launcher''s text'
    $other = [ordered]@{ Error = 'The model was not started: fixture'; Code = 'gpu_busy'; GgufFile = 'Small-0.6B.gguf'; ContextSize = 65536 }
    Write-ODSPrivateEnvFile -Path $readyFile -Content ($other | ConvertTo-Json -Compress)
    Assert-Control (-not (Invoke-ODSPortalModelControl (New-ControlRequest 'status')).Contains('startRefusal')) 'a refusal recorded for another plan is no start refusal for this one'
    Write-ODSPrivateEnvFile -Path $readyFile -Content ($refusal | ConvertTo-Json -Compress)
    $anyway = Invoke-ODSPortalModelControl (New-ControlRequest 'start' (Get-ODSPortalPlanDigest $planPath))
    $override = Get-Content -LiteralPath $overrideFile -Raw | ConvertFrom-Json
    Assert-Control ($anyway.running -and $override.GgufFile -ceq $plan.GgufFile -and $override.ContextSize -eq $plan.ContextSize) 'Resume of the refused plan is Start anyway: start-override.json names that plan for the launcher'
    Assert-ODSPortalPrivateControlFile $overrideFile
    Remove-Item -LiteralPath $overrideFile
    $script:running = $false
    Write-ODSPrivateEnvFile -Path $readyFile -Content ($refusal | ConvertTo-Json -Compress)
    $null = Invoke-ODSPortalModelControl (New-SwitchRequest $catalogModel 131072)
    Assert-Control (-not (Test-Path -LiteralPath $overrideFile)) 'a switch to another model never starts it anyway'

    # The durable launcher writes the refusal's code and plan beside its text,
    # and an AMD failure exactly as before.
    $launcherText = [IO.File]::ReadAllText((Join-Path $runtimeDir 'launch.ps1'))
    $invokeLine = '    $code = Invoke-ODSNativeLlamaRuntime $plan $options (Join-Path $PSScriptRoot ''ready.json'')'
    Assert-Control ($launcherText.Contains($invokeLine)) 'the generated launcher can be isolated before its runtime call'
    $probe = Join-Path $runtimeDir 'refusal-probe.ps1'
    Write-ODSPrivateEnvFile -Path $probe -Content $launcherText.Replace($invokeLine,
        '    throw (New-ODSNativeStartRefusal ([pscustomobject]@{ Code = ''gpu_busy''; Text = ''The model was not started: fixture refusal'' }) $plan)')
    & $testShell -NoProfile -ExecutionPolicy Bypass -File $probe
    $written = Get-Content -LiteralPath $readyFile -Raw | ConvertFrom-Json
    $plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
    Assert-Control ($LASTEXITCODE -eq 1 -and $written.Error -ceq 'The model was not started: fixture refusal' -and $written.Code -ceq 'gpu_busy' -and
        $written.GgufFile -ceq $plan.GgufFile -and $written.ContextSize -eq $plan.ContextSize) 'the launcher records a refusal with its code and the plan it was for'
    Write-ODSPrivateEnvFile -Path $probe -Content $launcherText.Replace($invokeLine, '    throw ''Port 18080 is already occupied; no existing process was changed.''')
    & $testShell -NoProfile -ExecutionPolicy Bypass -File $probe
    Assert-Control ($LASTEXITCODE -eq 1 -and [IO.File]::ReadAllText($readyFile) -ceq '{"Error":"llama-server startup failed: Port 18080 is already occupied; no existing process was changed."}') 'any other launcher failure is recorded exactly as before'
    $global:LASTEXITCODE = 0
    Remove-Item -LiteralPath $probe

    # Vulkan (AMD) never runs the memory check.
    Write-ODSPrivateEnvFile -Path $optionsPath -Content $amdOptionsJson
    $script:running = $true
    $script:memoryReads = 0
    $script:commitState = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 1; CommittedGiB = 1 }
    $amdSwitch = Invoke-ODSPortalModelControl (New-SwitchRequest 'Small-0.6B.gguf' 65536)
    Assert-Control ($amdSwitch.running -and $script:memoryReads -eq 0 -and
        (Get-Content -LiteralPath $memoryFile -Raw | ConvertFrom-Json).GgufFile -cne 'Small-0.6B.gguf') 'a Vulkan switch reads no memory figures and writes no memory plan'
    # Discrete x64 CUDA keeps its architecture through the real configuration
    # reader and never applies Spark's Arm64 unified-memory sizing policy.
    $cudaOptions.Architecture='x64'; $cudaOptions.ReleaseTag='b9014'
    Write-ODSPrivateEnvFile -Path $optionsPath -Content ($cudaOptions | ConvertTo-Json -Depth 4 -Compress)
    & {
        function Get-ODSPortalControlWslGiB { throw 'Spark WSL sizing called for x64 CUDA' }
        function Get-ODSPortalControlMemoryNeed { throw 'Spark catalog sizing called for x64 CUDA' }
        function Assert-ODSPortalControlMemoryFit { throw 'Spark memory fit called for x64 CUDA' }
        $configuration = Get-ODSPortalManagedConfiguration (New-ControlRequest 'status')
        Assert-Control ($configuration.Backend -ceq 'cuda' -and $configuration.Architecture -ceq 'x64') 'managed configuration projects validated CUDA x64 architecture'
        $beforeX64 = $configuration.Plan | ConvertTo-Json | ConvertFrom-Json
        $x64Switch = Invoke-ODSPortalModelControl (New-SwitchRequest 'Original-9B.gguf' 65536)
        Assert-Control ($x64Switch.running) 'CUDA x64 activates without Spark sizing'
        $request = New-ControlRequest 'restore' (Get-ODSPortalPlanDigest $planPath); $request.plan=$beforeX64
        $x64Restore = Invoke-ODSPortalModelControl $request
        Assert-Control ($x64Restore.running) 'CUDA x64 restores without Spark sizing'
        $x64Stop = Invoke-ODSPortalModelControl (New-ControlRequest 'stop' (Get-ODSPortalPlanDigest $planPath))
        $x64Start = Invoke-ODSPortalModelControl (New-ControlRequest 'start' $x64Stop.planDigest)
        Assert-Control ($x64Start.running) 'CUDA x64 starts the existing task without Spark sizing'
        $script:failReady=$true
        $failedX64=$null
        try { $null=Invoke-ODSPortalModelControl (New-SwitchRequest 'Original-9B.gguf' 65536) } catch { $failedX64=$_.Exception }
        Assert-Control ($failedX64.Message -ceq 'fixture load failure' -and $failedX64.Data['newPlanDigest']) 'CUDA x64 failed activation returns the published digest for recovery'
        $script:failReady=$false
        $request=New-ControlRequest 'restore' $failedX64.Data['newPlanDigest']; $request.plan=$beforeX64
        $x64Recovered=Invoke-ODSPortalModelControl $request
        Assert-Control ($x64Recovered.running) 'CUDA x64 recovers failed activation without Spark sizing'
    }
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
