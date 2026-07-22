$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$phasePath = Join-Path $root "installers\windows\phases\04-requirements.ps1"
. (Join-Path $root "installers\windows\lib\llm-endpoint.ps1")
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

$savedAmdPort = $env:AMD_INFERENCE_PORT
$savedOllamaPort = $env:OLLAMA_PORT
$savedLlamaPort = $env:LLAMA_SERVER_PORT
try {
    Remove-Item Env:AMD_INFERENCE_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:OLLAMA_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:LLAMA_SERVER_PORT -ErrorAction SilentlyContinue

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
    $installDir = Join-Path ([IO.Path]::GetTempPath()) "ods-port-preflight-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $installDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $installDir ".env") -Value @(
            "AMD_INFERENCE_PORT=19080",
            "OLLAMA_PORT=22434"
        )
        Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd" -InstallDir $installDir) `
            19080 "Persisted AMD port"
        Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "nvidia" -InstallDir $installDir) `
            22434 "Persisted Docker port"

        $env:AMD_INFERENCE_PORT = "29080"
        Assert-Equal (Resolve-WindowsLlmPreflightPort -GpuBackend "amd" -InstallDir $installDir) `
            29080 "Process override wins over persisted AMD port"
    } finally {
        Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
    }

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
}

Write-Host "[PASS] Windows backend-aware LLM port preflight"
