$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$phasePath = Join-Path $root "installers\windows\phases\04-requirements.ps1"
$installerPath = Join-Path $root "installers\windows\install-windows.ps1"
$cliPath = Join-Path $root "installers\windows\ods.ps1"
$reportPath = Join-Path $root "installers\windows\lib\install-report.ps1"
. (Join-Path $root "installers\windows\lib\llm-endpoint.ps1")
. (Join-Path $root "installers\windows\lib\detection.ps1")
. (Join-Path $root "installers\windows\lib\env-generator.ps1")
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $phasePath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Phase 04 failed to parse: $($errors[0].Message)"
}

$phaseText = Get-Content -LiteralPath $phasePath -Raw
if ($phaseText -notmatch [regex]::Escape('$env:WEBUI_PORT = "9090"')) {
    throw "Phase 04 does not show valid PowerShell syntax for WEBUI_PORT overrides"
}

foreach ($name in @(
    "Resolve-WindowsLlmPreflightPort",
    "Test-WindowsPortInUse",
    "Test-WindowsODSLemonadeOwnsPort"
)) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    if (-not $functionAst) { throw "Function not found: $name" }
    . ([scriptblock]::Create($functionAst.Extent.Text))
}

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

function Write-AIWarn {
    param([string]$Message)
}

$portMap = @{ WEBUI_PORT = "9090"; DASHBOARD_PORT = "3101" }
Assert-Equal (Get-WindowsODSEnvPort -EnvMap $portMap -Name "WEBUI_PORT" -DefaultPort 3000) `
    9090 "Persisted runtime WebUI port"
Assert-Equal (Get-WindowsODSEnvPort -EnvMap $portMap -Name "DASHBOARD_PORT" -DefaultPort 3001) `
    3101 "Persisted runtime dashboard port"
$portMap.WEBUI_PORT = ""
Assert-Equal (Get-WindowsODSEnvPort -EnvMap $portMap -Name "WEBUI_PORT" -DefaultPort 3000) `
    3000 "Empty runtime WebUI port"
$portMap.WEBUI_PORT = "70000"
Assert-Equal (Get-WindowsODSEnvPort -EnvMap $portMap -Name "WEBUI_PORT" -DefaultPort 3000) `
    3000 "Out-of-range runtime WebUI port"
