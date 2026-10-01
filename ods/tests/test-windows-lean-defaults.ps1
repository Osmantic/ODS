$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/installed-selection.ps1')
. (Join-Path $root 'installers/windows/lib/service-plan.ps1')

function Write-Phase { }
function Write-AI { }
function Write-AIWarn { }
function Write-InfoBox { }

$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods-windows-selection-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$script:menuAnswers = @()
function Read-Host {
    param([string]$Prompt)
    if ($script:menuAnswers.Count -eq 0) { return '' }
    $answer = $script:menuAnswers[0]
    $script:menuAnswers = @($script:menuAnswers | Select-Object -Skip 1)
    return $answer
}

function Set-InstalledFixture {
    param([string]$Path, [string[]]$Services)
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $Path '.env'), 'fixture')
    [IO.File]::WriteAllText((Join-Path $Path 'docker-compose.base.yml'), 'fixture')
    $flags = @('--env-file', '.env', '-f', 'docker-compose.base.yml')
    foreach ($service in $Services) {
        $flags += @('-f', "extensions/services/$service/compose.yaml")
    }
    [IO.File]::WriteAllText((Join-Path $Path '.compose-flags'), ($flags -join ' '))
}

function Invoke-Selection {
    param(
        [string]$Path,
        [bool]$Interactive = $false,
        [bool]$All = $false,
        [bool]$Hermes = $false,
        [bool]$Comfyui = $false,
        [bool]$Recommended = $false,
        [bool]$NoHermes = $false,
        [bool]$NoComfyui = $false,
        [bool]$NoRecommended = $false,
        [string]$MenuAnswer = ''
    )
    $script:menuAnswers = @($MenuAnswer)
    $installDir = $Path
    $nonInteractive = -not $Interactive
    $dryRun = $false
    $cloudMode = $false
    $selectedTier = '3'
    $tierConfig = @{ MaxContext = 65536; LlmModel = 'fixture'; TierName = 'fixture' }
    $gpuInfo = [PSCustomObject]@{ Backend = 'nvidia' }
    $voiceFlag = $false; $workflowsFlag = $false; $ragFlag = $false
    $recommendedFlag = $Recommended; $noRecommendedFlag = $NoRecommended
    $hermesFlag = $Hermes; $noHermesFlag = $NoHermes
    $openClawFlag = $false; $allFlag = $All
    $comfyuiFlag = $Comfyui; $noComfyuiFlag = $NoComfyui
    $langfuseFlag = $false; $noLangfuseFlag = $false
    . (Join-Path $root 'installers/windows/phases/03-features.ps1')
    return @{
        Voice = $enableVoice; Workflows = $enableWorkflows; Rag = $enableRag
        Recommended = $enableRecommended; Hermes = $enableHermes
        OpenClaw = $enableOpenClaw; Comfyui = $enableComfyui
        DeepResearch = $enableDeepResearch; PrivacyShield = $enablePrivacyShield
        Langfuse = $enableLangfuse
    }
}

