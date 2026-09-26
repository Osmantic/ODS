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
if ($phaseText -match 'Stop-WindowsODSLemonadePortConflicts|Stop-Process\s+-Id') {
    throw "Windows preflight must not stop an unrelated native Lemonade process"
}
if ($phaseText -notmatch [regex]::Escape('if ($NonInteractive -and -not $Force -and -not $DryRun)')) {
    throw "Non-interactive Windows preflight must reject occupied selected ports"
}

foreach ($name in @(
    "Resolve-WindowsLlmPreflightPort",
    "Test-WindowsPortInUse",
    "Test-WindowsODSLemonadeOwnsPort",
    "Get-WindowsODSSelectedPortConflicts",
    "Assert-WindowsODSSelectedPortAvailability"
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
        if ($defaultEnv -notmatch "(?m)^WEBUI_AUTH=false\r?$") {
            throw "Loopback Windows installs must open Open WebUI without a login"
        }
    } finally {
        Remove-Item -LiteralPath $defaultDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $lanDir = Join-Path ([IO.Path]::GetTempPath()) "ods-webui-auth-lan-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $lanDir | Out-Null
    try {
        New-ODSEnv -InstallDir $lanDir -TierConfig $tierConfig `
            -Tier "3" -GpuBackend "nvidia" -EnableLan $true | Out-Null
        $lanEnv = Get-Content -LiteralPath (Join-Path $lanDir ".env") -Raw
        if ($lanEnv -notmatch "(?m)^BIND_ADDRESS=0\.0\.0\.0\r?$" -or
            $lanEnv -notmatch "(?m)^WEBUI_AUTH=true\r?$") {
            throw "LAN-bound Windows installs must keep Open WebUI authentication enabled"
        }
    } finally {
        Remove-Item -LiteralPath $lanDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $lanTransitionDir = Join-Path ([IO.Path]::GetTempPath()) "ods-webui-auth-lan-transition-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $lanTransitionDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $lanTransitionDir ".env") -Value @(
            "BIND_ADDRESS=127.0.0.1",
            "WEBUI_AUTH=false"
        )
        New-ODSEnv -InstallDir $lanTransitionDir -TierConfig $tierConfig `
            -Tier "3" -GpuBackend "nvidia" -EnableLan $true | Out-Null
        $lanTransitionEnv = Get-Content -LiteralPath (Join-Path $lanTransitionDir ".env") -Raw
        if ($lanTransitionEnv -notmatch "(?m)^BIND_ADDRESS=0\.0\.0\.0\r?$" -or
            $lanTransitionEnv -notmatch "(?m)^WEBUI_AUTH=true\r?$") {
            throw "A Windows -Lan rerun must replace stale loopback/authless settings"
        }
    } finally {
        Remove-Item -LiteralPath $lanTransitionDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $proxyDir = Join-Path ([IO.Path]::GetTempPath()) "ods-webui-auth-proxy-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $proxyDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $proxyDir ".env") -Value @(
            "BIND_ADDRESS=127.0.0.1",
            "WEBUI_AUTH=false"
        )
        New-ODSEnv -InstallDir $proxyDir -TierConfig $tierConfig `
            -Tier "3" -GpuBackend "nvidia" -EnableODSProxy $true | Out-Null
        $proxyEnv = Get-Content -LiteralPath (Join-Path $proxyDir ".env") -Raw
        if ($proxyEnv -notmatch "(?m)^BIND_ADDRESS=127\.0\.0\.1\r?$" -or
            $proxyEnv -notmatch "(?m)^WEBUI_AUTH=true\r?$") {
            throw "A Windows proxy rerun must replace stale authless settings"
        }
    } finally {
        Remove-Item -LiteralPath $proxyDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $explicitAuthDir = Join-Path ([IO.Path]::GetTempPath()) "ods-webui-auth-explicit-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $explicitAuthDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $explicitAuthDir ".env") -Value @(
            "BIND_ADDRESS=127.0.0.1",
            "WEBUI_AUTH=true"
        )
        New-ODSEnv -InstallDir $explicitAuthDir -TierConfig $tierConfig `
            -Tier "3" -GpuBackend "nvidia" | Out-Null
        $explicitAuthEnv = Get-Content -LiteralPath (Join-Path $explicitAuthDir ".env") -Raw
        if ($explicitAuthEnv -notmatch "(?m)^WEBUI_AUTH=true\r?$") {
            throw "Windows env regeneration must preserve an explicit WEBUI_AUTH choice"
        }
    } finally {
        Remove-Item -LiteralPath $explicitAuthDir -Recurse -Force -ErrorAction SilentlyContinue
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

# A separate Lemonade runtime may be active without occupying any selected ODS
# port. Preflight must leave it alone, including for dry-run/non-interactive use.
$script:mockListeners = @{
    9000 = @{ InUse = $true; ProcessId = 4242; ProcessName = "LemonadeServer" }
    13305 = @{ InUse = $true; ProcessId = 4242; ProcessName = "LemonadeServer" }
}
function Test-WindowsPortInUse {
    param([int]$Port)
    if ($script:mockListeners.ContainsKey($Port)) { return $script:mockListeners[$Port] }
    return @{ InUse = $false; ProcessId = 0; ProcessName = "" }
}
function Stop-Process { throw "Preflight must never stop a process" }
function Write-AI { param([string]$Message) }
function Write-AIError { param([string]$Message) }
function Write-AISuccess { param([string]$Message) }

$selectedPorts = [ordered]@{
    "Open WebUI (chat)" = 3000
    "Dashboard" = 3001
    "llama-server (LLM)" = 11434
}
$conflicts = @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $selectedPorts)
Assert-Equal $conflicts.Count 0 "Unrelated Lemonade ports are not selected-port conflicts"
Assert-Equal (Assert-WindowsODSSelectedPortAvailability -Conflicts $conflicts -NonInteractive -DryRun) `
    $true "Dry-run leaves unrelated Lemonade running"

$script:mockListeners[3000] = @{ InUse = $true; ProcessId = 4242; ProcessName = "LemonadeServer" }
$conflicts = @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $selectedPorts)
Assert-Equal $conflicts.Count 1 "Actual selected-port collision is detected"
if ($conflicts[0] -notmatch 'Port 3000 .*LemonadeServer.*PID 4242') {
    throw "Selected-port conflict did not identify the owner and port"
}
$aborted = $false
try {
    $null = Assert-WindowsODSSelectedPortAvailability -Conflicts $conflicts -NonInteractive
} catch {
    $aborted = ($_.Exception.Message -eq "ODS_INSTALL_ABORTED")
}
Assert-Equal $aborted $true "Non-interactive install fails closed on actual selected-port collision"
Assert-Equal (Assert-WindowsODSSelectedPortAvailability -Conflicts $conflicts -NonInteractive -DryRun) `
    $false "Dry-run reports actual selected-port collision without stopping the owner"

function Get-WindowsODSLemonadeProcesses {
    return @([pscustomobject]@{ ProcessId = 4242; Name = "LemonadeServer.exe" })
}
$script:mockListeners[8080] = @{ InUse = $true; ProcessId = 4242; ProcessName = "LemonadeServer" }
$amdPort = [ordered]@{ "Lemonade (LLM)" = 8080 }
Assert-Equal @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $amdPort -UsesNativeLemonade).Count `
    0 "Native AMD installation reuses its own Lemonade listener"
$script:mockListeners[8080] = @{ InUse = $true; ProcessId = 4343; ProcessName = "OtherServer" }
Assert-Equal @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $amdPort -UsesNativeLemonade).Count `
    1 "Native AMD installation rejects a foreign listener"

