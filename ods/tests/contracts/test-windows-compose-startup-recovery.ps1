param([string]$SourcePath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $SourcePath) {
    $SourcePath = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'installers/windows/ods.ps1'
}
# Load only the actual recovery functions; never dispatch a host CLI operation.
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw $parseErrors[0] }
foreach ($name in @('Test-ODSComposeServicesStarted', 'Invoke-ODSComposeUpWithStartupRetry', 'Invoke-Restart')) {
    $functionAst = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $functionAst) { throw "Missing production function $name" }
    . ([scriptblock]::Create($functionAst.Extent.Text))
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-compose-recovery-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $fixture | Out-Null
$savedPath = $env:PATH
$savedAttempts = $env:ODS_RESTART_STARTUP_RETRY_ATTEMPTS
$savedDelay = $env:ODS_RESTART_STARTUP_RETRY_DELAY_SECONDS
try {
    # A native executable is essential: a PowerShell function does not reproduce
    # LASTEXITCODE or partial successful pipeline output from Docker.
    $nativeSource = @'
using System;
using System.IO;
public static class ComposeRecoveryFixture {
    public static int Main(string[] args) {
        File.AppendAllText(Environment.GetEnvironmentVariable("ODS_COMPOSE_FIXTURE_LOG"), String.Join("|", args) + "\n");
        string mode = Environment.GetEnvironmentVariable("ODS_COMPOSE_FIXTURE_MODE");
        if (args[0] == "compose") {
            if (mode == "missing") return 0;
            Console.WriteLine("container-a");
            if (mode == "partial-failure") return 37;
            if (mode == "replica-failure") Console.WriteLine("container-b");
            return 0;
        }
        if (args[0] == "inspect") {
            if (mode == "inspect-failure") { Console.WriteLine("running 0"); return 38; }
            if (mode == "inspect-empty") return 0;
            if (mode == "replica-failure" && args[args.Length - 1] == "container-b") Console.WriteLine("exited 1");
            else if (mode == "completed") Console.WriteLine("exited 0");
            else if (mode == "stopped") Console.WriteLine("exited 1");
            else if (mode == "restarting") Console.WriteLine("restarting 1");
            else Console.WriteLine("running 0");
            return 0;
        }
        return 99;
    }
}
'@
    # Compile a Framework executable so exactly the same native boundary works
    # in Windows PowerShell and PowerShell 7 (whose Add-Type cannot emit EXEs).
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    $nativePath = Join-Path $fixture 'docker.cs'
    [IO.File]::WriteAllText($nativePath, $nativeSource)
    & $compiler /nologo /target:exe ("/out:" + (Join-Path $fixture 'docker.exe')) $nativePath
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile native Docker fixture' }
    $env:PATH = $fixture + [IO.Path]::PathSeparator + $savedPath
    $env:ODS_COMPOSE_FIXTURE_LOG = Join-Path $fixture 'docker.log'
    $flags = @('-f', 'overlay with spaces.yml')
    $env:ODS_COMPOSE_FIXTURE_MODE = 'partial-failure'
    if (Test-ODSComposeServicesStarted -ComposeFlags $flags -Services @('api')) {
        throw 'Partial failed Docker inventory was accepted as successful startup'
    }
    if (@(Get-Content $env:ODS_COMPOSE_FIXTURE_LOG).Count -ne 1) {
        throw 'A failed inventory must not be used to inspect and certify partial IDs'
    }
    Write-Host 'PASS: partial native stdout plus exit 37 cannot certify startup'

    foreach ($mode in @('missing', 'inspect-failure', 'inspect-empty', 'replica-failure', 'stopped', 'restarting', 'running', 'completed')) {
        $env:ODS_COMPOSE_FIXTURE_MODE = $mode
        $actual = Test-ODSComposeServicesStarted -ComposeFlags $flags -Services @('api', 'worker')
        $expected = $mode -in @('running', 'completed')
        if ($actual -ne $expected) { throw "Unexpected started result for $mode : $actual" }
        Write-Host "PASS: native service-state control $mode"
    }
    if (Test-ODSComposeServicesStarted -ComposeFlags $flags -Services @()) { throw 'Empty targets must not certify startup' }

    $script:upCalls = @(); $script:sleeps = @(); $script:warnings = @()
    $script:upResults = @(19, 19, 0)
    $InstallDir = $fixture
    function Invoke-ODSDockerCompose {
        param($InstallDir, $ComposeFlags, $ComposeArgs)
        if ($InstallDir -ne $fixture -or $ComposeFlags.Count -ne 2 -or $ComposeFlags[1] -ne 'overlay with spaces.yml' -or
            ($ComposeArgs -join '|') -ne 'up|-d|--no-build|--pull|never|api') { throw 'Recovery changed the selected operation' }
        $script:upCalls += ,@($ComposeArgs)
        return $script:upResults[$script:upCalls.Count - 1]
    }
    function Start-Sleep {
        [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidOverwritingBuiltInCmdlets', '', Justification='Intercept and assert retry delays without waiting.')]
        param($Seconds)
        $script:sleeps += $Seconds
    }
    function Write-AIWarn { param($Message) $script:warnings += $Message }
    $env:ODS_RESTART_STARTUP_RETRY_ATTEMPTS = '3'
    $env:ODS_RESTART_STARTUP_RETRY_DELAY_SECONDS = '2'
    $composeArgs = @('up', '-d', '--no-build', '--pull', 'never', 'api')
    $env:ODS_COMPOSE_FIXTURE_MODE = 'partial-failure'
    $result = Invoke-ODSComposeUpWithStartupRetry -ComposeFlags $flags -ComposeArgs $composeArgs -Services @('api')
    if ($result -ne 0 -or $script:upCalls.Count -ne 3 -or ($script:sleeps -join ',') -ne '2,2') { throw 'Failed inventory prevented safe bounded recovery' }
    if (($script:warnings -join ' ') -like '*continuing*') { throw 'Failed inventory was presented as verified success' }
    Write-Host 'PASS: incomplete inventory retries unchanged operation until Compose succeeds'

    $script:upCalls = @(); $script:sleeps = @(); $script:upResults = @(19, 23, 27)
    $result = Invoke-ODSComposeUpWithStartupRetry -ComposeFlags $flags -ComposeArgs $composeArgs -Services @('api')
    if ($result -ne 27 -or $script:upCalls.Count -ne 3 -or $script:sleeps.Count -ne 2) { throw 'Retry exhaustion lost the final failure status' }
    Write-Host 'PASS: retry deadline retains final Compose failure'

    foreach ($mode in @('running', 'completed')) {
        $env:ODS_COMPOSE_FIXTURE_MODE = $mode
        $script:upCalls = @(); $script:sleeps = @(); $script:upResults = @(19)
        $result = Invoke-ODSComposeUpWithStartupRetry -ComposeFlags $flags -ComposeArgs $composeArgs -Services @('api')
        if ($result -ne 0 -or $script:upCalls.Count -ne 1 -or $script:sleeps.Count -ne 0) { throw "Verified $mode must retain successful recovery" }
        Write-Host "PASS: verified $mode retains existing nonzero-up recovery"
    }
    $logged = Get-Content $env:ODS_COMPOSE_FIXTURE_LOG
    if (-not ($logged -contains 'compose|-f|overlay with spaces.yml|ps|--all|-q|worker')) { throw 'Target service or spaced flag was lost at native boundary' }

    # Follow the real service-scoped restart caller through recreate failure,
    # incomplete inventory, retry, and recovery. Every host mutation boundary
    # is either replaced or unreachable for this fixture's optional service.
    function Test-Install { }
    function Ensure-LlamaCpuBudget { }
    function Get-ComposeFlags { return @('-f', 'overlay with spaces.yml') }
    function Test-ODSComposeServiceAvailable {
        param($ComposeFlags, $Service)
        if (($ComposeFlags -join '|') -ne '-f|overlay with spaces.yml') { throw 'Unexpected service probe flags' }
        return $Service -eq 'api'
    }
    function Write-AI { param($Message) $script:warnings += $Message }
    function Write-AISuccess { param($Message) $script:successMessages += $Message }
    function Write-AIError { param($Message) throw $Message }
    function Invoke-ODSDockerCompose {
        param($InstallDir, $ComposeFlags, $ComposeArgs)
        if ($InstallDir -ne $fixture -or ($ComposeFlags -join '|') -ne '-f|overlay with spaces.yml') { throw 'Restart changed installation or flags' }
        $expected = 'up|-d|--no-build|--pull|never|api'
        if ($script:upCalls.Count -eq 0) { $expected = 'up|-d|--force-recreate|--no-build|--pull|never|api' }
        if (($ComposeArgs -join '|') -ne $expected) { throw 'Restart recovery changed its target or mutation policy' }
        $script:upCalls += ,@($ComposeArgs)
        return @(19, 23, 0)[$script:upCalls.Count - 1]
    }
    $env:ODS_COMPOSE_FIXTURE_MODE = 'partial-failure'
    $script:upCalls = @(); $script:sleeps = @(); $script:successMessages = @()
    $locationBefore = (Get-Location).Path
    Invoke-Restart -Service 'api'
    if ($script:upCalls.Count -ne 3 -or $script:sleeps.Count -ne 1 -or
        $script:successMessages -notcontains 'api restarted' -or (Get-Location).Path -ne $locationBefore) {
        throw 'Public restart did not complete bounded recovery and restore caller location'
    }
    Write-Host 'PASS: service-scoped restart reaches safe recovery after failed native inventory'
} finally {
    $env:PATH = $savedPath
    $env:ODS_RESTART_STARTUP_RETRY_ATTEMPTS = $savedAttempts
    $env:ODS_RESTART_STARTUP_RETRY_DELAY_SECONDS = $savedDelay
    Remove-Item Env:ODS_COMPOSE_FIXTURE_LOG, Env:ODS_COMPOSE_FIXTURE_MODE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
# Expected nonzero native statuses must not become the hosted step's status.
exit 0
