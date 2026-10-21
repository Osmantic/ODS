param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$EmptyProcessProbe,
    [switch]$CheckCompose
)
$ErrorActionPreference = 'Stop'
. (Join-Path $SourceRoot 'installers/windows/lib/llm-endpoint.ps1')
. (Join-Path $SourceRoot 'installers/windows/lib/detection.ps1')
. (Join-Path $SourceRoot 'installers/windows/lib/env-generator.ps1')
function Get-ODSDockerMemoryGB { return 0 }
function Get-LlamaCpuBudget { return @{ Limit = '4.0'; Reservation = '1.0'; Available = '4.0' } }
function docker { throw 'Docker must not run in this fixture' }
function Test-WindowsLemonadeWhisperPortConflict { return $false }
function Stop-Process { throw 'No process may be stopped by port preflight' }
function Write-AIWarn { param($Message) }
function Write-AIError { param($Message) }
function Write-AI { param($Message) }

$script:checks = 0
$script:failures = @()
function Assert-Equal($Actual, $Expected, [string]$Label) {
    $script:checks++
    if ("$Actual" -cne "$Expected") { $script:failures += "$Label expected '$Expected', got '$Actual'" }
}
function Read-Ast([string]$Path) {
    $tokens = $null; $parseErrors = $null
    $tree = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw $parseErrors[0].Message }
    return $tree
}
$healthInstallerAst = Read-Ast (Join-Path $SourceRoot 'installers/windows/install-windows.ps1')
$workflowHealthNode = $healthInstallerAst.Find({
    param($n)
    $n -is [System.Management.Automation.Language.IfStatementAst] -and
        $n.Extent.Text.Contains('Name = "n8n (Workflows)"')
}, $true)
if (-not $workflowHealthNode) { throw 'Workflow health-check assembly was not found' }
$workflowHealthBlock = [scriptblock]::Create($workflowHealthNode.Extent.Text)
function Get-WorkflowHealthUrl([hashtable]$EnvMap) {
    $windowsEnvMap = $EnvMap
    $enableWorkflows = $true
    $healthChecks = @()
    . $workflowHealthBlock
    if ($healthChecks.Count -ne 1) { throw 'Expected one workflow health check' }
    return $healthChecks[0].Url
}
$phasePath = Join-Path $SourceRoot 'installers/windows/phases/04-requirements.ps1'
$phaseAst = Read-Ast $phasePath
foreach ($name in @('Get-WindowsODSSelectedPortConflicts', 'Assert-WindowsODSSelectedPortAvailability')) {
    $node = $phaseAst.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    . ([scriptblock]::Create($node.Extent.Text))
}
# Execute the real selected-port assembly, without hardware requirements or services.
$phaseText = Get-Content -LiteralPath $phasePath -Raw
$start = $phaseText.IndexOf('$_portsToCheck = [ordered]@{')
$end = $phaseText.IndexOf('$_portConflicts = @(', $start)
if ($start -lt 0 -or $end -lt $start) { throw 'Selected-port assembly was not found' }
$portBlock = [scriptblock]::Create($phaseText.Substring($start, $end - $start))
function Resolve-WindowsLlmPreflightPort { return 0 } # Native/LLM alias routing is a separate contract.
function Get-SelectedPorts([string]$InstallDir, [bool]$Features = $true) {
    $enableRecommended = $enableVoice = $enableWorkflows = $enableRag = $Features
    $enableHermes = $enableComfyui = $enableDeepResearch = $enablePrivacyShield = $Features
    $gpuInfo = @{ Backend = 'none' }; $cloudMode = $true
    $script:NATIVE_LLM_PORT = 8080
    . $portBlock
    return ,$_portsToCheck
}
$defaults = [ordered]@{
    DASHBOARD_PORT = 3001; DASHBOARD_REMOTE_PORT = 3011; DASHBOARD_API_PORT = 3002
    WEBUI_PORT = 3000; WHISPER_PORT = 9000; TTS_PORT = 8880; N8N_PORT = 5678
    QDRANT_PORT = 6333; QDRANT_GRPC_PORT = 6334; EMBEDDINGS_PORT = 8090
    LITELLM_PORT = 4000; SEARXNG_PORT = 8888; TOKEN_SPY_PORT = 3005
    HERMES_PROXY_PORT = 9120; APE_PORT = 7890; COMFYUI_PORT = 8188
    PERPLEXICA_PORT = 3004; SHIELD_PORT = 8085; OPENCODE_PORT = 3003
}
$labels = @{
    DASHBOARD_PORT = 'Dashboard'; DASHBOARD_REMOTE_PORT = 'Dashboard (remote)'; DASHBOARD_API_PORT = 'Dashboard API'
    WEBUI_PORT = 'Open WebUI (chat)'; WHISPER_PORT = 'Whisper (STT)'; TTS_PORT = 'Kokoro (TTS)'; N8N_PORT = 'n8n (workflows)'
    QDRANT_PORT = 'Qdrant (vector DB)'; QDRANT_GRPC_PORT = 'Qdrant (gRPC)'; EMBEDDINGS_PORT = 'TEI (embeddings)'
    LITELLM_PORT = 'LiteLLM (API gateway)'; SEARXNG_PORT = 'SearXNG (search)'; TOKEN_SPY_PORT = 'Token Spy (usage monitor)'
    HERMES_PROXY_PORT = 'Hermes auth proxy'; APE_PORT = 'APE (agent policy engine)'; COMFYUI_PORT = 'ComfyUI (image generation)'
    PERPLEXICA_PORT = 'Perplexica (deep research)'; SHIELD_PORT = 'Privacy Shield'
}
$saved = @{}
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-service-ports-' + [Guid]::NewGuid().ToString('N'))
$fixture = [IO.Path]::GetFullPath($fixture)
New-Item -ItemType Directory -Path $fixture | Out-Null
$tier = @{ TierName = 'Port fixture'; LlmModel = 'test'; GgufFile = 'test.gguf'; MaxContext = 4096 }
function Generate-Env {
    New-ODSEnv -InstallDir $fixture -TierConfig $tier -Tier '3' -GpuBackend 'nvidia' | Out-Null
    return Get-WindowsODSEnvMap -InstallDir $fixture
}
function Assert-ProcessOverrideRefused([string]$Key) {
    $before = (Get-FileHash -LiteralPath (Join-Path $fixture '.env') -Algorithm SHA256).Hash
    if ($labels.ContainsKey($Key)) {
        $rejected = $false
        try { $null = Get-SelectedPorts $fixture }
        catch { $rejected = $_.Exception.Message.StartsWith("$Key process override must be a decimal port") }
        Assert-Equal $rejected $true "Invalid $Key fails preflight"
    }
    $rejected = $false
    try { $null = Generate-Env }
    catch { $rejected = $_.Exception.Message.StartsWith("$Key process override must be a decimal port") }
    Assert-Equal $rejected $true "Invalid $Key fails generation"
    Assert-Equal (Get-FileHash -LiteralPath (Join-Path $fixture '.env') -Algorithm SHA256).Hash $before "Invalid $Key leaves env file unchanged"
}
try {
    if ($EmptyProcessProbe) {
        # PS5.1 GetEnvironmentVariable returns null for an inherited empty
        # variable, but the Process dictionary still distinguishes its presence.
        if (-not [Environment]::GetEnvironmentVariables('Process').Contains('N8N_PORT') -or
            -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('N8N_PORT'))) { throw 'Empty-process fixture was not inherited' }
        Set-Content -LiteralPath (Join-Path $fixture '.env') -Value 'N8N_PORT=15679'
        Assert-ProcessOverrideRefused 'N8N_PORT'
        if ($script:failures.Count) { throw ($script:failures -join '; ') }
        Write-Host 'PASS inherited empty process override refused before write'
        return
    }
    foreach ($key in $defaults.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key)
        [Environment]::SetEnvironmentVariable($key, [NullString]::Value)
    }
    foreach ($case in @('defaults', 'persisted', 'process', 'retained-rerun', 'invalid-retained')) {
        $expected = @{}
        foreach ($key in $defaults.Keys) {
            $value = [int]$defaults[$key]
            if ($case -eq 'persisted') { $value += 10000 }
            if ($case -in @('process', 'retained-rerun')) { $value += 20000 }
            $expected[$key] = $value
            $override = $null
            if ($case -eq 'process') { $override = [string]$value }
            [Environment]::SetEnvironmentVariable($key, $(if ($null -eq $override) { [NullString]::Value } else { $override }))
        }
        if ($case -in @('persisted', 'invalid-retained')) {
            $lines = foreach ($key in $defaults.Keys) {
                if ($case -eq 'invalid-retained') { "$key=0" } else { "$key=$($expected[$key])" }
            }
            Set-Content -LiteralPath (Join-Path $fixture '.env') -Value $lines
        }
        $selected = Get-SelectedPorts $fixture
        $generated = Generate-Env
        Assert-Equal (Get-WorkflowHealthUrl $generated) "http://localhost:$($expected.N8N_PORT)/healthz" "$case workflow health check uses the generated port"
        foreach ($key in $defaults.Keys) {
            Assert-Equal $generated[$key] $expected[$key] "$case generated $key"
            if ($labels.ContainsKey($key)) { Assert-Equal $selected[$labels[$key]] $expected[$key] "$case preflight $key" }
        }
    }
    foreach ($key in $defaults.Keys | Where-Object { $_ -notin @('WEBUI_PORT', 'WHISPER_PORT') }) {
        foreach ($bad in @('70000', 'not-a-port', ' 15680 ', '+15680', '0', '-1', '1.5', "15680`n")) {
            [Environment]::SetEnvironmentVariable($key, $bad)
            Assert-ProcessOverrideRefused $key
        }
        [Environment]::SetEnvironmentVariable($key, [NullString]::Value)
        foreach ($good in @('1', '65535', '015680')) {
            [Environment]::SetEnvironmentVariable($key, $good)
            Assert-Equal (Resolve-WindowsODSPort -Name $key -DefaultPort $defaults[$key] -RequireValidProcessOverride) ([int]$good) "Strict decimal $key $good"
        }
        [Environment]::SetEnvironmentVariable($key, [NullString]::Value)
    }
    # Construct an actual empty inherited environment entry. PS5.1's setter
    # removes empty values, so setting Env:N8N_PORT cannot represent this case.
    $probeInfo = New-Object Diagnostics.ProcessStartInfo
    $probeInfo.FileName = (Get-Process -Id $PID).Path
    $probeInfo.Arguments = '-NoProfile -File "' + $PSCommandPath + '" -SourceRoot "' + $SourceRoot + '" -EmptyProcessProbe'
    $probeInfo.UseShellExecute = $false
    $probeInfo.CreateNoWindow = $true
    $probeInfo.RedirectStandardOutput = $true
    $probeInfo.RedirectStandardError = $true
    $probeInfo.EnvironmentVariables['N8N_PORT'] = ''
    $probe = [Diagnostics.Process]::Start($probeInfo)
    if (-not $probe.WaitForExit(30000)) { $probe.Kill(); throw 'Empty-process fixture exceeded its bound' }
    $probeOutput = $probe.StandardOutput.ReadToEnd() + $probe.StandardError.ReadToEnd()
    Assert-Equal $probe.ExitCode 0 "Inherited empty-process refusal: $probeOutput"
    $probe.Dispose()

    if ($CheckCompose) {
        # config only: no daemon, image pull, listener, or container operation.
        $composeExecutable = (Get-Command docker -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $composePath = Join-Path $fixture 'compose.yaml'
        Set-Content -LiteralPath $composePath -Value @'
name: ods-service-port-contract
services:
  fixture:
    image: busybox:latest
    ports:
      - "127.0.0.1:${N8N_PORT:-5678}:5678"
'@
        Set-Content -LiteralPath (Join-Path $fixture '.env') -Value 'N8N_PORT=15679'
        foreach ($case in @('retained', 'explicit', 'new-shell')) {
            if ($case -eq 'explicit') { $env:N8N_PORT = '015680' }
            else { [Environment]::SetEnvironmentVariable('N8N_PORT', [NullString]::Value) }
            $generated = Generate-Env
            $config = & $composeExecutable compose --env-file (Join-Path $fixture '.env') -f $composePath config --format json
            Assert-Equal $LASTEXITCODE 0 "Compose config $case"
            $published = ($config | ConvertFrom-Json).services.fixture.ports[0].published
            Assert-Equal $published $generated.N8N_PORT "Effective Compose port equals generated $case port"
            Assert-Equal $published $(if ($case -eq 'retained') { 15679 } else { 15680 }) "Effective Compose $case port"
        }
        # Restore default fixture values for the later conflict checks.
        Set-Content -LiteralPath (Join-Path $fixture '.env') -Value ($defaults.Keys | ForEach-Object { "$_=$($defaults[$_])" })
    }
    foreach ($key in $defaults.Keys) {
        foreach ($value in @('1', '65535', '0', '-1', '65536', '1.5', '')) {
            [Environment]::SetEnvironmentVariable($key, $value)
            $expectedPort = if ($value -in @('1', '65535')) { [int]$value } else { 12345 }
            Assert-Equal (Resolve-WindowsODSPort -Name $key -DefaultPort $defaults[$key] -ExistingEnv @{ $key = '12345' }) $expectedPort "validated $key $value"
        }
        [Environment]::SetEnvironmentVariable($key, [NullString]::Value)
    }
    $selected = Get-SelectedPorts $fixture $false
    Assert-Equal $selected.Count 4 'Disabled optional features do not add port checks'
    Assert-Equal $selected['Dashboard (remote)'] 3011 'Remote Dashboard is still required without optional features'
    $script:occupied = 3011
    function Test-WindowsPortInUse {
        param([int]$Port)
        return @{ InUse = ($Port -eq $script:occupied); ProcessId = 4242; ProcessName = 'fixture-owner' }
    }
    $conflicts = @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $selected)
    Assert-Equal $conflicts.Count 1 'Occupied required remote Dashboard listener is detected'
    $aborted = $false
    try { $null = Assert-WindowsODSSelectedPortAvailability -Conflicts $conflicts -NonInteractive }
    catch { $aborted = $_.Exception.Message -eq 'ODS_INSTALL_ABORTED' }
    Assert-Equal $aborted $true 'Remote Dashboard collision aborts non-interactive install'
    $env:N8N_PORT = '15679'; $script:occupied = 5678
    $selected = Get-SelectedPorts $fixture
    Assert-Equal @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $selected).Count 0 'n8n override leaves unrelated default-port listener alone'
    $script:occupied = 15679
    Assert-Equal @(Get-WindowsODSSelectedPortConflicts -PortsToCheck $selected).Count 1 'n8n selected-port listener is a real conflict'

    # Execute real shortcut URL construction; never write Desktop/Start Menu files.
    $installerAst = Read-Ast (Join-Path $SourceRoot 'installers/windows/install-windows.ps1')
    $assignment = $installerAst.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$dashboardUrl' }, $true)
    $dashboardPort = 13101
    . ([scriptblock]::Create($assignment.Extent.Text))
    Assert-Equal $dashboardUrl 'http://localhost:13101' 'Shortcuts use selected Dashboard port'
    $urlAssignment = $installerAst.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$urlContent' }, $true)
    $iconContent = 'IconIndex=0'
    . ([scriptblock]::Create($urlAssignment.Extent.Text))
    Assert-Equal ($urlContent.Contains('URL=http://localhost:13101')) $true 'InternetShortcut content uses selected port'

    # Execute the real success-card call and retain the helper's actual defaults.
    # The card must receive the same selected URLs that readiness just checked.
    function Write-SuccessCard {
        param([string]$WebUIPort = '3000', [string]$DashboardPort = '3001')
        $script:successCardPorts = @{ WebUI = $WebUIPort; Dashboard = $DashboardPort }
    }
    $webuiPort = 13100
    $cardCall = $installerAst.Find({
        param($n)
        $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Write-SuccessCard'
    }, $true)
    if (-not $cardCall) { throw 'Success-card invocation was not found' }
    . ([scriptblock]::Create($cardCall.Extent.Text))
    Assert-Equal $script:successCardPorts.Dashboard 13101 'Success card receives selected Dashboard port'
    Assert-Equal $script:successCardPorts.WebUI 13100 'Success card receives selected WebUI port'

    $windowsEnvMap = @{ PERPLEXICA_PORT = '13004' }; $perplexicaPort = 0
    $readinessEnv = $windowsEnvMap
    $readinessFunction = $installerAst.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-ReadinessPort' }, $true)
    . ([scriptblock]::Create($readinessFunction.Extent.Text))
    $assignment = $installerAst.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$perplexicaPort' }, $true)
    if ($assignment) { . ([scriptblock]::Create($assignment.Extent.Text)) }
    function Set-PerplexicaConfig { param($PerplexicaPort, $LlmModel, $LlmBaseUrl, $ApiKey) $script:configuredPort = $PerplexicaPort; return $true }
    $call = $installerAst.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$perplexicaOk' }, $true)
    . ([scriptblock]::Create($call.Extent.Text))
    Assert-Equal $script:configuredPort 13004 'Perplexica configuration uses selected published port'
} finally {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $(if ($null -eq $saved[$key]) { [NullString]::Value } else { $saved[$key] })) }
    $expectedParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
    if ([IO.Path]::GetDirectoryName($fixture).TrimEnd('\', '/') -ne $expectedParent -or
        [IO.Path]::GetFileName($fixture) -notmatch '^ods-service-ports-[a-f0-9]{32}$' -or
        ((Get-Item -LiteralPath $fixture -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
foreach ($failure in $script:failures) { Write-Host "FAIL $failure" }
Write-Host "$($script:checks - $script:failures.Count)/$($script:checks) service-port checks passed"
if ($script:failures.Count) { exit 1 }
