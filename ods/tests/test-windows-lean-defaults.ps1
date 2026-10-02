$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/installed-selection.ps1')
. (Join-Path $root 'installers/windows/lib/service-plan.ps1')
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')

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
    $voiceFlag = $false; $noVoiceFlag = $false; $workflowsFlag = $false; $ragFlag = $false
    $recommendedFlag = $Recommended; $noRecommendedFlag = $NoRecommended
    $hermesFlag = $Hermes; $noHermesFlag = $NoHermes
    $openClawFlag = $false; $allFlag = $All
    $comfyuiFlag = $Comfyui; $noComfyuiFlag = $NoComfyui
    $langfuseFlag = $false; $noLangfuseFlag = $false
    . (Join-Path $root 'installers/windows/phases/03-features.ps1')
    return @{
        Voice = $enableVoice; Whisper = $enableWhisper; Tts = $enableTts
        Workflows = $enableWorkflows; Rag = $enableRag
        Recommended = $enableRecommended; Hermes = $enableHermes
        HermesProxy = $enableHermesProxy
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
    if (-not $explicit.Hermes -or -not $explicit.HermesProxy -or
        -not $explicit.Comfyui -or -not $explicit.Recommended) {
        throw 'Explicit CLI opt-ins were lost on a fresh noninteractive install'
    }
    $full = Invoke-Selection -Path $fresh -All $true
    if (-not $full.Langfuse -or -not $full.Comfyui -or -not $full.DeepResearch -or
        -not $full.Hermes -or -not $full.HermesProxy) {
        throw '-All did not select the full native Windows feature set'
    }
    if (-not $full.Whisper -or -not $full.Tts) {
        throw '-All did not select both voice services'
    }

    # Dashboard Library changes service filenames and invalidates the flags
    # cache. A completed managed install must retain each voice service on a
    # later noninteractive rerun, including when only one is selected.
    foreach ($selectedVoice in @('whisper', 'tts')) {
        $voiceRoot = Join-Path $scratch "library-$selectedVoice"
        Set-InstalledFixture -Path $voiceRoot -Services @('litellm')
        [IO.File]::WriteAllText((Join-Path $voiceRoot '.env'),
            "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`nODS_WINDOWS_RECOMMENDED_SELECTED=false`n")
        foreach ($service in @('whisper', 'tts')) {
            $serviceDir = Join-Path $voiceRoot "extensions/services/$service"
            New-Item -ItemType Directory -Path $serviceDir -Force | Out-Null
            $suffix = if ($service -eq $selectedVoice) { '' } else { '.disabled' }
            [IO.File]::WriteAllText((Join-Path $serviceDir "compose.yaml$suffix"), 'services: {}')
        }
        Write-ODSWindowsManagedComposeSelectionMarker -InstallDir $voiceRoot
        Remove-Item -LiteralPath (Join-Path $voiceRoot '.compose-flags') -Force
        $readVoice = Get-ODSWindowsInstalledFeatureSelection -InstallDir $voiceRoot
        $rerunVoice = Invoke-Selection -Path $voiceRoot
        if ($readVoice.Kind -ne 'preserved' -or
            $rerunVoice.Whisper -ne ($selectedVoice -eq 'whisper') -or
            $rerunVoice.Tts -ne ($selectedVoice -eq 'tts')) {
            throw "Library $selectedVoice add-back was not retained independently"
        }
        $voicePlan = New-ODSWindowsServicePlan -EnableWhisper $rerunVoice.Whisper `
            -EnableTts $rerunVoice.Tts
        if ($voicePlan['whisper'].Enabled -ne ($selectedVoice -eq 'whisper') -or
            $voicePlan['tts'].Enabled -ne ($selectedVoice -eq 'tts')) {
            throw "Library $selectedVoice add-back expanded to both voice services"
        }
        [IO.File]::WriteAllText((Join-Path $voiceRoot "extensions/services/$selectedVoice/compose.yaml.disabled"), 'services: {}')
        if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $voiceRoot).Kind -ne 'unknown') {
            throw "Conflicting $selectedVoice Compose filenames were accepted"
        }
    }

    $hermesOnlyRoot = Join-Path $scratch 'library-hermes-only'
    Set-InstalledFixture -Path $hermesOnlyRoot -Services @('litellm','hermes','hermes-proxy')
    [IO.File]::WriteAllText((Join-Path $hermesOnlyRoot '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`nODS_WINDOWS_RECOMMENDED_SELECTED=false`n")
    foreach ($service in @('hermes','hermes-proxy','whisper','tts')) {
        $serviceDir = Join-Path $hermesOnlyRoot "extensions/services/$service"
        New-Item -ItemType Directory -Path $serviceDir -Force | Out-Null
        $suffix = if ($service -eq 'hermes') { '' } else { '.disabled' }
        [IO.File]::WriteAllText((Join-Path $serviceDir "compose.yaml$suffix"), 'services: {}')
    }
    Write-ODSWindowsManagedComposeSelectionMarker -InstallDir $hermesOnlyRoot
    Remove-Item -LiteralPath (Join-Path $hermesOnlyRoot '.compose-flags') -Force
    $hermesData = Join-Path $hermesOnlyRoot 'data/hermes/retained.txt'
    New-Item -ItemType Directory -Path (Split-Path -Parent $hermesData) -Force | Out-Null
    [IO.File]::WriteAllText($hermesData, 'keep')
    $hermesOnlySelection = Get-ODSWindowsInstalledFeatureSelection -InstallDir $hermesOnlyRoot
    $hermesOnlyRerun = Invoke-Selection -Path $hermesOnlyRoot
    if ($hermesOnlySelection.Kind -ne 'preserved' -or -not $hermesOnlyRerun.Hermes -or
        $hermesOnlyRerun.HermesProxy -or
        (Get-Content -LiteralPath $hermesData -Raw) -cne 'keep') {
        throw 'Hermes-only Library selection or owner data was lost on rerun'
    }
    $hermesOnlyPlan = New-ODSWindowsServicePlan -EnableHermes $hermesOnlyRerun.Hermes `
        -EnableHermesProxy $hermesOnlyRerun.HermesProxy
    if (-not $hermesOnlyPlan['hermes'].Enabled -or $hermesOnlyPlan['hermes-proxy'].Enabled) {
        throw 'Hermes-only Library choice re-enabled its proxy'
    }
    $proxyDisabled = Join-Path $hermesOnlyRoot 'extensions/services/hermes-proxy/compose.yaml.disabled'
    $proxyMissingFixture = "$proxyDisabled.missing-fixture"
    Move-Item -LiteralPath $proxyDisabled -Destination $proxyMissingFixture
    try {
        if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $hermesOnlyRoot).Kind -ne 'unknown') {
            throw 'Managed Hermes selection accepted a missing proxy marker'
        }
    } finally {
        Move-Item -LiteralPath $proxyMissingFixture -Destination $proxyDisabled
    }
    $hermesOnlyCustom = Invoke-Selection -Path $hermesOnlyRoot -Interactive $true -MenuAnswer '3'
    if (-not $hermesOnlyCustom.Hermes -or $hermesOnlyCustom.HermesProxy) {
        throw 'Custom menu default lost Hermes-only Library choice'
    }
    $hermesExplicit = Invoke-Selection -Path $hermesOnlyRoot -Hermes $true
    $hermesDisabled = Invoke-Selection -Path $hermesOnlyRoot -NoHermes $true
    if (-not $hermesExplicit.Hermes -or -not $hermesExplicit.HermesProxy -or
        $hermesDisabled.Hermes -or $hermesDisabled.HermesProxy) {
        throw 'Explicit Hermes CLI choices did not update the proxy pair'
    }
    Move-Item -LiteralPath (Join-Path $hermesOnlyRoot 'extensions/services/hermes/compose.yaml') `
        -Destination (Join-Path $hermesOnlyRoot 'extensions/services/hermes/compose.yaml.disabled')
    Move-Item -LiteralPath (Join-Path $hermesOnlyRoot 'extensions/services/hermes-proxy/compose.yaml.disabled') `
        -Destination (Join-Path $hermesOnlyRoot 'extensions/services/hermes-proxy/compose.yaml')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $hermesOnlyRoot).Kind -ne 'unknown') {
        throw 'Proxy-only installed selection was accepted without Hermes'
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
    if ($overridden.Hermes -or $overridden.HermesProxy -or $overridden.Comfyui -or
        $overridden.Recommended -or -not $overridden.DeepResearch) {
        throw 'Negative flags did not override -All and prior selection'
    }
    $cloudCore = Join-Path $scratch 'cloud-core'
    Set-InstalledFixture -Path $cloudCore -Services @('litellm')
    [IO.File]::WriteAllText((Join-Path $cloudCore '.env'),
        "ODS_MODE=cloud`nODS_WINDOWS_RECOMMENDED_SELECTED=false`n")
    $cloudSelection = Get-ODSWindowsInstalledFeatureSelection -InstallDir $cloudCore
    if ($cloudSelection.Kind -ne 'preserved' -or $cloudSelection.Features.Recommended) {
        throw 'A required cloud gateway was mistaken for the optional Recommended bundle'
    }
    [IO.File]::WriteAllText((Join-Path $cloudCore '.env'), 'ODS_MODE=cloud')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $cloudCore).Kind -ne 'intent-required') {
        throw 'Ambiguous legacy cloud LiteLLM-only selection did not require intent'
    }
    try {
        Invoke-Selection -Path $cloudCore | Out-Null
        throw 'Legacy cloud selection silently chose Recommended'
    } catch {
        if ($_.Exception.Message -notmatch 'explicit Recommended choice') { throw }
    }
    if ((Invoke-Selection -Path $cloudCore -NoRecommended $true).Recommended) {
        throw 'Explicit cloud Core recovery enabled Recommended services'
    }
    if (-not (Invoke-Selection -Path $cloudCore -Recommended $true).Recommended) {
        throw 'Explicit cloud Recommended recovery did not enable its bundle'
    }
    [IO.File]::WriteAllText((Join-Path $cloudCore '.env'),
        "ODS_MODE=cloud`nODS_WINDOWS_RECOMMENDED_SELECTED=false`n")
    if ((Invoke-Selection -Path $cloudCore).Recommended) {
        throw 'Rerun expanded the cloud gateway into Recommended services'
    }
    $switchboardCore = Join-Path $scratch 'switchboard-core'
    Set-InstalledFixture -Path $switchboardCore -Services @('litellm')
    [IO.File]::WriteAllText((Join-Path $switchboardCore '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`nODS_WINDOWS_RECOMMENDED_SELECTED=false`n")
    if ((Invoke-Selection -Path $switchboardCore).Recommended) {
        throw 'Rerun expanded the native Core switchboard gateway into Recommended services'
    }
    [IO.File]::WriteAllText((Join-Path $switchboardCore '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`nODS_WINDOWS_RECOMMENDED_SELECTED=false`nODS_WINDOWS_RECOMMENDED_SELECTED=false`n")
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $switchboardCore).Kind -ne 'unknown') {
        throw 'Duplicate Recommended intent marker was accepted'
    }
    [IO.File]::WriteAllText((Join-Path $switchboardCore '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`nODS_WINDOWS_RECOMMENDED_SELECTED=maybe`n")
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $switchboardCore).Kind -ne 'unknown') {
        throw 'Invalid Recommended intent marker was accepted'
    }
    [IO.File]::WriteAllText((Join-Path $switchboardCore '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`nODS_WINDOWS_RECOMMENDED_SELECTED=true`n")
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $switchboardCore).Kind -ne 'unknown') {
        throw 'Incomplete native Recommended selection was accepted as Core'
    }
    [IO.File]::WriteAllText((Join-Path $switchboardCore '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`n")
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $switchboardCore).Kind -ne 'intent-required') {
        throw 'Ambiguous legacy LiteLLM-only selection did not require intent'
    }
    if ((Invoke-Selection -Path $switchboardCore -NoRecommended $true).Recommended) {
        throw 'Native legacy Core recovery enabled Recommended services'
    }
    $legacyMixed = Join-Path $scratch 'legacy-mixed'
    Set-InstalledFixture -Path $legacyMixed -Services @('litellm', 'n8n', 'hermes', 'hermes-proxy')
    [IO.File]::WriteAllText((Join-Path $legacyMixed '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`n")
    $mixedCore = Invoke-Selection -Path $legacyMixed -NoRecommended $true
    $mixedRecommended = Invoke-Selection -Path $legacyMixed -Recommended $true
    if (-not $mixedCore.Workflows -or -not $mixedCore.Hermes -or $mixedCore.Recommended -or
        -not $mixedRecommended.Workflows -or -not $mixedRecommended.Hermes -or
        -not $mixedRecommended.Recommended) {
        throw 'Explicit legacy recovery discarded other selected services or ignored Recommended intent'
    }
    try {
        Invoke-Selection -Path $legacyMixed -Recommended $true -NoRecommended $true | Out-Null
        throw 'Contradictory legacy Recommended choices were accepted'
    } catch {
        if ($_.Exception.Message -notmatch 'not both') { throw }
    }
    if (-not (Invoke-Selection -Path $legacyMixed -All $true).Langfuse) {
        throw 'Explicit Full Stack legacy recovery did not enable its selected services'
    }
    [IO.File]::WriteAllText((Join-Path $switchboardCore '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=observe`nOPEN_WEBUI_LLM_BASE_URL=http://litellm:4000`n")
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $switchboardCore).Kind -ne 'unknown') {
        throw 'Ambiguous switchboard-to-observe gateway selection did not fail closed'
    }
    if ((Invoke-Selection -Path $switchboardCore -Interactive $true -MenuAnswer '2').Recommended) {
        throw 'Explicit Core choice during switchboard-to-observe transition enabled Recommended services'
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
    $missingRecommended = Join-Path $scratch 'missing-recommended'
    Set-InstalledFixture -Path $missingRecommended -Services @()
    [IO.File]::WriteAllText((Join-Path $missingRecommended '.env'),
        "ODS_MODE=local`nODS_WINDOWS_RECOMMENDED_SELECTED=true`n")
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $missingRecommended).Kind -ne 'unknown') {
        throw 'Recommended intent with both fragments missing was accepted as Core'
    }

    # The CLI/Library add-back path updates Compose flags after installation.
    # Its Token Spy toggle must update the intent marker without touching
    # unrelated .env values, or the next installer run would reject valid
    # add-back and disable choices as partial writes.
    $cliText = Get-Content -LiteralPath (Join-Path $root 'installers/windows/ods.ps1') -Raw
    $cliStart = $cliText.IndexOf('function Update-ComposeFlags {')
    $cliEnd = $cliText.IndexOf("`nfunction Get-ExtensionServiceDir", $cliStart)
    if ($cliStart -lt 0 -or $cliEnd -lt 0) { throw 'Could not load the production CLI Compose flag updater' }
    Invoke-Expression $cliText.Substring($cliStart, $cliEnd - $cliStart)
    $libraryRecommended = Join-Path $scratch 'library-recommended'
    Set-InstalledFixture -Path $libraryRecommended -Services @('litellm')
    $ownerData = "preserve-$([char]0x2713)"
    [IO.File]::WriteAllText((Join-Path $libraryRecommended '.env'),
        "ODS_MODE=local`nODS_MODEL_SWITCHBOARD=enabled`nODS_WINDOWS_RECOMMENDED_SELECTED=false`nOWNER_DATA=$ownerData`n")
    $InstallDir = $libraryRecommended
    $tokenSpyDir = Join-Path $libraryRecommended 'extensions/services/token-spy'
    New-Item -ItemType Directory -Path $tokenSpyDir -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $tokenSpyDir 'compose.yaml'), 'services: {}')
    Update-ComposeFlags -ServiceId 'token-spy' -Action 'enable'
    $afterAdd = Get-ODSWindowsInstalledFeatureSelection -InstallDir $libraryRecommended
    $addedEnv = Get-Content -LiteralPath (Join-Path $libraryRecommended '.env') -Encoding UTF8 -Raw
    if ($afterAdd.Kind -ne 'preserved' -or -not $afterAdd.Features.Recommended -or
        $addedEnv -notmatch '(?m)^ODS_WINDOWS_RECOMMENDED_SELECTED=true\s*$' -or
        -not $addedEnv.Contains("OWNER_DATA=$ownerData")) {
        throw 'Library Token Spy add-back did not preserve the Recommended choice and owner data'
    }
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $acl = Get-Acl -LiteralPath (Join-Path $libraryRecommended '.env')
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 1 -or
            $rules[0].IdentityReference -ne $sid -or $rules[0].IsInherited -or
            $rules[0].FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {
            throw 'Library Token Spy add-back published a credential-bearing .env with a nonprivate ACL'
        }
    }
    Update-ComposeFlags -ServiceId 'token-spy' -Action 'disable'
    $afterDisable = Get-ODSWindowsInstalledFeatureSelection -InstallDir $libraryRecommended
    $disabledEnv = Get-Content -LiteralPath (Join-Path $libraryRecommended '.env') -Encoding UTF8 -Raw
    if ($afterDisable.Kind -ne 'preserved' -or $afterDisable.Features.Recommended -or
        $disabledEnv -notmatch '(?m)^ODS_WINDOWS_RECOMMENDED_SELECTED=false\s*$' -or
        -not $disabledEnv.Contains("OWNER_DATA=$ownerData")) {
        throw 'Library Token Spy disable did not preserve Core and owner data'
    }
    $flagsBeforeFailure = Get-Content -LiteralPath (Join-Path $libraryRecommended '.compose-flags') -Raw
    $envBeforeFailure = Get-Content -LiteralPath (Join-Path $libraryRecommended '.env') -Encoding UTF8 -Raw
    $privateWriter = ${function:Write-ODSPrivateEnvFile}
    try {
        function Write-ODSPrivateEnvFile { param($Path, $Content); throw 'Injected private publication failure' }
        try {
            Update-ComposeFlags -ServiceId 'token-spy' -Action 'enable'
            throw 'Token Spy toggle ignored a private publication failure'
        } catch {
            if ($_.Exception.Message -notmatch 'Injected private publication failure') { throw }
        }
    } finally {
        Set-Item -Path Function:Write-ODSPrivateEnvFile -Value $privateWriter
    }
    if ((Get-Content -LiteralPath (Join-Path $libraryRecommended '.compose-flags') -Raw) -cne $flagsBeforeFailure -or
        (Get-Content -LiteralPath (Join-Path $libraryRecommended '.env') -Encoding UTF8 -Raw) -cne $envBeforeFailure) {
        throw 'Failed private publication did not restore Compose flags and preserve .env'
    }
    [IO.File]::WriteAllText((Join-Path $libraryRecommended '.env'),
        $envBeforeFailure.Replace('ODS_WINDOWS_RECOMMENDED_SELECTED=false', 'ODS_WINDOWS_RECOMMENDED_SELECTED=true'))
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $libraryRecommended).Kind -ne 'unknown') {
        throw 'Interrupted marker publication was accepted as a complete service selection'
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
    $legacyHermesOnly = Get-ODSWindowsInstalledFeatureSelection -InstallDir $unknown
    $legacyHermesRerun = Invoke-Selection -Path $unknown
    if ($legacyHermesOnly.Kind -ne 'preserved' -or -not $legacyHermesRerun.Hermes -or
        $legacyHermesRerun.HermesProxy) {
        throw 'Legacy Hermes-only Compose selection was expanded or lost'
    }
    [IO.File]::WriteAllText((Join-Path $unknown '.compose-flags'),
        '-f docker-compose.base.yml -f extensions/services/hermes-proxy/compose.yaml')
    if ((Get-ODSWindowsInstalledFeatureSelection -InstallDir $unknown).Kind -ne 'unknown') {
        throw 'Legacy proxy-only Compose selection was accepted without Hermes'
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
    $amdHermesPlan = New-ODSWindowsServicePlan -EnableRecommended $false -SwitchboardMode 'observe' `
        -UseLemonade $true -EnableHermes $true
    $amdResearchPlan = New-ODSWindowsServicePlan -EnableRecommended $false -SwitchboardMode 'observe' `
        -UseLemonade $true -EnableDeepResearch $true
    if (-not $amdHermesPlan['litellm'].Enabled -or -not $amdResearchPlan['litellm'].Enabled) {
        throw 'Opted-in AMD observe consumers lost their LiteLLM provider'
    }
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