try {
    $fresh = Join-Path $scratch 'fresh'
    New-Item -ItemType Directory -Path $fresh | Out-Null
    [IO.File]::WriteAllText((Join-Path $fresh 'docker-compose.base.yml'), 'source checkout')
    $freshChoice = Invoke-Selection -Path $fresh
    foreach ($name in $freshChoice.Keys) {
        if ($freshChoice[$name]) { throw "Fresh default enabled optional feature: $name" }
    }
    $freshEnter = Invoke-Selection -Path $fresh -Interactive $true
    foreach ($name in $freshEnter.Keys) {
        if ($freshEnter[$name]) { throw "Fresh Enter enabled optional feature: $name" }
    }
    $explicit = Invoke-Selection -Path $fresh -Hermes $true -Comfyui $true -Recommended $true
    if (-not $explicit.Hermes -or -not $explicit.Comfyui -or -not $explicit.Recommended) {
        throw 'Explicit CLI opt-ins were lost on a fresh noninteractive install'
    }
    $full = Invoke-Selection -Path $fresh -All $true
    if (-not $full.Langfuse -or -not $full.Comfyui -or -not $full.DeepResearch) {
        throw '-All did not select the full native Windows feature set'
    }

    $prior = Join-Path $scratch 'prior'
    Set-InstalledFixture -Path $prior -Services @('litellm','token-spy','whisper','tts','n8n',
        'qdrant','embeddings','hermes','hermes-proxy','comfyui','perplexica','privacy-shield','langfuse')
    $retained = Invoke-Selection -Path $prior
    foreach ($name in $retained.Keys) {
        if (-not $retained[$name] -and $name -ne 'OpenClaw') { throw "Rerun lost selected feature: $name" }
    }
    $keepEnter = Invoke-Selection -Path $prior -Interactive $true
    if (-not $keepEnter.Hermes -or -not $keepEnter.Comfyui -or -not $keepEnter.DeepResearch) {
        throw 'Interactive Enter did not keep installed choices'
    }
    $customEnter = Invoke-Selection -Path $prior -Interactive $true -MenuAnswer '3'
    if (-not $customEnter.Hermes -or -not $customEnter.Comfyui -or -not $customEnter.DeepResearch) {
        throw 'Custom menu Enter prompts did not keep installed choices'
    }
    $core = Invoke-Selection -Path $prior -Interactive $true -MenuAnswer '2'
    if ($core.Hermes -or $core.Comfyui -or $core.DeepResearch) {
        throw 'Explicit Core Only did not disable optional services'
    }
    $overridden = Invoke-Selection -Path $prior -All $true -NoHermes $true -NoComfyui $true -NoRecommended $true
    if ($overridden.Hermes -or $overridden.Comfyui -or $overridden.Recommended -or -not $overridden.DeepResearch) {
        throw 'Negative flags did not override -All and prior selection'
    }
    $cloudCore = Join-Path $scratch 'cloud-core'
    Set-InstalledFixture -Path $cloudCore -Services @('litellm')
    [IO.File]::WriteAllText((Join-Path $cloudCore '.env'), 'ODS_MODE=cloud')
    $cloudSelection = Get-ODSWindowsInstalledFeatureSelection -InstallDir $cloudCore
    if ($cloudSelection.Kind -ne 'preserved' -or $cloudSelection.Features.Recommended) {
        throw 'A required cloud gateway was mistaken for the optional Recommended bundle'
    }
    if ((Invoke-Selection -Path $cloudCore).Recommended) {
        throw 'Rerun expanded the cloud gateway into Recommended services'
    }
    $switchboardCore = Join-Path $scratch 'switchboard-core'
    Set-InstalledFixture -Path $switchboardCore -Services @('litellm')
    [IO.File]::WriteAllText((Join-Path $switchboardCore '.env'), "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`n")
    if ((Invoke-Selection -Path $switchboardCore).Recommended) {
        throw 'Rerun expanded the native Core switchboard gateway into Recommended services'
    }
    $partialLocalGateway = Join-Path $scratch 'partial-local-gateway'
    Set-InstalledFixture -Path $partialLocalGateway -Services @('litellm')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $partialLocalGateway).Kind -ne 'unknown') {
        throw 'A partial local Recommended selection was mistaken for cloud Core'
    }
    $partialRecommended = Join-Path $scratch 'partial-recommended'
    Set-InstalledFixture -Path $partialRecommended -Services @('token-spy')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $partialRecommended).Kind -ne 'unknown') {
        throw 'Token Spy without its gateway was accepted as a complete bundle'
    }

    $unknown = Join-Path $scratch 'unknown'
    Set-InstalledFixture -Path $unknown -Services @('hermes')
    [IO.File]::WriteAllText((Join-Path $unknown '.compose-flags'), '-f extensions/services/hermes/compose.yaml')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $unknown).Kind -ne 'unknown') {
        throw 'Incomplete installed Compose record was accepted'
    }
    try {
        Invoke-Selection -Path $unknown | Out-Null
        throw 'Noninteractive rerun silently reset an unknown prior selection'
    } catch {
        if ($_.Exception.Message -notmatch 'selection is unknown') { throw }
    }
    [IO.File]::WriteAllText((Join-Path $unknown '.compose-flags'),
        '-f docker-compose.base.yml -f extensions/services/hermes/compose.yaml')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $unknown).Kind -ne 'unknown') {
        throw 'Partial Hermes group would have been expanded silently'
    }
    Remove-Item -LiteralPath (Join-Path $unknown '.compose-flags') -Force
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $unknown).Kind -ne 'unknown') {
        throw 'Missing installed Compose record was accepted'
    }
    $partialRoot = Join-Path $scratch 'partial-root'
    New-Item -ItemType Directory -Path $partialRoot | Out-Null
    [IO.File]::WriteAllText((Join-Path $partialRoot '.env'), 'fixture')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $partialRoot).Kind -ne 'unknown') {
        throw 'Partial installed root was treated as a fresh install'
    }

    $plan = New-ODSWindowsServicePlan -EnableRecommended $false -EnableVoice $false `
        -EnableWorkflows $false -EnableRag $false -EnableHermes $false `
        -EnableOpenClaw $false -EnableComfyui $false -EnableDeepResearch $false `
        -EnablePrivacyShield $false -EnableLangfuse $false
    foreach ($id in @('searxng','token-spy','hermes','comfyui','perplexica','langfuse')) {
        if ($plan[$id].Enabled) { throw "Core plan selected $id" }
    }
    if (-not $plan['litellm'].Enabled) { throw 'Native Core lost its switchboard gateway' }
    $observePlan = New-ODSWindowsServicePlan -EnableRecommended $false -SwitchboardMode 'observe'
    if ($observePlan['litellm'].Enabled) { throw 'Observe mode selected an unnecessary gateway' }
    $cloudPlan = New-ODSWindowsServicePlan -EnableRecommended $false -CloudMode $true
    if (-not $cloudPlan['litellm'].Enabled -or $cloudPlan['token-spy'].Enabled -or $cloudPlan['searxng'].Enabled) {
        throw 'Cloud Core did not select only its required gateway'
    }
    $fullPlan = New-ODSWindowsServicePlan -EnableRecommended $full.Recommended -EnableVoice $full.Voice `
        -EnableWorkflows $full.Workflows -EnableRag $full.Rag -EnableHermes $full.Hermes `
        -EnableOpenClaw $full.OpenClaw -EnableComfyui $full.Comfyui `
        -EnableDeepResearch $full.DeepResearch -EnablePrivacyShield $full.PrivacyShield `
        -EnableLangfuse $full.Langfuse
    if (-not $fullPlan['langfuse'].Enabled) { throw '-All did not include Langfuse in the service plan' }
    Write-Output 'PASS: Windows fresh defaults, legacy selection, overrides and fail-closed records'
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing cleanup outside the temporary root'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
