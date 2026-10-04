# A retained Windows -> WSL install must preserve the committed native model.
# All task, process, network and WSL boundaries are fixture-owned.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1')

$root = Join-Path ([IO.Path]::GetTempPath()) ('ods-retained-model-' + [guid]::NewGuid().ToString('N'))
$runtime = Join-Path $root 'portal-runtime'
$null = New-Item -ItemType Directory -Force -Path $runtime
$planPath = Join-Path $runtime 'runtime.json'
[IO.File]::WriteAllText($planPath, '{"GgufFile":"Qwen3.5-9B-Q4_K_M.gguf"}')
$script:checks = 0
$script:calls = [Collections.Generic.List[string]]::new()
$script:taskPresent = $true
$script:taskState = 'Ready'
$script:listenerOccupied = $false
$script:wslRootAbsent = $false
$script:observationFailsUntilResume = $false
$script:observedModel = 'Qwen3.5-9B-Q4_K_M'
$script:committedModel = 'Qwen3.5-9B-Q4_K_M'
$script:committedDigest = 'a' * 64
$script:currentDigest = 'a' * 64
$script:configurationDigest = 'a' * 64
$script:driftAfterStart = $false
$script:invalidVersion = $false
$script:observationFails = $false
$script:observationChangesOnSecondRead = $false
$script:observationReads = 0
$script:journalMissing = $false
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Invoke-ODSPortalWsl([string[]]$Arguments) {
    $script:journalWslArguments = $Arguments
    return [pscustomobject]@{ Code=0; Output='{"status":"missing"}' }
}
$missingJournal = Get-ODSPortalWslCommittedModel 'Ubuntu-24.04' '/home/owner/ods'
Check ($missingJournal.status -eq 'missing' -and
    ($script:journalWslArguments -join ' ') -notmatch '--user root' -and
    ($script:journalWslArguments -join ' ') -match '--distribution Ubuntu-24.04' -and
    $script:journalWslArguments[-1] -eq '/home/owner/ods') 'journal reader uses the bound WSL installation user'
function Get-ODSPortalStateDir { return $root }
function Get-ODSPortalLemonadeTask { if ($script:taskPresent) { return [pscustomobject]@{ TaskName='owned' } }; return $null }
function Get-ODSPortalManagedConfiguration($Request) {
    $script:calls.Add('configuration')
    if ($Request.distro -ne 'Ubuntu-24.04' -or $Request.installDir -ne '/home/owner/ods') { throw 'wrong binding' }
    return [pscustomobject]@{
        Task = [pscustomobject]@{ State=$script:taskState }
        TaskName = 'owned'
        Plan = [pscustomobject]@{ Port=13305; GgufFile='Qwen3.5-9B-Q4_K_M.gguf';
            ContextSize=65536; ExecutablePath='C:\owned\lemonade.exe' }
        PlanPath = $planPath; PlanDigest = $script:configurationDigest
        ReadyPath = Join-Path $runtime 'ready.json'
    }
}
function Assert-ODSPortalControlModel($Plan) { $script:calls.Add('model-file') }
function Get-ODSPortalManagedObservation($Configuration) {
    $script:calls.Add('observation')
    if ($script:observationFails -or $script:observationFailsUntilResume) { throw 'native runtime unavailable' }
    $script:observationReads++
    if ($script:observationChangesOnSecondRead -and $script:observationReads -eq 2) {
        return [ordered]@{ status='verified'; modelId='Qwen3.6-35B-A3B-UD-Q4_K_M'; contextLength=131072 }
    }
    return [ordered]@{ status='verified'; modelId=$script:observedModel; contextLength=65536 }
}
function Resolve-ODSLemonadeModelId { param($Port, $GgufFile)
    $script:calls.Add('model-id')
    if ($Port -ne 13305 -or $GgufFile -ne 'Qwen3.5-9B-Q4_K_M.gguf') { throw 'wrong native model query' }
    return 'Qwen3.5-9B-Q4_K_M'
}
function Get-ODSPortalWslCommittedModel([string]$Distro, [string]$InstallDir) {
    $script:calls.Add('journal')
    if ($script:journalMissing) { return [pscustomobject]@{ status='missing' } }
    return [pscustomobject]@{ status='completed'; modelId=$script:committedModel;
        contextLength=65536; windowsPlanDigest=$script:committedDigest }
}
function Get-ODSPortalPlanDigest([string]$Path) { return $script:currentDigest }
function Get-ODSPortalTaskEngineId { return 0 }
function Test-ODSPortalWslInstallRootAbsent { return $script:wslRootAbsent }
function Get-NetTCPConnection { if ($script:listenerOccupied) { return [pscustomobject]@{ LocalPort=13305 } } }
function Test-ODSPortalPortBindable { return (-not $script:listenerOccupied) }
function Invoke-ODSPortalModelControl($Request) {
    $script:calls.Add('resume')
    if ($Request.action -cne 'start' -or $Request.expectedPlanDigest -cne $script:currentDigest) {
        throw 'resume did not use the saved plan'
    }
    $script:taskState = 'Ready'
    $script:observationFailsUntilResume = $false
    if ($script:driftAfterStart) { $script:configurationDigest = 'b' * 64 }
    [IO.File]::WriteAllText((Join-Path $runtime 'intent.json'), '{"State":"running"}')
    return [pscustomobject]@{ running=$true; planDigest=$script:currentDigest }
}
function Get-ODSLemonadeExecutableVersion([string]$Path) {
    if ($script:invalidVersion) { return [version]'1.0.0' }
    return [version]'10.7.0'
}
function Assert-ODSPortalLemonadeVersion([version]$Version) {
    $script:calls.Add('version')
    if ($Version -lt [version]'10.7.0') { throw 'unsupported Lemonade version' }
}
function Get-ODSPortalAmdPlan([string]$SourceRoot) {
    return [pscustomobject]@{ GpuName='Strix Halo'; VramMB=98304; LinuxTier='4';
        Model='hardware-recommended-35B'; GgufFile='Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' }
}
function Initialize-ODSPortalAmdLemonade { $script:calls.Add('native-reinitialize'); return @('--lemonade-model', '35B') }
function Write-ODSPortalStage { param($Step, $Title, $Detail) $null = @($Step, $Title, $Detail) }

