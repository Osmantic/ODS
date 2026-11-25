$ErrorActionPreference = 'Stop'

# Native Windows must turn Open WebUI sign-in on before it recreates Open WebUI
# whenever Open WebUI is reachable beyond this machine: through ods-proxy or
# through a BIND_ADDRESS other than loopback. Runs the real ods.ps1 start,
# restart and update functions against a temporary install directory, with
# Docker and the native runtimes stubbed.

$root = Split-Path -Parent $PSScriptRoot

function Get-FunctionText {
    param([string]$Path, [string[]]$Names)

    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "PowerShell parse failed for ${Path}: $($errors[0].Message)" }
    foreach ($name in $Names) {
        $definition = $ast.Find({
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
        }, $true)
        if (-not $definition) { throw "Function $name not found in $Path" }
        $definition.Extent.Text
    }
}

$odsCli = @(
    'Get-ODSEnvValue', 'Read-ODSEnv', 'Set-ODSEnvValue', 'Set-ODSProxyAuthRequired',
    'Test-ODSBindAddressIsNetwork', 'Test-ODSNetworkAccessEnabled',
    'Invoke-Start', 'Invoke-Restart', 'Invoke-Update'
)
foreach ($text in (Get-FunctionText -Path (Join-Path $root 'installers/windows/ods.ps1') -Names $odsCli)) {
    . ([scriptblock]::Create($text))
}
foreach ($text in (Get-FunctionText -Path (Join-Path $root 'installers/windows/lib/llm-endpoint.ps1') -Names @('Get-WindowsODSEnvMap'))) {
    . ([scriptblock]::Create($text))
}

# Anything that would reach Docker or WSL fails the test instead.
function docker { throw 'docker must not run in this test' }
function wsl { throw 'wsl must not run in this test' }
function Test-Install { }
function Ensure-LlamaCpuBudget { }
function Get-ComposeFlags { return @('-f', 'compose.fixture.yml') }
function Test-ODSComposeServiceAvailable { param($ComposeFlags, $Service) return $script:Services -contains $Service }
function Get-ODSRunningComposeServices { param($ComposeFlags) return @($script:Services) }
function Get-NativeInferenceBackend { return 'none' }
function Invoke-Agent { param($Action) }
function Start-ODSOpenCodeRuntime { return $true }
function Stop-ODSOpenCodeRuntime { }
function Invoke-HermesSoulRefresh { param([switch]$SyncContainer) }
function Invoke-BootstrapUpgradeResume { }
function Test-ODSLegacyOpenClawContainer { return $false }
function Invoke-ODSProxyAuthPreflight { param($ComposeFlags) throw 'Only ods-proxy starts run the proxy preflight' }
function Invoke-ODSComposeUpWithStartupRetry { param($ComposeFlags, $ComposeArgs, $Services, $Description) throw 'No retry expected' }
function Invoke-Status { }
function Start-Sleep { param($Seconds) }
function Write-AI { param($Message) }
function Write-AISuccess { param($Message) }
function Write-AIWarn { param($Message) }
function Write-AIError { param($Message) Write-Host "ods.ps1 error: $Message" }
function Write-ODSComposeDiagnostics { param($InstallDir, $ComposeFlags, $Phase) }
function Write-ODSMissingComposeServiceHint { param($ComposeFlags, $Service) }

# Records what Open WebUI would be created with: the .env sign-in lines and the
# value Compose would inherit from this process.
function Invoke-ODSDockerCompose {
    param($InstallDir, $ComposeFlags, $ComposeArgs)
    if ($ComposeArgs[0] -eq 'up' -and $null -eq $script:AtUp) {
        $script:AtUp = @{
            File = (@(Get-Content -LiteralPath $script:EnvFile) -match '^WEBUI_AUTH=') -join ','
            Process = $env:WEBUI_AUTH
        }
    }
    return 0
}

function Assert-True {
    param($Value, [string]$Message)
    if (-not $Value) { throw $Message }
}

