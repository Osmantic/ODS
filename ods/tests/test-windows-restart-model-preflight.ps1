$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Restart' }, $true)
. ([scriptblock]::Create($definition.Extent.Text))
foreach ($name in @('Invoke-Start', 'Test-ODSOpenCodeAutoStart')) {
    $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
$InstallDir = $root
$script:Calls = @()
$script:Backend = 'llama-server'
$script:Failure = ''
$script:EnvMap = @{}
function Read-ODSEnv { return $script:EnvMap }
function Test-Install { }
function Ensure-LlamaCpuBudget { }
function Get-ComposeFlags { return @('-f', 'compose.fixture.yml') }
function Test-ODSComposeServiceAvailable { param($ComposeFlags, $Service) return $Service -eq 'dashboard' }
function Get-NativeInferenceBackend { return $script:Backend }
function Get-ODSNativeModelSelection {
    param([switch]$VerifyArtifacts)
    if (-not $VerifyArtifacts) { throw 'Artifact verification was omitted' }
    $script:Calls += 'verify'
    if ($script:Failure) { throw $script:Failure }
    return @{ modelPath = 'fixture-ssd/model.gguf' }
}
function Stop-ODSOpenCodeRuntime { $script:Calls += 'stop-opencode' }
function Start-ODSOpenCodeRuntime { $script:Calls += 'start-opencode'; return $true }
function Stop-NativeInferenceServer { $script:Calls += 'stop-model' }
function Start-NativeInferenceServer { $script:Calls += 'start-model' }
function Invoke-Agent { param($Action) if ($Action -eq 'start') { $script:Calls += 'agent' } }
function Get-ODSRunningComposeServices { param($ComposeFlags) return @('dashboard') }
function Invoke-ODSDockerCompose { param($InstallDir, $ComposeFlags, $ComposeArgs) $script:Calls += 'compose'; return 0 }
function Invoke-BootstrapUpgradeResume { $script:Calls += 'bootstrap' }
function Write-AI { param($Message) }
function Write-AISuccess { param($Message) }
function Write-AIWarn { param($Message) }
function Assert-True { param($Value, $Message) if (-not $Value) { throw $Message } }
foreach ($backend in @('llama-server', 'lemonade')) {
    $script:Backend = $backend
    foreach ($failure in @('SSD missing', 'Artifact hash changed', 'Runtime flags unsupported')) {
        $script:Calls = @(); $script:Failure = $failure
        try { Invoke-Restart; throw 'Restart should have failed' }
        catch { Assert-True ($_.Exception.Message -eq $failure) 'Unexpected failure' }
        Assert-True (($script:Calls -join ',') -eq 'verify') 'A failed preflight stopped or restarted a runtime'
    }
    $script:Calls = @(); $script:Failure = ''
    Invoke-Restart
    Assert-True (($script:Calls -join ',') -eq 'verify,stop-opencode,stop-model,start-model,compose,start-opencode,bootstrap') 'Restart did not preserve verification/stop/start order'
}
$script:Backend = 'none'; $script:Calls = @(); $script:Failure = 'No native model should be required'
Invoke-Restart
Assert-True ($script:Calls -notcontains 'verify' -and $script:Calls -notcontains 'stop-model') 'Container-only restart required a native model'
$script:Backend = 'llama-server'; $script:Calls = @()
Invoke-Restart -Service 'dashboard'
Assert-True (($script:Calls -join ',') -eq 'compose') 'An unrelated service restart touched native inference'
$script:EnvMap = @{ENABLE_DEVTOOLS='false'}
Assert-True (-not (Test-ODSOpenCodeAutoStart)) 'Disabled Dev Tools selected OpenCode autostart'
$script:Backend = 'llama-server'; $script:Failure = ''; $script:Calls = @()
Invoke-Restart
Assert-True (($script:Calls -join ',') -eq 'verify,stop-opencode,stop-model,start-model,compose,bootstrap') 'Disabled Dev Tools resurrected OpenCode during restart'
$script:Backend = 'none'; $script:Calls = @()
Invoke-Start
Assert-True (($script:Calls -join ',') -eq 'agent,compose,bootstrap') 'Disabled Dev Tools launched OpenCode during full start'
$script:Calls = @()
Invoke-Start -Service 'opencode'
Assert-True (($script:Calls -join ',') -eq 'start-opencode') 'Explicit OpenCode start was blocked'
$script:EnvMap = @{ENABLE_DEVTOOLS='invalid'}
Assert-True (-not (Test-ODSOpenCodeAutoStart)) 'Invalid Dev Tools selection launched OpenCode'
$script:EnvMap = @{ENABLE_DEVTOOLS='true'}
Assert-True (Test-ODSOpenCodeAutoStart) 'Selected Dev Tools did not start OpenCode'
$script:EnvMap = @{}
Assert-True (Test-ODSOpenCodeAutoStart) 'Legacy Windows install changed default behavior'
Write-Host '[PASS] Windows restart preflight and OpenCode start selection contracts'
