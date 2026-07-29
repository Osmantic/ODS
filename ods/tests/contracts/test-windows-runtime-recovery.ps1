$ErrorActionPreference = "Stop"

$rootDir = Resolve-Path (Join-Path $PSScriptRoot "../..")
$composeLibrary = Join-Path $rootDir "installers/windows/lib/compose-diagnostics.ps1"
$envGeneratorLibrary = Join-Path $rootDir "installers/windows/lib/env-generator.ps1"
$llmEndpointLibrary = Join-Path $rootDir "installers/windows/lib/llm-endpoint.ps1"
$windowsCli = Join-Path $rootDir "installers/windows/ods.ps1"
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    "ods-windows-runtime-recovery-$([Guid]::NewGuid().ToString('N'))"

Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

try {
    . $composeLibrary

    $userDockerConfig = Join-Path $testRoot "user-docker"
    $defaultPluginDir = Join-Path $userDockerConfig "cli-plugins"
    $extraPluginDir = Join-Path $testRoot "extra-plugins"
    $installDir = Join-Path $testRoot "install"
    New-Item -ItemType Directory -Path $defaultPluginDir -Force | Out-Null
    New-Item -ItemType Directory -Path $extraPluginDir -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $installDir "data/docker-client-public") -Force | Out-Null

    @{
        auths = @{ "private.example" = @{ auth = "must-not-copy" } }
        credsStore = "desktop"
        currentContext = "desktop-linux"
        cliPluginsExtraDirs = @($extraPluginDir, $defaultPluginDir)
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $userDockerConfig "config.json")

    # Simulate an existing install created before plugin forwarding existed.
    '{"auths":{"stale.example":{}},"credsStore":"desktop"}' |
        Set-Content -LiteralPath (Join-Path $installDir "data/docker-client-public/config.json")

    $isolatedDir = Initialize-ODSComposeDockerClientConfig `
        -InstallDir $installDir -UserDockerConfigDir $userDockerConfig
    $isolatedConfigPath = Join-Path $isolatedDir "config.json"
    $isolatedConfig = Get-Content -LiteralPath $isolatedConfigPath -Raw | ConvertFrom-Json

    if (@($isolatedConfig.auths.PSObject.Properties).Count -ne 0) {
        throw "Install-scoped Docker config copied registry auth state"
    }
    foreach ($forbidden in @("credsStore", "credHelpers", "currentContext")) {
        if ($isolatedConfig.PSObject.Properties.Name -contains $forbidden) {
            throw "Install-scoped Docker config copied forbidden field: $forbidden"
        }
    }
    $expectedPluginDirs = @(
        (Resolve-Path -LiteralPath $defaultPluginDir).Path,
        (Resolve-Path -LiteralPath $extraPluginDir).Path
    ) | Sort-Object
    $actualPluginDirs = @($isolatedConfig.cliPluginsExtraDirs) | Sort-Object
    if (Compare-Object $expectedPluginDirs $actualPluginDirs) {
        throw "Compose plugin directories were not preserved: $($actualPluginDirs -join ', ')"
    }

    # A second run must be idempotent and keep the same credential-free shape.
    $null = Initialize-ODSComposeDockerClientConfig `
        -InstallDir $installDir -UserDockerConfigDir $userDockerConfig
    $secondConfig = Get-Content -LiteralPath $isolatedConfigPath -Raw | ConvertFrom-Json
    if (@($secondConfig.cliPluginsExtraDirs).Count -ne 2 -or
        @($secondConfig.auths.PSObject.Properties).Count -ne 0) {
        throw "Second Docker config generation was not idempotent"
    }

    # A failed staged write must leave the last valid config intact.
    $validConfigBeforeFailure = Get-Content -LiteralPath $isolatedConfigPath -Raw
    $blockedTempPath = "$isolatedConfigPath.$PID.tmp"
    New-Item -ItemType Directory -Path $blockedTempPath -Force | Out-Null
    $writeFailed = $false
    try {
        $null = Initialize-ODSComposeDockerClientConfig `
            -InstallDir $installDir -UserDockerConfigDir $userDockerConfig
    } catch {
        $writeFailed = $true
    } finally {
        Remove-Item -LiteralPath $blockedTempPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not $writeFailed -or
        (Get-Content -LiteralPath $isolatedConfigPath -Raw) -ne $validConfigBeforeFailure) {
        throw "Failed Docker config staging did not preserve the last valid config"
    }

    # Relative DOCKER_CONFIG must resolve against the caller's location before
    # Invoke-ODSDockerCompose changes into the install directory.
    $relativeConfigName = "relative-docker"
    $relativeConfigDir = Join-Path $testRoot $relativeConfigName
    $relativePluginDir = Join-Path $relativeConfigDir "cli-plugins"
    New-Item -ItemType Directory -Path $relativePluginDir -Force | Out-Null
    $previousLocation = Get-Location
    $hadDockerConfig = Test-Path Env:DOCKER_CONFIG
    $previousDockerConfig = $env:DOCKER_CONFIG
    try {
        Set-Location $testRoot
        $env:DOCKER_CONFIG = $relativeConfigName
        function global:docker { $global:LASTEXITCODE = 0 }
        $composeExit = Invoke-ODSDockerCompose -InstallDir $installDir `
            -ComposeFlags @("-f", "docker-compose.base.yml") -ComposeArgs @("config", "--quiet")
        if ($composeExit -ne 0) {
            throw "Mock Compose invocation failed for relative DOCKER_CONFIG"
        }
    } finally {
        Remove-Item Function:\global:docker -ErrorAction SilentlyContinue
        Set-Location $previousLocation
        if ($hadDockerConfig) {
            $env:DOCKER_CONFIG = $previousDockerConfig
        } else {
            Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
        }
    }
    $relativeGeneratedConfig = Get-Content -LiteralPath $isolatedConfigPath -Raw |
        ConvertFrom-Json
    if (@($relativeGeneratedConfig.cliPluginsExtraDirs).Count -ne 1 -or
        $relativeGeneratedConfig.cliPluginsExtraDirs[0] -ne
            (Resolve-Path -LiteralPath $relativePluginDir).Path) {
        throw "Relative DOCKER_CONFIG was resolved from the Compose working directory"
    }

    . $llmEndpointLibrary
    . $envGeneratorLibrary
    function Write-AIWarn { param([string]$Message) }
    function Get-LlamaCpuBudget {
        return @{ Limit = "4.0"; Reservation = "1.0"; Available = "4.0" }
    }
    $script:ODS_VERSION = "test"
    $script:LEMONADE_PORT = 8080
    $script:LEMONADE_HEALTH_URL = "http://127.0.0.1:8080/api/v1/health"

    $endpoint = Get-WindowsLocalLlmEndpoint -GpuBackend "amd" -NativeBackend "llama-server" -EnvMap @{
        GPU_BACKEND = "amd"
        LLM_BACKEND = "llama-server"
        AMD_INFERENCE_RUNTIME = "llama-server"
        AMD_INFERENCE_LOCATION = "host"
        AMD_INFERENCE_RUNTIME_MODE = "windows-llama-server-fallback"
        AMD_INFERENCE_PORT = "18080"
    }
    if ($endpoint.Port -ne "18080" -or
        $endpoint.HealthUrl -ne "http://localhost:18080/health" -or
        $endpoint.ChatCompletionsUrl -ne "http://localhost:18080/v1/chat/completions") {
        throw "Native llama-server endpoint ignored AMD_INFERENCE_PORT"
    }
    $invalidPortEndpoint = Get-WindowsLocalLlmEndpoint `
        -GpuBackend "amd" -NativeBackend "llama-server" -EnvMap @{
            GPU_BACKEND = "amd"
            LLM_BACKEND = "llama-server"
            AMD_INFERENCE_RUNTIME = "llama-server"
            AMD_INFERENCE_LOCATION = "host"
            AMD_INFERENCE_RUNTIME_MODE = "windows-llama-server-fallback"
            AMD_INFERENCE_PORT = "70000"
        }
    if ($invalidPortEndpoint.Port -ne "8080" -or
        $invalidPortEndpoint.HealthUrl -ne "http://localhost:8080/health") {
        throw "Invalid native port did not fall back to the backend default"
    }

    $generatedInstall = Join-Path $testRoot "generated-install"
    $tier = @{
        TierName = "Test"
        LlmModel = "test-model"
        GgufFile = "test-model.gguf"
        MaxContext = 4096
    }
    $null = New-ODSEnv -InstallDir $generatedInstall -TierConfig $tier -Tier "test" `
        -GpuBackend "amd" -AmdInferenceRuntime "lemonade" `
        -AmdInferenceLocation "host" -AmdInferencePort "18080"
    $generatedEnv = Get-Content -LiteralPath (Join-Path $generatedInstall ".env") -Raw
    foreach ($assignment in @(
        "LLM_API_URL=http://host.docker.internal:18080",
        "AMD_INFERENCE_PORT=18080"
    )) {
        if (-not $generatedEnv.Contains($assignment)) {
            throw "Generated Windows env missed custom native port assignment: $assignment"
        }
    }
    $lemonadeConfig = Get-Content -LiteralPath (Join-Path $generatedInstall "config/litellm/lemonade.yaml") -Raw
    if (-not $lemonadeConfig.Contains("api_base: http://host.docker.internal:18080/api/v1")) {
        throw "LiteLLM Lemonade config ignored AMD_INFERENCE_PORT"
    }
    $routerConfig = Get-Content -LiteralPath (Join-Path $generatedInstall "config/model-router/endpoints.json") -Raw
    if (-not $routerConfig.Contains("http://host.docker.internal:18080/api")) {
        throw "Model router config ignored AMD_INFERENCE_PORT"
    }

    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path $windowsCli), [ref]$tokens, [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) { throw $parseErrors[0] }

    $functionNames = @(
        "Write-ODSUtf8NoBomFile",
        "Test-ODSNativeProcessExecutable",
        "Get-ODSNativeInferencePortOwnerProcessId",
        "Get-ODSManagedLemonadeTaskProcessId",
        "Test-ODSNativeInferenceHealth",
        "Get-NativeInferenceStatus",
        "Stop-ODSLemonadeRuntime"
    )
    foreach ($functionName in $functionNames) {
        $functionAst = $ast.Find(
            {
                param($node)
                $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                    $node.Name -eq $functionName
            },
            $true
        )
        if (-not $functionAst) { throw "Function not found: $functionName" }
        . ([scriptblock]::Create($functionAst.Extent.Text))
    }

    $invokeAgentAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq "Invoke-Agent"
        },
        $true
    )
    if (-not $invokeAgentAst -or
        $invokeAgentAst.Extent.Text -notmatch '\bWrite-ODSUtf8NoBomFile\b' -or
        $invokeAgentAst.Extent.Text -match '\bWrite-Utf8NoBom\b') {
        throw "Host Agent fallback does not use its self-contained UTF-8 writer"
    }

    $launcherPath = Join-Path $testRoot "startup/ods-host-agent.vbs"
    $launcherContent = "' ODS Host Agent`r`nWScript.Echo `"ready`"`r`n"
    Write-ODSUtf8NoBomFile -Path $launcherPath -Content $launcherContent
    $launcherBytes = [System.IO.File]::ReadAllBytes($launcherPath)
    if ($launcherBytes.Length -lt 3 -or
        ($launcherBytes[0] -eq 0xEF -and $launcherBytes[1] -eq 0xBB -and
            $launcherBytes[2] -eq 0xBF)) {
        throw "Host Agent startup launcher was written with a UTF-8 BOM"
    }
    if ([System.IO.File]::ReadAllText($launcherPath) -ne $launcherContent) {
        throw "Host Agent startup launcher content did not round-trip"
    }

    $script:INFERENCE_PID_FILE = Join-Path $testRoot "data/llama-server.pid"
    $script:LEMONADE_EXE = Join-Path $testRoot "LemonadeServer.exe"
    $script:LLAMA_SERVER_EXE = Join-Path $testRoot "llama-server.exe"
    $script:LEMONADE_PORT = 18080
    $script:LEMONADE_HEALTH_URL = "http://127.0.0.1:18080/api/v1/health"
    $script:LEMONADE_TASK_NAME = "ODSLemonadeRuntime"
    $script:MockBackend = "lemonade"
    $script:MockHealth = $false
    $script:MockProcesses = @{}
    $script:MockListeners = @()
    $script:UnfilteredCimQueries = 0
    $script:StoppedProcessIds = @()
    $script:LastHealthUrl = $null
    $script:MockTaskRunning = $false
    $global:InstallDir = $testRoot
    New-Item -ItemType Directory -Path (Split-Path $script:INFERENCE_PID_FILE) -Force | Out-Null

    function Sync-ODSNativeInferenceConfig { }
    function Get-NativeInferenceBackend { return $script:MockBackend }
    function Invoke-WebRequest {
        param($Uri, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
        $script:LastHealthUrl = [string]$Uri
        if (-not $script:MockHealth) { throw "mock endpoint unavailable" }
        return [pscustomobject]@{ StatusCode = 200 }
    }
    function Get-NetTCPConnection {
        param($LocalPort, $State, $ErrorAction)
        return @($script:MockListeners | Where-Object { $_.LocalPort -eq $LocalPort })
    }
    function Get-CimInstance {
        param($ClassName, $Filter, $ErrorAction)
        if ($Filter -match 'ProcessId\s*=\s*(\d+)') {
            $id = [int]$Matches[1]
            return $script:MockProcesses[$id]
        }
        $script:UnfilteredCimQueries += 1
        return @($script:MockProcesses.Values)
    }
    function Stop-ScheduledTask { param($TaskName, $ErrorAction) }
    function Unregister-ScheduledTask { param($TaskName, [switch]$Confirm, $ErrorAction) }
    function Get-ScheduledTask {
        param($TaskName, $ErrorAction)
        if (-not $script:MockTaskRunning -or $TaskName -ne "ODSLemonadeRuntime") {
            throw "mock task unavailable"
        }
        return [pscustomobject]@{ State = "Running" }
    }
    function Stop-ODSNativeProcessId {
        param([int]$ProcessId)
        $script:StoppedProcessIds += $ProcessId
    }

    # Disabled native inference must not probe or adopt host state.
    $script:MockBackend = "none"
    $script:MockHealth = $true
    $script:MockListeners = @([pscustomobject]@{ LocalPort = 18080; OwningProcess = 219 })
    $script:LastHealthUrl = $null
    $disabled = Get-NativeInferenceStatus
    if ($disabled.Backend -ne "none" -or $disabled.Running -or
        $null -ne $script:LastHealthUrl -or (Test-Path -LiteralPath $script:INFERENCE_PID_FILE)) {
        throw "Disabled native inference inspected or adopted host runtime state"
    }

    # Missing/stale state is repaired only from a healthy listener owned by
    # the exact configured executable.
    $script:MockBackend = "lemonade"
    $script:MockHealth = $true
    $script:MockProcesses[220] = [pscustomobject]@{
        ProcessId = 220
        ExecutablePath = $script:LEMONADE_EXE
        CommandLine = ""
    }
    $script:MockListeners = @([pscustomobject]@{ LocalPort = 18080; OwningProcess = 220 })
    $recovered = Get-NativeInferenceStatus
    if (-not $recovered.Running -or -not $recovered.Healthy -or
        -not $recovered.Recovered -or $recovered.Pid -ne 220) {
        throw "Healthy Lemonade listener was not reconciled"
    }
    $persistedPid = (Get-Content -LiteralPath $script:INFERENCE_PID_FILE -Raw).Trim()
    if ($persistedPid -ne "220") {
        throw "Reconciled Lemonade PID was not persisted (actual='$persistedPid')"
    }

    # Lemonade versions may put the listener in a child process. The exact
    # managed task plus one matching parent executable is still recoverable.
    Remove-Item -LiteralPath $script:INFERENCE_PID_FILE -Force
    $script:MockTaskRunning = $true
    $script:MockProcesses = @{
        221 = [pscustomobject]@{
            ProcessId = 221
            ParentProcessId = 1
            ExecutablePath = $script:LEMONADE_EXE
            CommandLine = ""
        }
        222 = [pscustomobject]@{
            ProcessId = 222
            ParentProcessId = 221
            ExecutablePath = (Join-Path $testRoot "lemonade-child.exe")
            CommandLine = "child --port 18080"
        }
    }
    $script:MockListeners = @([pscustomobject]@{ LocalPort = 18080; OwningProcess = 222 })
    $taskRecovered = Get-NativeInferenceStatus
    if (-not $taskRecovered.Recovered -or $taskRecovered.Pid -ne 221) {
        throw "Running managed Lemonade task was not reconciled through its parent executable"
    }
    if ($script:UnfilteredCimQueries -ne 1) {
        throw "Managed task reconciliation repeated the full process query"
    }
    $script:MockTaskRunning = $false

    # A running task and a healthy unrelated listener are not sufficient:
    # the listener must belong to the exact Lemonade process tree.
    Remove-Item -LiteralPath $script:INFERENCE_PID_FILE -Force
    $script:MockTaskRunning = $true
    $script:MockProcesses = @{
        223 = [pscustomobject]@{
            ProcessId = 223
            ParentProcessId = 1
            ExecutablePath = $script:LEMONADE_EXE
            CommandLine = ""
        }
        224 = [pscustomobject]@{
            ProcessId = 224
            ParentProcessId = 1
            ExecutablePath = (Join-Path $testRoot "unrelated-health-server.exe")
            CommandLine = "unrelated --port 18080"
        }
    }
    $script:MockListeners = @([pscustomobject]@{ LocalPort = 18080; OwningProcess = 224 })
    $unrelatedHealthy = Get-NativeInferenceStatus
    if ($unrelatedHealthy.Running -or (Test-Path -LiteralPath $script:INFERENCE_PID_FILE)) {
        throw "Managed task recovery adopted Lemonade without proving listener ancestry"
    }
    $script:MockTaskRunning = $false

    # A reused PID or unrelated process on the configured port must never be
    # adopted or stopped by ODS.
    Set-Content -LiteralPath $script:INFERENCE_PID_FILE -Value "330"
    $script:MockProcesses = @{
        330 = [pscustomobject]@{
            ProcessId = 330
            ExecutablePath = (Join-Path ([System.IO.Path]::GetTempPath()) "unrelated-runtime.exe")
            CommandLine = "unrelated --port 18080"
        }
    }
    $script:MockListeners = @([pscustomobject]@{ LocalPort = 18080; OwningProcess = 330 })
    $unrelated = Get-NativeInferenceStatus
    if ($unrelated.Running -or (Test-Path -LiteralPath $script:INFERENCE_PID_FILE)) {
        throw "Unrelated listener was adopted as the native inference runtime"
    }
    Stop-ODSLemonadeRuntime
    if ($script:StoppedProcessIds.Count -ne 0) {
        throw "Lemonade cleanup stopped an unrelated process"
    }

    # A matching saved process remains running while its endpoint is loading.
    $script:MockHealth = $false
    $script:MockProcesses = @{
        440 = [pscustomobject]@{
            ProcessId = 440
            ExecutablePath = $script:LEMONADE_EXE
            CommandLine = ""
        }
    }
    $script:MockListeners = @()
    Set-Content -LiteralPath $script:INFERENCE_PID_FILE -Value "440"
    $loading = Get-NativeInferenceStatus
    if (-not $loading.Running -or $loading.Healthy -or $loading.Pid -ne 440) {
        throw "Matching loading process was not preserved"
    }

    # llama-server fallback uses the same configured native port contract.
    Remove-Item -LiteralPath $script:INFERENCE_PID_FILE -Force
    $script:MockBackend = "llama-server"
    $script:MockHealth = $true
    $script:MockProcesses = @{
        550 = [pscustomobject]@{
            ProcessId = 550
            ExecutablePath = $script:LLAMA_SERVER_EXE
            CommandLine = ""
        }
    }
    $script:MockListeners = @([pscustomobject]@{ LocalPort = 18080; OwningProcess = 550 })
    $llama = Get-NativeInferenceStatus
    if (-not $llama.Running -or $script:LastHealthUrl -ne "http://127.0.0.1:18080/health") {
        throw "llama-server recovery ignored the configured native port"
    }

    Write-Host "[PASS] Windows Compose plugin and native runtime recovery contracts"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