function Remove-VoiceSeedDir {
    param([string]$Path)
    $canonical = [IO.Path]::GetFullPath($Path)
    $prefix = Join-Path ([IO.Path]::GetTempPath()) "ods-voice-seed-"
    if (-not $canonical.StartsWith($prefix, [StringComparison]::Ordinal) -or
        (Split-Path -Leaf $canonical) -notmatch '^ods-voice-seed-[a-f0-9]{32}$' -or
        ((Get-Item -LiteralPath $canonical -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Unexpected test cleanup path"
    }
    Remove-Item -LiteralPath $canonical -Recurse -Force
}

$savedVoiceOverride = $env:WHISPER_PORT
try {
    Remove-Item Env:WHISPER_PORT -ErrorAction SilentlyContinue
# ── Voice (Whisper) port parity: phase 04 preflight vs env generator ──────
$script:voicePass = 0
$script:voiceCase = 0
function Assert-VoiceEqual {
    param($Actual, $Expected, [string]$Label)
    $script:voiceCase++
    if ("$Actual" -ceq "$Expected") {
        $script:voicePass++
        Write-Host "[PASS] $Label"
    } else {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

$script:voiceIfAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.IfStatementAst] -and
        $node.Clauses[0].Item1.Extent.Text -match 'enableVoice'
}, $true)
if (-not $script:voiceIfAst) { throw "Phase 04 enableVoice block not found" }
$voiceBlock = [scriptblock]::Create(($script:voiceIfAst.Clauses[0].Item2.Statements.Extent.Text -join "`n"))

function Get-PhaseVoicePort {
    param([string]$Backend, [bool]$Cloud = $false, [string]$InstallDir = "")
    $gpuInfo = @{ Backend = $Backend }
    $cloudMode = $Cloud
    $installDir = $InstallDir
    $_usesNativeLemonade = ($Backend -eq "amd" -and -not $Cloud)
    $_portsToCheck = [ordered]@{}
    . $voiceBlock
    return ,$_portsToCheck
}

# Fake conflict probe: reads $script:mockListeners only; no real sockets.
function Test-WindowsLemonadeWhisperPortConflict {
    param([int]$Port = 9000)
    $listener = $script:mockListeners[$Port]
    return [bool]($listener -and $listener.InUse -and
        $listener.ProcessName -match '(?i)lemonade')
}

function New-VoiceSeedDir {
    param([string]$Content = "")
    $dir = Join-Path ([IO.Path]::GetTempPath()) "ods-voice-seed-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $dir | Out-Null
    if ($Content) { Set-Content -LiteralPath (Join-Path $dir ".env") -Value $Content }
    return $dir
}

function Get-GeneratorWhisperPort {
    param(
        [string]$Backend,
        [string]$Runtime = "",
        [string]$Location = "",
        [string]$SeedEnv = "",
        [string]$ProcessPort = ""
    )
    $dir = New-VoiceSeedDir -Content $SeedEnv
    $savedWhisper = $env:WHISPER_PORT
    try {
        if ($ProcessPort) { $env:WHISPER_PORT = $ProcessPort }
        function Write-WindowsODSLemonadeLiteLlmConfig {
            param($InstallDir, $ModelId, $Port, $ApiKey)
            return (Join-Path $InstallDir "config\\litellm\\lemonade.yaml")
        }
        $null = New-ODSEnv -InstallDir $dir -TierConfig $tierConfig -Tier "3" `
            -GpuBackend $Backend -AmdInferenceRuntime $Runtime `
            -AmdInferenceLocation $Location
        $envText = Get-Content -LiteralPath (Join-Path $dir ".env") -Raw
        if ($envText -notmatch "(?m)^WHISPER_PORT=([^\r\n]+)\r?$") {
            throw "Generator .env missing WHISPER_PORT"
        }
        return $Matches[1].Trim()
    } finally {
        if ($null -eq $savedWhisper) {
            Remove-Item Env:WHISPER_PORT -ErrorAction SilentlyContinue
        } else { $env:WHISPER_PORT = $savedWhisper }
        Remove-VoiceSeedDir $dir
    }
}

function Assert-VoiceAborts {
    param([System.Collections.IDictionary]$PortsToCheck, [string]$Label)
    $conflicts = @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $PortsToCheck)
    if ($conflicts.Count -eq 0) { throw "$Label expected a selected-port conflict" }
    $aborted = $false
    try {
        $null = Assert-WindowsODSSelectedPortAvailability -Conflicts $conflicts -NonInteractive
    } catch {
        $aborted = ($_.Exception.Message -eq "ODS_INSTALL_ABORTED")
    }
    Assert-VoiceEqual $aborted $true $Label
}

# 1. NVIDIA + foreign Lemonade on 9000 -> 9100 (old phase 04 fails here)
$script:mockListeners[9000] = @{ InUse = $true; ProcessId = 4242; ProcessName = "LemonadeServer" }
$ports = Get-PhaseVoicePort -Backend "nvidia"
Assert-VoiceEqual $ports["Whisper (STT)"] 9100 "phase nvidia foreign lemonade 9000 -> 9100"
Assert-VoiceEqual (Get-GeneratorWhisperPort -Backend "nvidia") "9100" `
    "generator nvidia foreign lemonade 9000 -> 9100"

Assert-VoiceEqual @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $ports).Count 0 "selected 9100 is free despite foreign 9000"

# 2. Persisted 9100 with a foreign listener still aborts non-interactive installs
$script:mockListeners[9100] = @{ InUse = $true; ProcessId = 4343; ProcessName = "OtherServer" }
$seedDir = New-VoiceSeedDir -Content "WHISPER_PORT=9100"
try {
    $ports = Get-PhaseVoicePort -Backend "nvidia" -InstallDir $seedDir
    Assert-VoiceAborts -PortsToCheck $ports "persisted 9100 foreign listener aborts"
} finally {
    Remove-VoiceSeedDir $seedDir
}

# 3. Non-Lemonade listener on default 9000 is rejected
$script:mockListeners[9000] = @{ InUse = $true; ProcessId = 4343; ProcessName = "nginx" }
$ports = Get-PhaseVoicePort -Backend "nvidia"
Assert-VoiceAborts -PortsToCheck $ports "non-lemonade 9000 listener aborts"

# 4. No 9000 listener -> default 9000 everywhere
$script:mockListeners.Remove(9000)
$ports = Get-PhaseVoicePort -Backend "nvidia"
Assert-VoiceEqual $ports["Whisper (STT)"] 9000 "phase free 9000 default"
Assert-VoiceEqual (Get-GeneratorWhisperPort -Backend "nvidia") "9000" `
    "generator free 9000 default"

# 5. Managed AMD (lemonade/host) default -> 9100
$ports = Get-PhaseVoicePort -Backend "amd"
Assert-VoiceEqual $ports["Whisper (STT)"] 9100 "phase managed amd default 9100"
Assert-VoiceEqual (Get-GeneratorWhisperPort -Backend "amd" -Runtime "lemonade" -Location "host") "9100" `
    "generator managed amd default 9100"

# 6. Persisted custom 9182 is preserved
$seedDir = New-VoiceSeedDir -Content "WHISPER_PORT=9182"
try {
    $ports = Get-PhaseVoicePort -Backend "nvidia" -InstallDir $seedDir
    Assert-VoiceEqual $ports["Whisper (STT)"] 9182 "phase persisted 9182 preserved"
    Assert-VoiceEqual (Get-GeneratorWhisperPort -Backend "nvidia" -SeedEnv "WHISPER_PORT=9182") "9182" `
        "generator persisted 9182 preserved"

    # 7. Process override 9282 beats persisted 9182 (old generator fails here)
    $env:WHISPER_PORT = "9282"
    $ports = Get-PhaseVoicePort -Backend "nvidia" -InstallDir $seedDir
    Assert-VoiceEqual $ports["Whisper (STT)"] 9282 "phase process 9282 overrides persisted 9182"
    Assert-VoiceEqual (Get-GeneratorWhisperPort -Backend "nvidia" `
        -SeedEnv "WHISPER_PORT=9182" -ProcessPort "9282") "9282" `
        "generator process 9282 overrides persisted 9182"
    Remove-Item Env:WHISPER_PORT -ErrorAction SilentlyContinue
} finally {
    Remove-VoiceSeedDir $seedDir
}

# 8. Cached 9000 + Lemonade listener -> 9100
$script:mockListeners[9000] = @{ InUse = $true; ProcessId = 4242; ProcessName = "LemonadeServer" }
$ports = Get-PhaseVoicePort -Backend "nvidia"
Assert-VoiceEqual $ports["Whisper (STT)"] 9100 "phase cached 9000 lemonade -> 9100"
Assert-VoiceEqual (Get-GeneratorWhisperPort -Backend "nvidia" -SeedEnv "WHISPER_PORT=9000") "9100" `
    "generator cached 9000 lemonade -> 9100"

Write-Host ("[PASS] Voice port parity: {0}/{1} cases" -f $script:voicePass, $script:voiceCase)
if ($script:voicePass -ne $script:voiceCase) { $global:LASTEXITCODE = 1; exit 1 }

} finally {
    if ($null -eq $savedVoiceOverride) { Remove-Item Env:WHISPER_PORT -ErrorAction SilentlyContinue } else { $env:WHISPER_PORT = $savedVoiceOverride }
}

Write-Host "[PASS] Windows service port preflight and env generation"
$global:LASTEXITCODE = 0
exit 0
