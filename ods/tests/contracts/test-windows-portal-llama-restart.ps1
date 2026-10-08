# Durable llama.cpp task (Round F): mocked task, process, socket and HTTP
# boundaries plus fixture-owned Windows dummy process trees. No real task,
# llama-server, network call or user process is touched.
param([switch]$SkipProcessFixtures)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-amd.ps1')
function Test-ODSPortalPortBindable([int]$Port) { return $true }
$script:restartChecks = 0
function Assert-Restart([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:restartChecks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ("ods-llama-task-' " + [char]0x00E9 + '-' + [guid]::NewGuid().ToString('N'))
$previousLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $fixture
New-Item -ItemType Directory -Path $fixture -Force | Out-Null
try {
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory)
        return [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
    }
    $runtimeDir = Get-ODSPortalRuntimeDir
    $exeDir = Join-Path (Join-Path (Join-Path $fixture 'ODS') 'llama.cpp') 'b9014-win-vulkan-x64'
    $plan = [ordered]@{ ExecutablePath = (Join-Path $exeDir 'llama-server.exe'); Port = 18080; ModelsDir = (Get-ODSPortalModelsDir)
        ContextSize = 65536; GgufFile = 'Model-9B.gguf' }
    $options = [ordered]@{ schemaVersion = 1; Device = 'Vulkan0'; NGpuLayers = 'auto'
        ApiKeyPath = (Join-Path $runtimeDir 'api-key'); LogPath = (Join-Path $runtimeDir 'llama-server.log')
        ReleaseTag = 'b9014'; ZipSha256 = ('a' * 64); ReasoningArguments = @('--reasoning', 'off'); ExtraArguments = @() }
    $apiKey = New-ODSNativeLlamaApiKey
    $registration = New-ODSPortalRuntimeAction $plan $options $apiKey 'Ubuntu-24.04' '/home/some user/ods'
    $saved = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Restart ((@($saved.PSObject.Properties.Name) -join ',') -ceq 'ExecutablePath,Port,ModelsDir,ContextSize,GgufFile,WslDistro,WslInstallDir') 'runtime.json keeps exactly the key set the WSL bridge enforces'
    Assert-Restart ($saved.ExecutablePath -ceq $plan.ExecutablePath -and $saved.GgufFile -ceq 'Model-9B.gguf' -and $saved.ContextSize -eq 65536 -and
        $saved.Port -eq 18080 -and $saved.WslInstallDir -ceq '/home/some user/ods') 'durable JSON preserves the llama-server, model, context, port and binding'
    $savedOptions = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime-options.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Restart ($savedOptions.Device -ceq 'Vulkan0' -and $savedOptions.ReleaseTag -ceq 'b9014' -and
        (@($savedOptions.ReasoningArguments) -join ' ') -ceq '--reasoning off' -and $savedOptions.ApiKeyPath -ceq $options.ApiKeyPath) 'extra launch options live in runtime-options.json'
    Assert-Restart ($registration.Action.Arguments -ceq ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $runtimeDir 'launch.ps1') + '"') -and
        $registration.Action.WorkingDirectory -ceq $runtimeDir) 'the task runs its durable launcher hidden from the private runtime folder'
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    if ($onWindows) { $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User }
    foreach ($name in @('runtime.json', 'runtime-options.json', 'launch.ps1', 'native-llama-runtime.ps1', 'private-file.ps1', 'api-key', 'intent.json')) {
        $path = Join-Path $runtimeDir $name
        if ($onWindows) {
            $acl = Get-Acl -LiteralPath $path
            $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
            Assert-Restart ($acl.AreAccessRulesProtected -and $rules.Count -eq 1 -and $rules[0].IdentityReference -eq $sid) "$name is private to the Windows user"
        } else {
            Assert-Restart (Test-Path -LiteralPath $path -PathType Leaf) "$name is materialized (Windows ACL checks run on Windows)"
        }
        if ($name -ne 'api-key') {
            Assert-Restart (-not (Get-Content -LiteralPath $path -Raw).Contains($apiKey)) "$name never contains the API key"
        }
    }
    Assert-Restart ((Read-ODSNativeLlamaApiKey (Join-Path $runtimeDir 'api-key')) -ceq $apiKey) 'the API key file holds the generated key'
    foreach ($legacy in @('backend-contract.ps1', 'env-generator.ps1')) {
        Assert-Restart (-not (Test-Path -LiteralPath (Join-Path $runtimeDir $legacy))) "the launcher no longer copies $legacy"
    }
    $tokens = $null; $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $runtimeDir 'launch.ps1'), [ref]$tokens, [ref]$parseErrors)
    Assert-Restart ($parseErrors.Count -eq 0) 'generated durable launcher parses in this PowerShell version'
    foreach ($name in @('launch.ps1', 'native-llama-runtime.ps1', 'private-file.ps1')) {
        $bytes = [IO.File]::ReadAllBytes((Join-Path $runtimeDir $name))
        Assert-Restart (@($bytes | Where-Object { $_ -gt 127 }).Count -eq 0) "durable $name is ASCII-only"
        foreach ($codepage in @(932, 936)) {
            $decoded = [Text.Encoding]::GetEncoding($codepage).GetString($bytes)
            $null = [Management.Automation.Language.Parser]::ParseInput($decoded, [ref]$tokens, [ref]$parseErrors)
            Assert-Restart ($parseErrors.Count -eq 0) "durable $name parses under Windows codepage $codepage"
        }
    }
    $launcher = Get-Content -LiteralPath (Join-Path $runtimeDir 'launch.ps1') -Raw -Encoding UTF8
    Assert-Restart ($launcher.Contains(". (Join-Path `$PSScriptRoot 'native-llama-runtime.ps1')") -and
        $launcher.Contains('Invoke-ODSNativeLlamaRuntime $plan $options') -and -not $launcher.Contains($PSScriptRoot)) 'task dependencies resolve beside the durable launcher, independently of the checkout'

    # Run the generated plan reader in this PowerShell, replacing only the
    # runtime call with an observation. No process, task or network API runs.
    $planFile = Join-Path $runtimeDir 'runtime.json'
    $originalPlanJson = [IO.File]::ReadAllText($planFile)
    $unicodeModels = Join-Path $fixture ('models-' + [char]0x4E2D)
    $unicodePlan = $originalPlanJson | ConvertFrom-Json
    $unicodePlan.ModelsDir = $unicodeModels
    Write-ODSPrivateEnvFile -Path $planFile -Content ($unicodePlan | ConvertTo-Json -Compress)
    $invokeLine = '    $code = Invoke-ODSNativeLlamaRuntime $plan $options (Join-Path $PSScriptRoot ''ready.json'')'
    $observeOnly = @'
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'read-plan-probe.json'), (@{ plan = $plan; options = $options } | ConvertTo-Json -Compress -Depth 4))
    $code = 0
