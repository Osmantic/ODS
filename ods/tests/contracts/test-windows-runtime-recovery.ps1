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
    $script:NATIVE_LLM_PORT = 8080

    $nativeKey = "ab" * 32
    foreach ($mode in @("windows-native-llama-server", "windows-llama-server-fallback")) {
        $endpoint = Get-WindowsLocalLlmEndpoint -GpuBackend "amd" -NativeBackend "llama-server" -EnvMap @{
            GPU_BACKEND = "amd"
            LLM_BACKEND = "llama-server"
            AMD_INFERENCE_RUNTIME = "llama-server"
            AMD_INFERENCE_LOCATION = "host"
            AMD_INFERENCE_RUNTIME_MODE = $mode
            AMD_INFERENCE_PORT = "18080"
            LLAMA_SERVER_API_KEY = $nativeKey
        }
        if ($endpoint.Port -ne "18080" -or
            $endpoint.HealthUrl -ne "http://localhost:18080/health" -or
            $endpoint.ChatCompletionsUrl -ne "http://localhost:18080/v1/chat/completions" -or
            $endpoint.ApiKey -ne $nativeKey) {
            throw "Native llama-server endpoint ($mode) ignored AMD_INFERENCE_PORT or LLAMA_SERVER_API_KEY"
        }
    }
    $invalidPortEndpoint = Get-WindowsLocalLlmEndpoint `
        -GpuBackend "amd" -NativeBackend "llama-server" -EnvMap @{
            GPU_BACKEND = "amd"
            LLM_BACKEND = "llama-server"
            AMD_INFERENCE_RUNTIME = "llama-server"
            AMD_INFERENCE_LOCATION = "host"
            AMD_INFERENCE_RUNTIME_MODE = "windows-native-llama-server"
            AMD_INFERENCE_PORT = "70000"
        }
    if ($invalidPortEndpoint.Port -ne "8080" -or
        $invalidPortEndpoint.HealthUrl -ne "http://localhost:8080/health") {
        throw "Invalid native port did not fall back to the backend default"
    }

    # Windows AMD: phase 06 renders the native llama-server values directly.
    $generatedInstall = Join-Path $testRoot "generated-install"
    $tier = @{
        TierName = "Test"
        LlmModel = "test-model"
        GgufFile = "test-model.gguf"
        MaxContext = 4096
    }
    $null = New-ODSEnv -InstallDir $generatedInstall -TierConfig $tier -Tier "test" `
        -GpuBackend "amd" -AmdInferenceRuntime "llama-server" -AmdInferenceBackend "vulkan" `
        -AmdInferenceLocation "host" -AmdInferencePort "18080" -AmdInferenceSupportedBackends "vulkan" `
        -AmdInferenceRuntimeMode "windows-native-llama-server" -AmdInferenceManaged "true"
    $generatedEnv = Get-Content -LiteralPath (Join-Path $generatedInstall ".env") -Raw
    foreach ($assignment in @(
        "ODS_MODE=local",
        "LLM_BACKEND=llama-server",
        "LLM_API_BASE_PATH=/v1",
        "LLM_API_URL=http://host.docker.internal:18080",
        "AMD_INFERENCE_RUNTIME=llama-server",
        "AMD_INFERENCE_BACKEND=vulkan",
        "AMD_INFERENCE_LOCATION=host",
        "AMD_INFERENCE_PORT=18080",
        "AMD_INFERENCE_RUNTIME_MODE=windows-native-llama-server",
        "ODS_HOST_LLM_TRANSPORT=direct",
        "NATIVE_LLM_BASE_URL=http://127.0.0.1:18080",
        "NATIVE_LLM_CONTAINER_BASE_URL=http://host.docker.internal:18080"
    )) {
        if ($generatedEnv -notmatch ("(?m)^" + [regex]::Escape($assignment) + "\r?$")) {
            throw "Generated Windows AMD env missed: $assignment"
        }
    }
    $keyMatch = [regex]::Match($generatedEnv, "(?m)^LLAMA_SERVER_API_KEY=([0-9a-f]{64})\r?$")
    if (-not $keyMatch.Success) { throw "Windows AMD env has no 64-hex LLAMA_SERVER_API_KEY" }
    if ($generatedEnv -match "(?m)^(LEMONADE_[A-Z_]*|LITELLM_LEMONADE_API_KEY)=") {
        throw "Windows AMD env still writes retired Lemonade keys"
    }
    $localConfig = Get-Content -LiteralPath (Join-Path $generatedInstall "config/litellm/local.yaml") -Raw
    if (-not $localConfig.Contains("api_base: http://host.docker.internal:18080/v1") -or
        -not $localConfig.Contains("api_key: os.environ/LLAMA_SERVER_API_KEY") -or
        $localConfig.Contains($keyMatch.Groups[1].Value)) {
        throw "LiteLLM local config must reach the native port and read the key from its environment"
    }
    if (Test-Path -LiteralPath (Join-Path $generatedInstall "config/litellm/lemonade.yaml")) {
        throw "Windows AMD still renders lemonade.yaml"
    }
    $routerConfig = Get-Content -LiteralPath (Join-Path $generatedInstall "config/model-router/endpoints.json") -Raw | ConvertFrom-Json
    if (@($routerConfig.endpoints).Count -ne 1 -or $routerConfig.endpoints[0].id -ne "llama-server-default" -or
        $routerConfig.endpoints[0].baseUrl -ne "http://host.docker.internal:18080") {
        throw "Model router must name the native llama-server origin (the router appends /v1)"
    }
    $null = New-ODSEnv -InstallDir $generatedInstall -TierConfig $tier -Tier "test" `
        -GpuBackend "amd" -AmdInferenceRuntime "llama-server" -AmdInferenceBackend "vulkan" `
        -AmdInferenceLocation "host" -AmdInferencePort "18080" -AmdInferenceSupportedBackends "vulkan" `
        -AmdInferenceRuntimeMode "windows-native-llama-server" -AmdInferenceManaged "true"
    if ((Get-Content -LiteralPath (Join-Path $generatedInstall ".env") -Raw) -notmatch ("(?m)^LLAMA_SERVER_API_KEY=" + $keyMatch.Groups[1].Value + "\r?$")) {
        throw "A rerun rotated LLAMA_SERVER_API_KEY; LiteLLM, the router and the host agent would lose access"
    }

    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path $windowsCli), [ref]$tokens, [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) { throw $parseErrors[0] }

    $functionNames = @(
        "Write-ODSUtf8NoBomFile",
        "Sync-ODSNativeInferenceConfig",
        "Test-ODSNativeProcessExecutable",
        "Get-ODSNativeInferencePortOwnerProcessId",
        "Test-ODSNativeInferenceHealth",
        "Get-NativeInferenceStatus",
        "Stop-NativeInferenceServer"
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

    # The persisted AMD_INFERENCE_PORT drives every native probe.
    function Read-ODSEnv { return @{ AMD_INFERENCE_PORT = "18080" } }
    Sync-ODSNativeInferenceConfig
    if ($script:NATIVE_LLM_PORT -ne 18080) { throw "ods.ps1 ignored the persisted AMD_INFERENCE_PORT" }
    function Read-ODSEnv { return @{ AMD_INFERENCE_PORT = "not-a-port" } }
    Sync-ODSNativeInferenceConfig
    if ($script:NATIVE_LLM_PORT -ne 18080) { throw "An invalid AMD_INFERENCE_PORT replaced the configured port" }

    $script:INFERENCE_PID_FILE = Join-Path $testRoot "data/llama-server.pid"
    $script:LLAMA_SERVER_EXE = Join-Path $testRoot "llama-server.exe"
    $script:MockBackend = "llama-server"
    $script:MockHealth = $false
    $script:MockProcesses = @{}
    $script:MockListeners = @()
    $script:StoppedProcessIds = @()
    $script:LastHealthUrl = $null
    $global:InstallDir = $testRoot
    New-Item -ItemType Directory -Path (Split-Path $script:INFERENCE_PID_FILE) -Force | Out-Null

    function Sync-ODSNativeInferenceConfig { }
    function Get-NativeInferenceBackend { return $script:MockBackend }
    function Get-ODSConfiguredNativeExecutable { return $script:LLAMA_SERVER_EXE }
    function Get-ODSNativeModelSelection { return [pscustomobject]@{ profile = $null } }
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
        throw "native status must query single processes only"
    }
    function Get-ScheduledTask { throw "native status must not inspect scheduled tasks" }
    function Stop-ODSNativeProcessId {
        param([int]$ProcessId)
        $script:StoppedProcessIds += $ProcessId
        $script:MockProcesses.Remove($ProcessId)
    }
    function Get-Process { param($Id, $ErrorAction) if ($script:MockProcesses.ContainsKey([int]$Id)) { return [pscustomobject]@{ Id = $Id } } }
    function Write-AI { param([string]$Message) }
    function Write-AISuccess { param([string]$Message) }

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
    $script:MockBackend = "llama-server"
    $script:MockProcesses[550] = [pscustomobject]@{ ProcessId = 550; ExecutablePath = $script:LLAMA_SERVER_EXE; CommandLine = "" }
    $script:MockListeners = @([pscustomobject]@{ LocalPort = 18080; OwningProcess = 550 })
    $llama = Get-NativeInferenceStatus
    if (-not $llama.Running -or -not $llama.Recovered -or $llama.Pid -ne 550 -or
        $script:LastHealthUrl -ne "http://127.0.0.1:18080/health") {
        throw "A healthy llama-server on the configured native port was not reconciled"
    }
    if ((Get-Content -LiteralPath $script:INFERENCE_PID_FILE -Raw).Trim() -ne "550") {
        throw "The reconciled llama-server PID was not persisted"
    }
    Stop-NativeInferenceServer
    if (($script:StoppedProcessIds -join ",") -ne "550" -or (Test-Path -LiteralPath $script:INFERENCE_PID_FILE)) {
        throw "Stop did not stop the proven llama-server and clear its PID record"
    }

    # A reused PID or unrelated process on the configured port must never be
    # adopted or stopped by ODS.
    $script:StoppedProcessIds = @()
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
    Stop-NativeInferenceServer
    if ($script:StoppedProcessIds.Count -ne 0) {
        throw "Native stop stopped an unrelated process"
    }

    # A matching saved process remains running while its model loads (503).
    $script:MockHealth = $false
    $script:MockProcesses = @{
        440 = [pscustomobject]@{ ProcessId = 440; ExecutablePath = $script:LLAMA_SERVER_EXE; CommandLine = "" }
    }
    $script:MockListeners = @()
    Set-Content -LiteralPath $script:INFERENCE_PID_FILE -Value "440"
    $loading = Get-NativeInferenceStatus
    if (-not $loading.Running -or $loading.Healthy -or $loading.Pid -ne 440) {
        throw "Matching loading process was not preserved"
    }

    Write-Host "[PASS] Windows Compose plugin and native llama-server runtime recovery contracts"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
