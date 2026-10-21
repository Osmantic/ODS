param([string]$Scenario = '', [string]$InstallRoot = '')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'installers/windows/lib/llm-endpoint.ps1')
. (Join-Path $root 'installers/windows/lib/install-report.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in @('Test-Install', 'Test-DockerRunning', 'Invoke-Report')) {
    $definition = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if (-not $definition) { throw "Missing CLI function: $name" }
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Write-AI { param($Message) }
function Write-AIError { param($Message); Write-Host $Message }
function Write-AIWarn { param($Message) }
function Write-AISuccess { param($Message) }
function Write-Chapter { param($Message) }
function docker {
    $script:dockerCalls += ,@($args)
    $global:LASTEXITCODE = 1
    'fixture Docker Desktop unavailable'
}
if ($Scenario -eq 'lifecycle-preflight') {
    $InstallDir = $InstallRoot
    Test-Install
    throw 'Lifecycle unexpectedly accepted unavailable Docker'
}
if ($Scenario -eq 'missing-install') {
    $InstallDir = $InstallRoot
    Invoke-Report
    throw 'Report unexpectedly accepted a missing installation'
}
function Get-GpuInfo { @{ Backend = 'cpu'; Name = 'fixture GPU'; VramMB = 0; MemoryType = 'shared' } }
function Get-NativeInferenceBackend { 'none' }
function Get-CimInstance {
    param($ClassName)
    if ($ClassName -eq 'Win32_ComputerSystem') { return @{ TotalPhysicalMemory = 16GB } }
    if ($ClassName -eq 'Win32_OperatingSystem') { return @{ Caption = 'fixture OS'; Version = '10'; BuildNumber = 'fixture' } }
    throw "Unexpected hardware request: $ClassName"
}
function Invoke-WebRequest {
    param($Uri, $TimeoutSec, [switch]$UseBasicParsing, $ErrorAction)
    $script:probes += $Uri
    throw 'fixture endpoint unavailable'
}
function Get-ComposeFlags { @('-f', 'docker-compose.base.yml', '-f', 'compose.fixture.yaml') }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods report outage ' + [guid]::NewGuid().ToString('N'))
try {
    [void][IO.Directory]::CreateDirectory($fixture)
    $InstallDir = Join-Path $fixture 'install'
    [void][IO.Directory]::CreateDirectory($InstallDir)
    [IO.File]::WriteAllText((Join-Path $InstallDir 'docker-compose.base.yml'), 'services: {}')
    [IO.File]::WriteAllText((Join-Path $InstallDir '.env'), "WEBUI_PORT=13000`nDASHBOARD_PORT=13001`nDASHBOARD_API_PORT=13002`nWHISPER_PORT=19000`nOLLAMA_PORT=18080`nAUDIO_STT_MODEL=fixture/model`n")
    $sentinel = Join-Path $fixture 'foreign-sentinel'
    [IO.File]::WriteAllText($sentinel, 'retain')
    $script:probes = @()
    $script:dockerCalls = @()
    $location = (Get-Location).Path
    Invoke-Report
    if ((Get-Location).Path -cne $location) { throw 'Report changed caller location' }
    $json = Get-Content (Join-Path $InstallDir 'artifacts/windows-report/report.json') -Raw | ConvertFrom-Json
    $text = Get-Content (Join-Path $InstallDir 'artifacts/windows-report/report.txt') -Raw
    foreach ($name in @('docker_version', 'docker_info', 'compose_config', 'compose_ps')) {
        $receipt = $json.compose.$name
        if ($receipt.ok -or $receipt.exit_code -ne 1 -or ($receipt.lines -join '') -notmatch 'fixture Docker Desktop unavailable') { throw "Missing failed Docker receipt: $name" }
    }
    foreach ($url in @('http://localhost:13000', 'http://localhost:13001', 'http://localhost:13002/health', 'http://localhost:19000/health', 'http://localhost:19000/v1/models/fixture%2Fmodel')) {
        if ($script:probes -notcontains $url) { throw "Report ignored configured endpoint: $url" }
    }
    if ($null -ne $json.health.stt_model.cached -or $text -notmatch 'skipped \(Whisper unavailable\)' -or $text -notmatch 'compose config exit: 1') { throw 'Outage report misrepresented diagnostic availability' }
    if (($json.compose.flags -join '|') -cne ((Get-ComposeFlags) -join '|')) { throw 'Report lost selected Compose flags' }
    $expectedCalls = @('version', 'info', 'compose|-f|docker-compose.base.yml|-f|compose.fixture.yaml|config', 'compose|-f|docker-compose.base.yml|-f|compose.fixture.yaml|ps|-a')
    $actualCalls = @($script:dockerCalls | ForEach-Object { $_ -join '|' })
    if (($actualCalls -join ';') -cne ($expectedCalls -join ';')) { throw "Wrong diagnostic command arguments: $actualCalls" }
    if ([IO.File]::ReadAllText($sentinel) -cne 'retain') { throw 'Report changed foreign state' }
    Write-Host 'PASS report writes configured diagnostics and failed Docker receipts during outage'

    # Preserve lifecycle refusal: the opt-out belongs only to report generation.
    $child = [Diagnostics.ProcessStartInfo]::new()
    $child.FileName = (Get-Process -Id $PID).Path
    $child.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Scenario lifecycle-preflight -InstallRoot "' + $InstallDir + '"'
    $child.UseShellExecute = $false; $child.CreateNoWindow = $true
    $child.RedirectStandardOutput = $true; $child.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($child)
    try {
        $output = $process.StandardOutput.ReadToEndAsync(); $errorOutput = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) { $process.Kill(); $process.WaitForExit(); throw 'Lifecycle preflight timed out' }
        if ($process.ExitCode -ne 1 -or $output.GetAwaiter().GetResult() -notmatch 'Docker Desktop is not running' -or $errorOutput.GetAwaiter().GetResult()) { throw 'Lifecycle no longer refuses unavailable Docker' }
    } finally { $process.Dispose() }
    Write-Host 'PASS lifecycle preflight still refuses unavailable Docker'
    $child.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Scenario missing-install -InstallRoot "' + (Join-Path $fixture 'missing') + '"'
    $process = [Diagnostics.Process]::Start($child)
    try {
        $output = $process.StandardOutput.ReadToEndAsync(); $errorOutput = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) { $process.Kill(); $process.WaitForExit(); throw 'Missing-install preflight timed out' }
        if ($process.ExitCode -ne 1 -or $output.GetAwaiter().GetResult() -notmatch 'ODS not found' -or $errorOutput.GetAwaiter().GetResult()) { throw 'Report no longer refuses a missing installation' }
    } finally { $process.Dispose() }
    Write-Host 'PASS report still refuses a missing installation'
} finally {
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temporary root' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
