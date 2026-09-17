$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/backend-contract.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$definitions = @{}
foreach ($name in @('Get-ODSNativeModelSelection','Get-ODSConfiguredNativeExecutable','ConvertTo-ODSNativeArgumentString',
    'Get-NativeInferenceBackend','Start-NativeInferenceServer','Start-ODSLemonadeRuntime','Wait-ODSLemonadeConfiguredModel',
    'Test-ODSLemonadeLoadedModelMatches','Stop-ODSLemonadeRuntime')) {
    $function = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $function) { throw "Missing function $name" }
    $definitions[$name] = $function.Extent.Text
    . ([scriptblock]::Create($function.Extent.Text))
}
function Assert-True { param($Condition, [string]$Message) if (-not $Condition) { throw $Message }; $script:Assertions++ }
function Assert-Throws {
    param([scriptblock]$Action, [string]$Pattern)
    try { & $Action } catch { Assert-True ($_.Exception.Message -match $Pattern) "Wrong error: $_"; return }
    throw "Expected error: $Pattern"
}
$script:Assertions = 0
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-model-stores-' + [Guid]::NewGuid().ToString('N'))
$InstallDir = Join-Path $fixtureRoot 'install with spaces'
$ssd = Join-Path $fixtureRoot 'SSD modelos'
$runtime = Join-Path $fixtureRoot "runtime's folder/llama-server.exe"
$script:EnvMap = @{ ODS_ACTIVE_MODEL_STORE = 'ssd'; GGUF_FILE = 'model.gguf'; CTX_SIZE = '8192';
    LLM_BACKEND = 'llama-server'; LLAMA_ARG_SPEC_TYPE = 'stale-mtp'; LLAMA_REASONING = 'off' }