$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-network-signin-" + [guid]::NewGuid())
$savedWebuiAuth = $env:WEBUI_AUTH
try {
    New-Item -ItemType Directory -Path $sandbox | Out-Null
    $InstallDir = $sandbox
    $script:EnvFile = Join-Path $sandbox '.env'

    # The same rule as _bind_address_is_network in bin/ods-host-agent.py.
    foreach ($bind in @('', '   ', '127.0.0.1', ' 127.0.0.1 ', '"127.0.0.1"', "'::1'", '::1', '[::1]', 'localhost', 'LOCALHOST')) {
        Assert-True (-not (Test-ODSBindAddressIsNetwork -BindAddress $bind)) "BIND_ADDRESS '$bind' should count as loopback"
    }
    foreach ($bind in @('0.0.0.0', '::', '[::]', '192.168.1.20', 'ods.lan', '127.0.0.2', '"0.0.0.0"')) {
        Assert-True (Test-ODSBindAddressIsNetwork -BindAddress $bind) "BIND_ADDRESS '$bind' should count as network-reachable"
    }
    Assert-True (-not (Test-ODSBindAddressIsNetwork -BindAddress $null)) 'A missing BIND_ADDRESS should count as loopback'

    $cases = @(
        # Loopback without the proxy keeps the operator's choice.
        @{ Bind = $null; Proxy = $false; Run = { Invoke-Start }; SignIn = $false },
        @{ Bind = '127.0.0.1'; Proxy = $false; Run = { Invoke-Restart }; SignIn = $false },
        @{ Bind = '"::1"'; Proxy = $false; Run = { Invoke-Update }; SignIn = $false },
        @{ Bind = 'localhost'; Proxy = $false; Run = { Invoke-Start -Service 'open-webui' }; SignIn = $false },
        # A network BIND_ADDRESS requires sign-in on every path that recreates Open WebUI.
        @{ Bind = '0.0.0.0'; Proxy = $false; Run = { Invoke-Start }; SignIn = $true },
        @{ Bind = '0.0.0.0'; Proxy = $false; Run = { Invoke-Start -Service 'open-webui' }; SignIn = $true },
        @{ Bind = '0.0.0.0'; Proxy = $false; Run = { Invoke-Restart }; SignIn = $true },
        @{ Bind = '192.168.1.20'; Proxy = $false; Run = { Invoke-Restart -Service 'open-webui' }; SignIn = $true },
        @{ Bind = '"0.0.0.0"'; Proxy = $false; Run = { Invoke-Update }; SignIn = $true },
        # Other single services leave Open WebUI alone, as on Linux and macOS.
        @{ Bind = '0.0.0.0'; Proxy = $false; Run = { Invoke-Start -Service 'dashboard' }; SignIn = $false },
        @{ Bind = '0.0.0.0'; Proxy = $false; Run = { Invoke-Restart -Service 'dashboard' }; SignIn = $false },
        # The proxy still requires sign-in on loopback.
        @{ Bind = '127.0.0.1'; Proxy = $true; Run = { Invoke-Start }; SignIn = $true },
        @{ Bind = '127.0.0.1'; Proxy = $true; Run = { Invoke-Restart -Service 'open-webui' }; SignIn = $true },
        @{ Bind = '127.0.0.1'; Proxy = $true; Run = { Invoke-Update }; SignIn = $true }
    )

    foreach ($case in $cases) {
        $label = "$($case.Run) with BIND_ADDRESS=$($case.Bind) proxy=$($case.Proxy)"
        $lines = @('KEEP_ME=1', 'WEBUI_AUTH=false')
        if ($null -ne $case.Bind) { $lines += "BIND_ADDRESS=$($case.Bind)" }
        [System.IO.File]::WriteAllLines($script:EnvFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
        Remove-Item Env:WEBUI_AUTH -ErrorAction SilentlyContinue
        $script:Services = @('open-webui', 'dashboard')
        if ($case.Proxy) { $script:Services += 'ods-proxy' }
        $script:AtUp = $null

        & $case.Run

        Assert-True ($null -ne $script:AtUp) "${label}: Compose never brought services up"
        if ($case.SignIn) {
            Assert-True ($script:AtUp.File -eq 'WEBUI_AUTH=true') "${label}: .env held '$($script:AtUp.File)' when services came up"
            Assert-True ($script:AtUp.Process -eq 'true') "${label}: Compose did not inherit WEBUI_AUTH=true"
        } else {
            Assert-True ($script:AtUp.File -eq 'WEBUI_AUTH=false') "${label}: sign-in changed to '$($script:AtUp.File)'"
            Assert-True ($script:AtUp.Process -ne 'true') "${label}: Compose inherited WEBUI_AUTH=true"
        }
        Assert-True (@(Get-Content -LiteralPath $script:EnvFile) -contains 'KEEP_ME=1') "${label}: other .env lines changed"
    }

    # A missing WEBUI_AUTH line is added once, and an existing true is left alone.
    foreach ($initial in @(@('KEEP_ME=1', 'BIND_ADDRESS=0.0.0.0'), @('WEBUI_AUTH=true', 'BIND_ADDRESS=0.0.0.0'))) {
        [System.IO.File]::WriteAllLines($script:EnvFile, [string[]]$initial, (New-Object System.Text.UTF8Encoding($false)))
        Remove-Item Env:WEBUI_AUTH -ErrorAction SilentlyContinue
        $script:Services = @('open-webui')
        $script:AtUp = $null
        Invoke-Restart
        Assert-True ($script:AtUp.File -eq 'WEBUI_AUTH=true') "Restart from '$($initial -join ';')' left '$($script:AtUp.File)'"
    }

    Write-Host '[PASS] Windows start, restart and update require Open WebUI sign-in whenever it is reachable from the network'
} finally {
    if ($null -eq $savedWebuiAuth) {
        Remove-Item Env:WEBUI_AUTH -ErrorAction SilentlyContinue
    } else {
        $env:WEBUI_AUTH = $savedWebuiAuth
    }
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}