$portMap.WEBUI_PORT = "not-a-port"
Assert-Equal (Get-WindowsODSEnvPort -EnvMap $portMap -Name "WEBUI_PORT" -DefaultPort 3000) `
    3000 "Invalid runtime WebUI port"

foreach ($consumerPath in @($installerPath, $cliPath, $reportPath)) {
    $consumerText = Get-Content -LiteralPath $consumerPath -Raw
    if ($consumerText -match 'Test-HttpEndpoint\s+-Url\s+"http://localhost:3000"' -or
        $consumerText -match '@\{\s*Name\s*=\s*"(?:Chat UI|Chat UI \(Open WebUI\))";\s*Url\s*=\s*"http://localhost:3000"') {
        throw "Windows runtime health consumer still hardcodes Open WebUI port 3000: $consumerPath"
    }
}

$savedAmdPort = $env:AMD_INFERENCE_PORT
$savedOllamaPort = $env:OLLAMA_PORT
$savedLlamaPort = $env:LLAMA_SERVER_PORT
$savedWebuiPort = $env:WEBUI_PORT
try {
    Remove-Item Env:AMD_INFERENCE_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:OLLAMA_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:LLAMA_SERVER_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:WEBUI_PORT -ErrorAction SilentlyContinue

    Assert-Equal (Resolve-WindowsODSPort -Name "WEBUI_PORT" -DefaultPort 3000) `
        3000 "WebUI default"

    $env:WEBUI_PORT = "9090"
    Assert-Equal (Resolve-WindowsODSPort -Name "WEBUI_PORT" -DefaultPort 3000) `
        9090 "WebUI process override"
    Remove-Item Env:WEBUI_PORT

    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd") 8080 "AMD default"
    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd" -LemonadeDefaultPort 18081) `
        18081 "AMD contract default"
    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "nvidia") 11434 "Docker default"
    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd" -CloudMode) 0 "Cloud mode"

    $env:AMD_INFERENCE_PORT = "18080"
    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd") 18080 "AMD override"

    $env:AMD_INFERENCE_PORT = "not-a-port"
    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd") 8080 "Invalid AMD override"

    $env:OLLAMA_PORT = "21434"
    $env:LLAMA_SERVER_PORT = "31434"
    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "nvidia") 21434 "OLLAMA_PORT precedence"

    Remove-Item Env:OLLAMA_PORT
    Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "none") 31434 "LLAMA_SERVER_PORT fallback"

    Remove-Item Env:AMD_INFERENCE_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:LLAMA_SERVER_PORT -ErrorAction SilentlyContinue

    $generatedDir = Join-Path ([IO.Path]::GetTempPath()) "ods-webui-port-env-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $generatedDir | Out-Null
    try {
        $tierConfig = @{
            TierName = "Windows port contract"
            LlmModel = "test-model"
            GgufFile = "test.gguf"
            MaxContext = 4096
        }

        $env:WEBUI_PORT = "9090"
        New-ODSEnv -InstallDir $generatedDir -TierConfig $tierConfig `
            -Tier "3" -GpuBackend "nvidia" | Out-Null
        $generatedEnv = Get-Content -LiteralPath (Join-Path $generatedDir ".env") -Raw
        if ($generatedEnv -notmatch "(?m)^WEBUI_PORT=9090\r?$") {
            throw "Clean Windows env generation did not persist WEBUI_PORT=9090"
        }

        Remove-Item Env:WEBUI_PORT
        New-ODSEnv -InstallDir $generatedDir -TierConfig $tierConfig `
            -Tier "3" -GpuBackend "nvidia" | Out-Null
        $generatedEnv = Get-Content -LiteralPath (Join-Path $generatedDir ".env") -Raw
        if ($generatedEnv -notmatch "(?m)^WEBUI_PORT=9090\r?$") {
            throw "Windows env regeneration did not preserve WEBUI_PORT=9090"
        }
    } finally {
        Remove-Item -LiteralPath $generatedDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item Env:WEBUI_PORT -ErrorAction SilentlyContinue
    }

    $defaultDir = Join-Path ([IO.Path]::GetTempPath()) "ods-webui-port-default-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $defaultDir | Out-Null
    try {
        New-ODSEnv -InstallDir $defaultDir -TierConfig $tierConfig `
            -Tier "3" -GpuBackend "nvidia" | Out-Null
        $defaultEnv = Get-Content -LiteralPath (Join-Path $defaultDir ".env") -Raw
        if ($defaultEnv -notmatch "(?m)^WEBUI_PORT=3000\r?$") {
            throw "Default Windows env generation no longer writes WEBUI_PORT=3000"
        }
    } finally {
        Remove-Item -LiteralPath $defaultDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $installDir = Join-Path ([IO.Path]::GetTempPath()) "ods-port-preflight-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $installDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $installDir ".env") -Value @(
            "AMD_INFERENCE_PORT=19080",
            "OLLAMA_PORT=22434",
            "WEBUI_PORT=3100"
        )
        Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd" -InstallDir $installDir) `
            19080 "Persisted AMD port"
        Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "nvidia" -InstallDir $installDir) `
            22434 "Persisted Docker port"
        Assert-Equal (Resolve-WindowsODSPort -Name "WEBUI_PORT" -DefaultPort 3000 -InstallDir $installDir) `
            3100 "Persisted WebUI port"

        $env:WEBUI_PORT = "9090"
        Assert-Equal (Resolve-WindowsODSPort -Name "WEBUI_PORT" -DefaultPort 3000 -InstallDir $installDir) `
            9090 "WebUI process override wins over persisted port"

        $env:WEBUI_PORT = "not-a-port"
        Assert-Equal (Resolve-WindowsODSPort -Name "WEBUI_PORT" -DefaultPort 3000 -InstallDir $installDir) `
            3100 "Invalid WebUI override falls back to persisted port"
        Remove-Item Env:WEBUI_PORT

        $env:AMD_INFERENCE_PORT = "29080"
        Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd" -InstallDir $installDir) `
            29080 "Process override wins over persisted AMD port"
    } finally {
        Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback,
            0
        )
        $listener.Start()
        try {
            $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
            $result = Test-WindowsPortInUse -Port $port
            Assert-Equal $result.InUse $true "Live listener detection"
            if ([int]$result.ProcessId -le 0) {
                throw "Live listener detection did not return an owning PID"
            }
        } finally {
            $listener.Stop()
        }
    }

    $managedProcesses = @(
        [pscustomobject]@{ ProcessId = 4101; Name = "lemonade-server.exe" }
    )
    Assert-Equal (Test-WindowsODSLemonadeOwnsPort `
        -PortResult @{ InUse = $true; ProcessId = 4101 } `
        -LemonadeProcesses $managedProcesses) $true "Managed Lemonade listener"
    Assert-Equal (Test-WindowsODSLemonadeOwnsPort `
        -PortResult @{ InUse = $true; ProcessId = 4102 } `
        -LemonadeProcesses $managedProcesses) $false "Foreign listener"
    Assert-Equal (Test-WindowsODSLemonadeOwnsPort `
        -PortResult @{ InUse = $false; ProcessId = 0 } `
        -LemonadeProcesses $managedProcesses) $false "Free port"
} finally {
    if ($null -eq $savedAmdPort) { Remove-Item Env:AMD_INFERENCE_PORT -ErrorAction SilentlyContinue } else { $env:AMD_INFERENCE_PORT = $savedAmdPort }
    if ($null -eq $savedOllamaPort) { Remove-Item Env:OLLAMA_PORT -ErrorAction SilentlyContinue } else { $env:OLLAMA_PORT = $savedOllamaPort }
    if ($null -eq $savedLlamaPort) { Remove-Item Env:LLAMA_SERVER_PORT -ErrorAction SilentlyContinue } else { $env:LLAMA_SERVER_PORT = $savedLlamaPort }
    if ($null -eq $savedWebuiPort) { Remove-Item Env:WEBUI_PORT -ErrorAction SilentlyContinue } else { $env:WEBUI_PORT = $savedWebuiPort }
}

Write-Host "[PASS] Windows service port preflight and env generation"
$global:LASTEXITCODE = 0
exit 0
