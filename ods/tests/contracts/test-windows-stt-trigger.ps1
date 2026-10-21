$ErrorActionPreference = 'Stop'

# Load only the real request boundary; never run the CLI dispatcher, Docker,
# network requests, model downloads, or the installed host's repair command.
$path = Join-Path $PSScriptRoot '../../installers/windows/ods.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$function = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Invoke-ODSSttModelDownloadTrigger'
}, $true)
if (-not $function) { throw 'Missing CLI STT trigger' }
. ([scriptblock]::Create($function.Extent.Text))
$repair = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-RepairVoice'
}, $true)
if (-not $repair) { throw 'Missing voice repair caller' }
. ([scriptblock]::Create($repair.Extent.Text))
function Write-AIWarn { param($Message); $script:warning = $Message }
function Invoke-WebRequest { throw 'The native curl contract must not make a network request' }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-stt-trigger-' + [guid]::NewGuid().ToString('N'))
$previousPath = $env:PATH
$previousExit = $env:ODS_STT_FIXTURE_EXIT
try {
    [void][IO.Directory]::CreateDirectory($fixture)
    # A real native program is essential: a PowerShell function cannot reproduce
    # Windows PowerShell 5.1's terminating redirected NativeCommandError.
    Add-Type -TypeDefinition @'
using System;
public class SttTriggerFixture {
    public static int Main(string[] args) {
        if (args.Length != 8 || args[0] != "--fail" || args[1] != "--silent" ||
            args[2] != "--show-error" || args[3] != "--max-time" || args[4] != "30" ||
            args[5] != "-X" || args[6] != "POST" || args[7] != "http://fixture.invalid/model") return 99;
        Console.Error.WriteLine("fixture native curl diagnostic");
        return int.Parse(Environment.GetEnvironmentVariable("ODS_STT_FIXTURE_EXIT"));
    }
}
'@ -OutputAssembly (Join-Path $fixture 'curl.exe') -OutputType ConsoleApplication
    $env:PATH = $fixture + [IO.Path]::PathSeparator + $previousPath
    foreach ($code in @(28, 0, 22, 7)) {
        $env:ODS_STT_FIXTURE_EXIT = [string]$code
        $script:warning = ''
        $diagnostics = @()
        $result = Invoke-ODSSttModelDownloadTrigger -ModelUrl 'http://fixture.invalid/model' 6>&1 |
            ForEach-Object {
                if ($_ -is [Management.Automation.InformationRecord]) { $diagnostics += $_.ToString() }
                else { $_ }
            }
        $expected = $code -in @(0, 28)
        if ($result -isnot [bool] -or $result -ne $expected) { throw "Wrong result for curl exit $code : $result" }
        if ($ErrorActionPreference -cne 'Stop') { throw 'Trigger changed caller error policy' }
        if (-not $expected -and ($script:warning -notmatch "curl exit $code" -or ($diagnostics -join '') -notmatch 'fixture native curl diagnostic')) {
            throw "Failed curl exit $code lost its exit code or native diagnostic"
        }
        if ($expected -and $script:warning) { throw "Accepted curl exit $code emitted a failure warning" }
        Write-Host "PASS native curl exit $code retains the bounded STT trigger contract"
    }
    # Exercise the real repair orchestration too. Replace every external
    # boundary, while retaining its real curl helper and cache-wait handoff.
    $InstallDir = $fixture
    function Test-Install { }
    function Get-ComposeFlags { @() }
    function Test-ODSComposeServiceAvailable { param($ComposeFlags, $Service); $true }
    function Invoke-ODSDockerCompose { param($InstallDir, $ComposeFlags, $ComposeArgs); 0 }
    function Write-AI { param($Message) }
    function Write-AISuccess { param($Message); $script:successes += $Message }
    function Write-AIError { param($Message); throw $Message }
    function Get-ODSVoiceDiagnosis {
        @{ WhisperHealthy = $true; TtsHealthy = $true; SttModelCached = $script:cacheVerified;
            SttModel = 'fixture'; SttModelUrl = 'http://fixture.invalid/model' }
    }
    function Wait-ODSSttModelCached {
        param($ModelUrl, $TimeoutSeconds)
        if ($TimeoutSeconds -ne 900 -or $ModelUrl -cne 'http://fixture.invalid/model') { throw 'Wrong cache wait contract' }
        $script:cacheVerified = $true
        $true
    }
    $env:ODS_STT_FIXTURE_EXIT = '28'
    $script:cacheVerified = $false
    $script:successes = @()
    $previousLocation = (Get-Location).Path
    Invoke-RepairVoice
    if (-not $script:cacheVerified -or $script:successes -notcontains 'Voice repair complete.') { throw 'Timeout prevented repair cache verification' }
    if ((Get-Location).Path -cne $previousLocation) { throw 'Repair did not restore caller location' }
    Write-Host 'PASS voice repair verifies the model cache after native curl timeout'
} finally {
    $env:PATH = $previousPath
    $env:ODS_STT_FIXTURE_EXIT = $previousExit
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temp root' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
# The native fixture deliberately ends with curl exit 28. GitHub's Windows
# PowerShell wrapper propagates LASTEXITCODE even after every assertion passes.
exit 0