try {
    $options = @{ InstallDir='/home/owner/ods'; Tier='' }
    $argsOut = @(Add-ODSPortalAmdArguments @('--pixel') $options 'C:\source' $true 'Ubuntu-24.04')
    Check (($argsOut -join ' ') -match '--lemonade-model Qwen3.5-9B-Q4_K_M' -and
        ($argsOut -join ' ') -notmatch '35B') 'retained install uses committed 9B instead of hardware recommendation 35B'
    Check (-not $script:calls.Contains('native-reinitialize')) 'healthy bound runtime is never stopped or re-registered'
    Check (($argsOut -join ' ') -match '--lemonade-gpu-name Strix Halo' -and
        ($argsOut -join ' ') -match '--tier 4') 'retained route keeps hardware display and Linux tier'

    $options['Tier'] = '3'
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @('--tier','3') $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'cannot change that model during setup' -and
        -not $script:calls.Contains('native-reinitialize')) 'explicit tier cannot silently override a retained Dashboard choice'
    $options['Tier'] = ''

    $script:committedDigest = 'b' * 64
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'differs from the last protected Pixel model choice' -and
        -not $script:calls.Contains('native-reinitialize')) 'journal plan digest drift refuses before native mutation'

    $script:committedDigest = 'a' * 64
    $script:committedModel = 'Qwen3.6-35B-A3B-UD-Q4_K_M'
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'differs from the last protected Pixel model choice' -and
        -not $script:calls.Contains('native-reinitialize')) 'journal model drift refuses before native mutation'

    $script:committedModel = 'Qwen3.5-9B-Q4_K_M'
    $script:observationFails = $true
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'native runtime unavailable' -and
        -not $script:calls.Contains('native-reinitialize')) 'unavailable native observation never falls back to 35B'
    $script:observationFails = $false

    $script:currentDigest = 'b' * 64
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'plan changed during verification' -and
        -not $script:calls.Contains('native-reinitialize')) 'racing plan replacement refuses before native mutation'
    $script:currentDigest = 'a' * 64

    $script:observationChangesOnSecondRead = $true
    $script:observationReads = 0
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'model changed during verification' -and
        -not $script:calls.Contains('native-reinitialize')) 'racing native model switch refuses before mutation'
    $script:observationChangesOnSecondRead = $false

    $script:journalMissing = $true
    $argsOut = @(Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04')
    Check (($argsOut -join ' ') -match '--lemonade-model Qwen3.5-9B-Q4_K_M') 'older bound runtime without model switch journal retains its healthy model'
    $script:journalMissing = $false

    # Official uninstall leaves this exact private plan and same-user task,
    # but disarms the task and removes runtime readiness. Setup must restart
    # the saved 9B model, not silently select the catalog's 35B replacement.
    [IO.File]::WriteAllText((Join-Path $runtime 'intent.json'), '{"State":"stopped"}')
    $script:taskState = 'Disabled'
    $script:wslRootAbsent = $true
    $script:observationFailsUntilResume = $true
    $script:journalMissing = $true
    $script:calls.Clear()
    $argsOut = @(Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04')
    Check (($argsOut -join ' ') -match '--lemonade-model Qwen3.5-9B-Q4_K_M' -and
        $script:calls.Contains('resume') -and -not $script:calls.Contains('native-reinitialize')) 'officially stopped runtime resumes its selected model and saved plan'
    Check ($script:calls.IndexOf('resume') -lt $script:calls.IndexOf('observation')) 'stopped runtime is resumed before live readiness is required'

    $script:taskState = 'Disabled'
    $script:observationFailsUntilResume = $true
    [IO.File]::WriteAllText((Join-Path $runtime 'intent.json'), '{"State":"stopped"}')
    $options['Tier'] = '3'
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'cannot change that model during setup' -and
        -not $script:calls.Contains('resume')) 'explicit tier is refused before a stopped model task can restart'
    $options['Tier'] = ''

    $script:invalidVersion = $true
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'unsupported Lemonade version' -and
        -not $script:calls.Contains('resume')) 'unsupported native executable refuses before stopped-task restart'
    $script:invalidVersion = $false

    $script:currentDigest = 'b' * 64
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'plan changed before restart' -and
        -not $script:calls.Contains('resume')) 'racing private plan refuses before stopped-task restart'
    $script:currentDigest = 'a' * 64

    $script:driftAfterStart = $true
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'plan changed after restart' -and
        $script:calls.Contains('resume')) 'post-start plan reread must match the original digest'
    $script:driftAfterStart = $false
    $script:configurationDigest = 'a' * 64

    $script:taskState = 'Disabled'
    $script:listenerOccupied = $true
    $script:observationFailsUntilResume = $true
    [IO.File]::WriteAllText((Join-Path $runtime 'intent.json'), '{"State":"stopped"}')
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'active or ambiguous runtime' -and
        -not $script:calls.Contains('resume') -and -not $script:calls.Contains('native-reinitialize')) 'foreign listener blocks stopped-runtime restart before mutation'
    $script:listenerOccupied = $false

    $script:wslRootAbsent = $false
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'deliberately stopped' -and
        -not $script:calls.Contains('resume')) 'existing WSL install cannot auto-restart a deliberately stopped model'
    $script:wslRootAbsent = $true

    $script:committedDigest = 'b' * 64
    $script:journalMissing = $false
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'protected WSL model journal still exists' -and
        -not $script:calls.Contains('resume')) 'protected model digest mismatch blocks stopped-runtime restart'
    $script:committedDigest = 'a' * 64
    Remove-Item -LiteralPath (Join-Path $runtime 'intent.json') -Force

    Remove-Item -LiteralPath $planPath -Force
    $script:calls.Clear()
    $errorText = ''
    try { $null = Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04' }
    catch { $errorText = $_.Exception.Message }
    Check ($errorText -match 'task and private plan are incomplete' -and
        -not $script:calls.Contains('native-reinitialize')) 'orphaned task refuses without inventing a new model'

    $script:taskPresent = $false
    $script:calls.Clear()
    $argsOut = @(Add-ODSPortalAmdArguments @() $options 'C:\source' $true 'Ubuntu-24.04')
    Check ($script:calls.Contains('native-reinitialize') -and ($argsOut -join ' ') -match '--lemonade-model 35B') 'fresh install still selects catalog-recommended model'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks retained Windows model contracts."
