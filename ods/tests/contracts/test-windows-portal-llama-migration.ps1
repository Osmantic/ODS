# Windows Portal cutover from Lemonade (and between llama.cpp builds) to the
# pinned llama-server.exe: stage and qualify first, read the owned plan, back
# up portal-runtime, stop only proven ODS identities, prove the new runtime,
# roll back to the previous ODS task on failure, retire ODS-owned leftovers.
# Task Scheduler, process control, downloads and llama-server are in-memory
# fixtures; only files under a temporary LOCALAPPDATA are written.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Write-Host { param([Parameter(ValueFromRemainingArguments = $true)]$Text) $script:output.Add([string]($Text -join ' ')) }
# Process control and installers must never be reached by the orchestration.
function Stop-Process { throw 'Stop-Process must never be used by the migration.' }
function Start-Process { throw 'Start-Process (msiexec or any runtime) must never be used by the migration.' }
function Get-CimInstance { throw 'Process enumeration belongs to the proven-stop helpers, which are fixtures here.' }
function msiexec.exe { throw 'ODS never runs msiexec.' }
function Start-Sleep { param($Seconds, $Milliseconds) }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-migration-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previousLocalAppData = $env:LOCALAPPDATA
$previousPort = $env:AMD_INFERENCE_PORT
$env:LOCALAPPDATA = $fixture
$env:AMD_INFERENCE_PORT = ''
try {
    $script:sid = 'S-1-5-21-100-200-300-1001'
    function Get-ODSPortalUserSid([string]$UserId) { if ($UserId) { return $UserId }; return $script:sid }
    $script:shellPath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    function Get-Command { param($Name, $CommandType, $ErrorAction)
        if ($CommandType -eq 'Application') { return [pscustomobject]@{ Source = $script:shellPath; Name = 'powershell.exe' } }
        Microsoft.PowerShell.Core\Get-Command -Name $Name -CommandType $CommandType -ErrorAction Stop
    }
    # --- In-memory Task Scheduler ----------------------------------------------
    $script:tasks = @{}
    $script:queried = [Collections.Generic.List[string]]::new()
    $script:events = [Collections.Generic.List[string]]::new()
    $script:output = [Collections.Generic.List[string]]::new()
    function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction, $ErrorVariable)
        $script:queried.Add($TaskName)
        if ($script:tasks.ContainsKey($TaskName)) { return $script:tasks[$TaskName] }
    }
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory)
        [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
    }
    function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User) [pscustomobject]@{ AtLogOn = [bool]$AtLogOn; User = $User } }
    function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, $ExecutionTimeLimit, $RestartCount, $RestartInterval) @{ RestartCount = $RestartCount } }
    function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel) [pscustomobject]@{ UserId = $script:sid; RunLevel = $RunLevel } }
    function Register-ScheduledTask { param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
        $script:events.Add('register:' + $TaskName)
        $script:tasks[$TaskName] = [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; Principal = $Principal; Actions = @($Action)
            State = 'Ready'; Trigger = $Trigger }
    }
    function Unregister-ScheduledTask { param($TaskName, $TaskPath, [switch]$Confirm, $ErrorAction)
        $script:events.Add('unregister:' + $TaskName); $script:tasks.Remove($TaskName)
    }
    function Enable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        $script:events.Add('enable:' + $TaskName); $script:tasks[$TaskName].State = 'Ready'
    }
    function Disable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        $script:events.Add('disable:' + $TaskName); $script:tasks[$TaskName].State = 'Disabled'
    }
    function Start-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
        $script:events.Add('start:' + $TaskName)
        if ($script:startFailure -and $TaskName -like 'ODSLlamaServerRuntime-*') { throw $script:startFailure }
    }
    function Get-ODSPortalTaskEngineId { param($TaskName) return 0 }
    # The proven-tree stops are covered by test-windows-portal-llama-restart.ps1.
    function Stop-ODSPortalLemonadeLaunch($Task, $Launch) {
        $script:events.Add('quiesce:' + $Launch.TaskName)
        # As the real stop: publish the stopped intent, then disable the task.
        Set-ODSPortalRuntimeIntent 'stopped'
        Disable-ScheduledTask -TaskName $Launch.TaskName -TaskPath '\'
    }
    function Stop-ODSPortalRuntime([string]$ExecutablePath = '') {
        $task = Get-ODSPortalRuntimeTask
        if (-not $task) { return }
        $script:events.Add('stop-llama')
        Set-ODSPortalRuntimeIntent 'stopped'
        Disable-ScheduledTask -TaskName $task.TaskName -TaskPath '\'
    }
    # --- llama.cpp acquisition, qualification and readiness fixtures ----------
    $script:pin = [pscustomobject]@{ ReleaseTag = 'b9014'; Build = 9014; Asset = 'llama-b9014-bin-win-vulkan-x64.zip'; Sha256 = ('c' * 64)
        Size = 33541404; Url = 'https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-vulkan-x64.zip'; Source = 'fixture' }
    function Get-ODSNativeLlamaPin { param($SourceRoot) return $script:pin }
    $script:installed = @{}
    function New-FixtureRuntime([string]$Tag) {
        $directory = Join-Path (Get-ODSNativeLlamaRoot) "$Tag-win-vulkan-x64"
        $null = New-Item -ItemType Directory -Path $directory -Force
        [IO.File]::WriteAllText((Join-Path $directory 'llama-server.exe'), 'fixture server, never executed')
        $script:installed[$directory] = $true
        return [pscustomobject]@{ Directory = $directory; ExecutablePath = (Join-Path $directory 'llama-server.exe'); ReleaseTag = $Tag; ZipSha256 = $script:pin.Sha256; FileCount = 1 }
    }
    function Test-ODSNativeLlamaInstall { param($Directory, $ExpectedZipSha256, $ExpectedReleaseTag)
        if (-not $script:installed[$Directory]) { throw 'no pin.json' }
        return [pscustomobject]@{ Directory = $Directory; ExecutablePath = (Join-Path $Directory 'llama-server.exe'); ReleaseTag = $ExpectedReleaseTag; ZipSha256 = $ExpectedZipSha256; FileCount = 1 }
    }
    function Install-ODSNativeLlamaRuntime { param($Pin, $Root)
        $script:events.Add('install:' + $Pin.ReleaseTag)
        if ($script:installFailure) { throw $script:installFailure }
        return (New-FixtureRuntime $Pin.ReleaseTag)
    }
    $script:device = [pscustomobject]@{ Name = 'Vulkan0'; Description = 'AMD Radeon RX 9070 XT'; TotalMiB = 16304; FreeMiB = 16000 }
    function Test-ODSNativeLlamaQualification { param($ExecutablePath, $ExpectedBuild, $AdapterName, $CodeIntegrity)
        $script:events.Add('qualify')
        if ($script:qualifyFailure) { throw $script:qualifyFailure }
        $devices = if ($script:device) { @($script:device) } else { @() }
        return [pscustomobject]@{ Build = 9014; Commit = 'd4b0c22f'; Devices = $devices; Device = $script:device; PolicyHint = $script:policyHint }
    }
    function Get-ODSNativeReasoningArgs { param($Executable, $Mode, $FallbackFormat) return @('--reasoning', 'off') }
    function Get-ODSPortalModel($Plan) {
        $script:events.Add('model:' + $Plan.GgufFile)
        $null = New-Item -ItemType Directory -Path (Get-ODSPortalModelsDir) -Force
        [IO.File]::WriteAllText((Join-Path (Get-ODSPortalModelsDir) $Plan.GgufFile), 'fixture model')
        return (Get-ODSPortalModelsDir)
    }
    function Wait-ODSPortalRuntimeReady($Registration, [int]$Seconds = 1020) {
        if (-not $Registration.PSObject.Properties['Action']) {
            # The previous runtime a rollback restarted.
            $script:events.Add('ready-restored')
            if ($script:restoredReadyFailure) { throw $script:restoredReadyFailure }
            return [string]$Registration.Plan.GgufFile
        }
        $script:events.Add('ready')
        if ($script:readyFailure) { throw $script:readyFailure }
        return [string]$Registration.Plan.GgufFile
    }
    $script:owners = @{}
    $script:reserved = @()
    function Get-ODSPortalPortOwner([int]$Port) { return $script:owners[$Port] }
    function Test-ODSPortalPortBindable([int]$Port) { return $Port -notin $script:reserved }
    function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
        $script:events.Add('confirm'); $script:prompts.Add($Message)
        return (-not $NonInteractive -and $script:consent)
    }
    function Reset-Scenario {
        $script:events.Clear(); $script:output.Clear(); $script:queried.Clear()
        $script:prompts = [Collections.Generic.List[string]]::new()
        $script:consent = $true; $script:installFailure = ''; $script:qualifyFailure = ''; $script:readyFailure = ''; $script:startFailure = ''
        $script:restoredReadyFailure = ''
        $script:policyHint = ''; $script:owners = @{}; $script:reserved = @()
        $script:device = [pscustomobject]@{ Name = 'Vulkan0'; Description = 'AMD Radeon RX 9070 XT'; TotalMiB = 16304; FreeMiB = 16000 }
    }
    function Get-Index([string]$Event) { return $script:events.IndexOf($Event) }
    $plan = [pscustomobject]@{ GpuName = 'AMD Radeon RX 9070 XT'; VramMB = 16304; MemoryType = 'discrete'; Tier = '2'; LinuxTier = '2'
        Model = 'qwen3.5-9b'; GgufFile = 'Recommended-Q4.gguf'; GgufUrl = 'https://example.invalid/r.gguf'; GgufSha256 = ''; ContextSize = 65536 }
    $runtimeDir = Get-ODSPortalRuntimeDir
    $llamaTask = Get-ODSPortalRuntimeTaskName
    $lemonadeTask = Get-ODSPortalLemonadeTaskName
    # A user's own Lemonade (never ODS's to change) and an unrelated task.
    $userLemonade = Join-Path (Join-Path (Join-Path $fixture 'lemonade_server') 'bin') 'LemonadeServer.exe'
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $userLemonade) -Force
    [IO.File]::WriteAllText($userLemonade, 'user lemonade binary')
    $userLemonadeHash = (Get-FileHash -LiteralPath $userLemonade -Algorithm SHA256).Hash
    $foreignTask = [pscustomobject]@{ TaskName = 'ForeignLemonadeRuntime'; TaskPath = '\'; State = 'Running'
        Principal = [pscustomobject]@{ UserId = $script:sid }
        Actions = @([pscustomobject]@{ Execute = $userLemonade; Arguments = 'serve --port 9000'; WorkingDirectory = (Split-Path -Parent $userLemonade) }) }

    # --- Fresh install: consent, CPU route, qualification failures ------------
    Reset-Scenario
    $script:consent = $false
    Check ($null -eq (Initialize-ODSPortalAmdRuntime $plan $fixture $false 'Ubuntu-24.04' '/home/user/ods')) 'declining the llama.cpp download keeps the CPU route'
    Check ($script:prompts.Count -eq 1 -and $script:prompts[0] -match 'llama\.cpp b9014 \(Vulkan\) from github\.com/ggml-org' -and $script:prompts[0] -match 'SHA-256 pinned' -and
        $script:prompts[0] -match '127\.0\.0\.1' -and -not ($script:events -match '^(install|register)')) 'a new install asks before downloading and changes nothing when declined'
    Reset-Scenario
    Check ($null -eq (Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods')) 'a non-interactive new install keeps the CPU route without downloading'
    Reset-Scenario
    $script:device = $null
    Check ($null -eq (Initialize-ODSPortalAmdRuntime $plan $fixture $false 'Ubuntu-24.04' '/home/user/ods')) 'no Vulkan device keeps the CPU route'
    Check ((($script:output -join ' ') -match 'found no usable Vulkan device' -and ($script:output -join ' ') -match 'Adrenalin driver') -and
        -not ($script:events -match '^register')) 'the CPU route is announced with driver guidance, never a silent CPU llama-server'
    Reset-Scenario
    $script:qualifyFailure = 'llama-server.exe --version failed with exit code -1073741515. A DLL it needs is missing (0xC0000135), most likely the Microsoft Visual C++ 2015-2022 Redistributable (x64).'
    $message = ''
    try { $null = Initialize-ODSPortalAmdRuntime $plan $fixture $false 'Ubuntu-24.04' '/home/user/ods' } catch { $message = $_.Exception.Message }
    Check ($message -match 'Visual C\+\+' -and -not ($script:events -match '^(register|model)')) 'a binary that cannot run stops setup with guidance before any model or task change'

    # --- Fresh install: success --------------------------------------------------
    Reset-Scenario
    $script:installed = @{}
    $route = Initialize-ODSPortalAmdRuntime $plan $fixture $false 'Ubuntu-24.04' '/home/user/ods'
    $key = Read-ODSNativeLlamaApiKey (Join-Path $runtimeDir 'api-key')
    $expectedArgs = @('--native-llm-url', 'http://localhost:8080', '--native-llm-host-transport', 'model-router',
        '--native-llm-model', 'Recommended-Q4.gguf', '--native-llm-context-size', '65536',
        '--native-llm-gpu-name', 'AMD Radeon RX 9070 XT', '--native-llm-gpu-vram-mb', '16304',
        '--native-llm-api-key-env', 'ODS_NATIVE_LLM_API_KEY')
    Check ((@($route.Arguments) -join '|') -ceq ($expectedArgs -join '|')) 'a new install returns the neutral --native-llm-* Linux flags (no --lemonade-* flag)'
    Check ($route.Environment.Count -eq 1 -and $route.Environment['ODS_NATIVE_LLM_API_KEY'] -ceq $key -and
        -not ((@($route.Arguments) -join ' ').Contains($key))) 'the API key travels by environment variable name, never as an argument'
    Check ((Get-Index 'install:b9014') -lt (Get-Index 'qualify') -and (Get-Index 'qualify') -lt (Get-Index 'model:Recommended-Q4.gguf') -and
        (Get-Index 'model:Recommended-Q4.gguf') -lt (Get-Index "register:$llamaTask") -and
        (Get-Index "register:$llamaTask") -lt (Get-Index "start:$llamaTask") -and (Get-Index "start:$llamaTask") -lt (Get-Index 'ready')) 'stage, qualify, model, register, start and prove happen in order'
    Check ($script:tasks[$llamaTask].Trigger.AtLogOn -and $script:tasks[$llamaTask].Trigger.User -ceq $script:sid -and
        $script:tasks[$llamaTask].Principal.RunLevel -eq 'Limited') 'the task starts at this user''s sign-in with a limited token'
    $saved = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json
    Check ($saved.ExecutablePath -ceq (Join-Path (Join-Path (Get-ODSNativeLlamaRoot) 'b9014-win-vulkan-x64') 'llama-server.exe') -and $saved.Port -eq 8080 -and
        $saved.GgufFile -ceq 'Recommended-Q4.gguf' -and $saved.ContextSize -eq 65536 -and $saved.WslDistro -ceq 'Ubuntu-24.04') 'the plan names the versioned llama-server.exe, port 8080 and the recommended model'
    Check (-not ($script:queried -contains 'ForeignLemonadeRuntime')) 'a fresh install never looks at tasks ODS does not own'

    # --- Rerun: keep the user's selection, key and port (fixes the 5.7 reset) --
    Reset-Scenario
    [IO.File]::WriteAllText((Join-Path (Get-ODSPortalModelsDir) 'Dashboard-Pick.gguf'), 'fixture model')
    $selected = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json
    # 28080 is not the first new-install candidate, so keeping it is visible.
    $selected.GgufFile = 'Dashboard-Pick.gguf'; $selected.ContextSize = 32768; $selected.Port = 28080
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($selected | ConvertTo-Json -Compress)
    $route = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods'
    Check ((@($route.Arguments) -join ' ') -match '--native-llm-url http://localhost:28080 .+--native-llm-model Dashboard-Pick\.gguf --native-llm-context-size 32768') 'a rerun keeps the dashboard-selected model, context and port instead of the tier default'
    Check (-not ($script:events -match '^model:') -and -not ($script:events -contains 'confirm')) 'a rerun neither downloads the tier model nor prompts'
    Check ($route.Environment['ODS_NATIVE_LLM_API_KEY'] -ceq $key) 'a rerun keeps the API key the WSL side already stores'
    Check ((Get-Index 'stop-llama') -lt (Get-Index "register:$llamaTask") -and -not ($script:events -match '^unregister')) 'a rerun stops its own task tree first and keeps the same task'
    Check (@(Get-ChildItem -LiteralPath (Get-ODSPortalStateDir) -Directory | Where-Object { $_.Name -like 'portal-runtime.backup-*' }).Count -eq 0) 'a successful rerun leaves no backup behind'
    $script:owners = @{}

    # --- Rerun: a selected vision model keeps its projector while it is stored --
    Reset-Scenario
    $projectorPath = Join-Path (Get-ODSPortalModelsDir) 'mmproj-Pick-F16.gguf'
    [IO.File]::WriteAllText($projectorPath, 'fixture projector')
    $selected = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json
    $selected | Add-Member -NotePropertyName MmprojFile -NotePropertyValue 'mmproj-Pick-F16.gguf' -Force
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($selected | ConvertTo-Json -Compress)
    $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods'
    $kept = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json
    $declared = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime-options.json') -Raw | ConvertFrom-Json
    Check ($kept.GgufFile -ceq 'Dashboard-Pick.gguf' -and $kept.MmprojFile -ceq 'mmproj-Pick-F16.gguf' -and
        (@($declared.PlanFeatures) -join ',') -ceq 'MmprojFile') 'a rerun keeps the selected vision projector, and the options declare that the launcher reads it'
    Remove-Item -LiteralPath $projectorPath
    Reset-Scenario
    $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods'
    $kept = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json
    Check ($kept.GgufFile -ceq 'Dashboard-Pick.gguf' -and $null -eq $kept.MmprojFile) 'a projector no longer in the store is dropped and the model kept'
    $script:owners = @{}

    # --- Rerun failure rolls back to the previous llama.cpp plan ----------------
    Reset-Scenario
    $before = @{}
    foreach ($name in @('runtime.json', 'runtime-options.json', 'launch.ps1', 'api-key')) { $before[$name] = [IO.File]::ReadAllBytes((Join-Path $runtimeDir $name)) }
    $script:pin = $script:pin.PSObject.Copy(); $script:pin.ReleaseTag = 'b9100'; $script:pin.Build = 9100
    $script:readyFailure = 'llama-server exited during startup with code 1.'
    $message = ''
    try { $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods' } catch { $message = $_.Exception.Message }
    Check ($message -match 'did not start: llama-server exited during startup' -and $message -match 'previous ODS runtime was restored and is running again') 'a failed repin reports the cause and the restored runtime once it proves ready'
    $restored = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json
    Check ($restored.ExecutablePath -match 'b9014-win-vulkan-x64' -and $restored.GgufFile -ceq 'Dashboard-Pick.gguf') 'rollback restores the previous llama-server and selection'
    foreach ($name in $before.Keys) {
        Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $runtimeDir $name))) -ceq [Convert]::ToBase64String($before[$name])) "rollback restores $name byte for byte"
    }
    Check ($script:tasks.ContainsKey($llamaTask) -and $script:events[$script:events.Count - 3] -ceq "enable:$llamaTask" -and
        $script:events[$script:events.Count - 2] -ceq "start:$llamaTask" -and $script:events[$script:events.Count - 1] -ceq 'ready-restored' -and
        (Test-ODSNativeLlamaWanted (Join-Path $runtimeDir 'intent.json'))) 'rollback re-enables and restarts the same task with a running intent, then checks it is ready'
    Check (@(Get-ChildItem -LiteralPath (Get-ODSPortalStateDir) -Directory | Where-Object { $_.Name -like 'portal-runtime.*backup-*' }).Count -eq 0) 'a completed rollback removes its redundant backup'

    # --- A restored runtime that cannot start again is not reported running ----
    Reset-Scenario
    $script:readyFailure = 'llama-server exited during startup with code 1.'
    $script:restoredReadyFailure = 'Port 28080 is used by ''other-app''.'
    $message = ''
    try { $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods' } catch { $message = $_.Exception.Message }
    Check ($message -match 'did not start: llama-server exited during startup' -and
        $message -match 'previous ODS runtime was restored but did not start again: Port 28080 is used by ''other-app''\. Fix that, then rerun setup\.' -and
        $message -notmatch 'is running again') 'a restored runtime that cannot start again says so and why, instead of claiming it restarted'
    Check (@(Get-ChildItem -LiteralPath (Get-ODSPortalStateDir) -Directory | Where-Object { $_.Name -like 'portal-runtime.*backup-*' }).Count -eq 0) 'a completed rollback removes its redundant backup'
    $script:pin = $script:pin.PSObject.Copy(); $script:pin.ReleaseTag = 'b9014'; $script:pin.Build = 9014

    # --- Successful repin keeps N-1 for rollback --------------------------------
    Reset-Scenario
    $script:pin = $script:pin.PSObject.Copy(); $script:pin.ReleaseTag = 'b9100'; $script:pin.Build = 9100
    $realCleanup = ${function:Remove-ODSNativeLlamaOldRuntimes}
    function Remove-ODSNativeLlamaOldRuntimes { param($KeepDirectory, $PreviousDirectory, $Root)
        $script:events.Add('gc:' + (Split-Path -Leaf $KeepDirectory) + ':' + (Split-Path -Leaf $PreviousDirectory))
    }
    $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods'
    Check ($script:events -contains 'gc:b9100-win-vulkan-x64:b9014-win-vulkan-x64') 'a repin keeps the previous build for rollback and only then cleans older ones'
    Check ((Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw | ConvertFrom-Json).ExecutablePath -match 'b9100-win-vulkan-x64') 'the new build is published through the same plan path'
    $script:pin = $script:pin.PSObject.Copy(); $script:pin.ReleaseTag = 'b9014'; $script:pin.Build = 9014
    Set-Item -Path Function:Remove-ODSNativeLlamaOldRuntimes -Value $realCleanup

    # --- Migration from the durable Lemonade Portal task -----------------------
    function New-LemonadeInstall {
        # A Lemonade-era machine has no llama.cpp runtime yet.
        $script:installed = @{}
        $script:tasks.Clear()
        Remove-Item -LiteralPath (Get-ODSPortalStateDir) -Recurse -Force
        $null = New-Item -ItemType Directory -Path $runtimeDir -Force
        [IO.File]::WriteAllText((Join-Path (New-Item -ItemType Directory -Path (Get-ODSPortalModelsDir) -Force).FullName 'Selected-Q4.gguf'), 'fixture model')
        $old = [ordered]@{ ExecutablePath = $userLemonade; Port = 13305; ModelsDir = (Get-ODSPortalModelsDir); ContextSize = 32768
            GgufFile = 'Selected-Q4.gguf'; WslDistro = 'Ubuntu-24.04'; WslInstallDir = '/home/user/ods' }
        Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($old | ConvertTo-Json -Compress)
        foreach ($name in @('launch.ps1', 'backend-contract.ps1', 'env-generator.ps1')) {
            Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir $name) -Content "# former Lemonade $name"
        }
        Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'intent.json') -Content '{"State":"running"}'
        Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'ready.json') -Content '{"ProcessId":77,"StartedAt":"2026-01-01T00:00:00Z","Port":13305,"ModelId":"Selected-Q4","ContextSize":32768}'
        [IO.File]::WriteAllText((Join-Path $runtimeDir 'lemonade-launch.log'), 'former log')
        [IO.File]::WriteAllText((Join-Path (Get-ODSPortalStateDir) 'lemonade-msi-install.log'), 'Property(S): ProductVersion = 10.0.0')
        $script:tasks[$lemonadeTask] = [pscustomobject]@{ TaskName = $lemonadeTask; TaskPath = '\'; State = 'Running'
            Principal = [pscustomobject]@{ UserId = $script:sid }
            Actions = @([pscustomobject]@{ Execute = $script:shellPath; Arguments = (Get-ODSPortalLauncherArguments); WorkingDirectory = $runtimeDir }) }
        $script:tasks['ForeignLemonadeRuntime'] = $foreignTask
    }
    Reset-Scenario
    New-LemonadeInstall
    $oldBytes = @{}
    foreach ($name in @('runtime.json', 'launch.ps1', 'backend-contract.ps1', 'env-generator.ps1')) { $oldBytes[$name] = [IO.File]::ReadAllBytes((Join-Path $runtimeDir $name)) }
    $route = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods'
    Check (-not ($script:events -contains 'confirm') -and (Get-Index 'install:b9014') -ge 0) 'migration stages llama.cpp without a prompt, even non-interactively'
    Check ((Get-Index 'install:b9014') -lt (Get-Index "quiesce:$lemonadeTask") -and (Get-Index 'qualify') -lt (Get-Index "quiesce:$lemonadeTask")) 'the new runtime is staged and qualified while Lemonade still serves'
    Check ((Get-Index "quiesce:$lemonadeTask") -lt (Get-Index "register:$llamaTask") -and (Get-Index 'ready') -lt (Get-Index "unregister:$lemonadeTask")) 'the ODS Lemonade task is stopped before the cutover and retired only after llama.cpp proves its model'
    Check ((@($route.Arguments) -join ' ') -match '--native-llm-url http://localhost:13305 .+--native-llm-model Selected-Q4\.gguf --native-llm-context-size 32768') 'migration keeps the owned plan''s port, model and context (never a fresh Lemonade search)'
    Check (-not ($script:events -match '^model:')) 'migration reuses the GGUF already in the ODS model store'
    $backups = @(Get-ChildItem -LiteralPath (Get-ODSPortalStateDir) -Directory | Where-Object { $_.Name -like 'portal-runtime.lemonade-backup-*' })
    Check ($backups.Count -eq 1) 'portal-runtime is backed up once before the cutover'
    foreach ($name in $oldBytes.Keys) {
        Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $backups[0].FullName $name))) -ceq [Convert]::ToBase64String($oldBytes[$name])) "the backup keeps the former $name byte for byte"
    }
    Check (-not $script:tasks.ContainsKey($lemonadeTask) -and $script:tasks.ContainsKey($llamaTask)) 'the ODS Lemonade task is replaced by the llama.cpp task'
    Check (-not (Test-Path -LiteralPath (Join-Path $runtimeDir 'backend-contract.ps1')) -and -not (Test-Path -LiteralPath (Join-Path $runtimeDir 'env-generator.ps1')) -and
        (Test-Path -LiteralPath (Join-Path $runtimeDir 'lemonade-launch.log'))) 'ODS-owned Lemonade launcher copies are removed and logs are kept'
    Check ($script:tasks.ContainsKey('ForeignLemonadeRuntime') -and $script:tasks['ForeignLemonadeRuntime'].State -eq 'Running' -and
        -not ($script:events -match 'ForeignLemonadeRuntime') -and -not ($script:queried -contains 'ForeignLemonadeRuntime')) 'a Lemonade task ODS never owned is never examined, stopped or removed'
    Check ((Get-FileHash -LiteralPath $userLemonade -Algorithm SHA256).Hash -eq $userLemonadeHash -and (Test-Path -LiteralPath $userLemonade)) 'the Lemonade installation itself is left unchanged'
    Check ((($script:output -join ' ') -match 'ODS installed Lemonade Server 10\.0\.0 on .+ no longer uses it') -and
        (($script:output -join ' ') -match 'Settings > Apps')) 'a one-time notice explains that ODS left its former Lemonade installed'
    Reset-Scenario
    $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods'
    Check (-not (($script:output -join ' ') -match 'Lemonade Server')) 'the Lemonade notice is shown only once'

    # --- Migration failure rolls back to the former Lemonade task -------------
    Reset-Scenario
    New-LemonadeInstall
    $script:readyFailure = 'llama-server did not finish loading the model.'
    $message = ''
    try { $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods' } catch { $message = $_.Exception.Message }
    Check ($message -match 'did not start: llama-server did not finish loading' -and $message -match 'previous ODS runtime was restored and its task was started again') 'a failed migration names the cause and the restored runtime'
    foreach ($name in $oldBytes.Keys) {
        Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $runtimeDir $name))) -ceq [Convert]::ToBase64String($oldBytes[$name])) "rollback restores the former $name byte for byte"
    }
    foreach ($name in @('runtime-options.json', 'api-key', 'native-llama-runtime.ps1', 'private-file.ps1', 'ready.json')) {
        Check (-not (Test-Path -LiteralPath (Join-Path $runtimeDir $name))) "rollback removes the new runtime's $name"
    }
    Check (-not $script:tasks.ContainsKey($llamaTask) -and $script:tasks.ContainsKey($lemonadeTask) -and $script:tasks[$lemonadeTask].State -eq 'Ready' -and
        $script:events[$script:events.Count - 1] -ceq "start:$lemonadeTask" -and (Test-ODSNativeLlamaWanted (Join-Path $runtimeDir 'intent.json'))) 'rollback unregisters the new task and re-enables and restarts the former ODS task'
    Check (-not ($script:events -match "^unregister:$lemonadeTask")) 'a failed migration never retires the former task'

    # --- A port reserved after reboot is caught after the stop, then rolled back
    Reset-Scenario
    New-LemonadeInstall
    $script:reserved = @(13305)
    $message = ''
    try { $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods' } catch { $message = $_.Exception.Message }
    Check ($message -match 'Port 13305 cannot bind' -and -not ($script:events -match '^register') -and
        $script:events[$script:events.Count - 1] -ceq "start:$lemonadeTask") 'a kept port that cannot bind restores the former task before any registration'

    # --- Nothing changes when staging or qualification fails ------------------
    foreach ($failure in @('install', 'qualify', 'device')) {
        Reset-Scenario
        New-LemonadeInstall
        switch ($failure) {
            'install' { $script:installFailure = 'The llama.cpp download does not match its pinned SHA-256; it was discarded.' }
            'qualify' { $script:qualifyFailure = 'llama-server.exe could not start: Smart App Control blocks it.' }
            'device' { $script:device = $null }
        }
        $message = ''
        try { $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods' } catch { $message = $_.Exception.Message }
        Check ($message -and -not ($script:events -match '^(quiesce|register|disable|unregister)') -and $script:tasks[$lemonadeTask].State -eq 'Running' -and
            [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $runtimeDir 'launch.ps1'))) -ceq [Convert]::ToBase64String($oldBytes['launch.ps1'])) "a $failure failure during staging leaves the running Lemonade route unchanged"
        if ($failure -eq 'device') { Check ($message -match 'current ODS runtime was not changed') 'no Vulkan device on a migration stops instead of silently moving to the CPU' }
    }

    # --- An unrecognized Lemonade task is never touched ------------------------
    Reset-Scenario
    New-LemonadeInstall
    $script:tasks[$lemonadeTask].Actions = @([pscustomobject]@{ Execute = 'C:\Tools\other.exe'; Arguments = '--port 13305'; WorkingDirectory = 'C:\Tools' })
    $message = ''
    try { $null = Initialize-ODSPortalAmdRuntime $plan $fixture $true 'Ubuntu-24.04' '/home/user/ods' } catch { $message = $_.Exception.Message }
    Check ($message -match 'not recognized' -and $script:events.Count -eq 0) 'a task ODS cannot prove it wrote stops setup before any download or change'
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    $env:AMD_INFERENCE_PORT = $previousPort
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows Portal llama.cpp migration contracts."