function Read-ODSEnv { return $script:EnvMap }
function Sync-ODSNativeInferenceConfig { }
function Get-ODSEnvValue { param($Name, $Default) if ($script:EnvMap[$Name]) { return $script:EnvMap[$Name] }; return $Default }
function Resolve-ODSHostAgentPython { return [pscustomobject]@{ FilePath = (Get-Command python -CommandType Application).Source; PrefixArgs = @() } }
function Write-AI { param($Message) }
function Write-AIWarn { param($Message) }
function Write-AISuccess { param($Message) }
function Write-AIError { param($Message) throw $Message }
function Start-Sleep { param($Seconds, $Milliseconds) }
function Invoke-WebRequest { param($Uri, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction) return @{ StatusCode = 200 } }
function Get-NativeInferenceStatus { return @{ Running = $false; Backend = 'llama-server' } }
function Write-FixtureRegistry {
    $script:Registry | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $InstallDir 'data/model-stores.json') -Encoding utf8
}
function Write-FixtureEnv {
    @($script:EnvMap.Keys | ForEach-Object { "$_=$($script:EnvMap[$_])" }) |
        Set-Content -LiteralPath (Join-Path $InstallDir '.env') -Encoding utf8
}
try {
    foreach ($directory in @('data/models','scripts','extensions/services/dashboard-api')) {
        New-Item -ItemType Directory -Path (Join-Path $InstallDir $directory) -Force | Out-Null
    }
    New-Item -ItemType Directory -Path $ssd, (Split-Path -Parent $runtime) -Force | Out-Null
    [IO.File]::WriteAllText($runtime, 'fixture runtime; never execute')
    [IO.File]::WriteAllText((Join-Path $ssd 'model.gguf'), 'fixture checkpoint')
    Copy-Item -LiteralPath (Join-Path $root 'scripts/resolve-model-store.py') -Destination (Join-Path $InstallDir 'scripts')
    foreach ($module in @('model_stores.py','env_values.py')) {
        Copy-Item -LiteralPath (Join-Path $root "extensions/services/dashboard-api/$module") -Destination (Join-Path $InstallDir 'extensions/services/dashboard-api')
    }
    $script:Registry = @{ schemaVersion = 1; stores = @(@{ id = 'ssd'; hostPath = $ssd; containerPath = '/model-stores/ssd';
        profiles = @{ 'model.gguf' = @{ backend = 'vulkan'; executable = $runtime; contextLength = 16384; mtp = $true; draftTokens = 2;
            runtimeSha256 = (Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash.ToLowerInvariant();
            modelSha256 = (Get-FileHash -LiteralPath (Join-Path $ssd 'model.gguf') -Algorithm SHA256).Hash.ToLowerInvariant() } } }) }
    Write-FixtureRegistry; Write-FixtureEnv
    $selection = Get-ODSNativeModelSelection -VerifyArtifacts
    Assert-True ($selection.modelsDirectory -eq $ssd) 'SSD was not resolved'
    Assert-True ($selection.profile.executable -eq $runtime) 'Qualified executable was lost'
    Assert-True ($selection.profile.contextLength -eq 8192) 'Persisted context was lost'

    # Missing/remounted disks never become the default directory. Stop can still
    # identify a running qualified executable without needing the checkpoint.
    $modelFile = Join-Path $ssd 'model.gguf'
    Move-Item -LiteralPath $modelFile -Destination "$modelFile.offline"
    Assert-Throws { Get-ODSNativeModelSelection -VerifyArtifacts } 'missing|unavailable'
    Assert-True ((Get-ODSNativeModelSelection -AllowMissingModel).profile.executable -eq $runtime) 'Stop ownership metadata requires model availability'
    Move-Item -LiteralPath "$modelFile.offline" -Destination $modelFile
    [IO.File]::AppendAllText($runtime, ' changed')
    Assert-Throws { Get-ODSNativeModelSelection -VerifyArtifacts } 'changed since qualification'
    [IO.File]::WriteAllText($runtime, 'fixture runtime; never execute')

    $script:LLAMA_SERVER_EXE = Join-Path $fixtureRoot 'missing-default.exe'
    $script:LEMONADE_EXE = Join-Path $fixtureRoot 'Lemonade/LemonadeServer.exe'
    $script:LEMONADE_PORT = 13305
    $script:LEMONADE_HEALTH_URL = 'http://127.0.0.1:13305/api/v1/health'
    $script:INFERENCE_PID_FILE = Join-Path $InstallDir 'data/llama-server.pid'
    $script:LEMONADE_TASK_NAME = 'ODSFixtureOnly'
    Assert-True ((Get-NativeInferenceBackend) -eq 'llama-server') 'Custom executable was ignored when bundled executable is absent'
    Move-Item -LiteralPath $runtime -Destination "$runtime.offline"
    Assert-True ((Get-NativeInferenceBackend) -eq 'llama-server') 'Missing external executable erased the identity needed for Stop'
    Move-Item -LiteralPath "$runtime.offline" -Destination $runtime
    $script:Started = @()
    function Start-Process {
        param($FilePath, $ArgumentList, $WindowStyle, [switch]$PassThru, $WorkingDirectory, $RedirectStandardOutput, $RedirectStandardError)
        $script:Started += @{ executable = $FilePath; arguments = [string]$ArgumentList; cwd = $WorkingDirectory;
            runtime = $env:LEMONADE_LLAMACPP_VULKAN_BIN; window = $WindowStyle }
        return [pscustomobject]@{ Id = 19001 }
    }
    Start-NativeInferenceServer
    Assert-True ($script:Started[-1].executable -eq $runtime) 'Native start used the bundled runtime instead of qualified executable'
    Assert-True ($script:Started[-1].arguments.Contains('"' + $modelFile + '"')) 'Native model path with spaces was not quoted'
    Assert-True ($script:Started[-1].arguments.Contains('"--spec-type" "draft-mtp"')) 'Native MTP args missing'
    Assert-True ($script:Started[-1].arguments.Contains('"--ctx-size" "8192"')) 'Native persisted context missing'
    Assert-True ($script:Started[-1].window -eq 'Hidden') 'Native launch was not hidden'
    $script:Registry.stores[0].profiles['model.gguf'].mtp = $false
    Write-FixtureRegistry
    Start-NativeInferenceServer
    Assert-True (-not $script:Started[-1].arguments.Contains('spec-type')) 'Stale global MTP was applied to a baseline model'
    $script:Registry.stores[0].profiles['model.gguf'].mtp = $true
    Write-FixtureRegistry

    # Lemonade receives the actual per-model load options, without persisting a
    # speculative change; a failed load cannot be mistaken for an old healthy model.
    $script:Loads = @(); $script:LoadFails = $false
    function Resolve-ODSLemonadeModelId { param($Port, $GgufFile) return 'model' }
    function Get-ODSLemonadeAdminApiKey { param($EnvPath) return 'fixture-key' }
    function Invoke-RestMethod {
        param($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, $ErrorAction)
        if ($Uri.EndsWith('/load')) {
            $script:Loads += ($Body | ConvertFrom-Json)
            if ($script:LoadFails) { throw 'fixture load failed' }
            return @{ status = 'success' }
        }
        if ($Uri.EndsWith('/health')) { return @{ model_loaded = 'model' } }
        throw 'Unexpected request in fixture'
    }
    Wait-ODSLemonadeConfiguredModel -EnvVars $script:EnvMap
    Assert-True ($script:Loads[-1].llamacpp_args.Contains('--spec-type draft-mtp')) 'Lemonade MTP options were not applied'
    Assert-True ($script:Loads[-1].ctx_size -eq 8192 -and $script:Loads[-1].save_options -eq $false) 'Lemonade load persisted speculative settings or wrong context'
    $script:LoadFails = $true
    Assert-Throws { Wait-ODSLemonadeConfiguredModel -EnvVars $script:EnvMap } 'fixture load failed'
    $script:LoadFails = $false

    # Both task-scheduler and direct-process paths carry the selected binary.
    $contract = Get-ODSLemonadeLaunchContract -ExecutablePath $script:LEMONADE_EXE -Port 13305 -ModelsDir $ssd `
        -ContextSize 8192 -VersionOverride '10.7.0' -RuntimeEnvironment @{ LEMONADE_LLAMACPP_VULKAN_BIN = $runtime }
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory) return @{ Execute = $Execute; Argument = $Argument } }
    $logPath = Join-Path $InstallDir 'logs/test-launch.log'
    $null = New-ODSLemonadeScheduledTaskAction -Contract $contract -EnvPath (Join-Path $InstallDir '.env') -DiagnosticLogPath $logPath
    $wrapperPath = [IO.Path]::ChangeExtension($logPath, '.task.ps1')
    $wrapper = [IO.File]::ReadAllText($wrapperPath)
    Assert-True ($wrapper.Contains("'LEMONADE_LLAMACPP_VULKAN_BIN'")) 'Scheduled task omitted runtime environment'
    Assert-True ($wrapper.Contains($runtime.Replace("'", "''"))) 'Scheduled task did not escape qualified executable path'
    [void][Management.Automation.Language.Parser]::ParseFile($wrapperPath, [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) 'Generated task wrapper is invalid PowerShell'
    $priorRuntime = $env:LEMONADE_LLAMACPP_VULKAN_BIN
    try {
        $env:LEMONADE_LLAMACPP_VULKAN_BIN = 'old-owner-value'
        $null = Start-ODSLemonadeDirectProcess -Contract $contract -DiagnosticLogPath $logPath
        Assert-True ($script:Started[-1].runtime -eq $runtime) 'Direct launch omitted qualified executable'
        Assert-True ($env:LEMONADE_LLAMACPP_VULKAN_BIN -eq 'old-owner-value') 'Direct launch leaked profile into subsequent models'
    } finally { $env:LEMONADE_LLAMACPP_VULKAN_BIN = $priorRuntime }
    Assert-Throws { Get-ODSLemonadeLaunchContract -ExecutablePath $script:LEMONADE_EXE -Port 13305 -ModelsDir $ssd -VersionOverride '10.7.0' -RuntimeEnvironment @{ PATH = $runtime } } 'Invalid registered'

    # Start performs strict resolution before its first destructive stop.
    $script:StopCalls = 0
    function Stop-ODSLemonadeRuntime { $script:StopCalls++ }
    Move-Item -LiteralPath $modelFile -Destination "$modelFile.offline"
    Assert-Throws { Start-ODSLemonadeRuntime } 'missing|unavailable'
    Assert-True ($script:StopCalls -eq 0) 'Missing SSD stopped the working runtime'
    Move-Item -LiteralPath "$modelFile.offline" -Destination $modelFile

    function Get-ODSLemonadeExecutableVersion { param($ExecutablePath, $VersionOverride) return [Version]'10.7.0' }
    function New-ScheduledTaskTrigger { param([switch]$Once, $At) return @{} }
    function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, $ExecutionTimeLimit) return @{} }
    function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel) return @{} }
    function Register-ScheduledTask { param($TaskName, $Action, $Trigger, $Settings, $Principal, [switch]$Force, $ErrorAction) }
    function Start-ScheduledTask { param($TaskName, $ErrorAction) }
    function Get-CimInstance { param($ClassName, $ErrorAction) return [pscustomobject]@{ ProcessId = 19100; ExecutablePath = $script:LEMONADE_EXE } }
    $script:ConfiguredDirectory = $null
    function Set-ODSLemonadeModernRuntimeConfig { param($Port, $ModelsDir, $AdminApiKey, $ContextSize) $script:ConfiguredDirectory = $ModelsDir }
    $null = Start-ODSLemonadeRuntime
    Assert-True ($script:ConfiguredDirectory -eq $ssd) 'Normal Windows start reverted Lemonade extra_models_dir to the default'

    # Stop owns the custom child by ancestry, not its filename. An unrelated
    # process using the same qualified executable must survive.
    . ([scriptblock]::Create($definitions['Stop-ODSLemonadeRuntime']))
    $script:Processes = @(
        [pscustomobject]@{ ProcessId = 19100; ParentProcessId = 1; ExecutablePath = $script:LEMONADE_EXE; CommandLine = '' },
        [pscustomobject]@{ ProcessId = 19101; ParentProcessId = 19100; ExecutablePath = $runtime; CommandLine = '' },
        [pscustomobject]@{ ProcessId = 19102; ParentProcessId = 1; ExecutablePath = $runtime; CommandLine = '' })
    function Get-CimInstance { param($ClassName, $ErrorAction) return $script:Processes }
    function Stop-ScheduledTask { param($TaskName, $ErrorAction) }
    function Unregister-ScheduledTask { param($TaskName, [switch]$Confirm, $ErrorAction) }
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) return @() }
    function Test-ODSNativeProcessExecutable { param($ProcessId, $ExpectedExecutable) return @($script:Processes | Where-Object { $_.ProcessId -eq $ProcessId -and $_.ExecutablePath -eq $ExpectedExecutable }).Count -eq 1 }
    $script:Stopped = @()
    function Stop-ODSNativeProcessId { param($ProcessId) $script:Stopped += $ProcessId }
    Move-Item -LiteralPath $modelFile -Destination "$modelFile.offline"
    Stop-ODSLemonadeRuntime
    Assert-True ($script:Stopped -contains 19101) 'Owned qualified child survived Stop after SSD disappearance'
    Assert-True ($script:Stopped -notcontains 19102) 'Stop killed an unrelated user of the same executable'

    . (Join-Path $root 'installers/windows/lib/env-generator.ps1')
    function Get-LlamaCpuBudget { return @{ Limit = '4.0'; Reservation = '1.0'; Available = '4.0' } }
    $tier = @{ TierName = 'Test'; LlmModel = 'model'; GgufFile = 'model.gguf'; MaxContext = 8192 }
    New-ODSEnv -InstallDir $InstallDir -TierConfig $tier -Tier '1' -GpuBackend 'none' -ODSMode 'local' -SystemRamGB 8 | Out-Null
    Assert-True (([IO.File]::ReadAllText((Join-Path $InstallDir '.env'))) -match '(?m)^ODS_ACTIVE_MODEL_STORE=ssd\r?$') 'Reinstall dropped the active store for the unchanged model'
    $tier.GgufFile = 'other.gguf'
    New-ODSEnv -InstallDir $InstallDir -TierConfig $tier -Tier '1' -GpuBackend 'none' -ODSMode 'local' -SystemRamGB 8 | Out-Null
    Assert-True (([IO.File]::ReadAllText((Join-Path $InstallDir '.env'))) -match '(?m)^ODS_ACTIVE_MODEL_STORE=default\r?$') 'New tier model inherited the previous model store'
    Write-Host "[PASS] $script:Assertions Windows model-store and runtime checks"
} finally {
    $resolved = [IO.Path]::GetFullPath($fixtureRoot)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if ($resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved).StartsWith('ods-model-stores-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}