'@
    Assert-Restart ($launcher.Contains($invokeLine)) 'generated plan reader can be isolated before any runtime call'
    $probePath = Join-Path $runtimeDir 'read-plan-probe.ps1'
    Write-ODSPrivateEnvFile -Path $probePath -Content $launcher.Replace($invokeLine, $observeOnly)
    $testShell = (Get-Process -Id $PID).Path
    & $testShell -NoProfile -ExecutionPolicy Bypass -File $probePath
    Assert-Restart ($LASTEXITCODE -eq 0) 'generated UTF-8 plan reader runs without invoking a runtime'
    $readBack = [IO.File]::ReadAllText((Join-Path $runtimeDir 'read-plan-probe.json')) | ConvertFrom-Json
    Assert-Restart ($readBack.plan.ModelsDir -ceq $unicodeModels -and $readBack.plan.ExecutablePath -ceq $plan.ExecutablePath -and
        $readBack.options.Device -ceq 'Vulkan0') 'durable startup preserves Unicode Windows user paths from UTF-8 JSON'
    Set-ODSPortalRuntimeIntent 'stopped'
    Remove-Item -LiteralPath (Join-Path $runtimeDir 'read-plan-probe.json')
    & $testShell -NoProfile -ExecutionPolicy Bypass -File $probePath
    Assert-Restart ($LASTEXITCODE -eq 0 -and -not (Test-Path -LiteralPath (Join-Path $runtimeDir 'read-plan-probe.json'))) 'the generated launcher exits successfully before a runtime attempt when deliberately stopped'
    Set-ODSPortalRuntimeIntent 'running'
    Write-ODSPrivateEnvFile -Path $planFile -Content $originalPlanJson

    # Simulate sign-in: the launcher starts llama-server and proves its model.
    $script:runtimePlan = $registration.Plan
    $script:calls = [Collections.Generic.List[string]]::new()
    $script:launched = $false; $script:occupied = $false; $script:foreignAfter = $false; $script:publicBind = $false
    $script:healthCodes = @(0, 503, 200); $script:healthIndex = 0; $script:crashOnHealth = $false
    $script:servedModel = 'Model-9B.gguf'; $script:servedContext = 65536; $script:servedPath = ''; $script:propsStatus = 200
    $script:pinFailure = ''; $script:workers = $false; $script:exitBeforeFailure = $false; $script:reusedRoot = $false
    $script:killFailure = 0; $script:killExitRace = $false; $script:runtimeHandles = @{}
    $script:failOwnershipWrites = $false; $script:denyRecordOnFailure = $false; $script:runtimeExitCode = 0; $script:arguments = ''
    $script:privateWriter = ${function:Write-ODSPrivateEnvFile}
    function Write-ODSPrivateEnvFile { param($Path, $Content)
        if ($script:failOwnershipWrites -and $Path -like '*process-ownership.json') {
            throw [IO.IOException]::new('mock ownership write denied')
        }
        & $script:privateWriter -Path $Path -Content $Content
    }
    function Test-ODSNativeLlamaInstall { param($Directory, $ExpectedZipSha256, $ExpectedReleaseTag)
        $script:calls.Add('pin')
        if ($script:pinFailure) { throw $script:pinFailure }
        Assert-Restart ($Directory -ceq $exeDir -and $ExpectedZipSha256 -ceq ('a' * 64) -and $ExpectedReleaseTag -ceq 'b9014') 'every launch re-verifies pin.json for the planned runtime'
    }
    function Get-ODSNativeLlamaShortPath([string]$Path) { return 'C:\FIXTUR~1\' + [IO.Path]::GetFileName($Path).ToUpperInvariant() }
    function Start-Sleep { param($Seconds, $Milliseconds) }
    function New-FakeChild {
        $child = [pscustomobject]@{ Id = 4242; Handle = 4242; Path = $script:runtimePlan.ExecutablePath
            StartTime = [datetime]'2026-01-01T00:00:00'; ExitTime = [datetime]'2026-01-01T00:00:03'
            HasExited = $false; ExitCode = $script:runtimeExitCode; Killed = $false }
        $child | Add-Member ScriptMethod WaitForExit { param($Milliseconds)
            if ($null -ne $Milliseconds) { return $this.HasExited }
            $script:calls.Add('wait'); $this.HasExited = $true
        }
        $child | Add-Member ScriptMethod Kill { $script:calls.Add('kill'); $this.Killed = $true; $this.HasExited = $true }
        $child | Add-Member ScriptMethod Dispose { }
        return $child
    }
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction)
        if ($script:workers -and $script:runtimeHandles.ContainsKey(4243) -and $script:runtimeHandles[4243].HasExited) { return }
        if ($script:occupied -or ($script:launched -and $script:foreignAfter)) {
            return [pscustomobject]@{ LocalAddress = '127.0.0.1'; OwningProcess = 9999 }
        }
        if ($script:launched) {
            $address = if ($script:publicBind) { '0.0.0.0' } else { '127.0.0.1' }
            return [pscustomobject]@{ LocalAddress = $address; OwningProcess = 4242 }
        }
    }
    function Get-Process { param($Id, $ErrorAction)
        if ($Id -eq 4242) { return $script:child }
        if ($script:runtimeHandles.ContainsKey($Id)) { return $script:runtimeHandles[$Id] }
        $node = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $Id })[0]
        if (-not $node) { throw 'Mock process is absent.' }
        $handle = [pscustomobject]@{ Id = $Id; Handle = $Id; Path = $node.ExecutablePath
            StartTime = $node.CreationDate; HasExited = $false }
        $handle | Add-Member ScriptMethod Kill {
            if ($script:killFailure -eq $this.Id) {
                $script:calls.Add("kill-error:$($this.Id)")
                if ($script:killExitRace) { $this.HasExited = $true }
                throw [System.ComponentModel.Win32Exception]::new('mock cleanup denied')
            }
            $script:calls.Add("kill:$($this.Id)"); $this.HasExited = $true
        }
        $handle | Add-Member ScriptMethod WaitForExit { param($Milliseconds) return $this.HasExited }
        $handle | Add-Member ScriptMethod Dispose { }
        $script:runtimeHandles[$Id] = $handle
        return $handle
    }
    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction)
        if (-not $script:child.HasExited) {
            [pscustomobject]@{ ProcessId = 4242; ParentProcessId = 5; ExecutablePath = $script:runtimePlan.ExecutablePath; CreationDate = $script:child.StartTime }
        } elseif ($script:reusedRoot) {
            [pscustomobject]@{ ProcessId = 4242; ParentProcessId = 5; ExecutablePath = (Join-Path $fixture 'unrelated.exe'); CreationDate = [datetime]'2026-01-01T00:00:04' }
            [pscustomobject]@{ ProcessId = 4245; ParentProcessId = 4242; ExecutablePath = (Join-Path $fixture 'unrelated-child.exe'); CreationDate = [datetime]'2026-01-01T00:00:05' }
        }
        if ($script:workers) {
            # Defensive fixture: llama-server normally has no children, but any
            # proven descendant must be closed and nothing else.
            [pscustomobject]@{ ProcessId = 4243; ParentProcessId = 4242; ExecutablePath = (Join-Path $fixture 'helper.exe'); CreationDate = [datetime]'2026-01-01T00:00:01' }
            [pscustomobject]@{ ProcessId = 4244; ParentProcessId = 4243; ExecutablePath = (Join-Path $fixture 'helper-child.exe'); CreationDate = [datetime]'2026-01-01T00:00:02' }
            [pscustomobject]@{ ProcessId = 4999; ParentProcessId = 5; ExecutablePath = $script:runtimePlan.ExecutablePath; CreationDate = [datetime]'2025-01-01T00:00:00' }
        }
    }
    function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
        $script:arguments = $ArgumentList
        Assert-Restart ($FilePath -ceq $script:runtimePlan.ExecutablePath -and $WindowStyle -eq 'Hidden' -and
            $WorkingDirectory -ceq $exeDir) 'the pinned llama-server.exe starts hidden from its versioned folder'
        $script:calls.Add('start'); $script:launched = $true
        $script:healthIndex = 0
        $script:runtimeHandles = @{}
        $script:child = New-FakeChild
        return $script:child
    }
    function Invoke-ODSNativeLlamaHttp { param([int]$Port, [string]$Path, [string]$ApiKey = '', [int]$TimeoutMilliseconds = 5000)
        if ($Port -ne 18080) { throw "unexpected port $Port" }
        switch ($Path) {
            '/health' {
                if ($script:crashOnHealth) { $script:child.HasExited = $true; $script:child.ExitCode = -1073741515 }
                $code = $script:healthCodes[[math]::Min($script:healthIndex, $script:healthCodes.Count - 1)]
                $script:healthIndex++
                $script:calls.Add("health:$code")
                return [pscustomobject]@{ StatusCode = $code; Body = ''; Error = '' }
            }
            '/v1/models' {
                $script:calls.Add('models')
                Assert-Restart (-not $ApiKey) '/v1/models is public and gets no credential'
                if ($script:exitBeforeFailure) { $script:child.HasExited = $true }
                if ($script:denyRecordOnFailure) { $script:failOwnershipWrites = $true }
                $body = @{ data = @(@{ id = $script:servedModel; meta = @{ n_ctx_train = 65536 } }) } | ConvertTo-Json -Depth 5
                return [pscustomobject]@{ StatusCode = 200; Body = $body; Error = '' }
            }
            '/props' {
                $script:calls.Add('props')
                if ($script:propsStatus -ne 200) { return [pscustomobject]@{ StatusCode = $script:propsStatus; Body = '{}'; Error = '' } }
                Assert-Restart ($ApiKey -ceq $apiKey) '/props is read with the private API key'
                $served = if ($script:servedPath) { $script:servedPath } else { 'C:\FIXTUR~1\MODEL-9B.GGUF' }
                $body = @{ model_path = $served; total_slots = 1; default_generation_settings = @{ n_ctx = $script:servedContext } } | ConvertTo-Json -Depth 5
                return [pscustomobject]@{ StatusCode = 200; Body = $body; Error = '' }
            }
        }
        throw "unexpected path $Path"
    }
    function Reset-Launch {
        $script:launched = $false; $script:calls.Clear(); $script:healthCodes = @(0, 503, 200)
        $script:servedModel = 'Model-9B.gguf'; $script:servedContext = 65536; $script:servedPath = ''; $script:propsStatus = 200
    }
    Reset-Launch
    $code = Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath
    $ready = Get-Content -LiteralPath $registration.ReadyPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Restart ($code -eq 0 -and $ready.ModelId -ceq 'Model-9B.gguf' -and $ready.ProcessId -eq 4242 -and $ready.ContextSize -eq 65536 -and
        $ready.Port -eq 18080 -and ($script:calls -join ',') -ceq 'pin,start,health:0,health:503,health:200,models,props,wait') 'sign-in verifies pin.json, waits through loading and proves the model before publishing readiness'
    $expectedArguments = ConvertTo-ODSNativeLlamaArgumentString @('--model', 'C:\FIXTUR~1\MODEL-9B.GGUF', '--alias', 'Model-9B.gguf',
        '--host', '127.0.0.1', '--port', '18080', '--ctx-size', '65536', '--parallel', '1', '--n-gpu-layers', 'auto',
        '--device', 'Vulkan0', '--metrics', '--no-webui', '--api-key-file', 'C:\FIXTUR~1\API-KEY', '--log-file', 'C:\FIXTUR~1\LLAMA-SERVER.LOG',
        '--reasoning', 'off')
    Assert-Restart ($script:arguments -ceq $expectedArguments -and -not $script:arguments.Contains($apiKey)) 'the durable launch uses the contract arguments (8.3 paths for a non-ASCII profile) and never puts the key on argv'
    Reset-Launch
    $script:servedContext = 65536 + 200
    Assert-Restart ((Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath) -eq 0) 'llama.cpp context alignment below 256 cells still proves the requested context'
    Assert-Restart ((Wait-ODSPortalRuntimeReady $registration 1) -ceq 'Model-9B.gguf') 'setup accepts only readiness tied to its owned listener'

    foreach ($failure in @('crash', 'wrong-model', 'capped-context', 'wrong-path', 'rejected-key', 'foreignAfter', 'publicBind')) {
        Reset-Launch
        switch ($failure) {
            'crash' { $script:crashOnHealth = $true }
            'wrong-model' { $script:servedModel = 'Other.gguf' }
            'capped-context' { $script:servedContext = 32768 }
            'wrong-path' { $script:servedPath = 'C:\other\Model-9B.gguf' }
            'rejected-key' { $script:propsStatus = 401 }
            'foreignAfter' { $script:foreignAfter = $true }
            'publicBind' { $script:publicBind = $true }
        }
        $message = ''
        try { $null = Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath } catch { $message = $_.Exception.Message }
        Assert-Restart ($message -and -not (Test-Path -LiteralPath $registration.ReadyPath) -and
            ($failure -eq 'crash' -or $script:child.Killed) -and -not (Test-Path -LiteralPath (Join-Path $runtimeDir 'process-ownership.json'))) "$failure publishes no readiness and stops only the child it launched"
        if ($failure -eq 'crash') { Assert-Restart ($message -match 'Visual C\+\+' -and -not $script:calls.Contains('models')) 'a llama-server that exits during load fails at once with its exit code explained' }
        if ($failure -eq 'capped-context') { Assert-Restart ($message -match 'less than the planned 65536' -and $message -match 'training context is 65536') 'a capped context names the model training context' }
        $script:crashOnHealth = $false; $script:foreignAfter = $false; $script:publicBind = $false
    }
    Reset-Launch
    $script:pinFailure = 'llama.cpp file ggml-vulkan.dll is missing (antivirus quarantine or a partial copy). Rerun setup to restore it.'
    $message = ''
    try { $null = Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'antivirus quarantine' -and -not $script:calls.Contains('start')) 'a damaged runtime is refused at launch before any process starts'
    $script:pinFailure = ''
    $keyPath = Join-Path $runtimeDir 'api-key'
    $goodKey = [IO.File]::ReadAllBytes($keyPath)
    [IO.File]::WriteAllText($keyPath, "$apiKey`r`n")
    Reset-Launch
    $message = ''
    try { $null = Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'malformed' -and -not $script:calls.Contains('start')) 'a key file with a trailing CRLF is refused before launch (std::getline would keep the CR)'
    Write-ODSPrivateFileBytes -Path $keyPath -Bytes $goodKey

    $ownershipPath = Join-Path $runtimeDir 'process-ownership.json'
    $script:workers = $true
    foreach ($failure in @('wrong-model', 'root-exited', 'root-pid-reused', 'ownership-write-failure', 'nonzero-root-exit', 'partial-cleanup', 'exited-on-kill-error')) {
        Reset-Launch
        $script:servedModel = if ($failure -eq 'nonzero-root-exit') { 'Model-9B.gguf' } else { 'Other.gguf' }
        $script:failOwnershipWrites = $false
        $script:runtimeExitCode = if ($failure -eq 'nonzero-root-exit') { 7 } else { 0 }
        $script:exitBeforeFailure = $failure -in @('root-exited', 'root-pid-reused')
        $script:reusedRoot = $failure -eq 'root-pid-reused'
        $script:killFailure = if ($failure -in @('partial-cleanup', 'exited-on-kill-error')) { 4243 } else { 0 }
        $script:killExitRace = $failure -eq 'exited-on-kill-error'
        # The failure record itself cannot be written: cleanup must still run.
        $script:denyRecordOnFailure = $failure -eq 'ownership-write-failure'
        $message = ''
        try { $null = Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath } catch { $message = $_.Exception.Message }
        Assert-Restart ($message -and -not (Test-Path -LiteralPath $registration.ReadyPath) -and
            -not $script:calls.Contains('kill:4999') -and -not $script:calls.Contains('kill:4245')) "$failure never publishes readiness or kills an unrelated process"
        if ($failure -eq 'exited-on-kill-error') {
            Assert-Restart ($script:calls.Contains('kill-error:4243') -and $script:runtimeHandles[4243].HasExited) 'an access error is ignored only after the same owned process handle proves exit'
        }
        if ($failure -eq 'partial-cleanup') {
            $script:partialOwnership = Get-Content -LiteralPath $ownershipPath -Raw -Encoding UTF8
            $savedOwnership = $script:partialOwnership | ConvertFrom-Json
            Assert-Restart ($message -match 'Could not stop owned runtime process 4243' -and
                $savedOwnership.Processes.Count -eq 3 -and $script:child.HasExited -and
                -not $script:runtimeHandles[4243].HasExited -and -not $script:runtimeHandles[4244].HasExited) 'partial cleanup keeps exact descendant identities after its parent exits'
            Remove-Item -LiteralPath $ownershipPath -Force
        } elseif ($failure -eq 'ownership-write-failure') {
            Assert-Restart ($script:runtimeHandles[4243].HasExited -and $script:runtimeHandles[4244].HasExited -and
                $message -match "serves 'Other.gguf'" -and $message -match 'mock ownership write denied') 'ownership write failure still closes the tree and retains both actionable failures'
            $script:failOwnershipWrites = $false; $script:denyRecordOnFailure = $false
        } else {
            Assert-Restart ($script:runtimeHandles[4243].HasExited -and $script:runtimeHandles[4244].HasExited -and
                -not (Test-Path -LiteralPath $ownershipPath)) "$failure closes the owned descendants outside the runtime folder"
            $script:runtimeExitCode = 0
            $script:exitBeforeFailure = $false; $script:reusedRoot = $false
            Reset-Launch
            Assert-Restart ((Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath) -eq 0) "$failure can start and verify its model on the next attempt"
        }
    }
    $script:workers = $false; $script:exitBeforeFailure = $false; $script:reusedRoot = $false
    $script:killFailure = 0; $script:killExitRace = $false; $script:runtimeExitCode = 0
    Reset-Launch
    $script:occupied = $true
    $message = ''
    try { $null = Invoke-ODSNativeLlamaRuntime $registration.Plan $registration.Options $registration.ReadyPath } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'already occupied' -and -not $script:calls.Contains('start')) 'occupied port is refused without launching another process'
    $script:occupied = $false
    $message = ''
    try { $null = Wait-ODSPortalRuntimeReady $registration 0 } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'did not finish loading') 'setup readiness wait is bounded and names the llama-server log'
    Write-ODSPrivateEnvFile -Path $registration.ReadyPath -Content '{"Error":"llama-server startup failed: Port 18080 is already occupied."}'
    $message = ''
    try { $null = Wait-ODSPortalRuntimeReady $registration 1 } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'startup failed: Port 18080') 'launcher failure is reported immediately with its cause'
    Write-ODSPrivateEnvFile -Path $registration.ReadyPath -Content '{"ProcessId":4242,"StartedAt":"2026-01-01T00:00:00Z","Port":18080,"ModelId":"Other.gguf","ContextSize":65536}'
    $message = ''
    try { $null = Wait-ODSPortalRuntimeReady $registration 1 } catch { $message = $_.Exception.Message }
    Assert-Restart ($message -match 'does not match its plan') 'a ready record for another model is never accepted'
    Remove-Item -LiteralPath $registration.ReadyPath -Force

    # Teardown uses mocked Task Scheduler/CIM/process handles. No real task or
    # process API is invoked, including for malformed or missing ownership.
    function Get-ODSPortalUserSid([string]$UserId) {
        if (-not $UserId -or $UserId -eq 'fixture-user') { return 'S-1-5-21-1' }
        return 'S-1-5-21-2'
    }
    $script:stopTaskName = ''
    function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction, $ErrorVariable)
        $script:taskReads++
        if ($null -eq $script:stopTask -or $TaskName -cne $script:stopTaskName) { return }
        if ($script:changedTask -and $script:taskReads -gt 1) {
            return [pscustomobject]@{ TaskName = $TaskName; TaskPath = '\'; Principal = $script:stopTask.Principal; Actions = @(); State = 'Running' }
        }
        return $script:stopTask
    }
    function Get-ODSPortalTaskEngineId { return $script:engineId }
    function Get-CimInstance { param($ClassName, $ErrorAction) return $script:processNodes }
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction)
        if ($script:portTaken) { return [pscustomobject]@{ LocalAddress = '127.0.0.1'; OwningProcess = 9999 } }
    }
    function Stop-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:stopCalls.Add('task') }
    function Disable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:stopCalls.Add('disable') }
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
    # Task Scheduler and its Windows shell are mocked on every platform.
    $script:fixtureShell = $registration.Action.Execute
    function Get-Command { param($Name, $CommandType, $ErrorAction)
        return [pscustomobject]@{ Source = $script:fixtureShell; Name = 'powershell.exe' }
    }
    $wrapperArgs = $registration.Action.Arguments
    function Reset-LlamaStopFixture {
        $script:stopCalls = [Collections.Generic.List[string]]::new()
        $script:disposed = [Collections.Generic.List[int]]::new()
        $script:engineId = 100; $script:reusedPid = 0; $script:portTaken = $false
        $script:changedTask = $false; $script:taskReads = 0
        $script:stopTaskName = Get-ODSPortalRuntimeTaskName
        $script:stopTask = [pscustomobject]@{ TaskName = $script:stopTaskName
            TaskPath = '\'; State = 'Running'; Principal = [pscustomobject]@{ UserId = 'fixture-user' }
            Actions = @([pscustomobject]@{ Execute = $script:fixtureShell; Arguments = $wrapperArgs; WorkingDirectory = $runtimeDir }) }
        $script:processNodes = @(
            [pscustomobject]@{ ProcessId = 100; ParentProcessId = 5; ExecutablePath = $script:fixtureShell; CommandLine = ('"' + $script:fixtureShell + '" ' + $wrapperArgs); CreationDate = [datetime]'2025-12-31T23:59:59' }
            [pscustomobject]@{ ProcessId = 101; ParentProcessId = 100; ExecutablePath = $plan.ExecutablePath; CommandLine = 'llama-server'; CreationDate = [datetime]'2026-01-01T00:00:00' }
            [pscustomobject]@{ ProcessId = 999; ParentProcessId = 5; ExecutablePath = $plan.ExecutablePath; CommandLine = 'user-started llama-server'; CreationDate = [datetime]'2025-01-01T00:00:00' }
            [pscustomobject]@{ ProcessId = 998; ParentProcessId = 5; ExecutablePath = 'C:\Users\u\AppData\Local\lemonade_server\bin\LemonadeServer.exe'; CommandLine = 'user Lemonade'; CreationDate = [datetime]'2025-01-01T00:00:01' }
        )
        Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($registration.Plan | ConvertTo-Json -Compress)
        if (Test-Path -LiteralPath $registration.ReadyPath) { Remove-Item -LiteralPath $registration.ReadyPath -Force }
    }
    Reset-LlamaStopFixture
    Stop-ODSPortalRuntime $plan.ExecutablePath
    Assert-Restart (($script:stopCalls -join ',') -ceq 'disable,task,kill:100,kill:101' -and $script:disposed.Count -eq 2) 'the llama.cpp task stops its exact wrapper and llama-server and preserves a user llama-server and Lemonade'
    Assert-Restart (-not (Test-ODSNativeLlamaWanted (Join-Path $runtimeDir 'intent.json'))) 'every stop publishes the stopped intent before disabling the task'
    Reset-LlamaStopFixture
    $script:stopTask = $null; $script:portTaken = $true
    Stop-ODSPortalRuntime
    Assert-Restart ($script:stopCalls.Count -eq 0 -and $script:disposed.Count -eq 0) 'no llama.cpp task means no process is stopped, even with a listener present'
    foreach ($case in @('saved-child', 'stale-ready', 'orphan-ready')) {
        Reset-LlamaStopFixture
        $script:engineId = 0; $script:stopTask.State = 'Ready'
        $script:processNodes = @($script:processNodes | Where-Object { $_.ProcessId -ne 100 })
        $identity = @{ ProcessId = 101; StartedAt = '2026-01-01T00:00:00'; Port = 18080; ModelId = 'Model-9B.gguf'; ContextSize = 65536 }
        if ($case -eq 'stale-ready') { $identity.StartedAt = '2025-12-31T00:00:00' }
        if ($case -eq 'orphan-ready') { $script:processNodes = @($script:processNodes | Where-Object { $_.ProcessId -ne 101 }); $script:processNodes += [pscustomobject]@{ ProcessId = 102; ParentProcessId = 101; ExecutablePath = 'C:\x.exe'; CreationDate = [datetime]'2026-01-01T00:00:01' } }
        Write-ODSPrivateEnvFile -Path $registration.ReadyPath -Content ($identity | ConvertTo-Json -Compress)
        $message = ''
        try { Stop-ODSPortalRuntime $plan.ExecutablePath } catch { $message = $_.Exception.Message }
        if ($case -eq 'saved-child') {
            Assert-Restart (-not $message -and ($script:stopCalls -join ',') -ceq 'disable,kill:101') 'a stopped wrapper recovers only its llama-server with the matching saved identity'
        } else { Assert-Restart ($message -and -not ($script:stopCalls -match '^kill')) "$case never treats stale PID evidence as process ownership" }
    }
    foreach ($case in @('foreign-user', 'foreign-action', 'foreign-plan', 'lemonade-plan', 'duplicate-task', 'unrelated-root', 'old-child', 'recycled-pid', 'changed-task', 'queued-task', 'unknown-orphan')) {
        Reset-LlamaStopFixture
        switch ($case) {
            'foreign-user' { $script:stopTask.Principal.UserId = 'other-user' }
            'foreign-action' { $script:stopTask.Actions[0].Arguments += ' -Command bad' }
            'foreign-plan' { $other = $registration.Plan | ConvertTo-Json | ConvertFrom-Json; $other.ModelsDir = 'C:\other\models'; Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($other | ConvertTo-Json -Compress) }
            'lemonade-plan' { $other = $registration.Plan | ConvertTo-Json | ConvertFrom-Json; $other.ExecutablePath = 'C:\Users\u\AppData\Local\lemonade_server\bin\LemonadeServer.exe'; Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($other | ConvertTo-Json -Compress) }
            'duplicate-task' { $script:stopTask = @($script:stopTask, $script:stopTask) }
            'unrelated-root' { $script:engineId = 777 }
            'old-child' { $script:processNodes[1].CreationDate = [datetime]'2025-01-01T00:00:00' }
            'recycled-pid' { $script:reusedPid = 101 }
            'changed-task' { $script:changedTask = $true }
            'queued-task' { $script:engineId = 0; $script:stopTask.State = 'Queued' }
            'unknown-orphan' { $script:engineId = 0; $script:stopTask.State = 'Ready'; $script:portTaken = $true }
        }
        $message = ''
        try { Stop-ODSPortalRuntime } catch { $message = $_.Exception.Message }
        Assert-Restart ($message -and -not ($script:stopCalls -match '^(kill|task)')) "$case refuses teardown before stopping any task or process"
    }

    foreach ($case in @('startup-cleanup-retry', 'startup-recycled-worker')) {
        Reset-LlamaStopFixture
        $script:stopTask.State = 'Ready'; $script:engineId = 0
        $script:processNodes = @(
            [pscustomobject]@{ ProcessId = 4243; ParentProcessId = 4242; ExecutablePath = (Join-Path $fixture 'helper.exe'); CreationDate = [datetime]'2026-01-01T00:00:01' }
            [pscustomobject]@{ ProcessId = 4244; ParentProcessId = 4243; ExecutablePath = (Join-Path $fixture 'helper-child.exe'); CreationDate = [datetime]'2026-01-01T00:00:02' }
            [pscustomobject]@{ ProcessId = 4999; ParentProcessId = 5; ExecutablePath = $plan.ExecutablePath; CreationDate = [datetime]'2025-01-01T00:00:00' }
        )
        if ($case -eq 'startup-recycled-worker') {
            $script:processNodes[1].ParentProcessId = 4999
            $script:processNodes[1].CreationDate = [datetime]'2026-01-02T00:00:00'
        }
        Write-ODSPrivateEnvFile -Path $ownershipPath -Content $script:partialOwnership
        Write-ODSPrivateEnvFile -Path $registration.ReadyPath -Content '{"Error":"Startup failed; cleanup was interrupted."}'
        Stop-ODSPortalRuntime
        $expectedStops = if ($case -eq 'startup-cleanup-retry') { 'disable,kill:4243,kill:4244' } else { 'disable,kill:4243' }
        Assert-Restart (($script:stopCalls -join ',') -ceq $expectedStops -and -not (Test-Path -LiteralPath $ownershipPath)) "$case recovers only exact surviving process identities independently of a failed readiness record"
        $message = ''
        try { $null = Wait-ODSPortalRuntimeReady $registration 1 } catch { $message = $_.Exception.Message }
        Assert-Restart ($message -match 'Startup failed') "$case never promotes its ownership record to readiness"
    }

    # Retire-only recognition of the three launchers ODS wrote for Lemonade.
    $lemonadeExe = Join-Path (Join-Path $fixture 'Lemonade') 'LemonadeServer.exe'
    function Reset-LemonadeStopFixture([string]$Generation = 'direct') {
        $script:stopCalls = [Collections.Generic.List[string]]::new()
        $script:disposed = [Collections.Generic.List[int]]::new()
        $script:engineId = 101; $script:reusedPid = 0; $script:portTaken = $false
        $script:changedTask = $false; $script:taskReads = 0
        $script:stopTaskName = Get-ODSPortalLemonadeTaskName
        $script:stopArgs = 'serve --port 13305 --host 127.0.0.1 --no-tray --llamacpp vulkan --extra-models-dir "' + (Get-ODSPortalModelsDir) + '" --ctx-size 65536'
        $script:stopTask = [pscustomobject]@{ TaskName = $script:stopTaskName
            TaskPath = '\'; State = 'Running'; Principal = [pscustomobject]@{ UserId = 'fixture-user' }
            Actions = @([pscustomobject]@{ Execute = $lemonadeExe; Arguments = $script:stopArgs; WorkingDirectory = (Split-Path -Parent $lemonadeExe) })
        }
        $script:processNodes = @(
            [pscustomobject]@{ ProcessId = 101; ParentProcessId = 5; ExecutablePath = $lemonadeExe; CommandLine = ('"' + $lemonadeExe + '" ' + $script:stopArgs); CreationDate = [datetime]'2026-01-01T00:00:00' }
            [pscustomobject]@{ ProcessId = 102; ParentProcessId = 101; ExecutablePath = (Join-Path (Split-Path -Parent $lemonadeExe) 'lemonade-router.exe'); CreationDate = [datetime]'2026-01-01T00:00:01' }
            [pscustomobject]@{ ProcessId = 103; ParentProcessId = 102; ExecutablePath = (Join-Path $fixture 'runtime-cache/llama-server.exe'); CreationDate = [datetime]'2026-01-01T00:00:02' }
            [pscustomobject]@{ ProcessId = 999; ParentProcessId = 5; ExecutablePath = $lemonadeExe; CommandLine = 'user-started server'; CreationDate = [datetime]'2025-01-01T00:00:00' }
            [pscustomobject]@{ ProcessId = 998; ParentProcessId = 999; ExecutablePath = (Join-Path $fixture 'runtime-cache/llama-server.exe'); CreationDate = [datetime]'2025-01-01T00:00:01' }
        )
    }
    Reset-LemonadeStopFixture
    $launch = Get-ODSPortalLemonadeOwnedLaunch $script:stopTask
    Assert-Restart ($launch.ExecutablePath -ceq $lemonadeExe -and $launch.Port -eq 13305 -and $launch.ContextSize -eq 65536 -and $launch.Generation -eq 'direct') 'the Lemonade executable comes from the ODS task itself, never from a search'
    Stop-ODSPortalLemonade $lemonadeExe
    Assert-Restart (($script:stopCalls -join ',') -ceq 'disable,task,kill:101,kill:102,kill:103' -and $script:disposed.Count -eq 3) 'the former ODS Lemonade task stops its proven descendants and preserves a separate user Lemonade and llama instance'
    Reset-LemonadeStopFixture
    $script:stopTask = $null; $script:portTaken = $true
    Stop-ODSPortalLemonade
    Assert-Restart ($script:stopCalls.Count -eq 0 -and $script:disposed.Count -eq 0) 'with no ODS Lemonade task, a running user Lemonade is never stopped'
    foreach ($case in @('foreign-user', 'foreign-action', 'foreign-models', 'duplicate-task', 'duplicate-root', 'unrelated-root', 'old-child', 'recycled-pid', 'changed-task', 'queued-task', 'unknown-orphan')) {
        Reset-LemonadeStopFixture
        switch ($case) {
            'foreign-user' { $script:stopTask.Principal.UserId = 'other-user' }
            'foreign-action' { $script:stopTask.Actions[0].Execute += '-other' }
            'foreign-models' { $script:stopTask.Actions[0].Arguments = $script:stopArgs.Replace('models', 'other-models') }
            'duplicate-task' { $script:stopTask = @($script:stopTask, $script:stopTask) }
            'duplicate-root' { $script:processNodes += [pscustomobject]@{ ProcessId = 104; ParentProcessId = 101; ExecutablePath = $lemonadeExe; CommandLine = $script:processNodes[0].CommandLine; CreationDate = [datetime]'2026-01-01T00:00:02' } }
            'unrelated-root' { $script:engineId = 777 }
            'old-child' { $script:processNodes[1].CreationDate = [datetime]'2025-01-01T00:00:00' }
            'recycled-pid' { $script:reusedPid = 102 }
            'changed-task' { $script:changedTask = $true }
            'queued-task' { $script:engineId = 0; $script:stopTask.State = 'Queued' }
            'unknown-orphan' { $script:engineId = 0; $script:stopTask.State = 'Ready'; $script:portTaken = $true }
        }
        $message = ''
        try { Stop-ODSPortalLemonade $lemonadeExe } catch { $message = $_.Exception.Message }
        Assert-Restart ($message -and -not ($script:stopCalls -match '^(kill|task)')) "former Lemonade task: $case refuses teardown before stopping any task or process"
    }
    $legacyPath = Join-Path (Get-ODSPortalStateDir) 'lemonade-launch.task.ps1'
    $legacyText = '$exe = ' + (ConvertTo-ODSPowerShellSingleQuotedLiteral $lemonadeExe) + "`n" +
        '$argumentString = ''--port 13305 --host 127.0.0.1''' + "`n" +
        '$workingDirectory = ' + (ConvertTo-ODSPowerShellSingleQuotedLiteral (Split-Path -Parent $lemonadeExe))
    foreach ($case in @('former-modern', 'dynamic-setting', 'compound-setting', 'duplicate-setting', 'foreign-legacy-exe', 'nonloopback-setting')) {
        Reset-LemonadeStopFixture
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
        # Legacy PowerShell source needs a BOM for Unicode literals on 5.1;
        # the runtime JSON above remains UTF-8 without a BOM.
        Write-ODSPrivateEnvFile -Path $legacyPath -Content ([string][char]0xFEFF + $contents)
        $message = ''
        try { Stop-ODSPortalLemonade } catch { $message = $_.Exception.Message }
        if ($case -eq 'former-modern') {
            Assert-Restart (-not $message -and ($script:stopCalls -join ',') -ceq 'disable,task,kill:100,kill:101,kill:102,kill:103') 'the former ODS 10.7 launcher is retired using literal AST settings and its exact task process tree'
        } else {
            Assert-Restart ($message -and $message -notmatch 'must never execute' -and -not ($script:stopCalls -match '^(kill|task)')) "$case is rejected without evaluating the former launcher or stopping processes"
        }
    }
    # The durable Lemonade launcher shared portal-runtime\launch.ps1.
    Reset-LemonadeStopFixture
    $lemonadePlan = [ordered]@{ ExecutablePath = $lemonadeExe; Port = 13305; ModelsDir = (Get-ODSPortalModelsDir); ContextSize = 65536; GgufFile = 'Model-9B.gguf'
        WslDistro = 'Ubuntu-24.04'; WslInstallDir = '/home/some user/ods' }
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($lemonadePlan | ConvertTo-Json -Compress)
    $script:stopTask.Actions[0].Execute = $script:fixtureShell
    $script:stopTask.Actions[0].Arguments = $wrapperArgs
    $script:stopTask.Actions[0].WorkingDirectory = $runtimeDir
    $script:processNodes[0].ParentProcessId = 100
    $script:processNodes += [pscustomobject]@{ ProcessId = 100; ParentProcessId = 5; ExecutablePath = $script:fixtureShell; CommandLine = ('"' + $script:fixtureShell + '" ' + $wrapperArgs); CreationDate = [datetime]'2025-12-31T23:59:59' }
    $script:engineId = 100
    $launch = Get-ODSPortalLemonadeOwnedLaunch $script:stopTask
    Assert-Restart ($launch.Generation -eq 'durable' -and $launch.GgufFile -ceq 'Model-9B.gguf' -and $launch.ContextSize -eq 65536 -and $launch.Port -eq 13305) 'the durable Lemonade plan carries the selected model, context and port'
    Stop-ODSPortalLemonade
    Assert-Restart (($script:stopCalls -join ',') -ceq 'disable,task,kill:100,kill:101,kill:102,kill:103') 'the former durable Lemonade task stops its exact wrapper and tree'

    if ($onWindows -and -not $SkipProcessFixtures) {
        # Real disposable process trees prove Windows handle semantics. Every
        # process is started by this fixture; no service, task or runtime runs.
        foreach ($name in @('Start-Process', 'Get-Process', 'Get-CimInstance', 'Get-Command', 'Start-Sleep')) {
            Remove-Item -LiteralPath "Function:$name"
        }
        $shellPath = (Microsoft.PowerShell.Management\Get-Process -Id $PID).Path
        $dummyScript = Join-Path $fixture 'dummy-parent.ps1'
        $dummyRecord = Join-Path $fixture 'dummy-child.json'
        $dummySource = @'
param([string]$Shell, [string]$Record)
$ErrorActionPreference = 'Stop'
$child = Start-Process -FilePath $Shell -ArgumentList '-NoProfile -NonInteractive -Command "Start-Sleep -Seconds 120"' -WindowStyle Hidden -PassThru
$null = $child.Handle
@{ ProcessId = $child.Id; StartedAt = $child.StartTime.ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath ($Record + '.partial')
Move-Item -LiteralPath ($Record + '.partial') -Destination $Record
Start-Sleep -Seconds 120
'@
        [IO.File]::WriteAllText($dummyScript, $dummySource, [Text.UTF8Encoding]::new($true))
        $unrelated = Microsoft.PowerShell.Management\Start-Process -FilePath $shellPath `
            -ArgumentList '-NoProfile -NonInteractive -Command "Start-Sleep -Seconds 120"' -WindowStyle Hidden -PassThru
        $null = $unrelated.Handle
        try {
            foreach ($exitFirst in @($false, $true)) {
                $parent = $null; $dummyChild = $null; $owned = $null
                if (Test-Path -LiteralPath $dummyRecord) { Remove-Item -LiteralPath $dummyRecord -Force }
                try {
                    $arguments = '-NoProfile -NonInteractive -File "' + $dummyScript + '" -Shell "' + $shellPath + '" -Record "' + $dummyRecord + '"'
                    $parent = Microsoft.PowerShell.Management\Start-Process -FilePath $shellPath -ArgumentList $arguments -WindowStyle Hidden -PassThru
                    $null = $parent.Handle
                    # JSON ownership dates are UTC; CIM returns local dates.
                    $rootIdentity = [pscustomobject]@{ ProcessId = $parent.Id; CreationDate = $parent.StartTime.ToUniversalTime(); ExecutablePath = $shellPath; ExitedAt = $null }
                    $deadline = [datetime]::UtcNow.AddSeconds(15)
                    while (-not (Test-Path -LiteralPath $dummyRecord) -and [datetime]::UtcNow -lt $deadline) { Microsoft.PowerShell.Utility\Start-Sleep -Milliseconds 100 }
                    $record = Get-Content -LiteralPath $dummyRecord -Raw | ConvertFrom-Json
                    $candidate = Microsoft.PowerShell.Management\Get-Process -Id $record.ProcessId -ErrorAction Stop
                    try {
                        $null = $candidate.Handle
                        Assert-Restart ([math]::Abs(($candidate.StartTime.ToUniversalTime() - ([datetime]$record.StartedAt).ToUniversalTime()).TotalMilliseconds) -lt 1) 'real dummy child is retained by its exact recorded process identity'
                        $dummyChild = $candidate
                        $candidate = $null
                    } finally {
                        # A failed identity check never authorizes final cleanup
                        # to terminate whichever process now owns that PID.
                        if ($candidate) { $candidate.Dispose() }
                    }
                    if ($exitFirst) {
                        $parent.Kill()
                        if (-not $parent.WaitForExit(5000)) { throw 'Dummy parent did not exit.' }
                        $rootIdentity.ExitedAt = $parent.ExitTime
                        Assert-Restart (-not $dummyChild.HasExited) 'Windows dummy child survives its parent, exercising the original orphan failure'
                    }
                    # Keep this integration fixture's snapshot confined to its
                    # three real processes. An unrelated orphan on a busy runner
                    # can retain a reused parent PID and correctly trigger the
                    # production ancestry guard. The stale/recycled-PID cases
                    # above test that refusal deterministically.
                    $fixtureFilter = 'ProcessId = {0} OR ProcessId = {1} OR ProcessId = {2}' -f $parent.Id, $dummyChild.Id, $unrelated.Id
                    $fixtureNodes = @(CimCmdlets\Get-CimInstance Win32_Process -Filter $fixtureFilter -ErrorAction Stop)
                    Assert-Restart (@($fixtureNodes | Where-Object { $_.ProcessId -eq $dummyChild.Id -and $_.ParentProcessId -eq $parent.Id }).Count -eq 1) 'real dummy child has the expected CIM parent identity'
                    $owned = Get-ODSPortalOwnedProcessTree @($rootIdentity) $fixtureNodes
                    Stop-ODSPortalOwnedProcesses $owned.Handles
                    Assert-Restart ($parent.HasExited -and $dummyChild.HasExited -and -not $unrelated.HasExited) "real cleanup (parent exited=$exitFirst) closes only its own tree and preserves the unrelated dummy"
                } finally {
                    if ($owned) { foreach ($handle in $owned.Handles) { $handle.Dispose() } }
                    foreach ($process in @($parent, $dummyChild)) {
                        if ($process) {
                            if (-not $process.HasExited) { $process.Kill(); $null = $process.WaitForExit(5000) }
                            $process.Dispose()
                        }
                    }
                }
            }
        } finally {
            if (-not $unrelated.HasExited) { $unrelated.Kill(); $null = $unrelated.WaitForExit(5000) }
            $unrelated.Dispose()
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
Microsoft.PowerShell.Utility\Write-Host "Passed $script:restartChecks Portal llama.cpp restart contracts."
