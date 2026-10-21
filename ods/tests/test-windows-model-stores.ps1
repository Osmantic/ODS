$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/backend-contract.ps1')
. (Join-Path $root 'installers/windows/lib/native-llama-runtime.ps1')
. (Join-Path $root 'installers/windows/lib/native-llama-args.ps1')
. (Join-Path $root 'installers/windows/lib/native-llama-legacy.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$definitions = @{}
foreach ($name in @('Get-ODSNativeModelSelection','Get-ODSConfiguredNativeExecutable','Get-NativeInferenceBackend','Start-NativeInferenceServer',
    'Get-ODSNativeLlamaStartPlan','Start-ODSNativeLlamaFromPlan')) {
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
$previousLocalAppData = $env:LOCALAPPDATA
$InstallDir = Join-Path $fixtureRoot 'install with spaces'
$ssd = Join-Path $fixtureRoot 'SSD modelos'
$runtime = Join-Path $fixtureRoot "runtime's folder/llama-server.exe"
$script:EnvMap = @{ ODS_ACTIVE_MODEL_STORE = 'ssd'; GGUF_FILE = 'model.gguf'; CTX_SIZE = '8192';
    LLM_BACKEND = 'llama-server'; LLAMA_ARG_SPEC_TYPE = 'stale-mtp'; LLAMA_REASONING = 'off' }
function Read-ODSEnv { return $script:EnvMap }
function Sync-ODSNativeInferenceConfig { }
function Get-ODSEnvValue { param($Name, $Default) if ($script:EnvMap[$Name]) { return $script:EnvMap[$Name] }; return $Default }
function Resolve-ODSHostAgentPython { return [pscustomobject]@{ FilePath = (Get-Command python -CommandType Application | Select-Object -First 1).Source; PrefixArgs = @() } }
function Write-AI { param($Message) }
function Write-AIWarn { param($Message) }
function Write-AISuccess { param($Message) }
function Write-AIError { param($Message) throw $Message }
function Start-Sleep { param($Seconds, $Milliseconds) }
function Invoke-WebRequest { param($Uri, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction) return @{ StatusCode = 200 } }
function Get-NativeInferenceStatus { return @{ Running = $false; Backend = 'llama-server' } }
function Write-FixtureRegistry {
    [IO.File]::WriteAllText((Join-Path $InstallDir 'data/model-stores.json'), ($script:Registry | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
}
function Write-FixtureEnv {
    $content = @($script:EnvMap.Keys | ForEach-Object { "$_=$($script:EnvMap[$_])" }) -join "`n"
    [IO.File]::WriteAllText((Join-Path $InstallDir '.env'), $content, [Text.UTF8Encoding]::new($false))
}
try {
    foreach ($directory in @('data/models','scripts','extensions/services/dashboard-api')) {
        New-Item -ItemType Directory -Path (Join-Path $InstallDir $directory) -Force | Out-Null
    }
    New-Item -ItemType Directory -Path $ssd, (Split-Path -Parent $runtime) -Force | Out-Null
    [IO.File]::WriteAllText($runtime, 'fixture runtime; never execute')
    [IO.File]::WriteAllText((Join-Path $ssd 'model.gguf'), 'fixture checkpoint')
    Copy-Item -LiteralPath (Join-Path $root 'scripts/resolve-model-store.py') -Destination (Join-Path $InstallDir 'scripts')
    foreach ($module in @('model_stores.py','env_values.py','model_mtp.py')) {
        Copy-Item -LiteralPath (Join-Path $root "extensions/services/dashboard-api/$module") -Destination (Join-Path $InstallDir 'extensions/services/dashboard-api')
    }
    # The real resolver now validates native launch arguments with --help.
    # Keep the real model-store/hash/command validators; mock only that external
    # process boundary because the fixture executable is deliberately inert.
    $probeShim = @'
import importlib.util
import json
import sysconfig
from pathlib import Path
spec = importlib.util.spec_from_file_location('_fixture_real_subprocess', Path(sysconfig.get_path('stdlib')) / 'subprocess.py')
real = importlib.util.module_from_spec(spec)
spec.loader.exec_module(real)
DEVNULL, PIPE, STDOUT = real.DEVNULL, real.PIPE, real.STDOUT
CREATE_NO_WINDOW = getattr(real, 'CREATE_NO_WINDOW', 0)
def run(command, **kwargs):
    assert command[-1] == '--help', 'Fixture attempted to start inference'
    assert Path(command[0]).read_text() == 'fixture runtime; never execute', 'Unexpected runtime probe'
    assert '--model' in command and '--ctx-size' in command, 'Incomplete runtime arguments'
    with (Path(__file__).parent / 'runtime-probes.jsonl').open('a') as out:
        out.write(json.dumps(command) + '\n')
    return real.CompletedProcess(command, 0, '--model FNAME\n--ctx-size N\n')
'@
    [IO.File]::WriteAllText((Join-Path $InstallDir 'extensions/services/dashboard-api/subprocess.py'), $probeShim)
    $script:Registry = @{ schemaVersion = 1; stores = @(@{ id = 'ssd'; hostPath = $ssd; containerPath = '/model-stores/ssd';
        profiles = @{ 'model.gguf' = @{ backend = 'vulkan'; executable = $runtime; contextLength = 16384; mtp = $true; draftTokens = 2;
            runtimeSha256 = (Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash.ToLowerInvariant();
            modelSha256 = (Get-FileHash -LiteralPath (Join-Path $ssd 'model.gguf') -Algorithm SHA256).Hash.ToLowerInvariant() } } }) }
    Write-FixtureRegistry; Write-FixtureEnv
    $selection = Get-ODSNativeModelSelection -VerifyArtifacts
    Assert-True ($selection.modelsDirectory -eq $ssd) 'SSD was not resolved'
    Assert-True ($selection.profile.executable -eq $runtime) 'Qualified executable was lost'
    Assert-True ($selection.profile.contextLength -eq 8192) 'Persisted context was lost'
    $probe = Get-Content -LiteralPath (Join-Path $InstallDir 'extensions/services/dashboard-api/runtime-probes.jsonl') | Select-Object -First 1 | ConvertFrom-Json
    Assert-True ($probe[0] -eq $runtime -and $probe[-1] -eq '--help') 'Real resolver did not validate the selected runtime command'

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
    $script:LLAMA_SERVER_DIR = $fixtureRoot
    $script:INFERENCE_PID_FILE = Join-Path $InstallDir 'data/llama-server.pid'
    $script:NATIVE_LLM_PORT = 18080
    Assert-True ((Get-NativeInferenceBackend) -eq 'llama-server') 'Custom executable was ignored when bundled executable is absent'
    Move-Item -LiteralPath $runtime -Destination "$runtime.offline"
    Assert-True ((Get-NativeInferenceBackend) -eq 'llama-server') 'Missing external executable erased the identity needed for Stop'
    Move-Item -LiteralPath "$runtime.offline" -Destination $runtime

    # Start launches through native-llama-legacy.ps1: the registered profile
    # keeps its qualified runtime and arguments and gains the Round F
    # loopback listener, --alias and --api-key-file. Process start and the
    # HTTP proof are replaced at Start-ODSNativeLlamaLegacyProcess.
    $env:LOCALAPPDATA = Join-Path $fixtureRoot 'LocalAppData'
    $null = Write-ODSNativeLlamaLegacyOptions -Runtime ([pscustomobject]@{ ReleaseTag = 'b9014'; ZipSha256 = ('c' * 64) }) `
        -Device ([pscustomobject]@{ Name = 'Vulkan0' })
    $script:EnvMap.LLAMA_SERVER_API_KEY = 'ab' * 32
    $script:Started = @()
    function Start-ODSNativeLlamaLegacyProcess {
        param($Launch, $Port, $ApiKey, $PidFile, $TimeoutSeconds)
        $script:Started += [pscustomobject]@{ Launch = $Launch; Port = $Port; ApiKey = $ApiKey; PidFile = $PidFile }
        return [pscustomobject]@{ ProcessId = 19001; Proof = [pscustomobject]@{ ModelId = $Launch.GgufFile; ContextLength = $Launch.ContextSize } }
    }
    function Get-ODSNativeReasoningArgs { param($Executable, $Mode, $FallbackFormat) return @('--reasoning', $Mode) }
    function Get-ODSNativeCheckpointIntervalArgs { param($Executable, $Value) return [pscustomobject]@{ Arguments = @(); Warning = '' } }
    Start-NativeInferenceServer
    $launch = $script:Started[-1].Launch
    $joined = ConvertTo-ODSNativeLlamaArgumentString $launch.Arguments
    Assert-True ($launch.ExecutablePath -eq $runtime -and -not $launch.Pinned) 'Native start used the bundled runtime instead of qualified executable'
    Assert-True ($joined.Contains('"' + $modelFile + '"')) 'Native model path with spaces was not quoted'
    Assert-True ($joined.Contains('"--spec-type" "draft-mtp"')) 'Native MTP args missing'
    Assert-True ($joined.Contains('"--ctx-size" "8192"')) 'Native persisted context missing'
    Assert-True ($joined.Contains('"--alias" "model.gguf"') -and $joined.Contains('"--host" "127.0.0.1"') -and $joined.Contains('"--no-webui"') -and
        $joined.Contains('"--api-key-file"') -and -not $joined.Contains('ab' * 32)) 'Native start lost the loopback listener, alias or key file, or put the key on argv'
    Assert-True ($script:Started[-1].Port -eq 18080 -and $script:Started[-1].ApiKey -eq ('ab' * 32) -and
        $script:Started[-1].PidFile -eq $script:INFERENCE_PID_FILE) 'Native start used the wrong port, key or PID record'
    Assert-True (-not $joined.Contains('stale-mtp')) 'A registered profile received the global .env MTP setting'
    $script:Registry.stores[0].profiles['model.gguf'].mtp = $false
    Write-FixtureRegistry
    Start-NativeInferenceServer
    Assert-True (-not (ConvertTo-ODSNativeLlamaArgumentString $script:Started[-1].Launch.Arguments).Contains('spec-type')) 'Stale global MTP was applied to a baseline model'
    $script:Registry.stores[0].profiles['model.gguf'].mtp = $true
    Write-FixtureRegistry

    # The default store runs the pinned runtime, verified against pin.json
    # before every start.
    $pinned = Join-Path $fixtureRoot 'published llama-server'
    $null = New-Item -ItemType Directory -Path $pinned -Force
    foreach ($name in @('llama-server.exe', 'ggml-vulkan.dll')) { [IO.File]::WriteAllText((Join-Path $pinned $name), "fixture $name; never execute") }
    $pinRecord = [ordered]@{ schemaVersion = 1; releaseTag = 'b9014'; asset = 'llama-b9014-bin-win-vulkan-x64.zip'; zipSha256 = ('c' * 64)
        zipSize = 1; source = 'fixture'; files = @(Get-ODSNativeLlamaFileManifest $pinned) }
    [IO.File]::WriteAllText((Join-Path $pinned 'pin.json'), ($pinRecord | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
    $script:LLAMA_SERVER_DIR = $pinned
    $script:LLAMA_SERVER_EXE = Join-Path $pinned 'llama-server.exe'
    [IO.File]::WriteAllText((Join-Path $InstallDir 'data/models/model.gguf'), 'fixture checkpoint')
    $script:EnvMap.ODS_ACTIVE_MODEL_STORE = 'default'
    Write-FixtureEnv
    Start-NativeInferenceServer
    $joined = ConvertTo-ODSNativeLlamaArgumentString $script:Started[-1].Launch.Arguments
    Assert-True ($script:Started[-1].Launch.Pinned -and $script:Started[-1].Launch.ExecutablePath -eq $script:LLAMA_SERVER_EXE) 'The default store did not use the pinned runtime'
    Assert-True ($joined.Contains('"--parallel" "1"') -and $joined.Contains('"--device" "Vulkan0"') -and $joined.Contains('"--metrics"') -and
        $joined.Contains('"--reasoning" "off"') -and $joined.Contains('"--spec-type" "stale-mtp"')) 'The pinned runtime lost the one-slot, device, metrics, reasoning or .env tuning arguments'
    $startsBefore = $script:Started.Count
    [IO.File]::AppendAllText((Join-Path $pinned 'ggml-vulkan.dll'), 'quarantined')
    Assert-Throws { Start-NativeInferenceServer } 'no longer matches its pinned SHA-256'
    Assert-True ($script:Started.Count -eq $startsBefore) 'A tampered runtime was started'
    $script:EnvMap.ODS_ACTIVE_MODEL_STORE = 'ssd'
    Write-FixtureEnv

    # A restart prepares and validates the whole launch (selection, options,
    # pin.json, key, arguments) before it stops the running model; the
    # behaviour is covered by contracts/test-windows-native-llama-legacy.ps1.
    $restart = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Restart-ODSNativeLlamaServer' }, $true).Extent.Text
    $planAt = $restart.IndexOf('Get-ODSNativeLlamaStartPlan')
    $stopAt = $restart.IndexOf('Stop-ODSNativeLlamaLegacyProcess')
    Assert-True ($planAt -ge 0 -and $stopAt -gt $planAt) 'native-llm-restart stops the running model before validating the new launch'
    $plan = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-ODSNativeLlamaStartPlan' }, $true).Extent.Text
    $planChecks = @('Get-ODSNativeModelSelection -VerifyArtifacts', 'Read-ODSNativeLlamaLegacyOptions', 'Test-ODSNativeLlamaInstall',
        'Sync-ODSNativeLlamaLegacyApiKey', 'New-ODSNativeLlamaLegacyLaunch')
    Assert-True (@($planChecks | Where-Object { -not $plan.Contains($_) }).Count -eq 0) 'the launch plan skips a check'

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
    $env:LOCALAPPDATA = $previousLocalAppData
    $resolved = [IO.Path]::GetFullPath($fixtureRoot)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if ($resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved).StartsWith('ods-model-stores-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}
