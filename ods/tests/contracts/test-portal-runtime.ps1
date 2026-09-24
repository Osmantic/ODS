$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-runtime.ps1')
$fixture=[IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('ods-runtime-test-'+[guid]::NewGuid().ToString('N'))))
$identity=[pscustomobject]@{directory=$fixture;taskName='ODS-WSL-fixture'}
$model=@{GgufFile='test.gguf';MaxContext=8192}
$script:registered=0; $script:started=0; $script:loads=0
$script:conflict=$false; $script:wrongModel=$false
function Check($value,$message) { if (-not $value) { throw $message }; Write-Host "PASS $message" }
function Initialize-ODSPrivateDirectory($Path) { New-Item -ItemType Directory -Path $Path -Force | Out-Null }
function Open-ODSPrivateLock($Path) { return [IO.File]::Open($Path,'OpenOrCreate','ReadWrite','None') }
function Install-ODSLemonadeRuntime { param($RootPath,$WorkDirectory); return [pscustomobject]@{ExecutablePath='C:\Fixture\lemonade-server.exe'} }
$script:existingRouter=$false; $script:foreignRouter=$false
function Get-CimInstance {
    param($ClassName)
    if ($script:existingRouter) {
        return @(
            [pscustomobject]@{ExecutablePath='C:\Fixture\lemonade-server.exe';ProcessId=100;CommandLine='serve --extra-models-dir "D:\ODS Models"'},
            [pscustomobject]@{ExecutablePath='C:\Fixture\lemonade-router.exe';ProcessId=101;ParentProcessId=$(if ($script:foreignRouter) {999} else {100})}
        )
    }
    return @()
}
function Get-NetTCPConnection {
    param($LocalPort,$State,$ErrorAction)
    if ($script:conflict) { return [pscustomobject]@{OwningProcess=999;LocalAddress='127.0.0.1'} }
    if ($script:existingRouter) {
        return @([pscustomobject]@{OwningProcess=101;LocalAddress='127.0.0.1'}, [pscustomobject]@{OwningProcess=999;LocalAddress='172.26.0.1'})
    }
    return @()
}
function Initialize-ODSPortalModelStore { param($WindowsPath,$Model); return @{Filename=$Model.GgufFile} }
function Get-ODSLemonadeLaunchContract { param($ExecutablePath,$Port,$ModelsDir,$ContextSize,$AdminApiKey); return [pscustomobject]@{Modern=$false;Version='10.0.0';RequiresRuntimeConfiguration=$false} }
function New-ODSLemonadeScheduledTaskAction { param($Contract,$EnvPath,$DiagnosticLogPath); Check ((Get-Content $EnvPath -Raw) -match '^LITELLM_LEMONADE_API_KEY=[a-f0-9]{64}') 'launcher reads a stored key instead of embedding it in task arguments'; return @{} }
function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel); Check ($RunLevel -eq 'Limited') 'runtime task does not require elevation'; return @{} }
function Resolve-ODSInteractiveScheduledTaskUser { return 'TEST\user' }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User); return @{} }
function New-ScheduledTaskSettingsSet { param([switch]$StartWhenAvailable,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,$ExecutionTimeLimit,$MultipleInstances); return @{} }
function Register-ScheduledTask { param($TaskName,$Action,$Principal,$Trigger,$Settings,[switch]$Force); $script:registered++ }
function Start-ScheduledTask { param($TaskName); $script:started++ }
function Resolve-ODSLemonadeModelId { param($Port,$GgufFile,$VersionOverride); return 'extra.test.gguf' }
function Set-ODSLemonadeLoadedModel { param($Port,$ModelId,$ContextSize,$ApiKey); $script:loads++ }
function Invoke-RestMethod {
    param($Uri,$Headers,$TimeoutSec,$Method,$ContentType,$Body)
    if ($Uri.EndsWith('/health')) { return @{status='ok';all_models_loaded=@(@{model_name='extra.test.gguf';device='gpu';recipe_options=@{llamacpp_backend='vulkan';ctx_size=8192}})} }
    if ($Uri.EndsWith('/chat/completions')) { return @{model=$(if ($script:wrongModel) {'wrong'} else {'extra.test.gguf'});choices=@(@{message=@{content='OK'}})} }
    throw "Unexpected runtime request: $Uri"
}
try {
    $preview=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $identity -ModelsDirectory 'D:\ODS Models' -Model $model -DryRun
    Check (-not $preview.Prepared -and -not (Test-Path $fixture) -and $script:registered -eq 0) 'dry run does not provision or claim a ready runtime'
    $script:conflict=$true
    $rejected=$false
    try { $null=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $identity -ModelsDirectory 'D:\ODS Models' -Model $model } catch { $rejected=$true }
    Check ($rejected -and $script:registered -eq 0 -and $script:loads -eq 0) 'another process on the port is never adopted or stopped'
    $script:conflict=$false
    $result=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $identity -ModelsDirectory 'D:\ODS Models' -Model $model
    Check ($result.Prepared -and $script:registered -eq 1 -and $script:started -eq 1 -and $script:loads -eq 1) 'fresh runtime completes task startup, model load, GPU and completion checks'
    Check ($result.ModelsWslPath -eq '/mnt/d/ODS Models' -and $result.Context -eq 8192) 'handoff retains the shared model directory and context'
    $script:existingRouter=$true
    $result=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $identity -ModelsDirectory 'D:\ODS Models' -Model $model
    Check ($result.Prepared -and $script:started -eq 1) 'verified router child is reused without starting another server; separate-address proxy does not conflict'
    $script:foreignRouter=$true
    $priorRegistered=$script:registered
    $rejected=$false
    try { $null=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $identity -ModelsDirectory 'D:\ODS Models' -Model $model } catch { $rejected=$true }
    Check ($rejected -and $script:registered -eq $priorRegistered) 'router with an unrelated parent cannot be adopted'
    $script:existingRouter=$false
    $script:wrongModel=$true
    $rejected=$false
    try { $null=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $identity -ModelsDirectory 'D:\ODS Models' -Model $model } catch { $rejected=$true }
    Check $rejected 'a completion from a different model cannot mark the runtime ready'
    $script:wrongModel=$false
    $originalLocalAppData=$env:LOCALAPPDATA
    try {
        $env:LOCALAPPDATA=$fixture
        $migrated=[pscustomobject]@{directory=(Join-Path $fixture 'new-location');taskName=$identity.taskName;id='migration-fixture'}
        $legacyDirectory=Join-Path $fixture 'ODS\wsl\migration-fixture'
        New-Item -ItemType Directory -Path $legacyDirectory -Force | Out-Null
        $legacyKey='ab'*32
        Set-Content -LiteralPath (Join-Path $legacyDirectory 'inference.env') -Value "LITELLM_LEMONADE_API_KEY=$legacyKey"
        $script:privateChecks=@();$script:unsafeLegacy=$false
        function Assert-ODSPrivatePath { param($Path,[switch]$Directory); if($script:unsafeLegacy){throw 'unsafe old credential'}; $script:privateChecks+= $Path }
        $result=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $migrated -ModelsDirectory 'D:\ODS Models' -Model $model
        Check ($result.ApiKey -ceq $legacyKey -and $script:privateChecks.Count -eq 2) 'credential migration preserves the running server key after validating directory and file ownership'
        $migrated.directory=Join-Path $fixture 'unsafe-migration'
        $script:unsafeLegacy=$true;$priorRegistered=$script:registered;$rejected=$false
        try { $null=Initialize-ODSPortalWindowsRuntime -SourceRoot . -Identity $migrated -ModelsDirectory 'D:\ODS Models' -Model $model } catch { $rejected=$true }
        Check ($rejected -and $script:registered -eq $priorRegistered) 'unsafe old credentials cannot be adopted or replace the inference task'
    } finally { $env:LOCALAPPDATA=$originalLocalAppData }
} finally {
    $prefix=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $fixture.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temporary directory' }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
