$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $root "installers\windows\lib\service-plan.ps1")

function Assert-Selection {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$scratch = Join-Path ([IO.Path]::GetTempPath()) ("ods-voice-selection-" + [guid]::NewGuid().ToString("N"))
$installDir = Join-Path $scratch "install"
$whisperDir = Join-Path $installDir "extensions\services\whisper"
$ttsDir = Join-Path $installDir "extensions\services\tts"
$whisperActive = Join-Path $whisperDir "compose.yaml"
$ttsActive = Join-Path $ttsDir "compose.yaml"
try {
    New-Item -ItemType Directory -Path $whisperDir, $ttsDir -Force | Out-Null
    $fresh = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false
    Assert-Selection (-not $fresh.Whisper -and -not $fresh.Tts) "Fresh Core voice choice changed"
    $freshFull = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true
    Assert-Selection ($freshFull.Whisper -and $freshFull.Tts) "Fresh Full voice choice changed"
    $customWhisper = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir `
        -ComputedWhisper $true -ComputedTts $false -MenuExplicit $true
    Assert-Selection ($customWhisper.Whisper -and -not $customWhisper.Tts) `
        "Custom Whisper-only choice was replaced by paired voice defaults"
    $customTts = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir `
        -ComputedWhisper $false -ComputedTts $true -MenuExplicit $true
    Assert-Selection (-not $customTts.Whisper -and $customTts.Tts) `
        "Custom Kokoro-only choice was replaced by paired voice defaults"
    $legacyOnPlan = New-ODSWindowsServicePlan -EnableVoice $true
    $legacyOffPlan = New-ODSWindowsServicePlan -EnableVoice $false
    Assert-Selection ($legacyOnPlan["whisper"].Enabled -and $legacyOnPlan["tts"].Enabled) `
        "Legacy service-plan voice enable stopped selecting both services"
    Assert-Selection (-not $legacyOffPlan["whisper"].Enabled -and -not $legacyOffPlan["tts"].Enabled) `
        "Legacy service-plan voice disable unexpectedly selected a service"
    Set-Content -LiteralPath (Join-Path $installDir ".env") -Value "ODS_MODE=local"
    $legacyFlags = Join-Path $installDir ".compose-flags"
    Set-Content -LiteralPath $legacyFlags -Value "--env-file .env -f docker-compose.base.yml -f extensions/services/whisper/compose.yaml"
    $legacyWhisperOnly = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false
    Assert-Selection ($legacyWhisperOnly.Whisper -and -not $legacyWhisperOnly.Tts) `
        "Legacy installed Compose flags lost a Whisper-only selection"
    Remove-Item -LiteralPath $legacyFlags -Force
    $dataDir = Join-Path $installDir "data\voice"
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $sentinel = Join-Path $dataDir "keep.txt"
    Set-Content -LiteralPath $sentinel -Value "retain voice data"

    foreach ($state in @(
        @{ Whisper = $false; Tts = $false },
        @{ Whisper = $true; Tts = $false },
        @{ Whisper = $false; Tts = $true },
        @{ Whisper = $true; Tts = $true }
    )) {
        foreach ($service in @("whisper", "tts")) {
            $active = if ($service -eq "whisper") { $whisperActive } else { $ttsActive }
            $selected = if ($service -eq "whisper") { $state.Whisper } else { $state.Tts }
            Remove-Item -LiteralPath $active, "$active.disabled" -Force -ErrorAction SilentlyContinue
            $marker = if ($selected) { $active } else { "$active.disabled" }
            Set-Content -LiteralPath $marker -Value "services: {}"
        }

        $resolved = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true
        Assert-Selection (($resolved.Whisper -eq $state.Whisper) -and ($resolved.Tts -eq $state.Tts)) `
            "Rerun lost independent voice selection"
        $plan = New-ODSWindowsServicePlan -EnableVoice ($resolved.Whisper -or $resolved.Tts) `
            -EnableWhisper $resolved.Whisper -EnableTts $resolved.Tts
        Assert-Selection (($plan["whisper"].Enabled -eq $state.Whisper) -and
            ($plan["tts"].Enabled -eq $state.Tts)) "Service plan re-coupled voice choices"

        # Robocopy restores the source's active fragment beside a disabled
        # marker. The pre-copy decision must win during Compose sync.
        Set-Content -LiteralPath $whisperActive -Value "services: {}"
        Set-Content -LiteralPath $ttsActive -Value "services: {}"
        foreach ($service in @("whisper", "tts")) {
            $active = if ($service -eq "whisper") { $whisperActive } else { $ttsActive }
            $selected = if ($service -eq "whisper") { $state.Whisper } else { $state.Tts }
            $null = Set-ODSWindowsExtensionComposeState -ComposePath $active -Enabled $selected
            Assert-Selection ((Test-Path -LiteralPath $active -PathType Leaf) -eq $selected) `
                "Source refresh changed $service selection"
            Assert-Selection ((Test-Path -LiteralPath "$active.disabled" -PathType Leaf) -eq (-not $selected)) `
                "Source refresh left ambiguous $service markers"
        }
        Assert-Selection ((Get-Content -LiteralPath $sentinel -Raw).Trim() -eq "retain voice data") `
            "Voice data changed during selection"
    }
    Write-Output "PASS: Windows rerun retains all four independent voice states and data"

    Move-Item -LiteralPath $ttsActive -Destination "$ttsActive.disabled"
    . (Join-Path $root "installers\windows\lib\installed-selection.ps1")
    Set-Content -LiteralPath (Join-Path $installDir "docker-compose.base.yml") -Value "services: {}"
    $selectionMarkerDir = Join-Path $installDir "data\config"
    New-Item -ItemType Directory -Path $selectionMarkerDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $selectionMarkerDir "windows-managed-compose-selection.v1") `
        -Value "ods.windows.managed-compose-selection.v1"
    & {
        function Write-Phase { param($Phase, $Total, $Name, $Estimate) }
        function Write-AI { param($Message) }
        function Write-AIWarn { param($Message) }
        function Write-InfoBox { param($Label, $Value) }
        $voiceFlag = $false; $noVoiceFlag = $false; $allFlag = $false
        $workflowsFlag = $false; $ragFlag = $false
        $recommendedFlag = $false; $noRecommendedFlag = $true
        $hermesFlag = $false; $noHermesFlag = $true; $openClawFlag = $false
        $comfyuiFlag = $false; $noComfyuiFlag = $true
        $langfuseFlag = $false; $noLangfuseFlag = $true
        $nonInteractive = $true; $dryRun = $false; $cloudMode = $false
        $selectedTier = "2"; $gpuInfo = [PSCustomObject]@{ Backend = "nvidia" }
        . (Join-Path $root "installers\windows\phases\03-features.ps1")
        Assert-Selection ($enableWhisper -and -not $enableTts -and $enableVoice) `
            "Phase 03 re-coupled a retained Whisper-only selection"
        $noVoiceFlag = $true
        . (Join-Path $root "installers\windows\phases\03-features.ps1")
        Assert-Selection (-not $enableWhisper -and -not $enableTts -and -not $enableVoice) `
            "Phase 03 ignored explicit -NoVoice on a retained install"
    }
    Write-Output "PASS: actual Windows feature phase preserves split selection and CLI override"

    $requirements = Get-Content -LiteralPath (Join-Path $root "installers\windows\phases\04-requirements.ps1") -Raw
    $portStart = $requirements.IndexOf('# Build list of ports to check based on enabled features.')
    $portEnd = $requirements.IndexOf('if ($enableWorkflows) {', $portStart)
    Assert-Selection ($portStart -ge 0 -and $portEnd -gt $portStart) "Voice port preflight block was not found"
    $portPreflight = [scriptblock]::Create($requirements.Substring($portStart, $portEnd - $portStart))
    & {
        function Resolve-WindowsODSPort { param($Name, $DefaultPort, $InstallDir) return $DefaultPort }
        function Resolve-WindowsLlmPreflightPort {
            param($GpuBackend, [switch]$CloudMode, $LemonadeDefaultPort, $InstallDir)
            return 0
        }
        function Resolve-WindowsWhisperHostPort {
            param($ConfiguredPort, $GpuBackend, $AmdInferenceRuntime, $AmdInferenceLocation)
            return $ConfiguredPort
        }
        $gpuInfo = [PSCustomObject]@{ Backend = "nvidia" }
        $cloudMode = $false; $enableRecommended = $false
        $script:LEMONADE_PORT = 9000
        foreach ($state in @(
            @{ Whisper = $true; Tts = $false },
            @{ Whisper = $false; Tts = $true }
        )) {
            $enableWhisper = $state.Whisper; $enableTts = $state.Tts
            . $portPreflight
            Assert-Selection ($_portsToCheck.Contains("Whisper (STT)") -eq $enableWhisper) `
                "Port preflight checked Whisper in the wrong voice state"
            Assert-Selection ($_portsToCheck.Contains("Kokoro (TTS)") -eq $enableTts) `
                "Port preflight checked Kokoro in the wrong voice state"
        }
    }
    Write-Output "PASS: Windows port preflight follows each voice selection"

    $pairedEnable = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false -CliEnable $true
    $pairedDisable = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true -CliDisable $true
    $all = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false -All $true
    $allNoVoice = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true -All $true -CliDisable $true
    $menuCore = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false -MenuExplicit $true
    $menuFull = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true -MenuExplicit $true
    $cliOverMenu = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false `
        -CliEnable $true -MenuExplicit $true
    $noVoiceOverMenu = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true `
        -CliDisable $true -MenuExplicit $true
    Assert-Selection ($pairedEnable.Whisper -and $pairedEnable.Tts) "-Voice did not select both services"
    Assert-Selection (-not $pairedDisable.Whisper -and -not $pairedDisable.Tts) "-NoVoice did not disable both services"
    Assert-Selection ($all.Whisper -and $all.Tts) "-All did not select both services"
    Assert-Selection (-not $allNoVoice.Whisper -and -not $allNoVoice.Tts) "-NoVoice did not override -All"
    Assert-Selection (-not $menuCore.Whisper -and -not $menuCore.Tts) "Core menu did not disable both services"
    Assert-Selection ($menuFull.Whisper -and $menuFull.Tts) "Full menu did not select both services"
    Assert-Selection ($cliOverMenu.Whisper -and $cliOverMenu.Tts) "Explicit -Voice lost to menu choice"
    Assert-Selection (-not $noVoiceOverMenu.Whisper -and -not $noVoiceOverMenu.Tts) `
        "Explicit -NoVoice lost to menu choice"
    Write-Output "PASS: paired CLI and menu choices override retained voice selection"

    Set-Content -LiteralPath $ttsActive -Value "services: {}"
    Set-Content -LiteralPath "$ttsActive.disabled" -Value "services: {}"
    $before = @(Get-ChildItem -LiteralPath $ttsDir -File | Select-Object -ExpandProperty FullName | Sort-Object)
    foreach ($args in @(@{}, @{ CliEnable = $true }, @{ CliDisable = $true }, @{ All = $true }, @{ MenuExplicit = $true })) {
        $rejected = $false
        try { $null = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false @args }
        catch { $rejected = $_.Exception.Message -like "Ambiguous installed tts selection*" }
        Assert-Selection $rejected "Ambiguous TTS markers were accepted"
    }
    $after = @(Get-ChildItem -LiteralPath $ttsDir -File | Select-Object -ExpandProperty FullName | Sort-Object)
    Assert-Selection ((@($before) -join '|') -eq (@($after) -join '|')) "Rejected choice mutated markers"
    Assert-Selection ((Get-Content -LiteralPath $sentinel -Raw).Trim() -eq "retain voice data") `
        "Rejected choice changed voice data"
    Write-Output "PASS: unsafe retained markers fail before source copy and data stays intact"
} finally {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $resolvedScratch = [IO.Path]::GetFullPath($scratch)
    if ($resolvedScratch.StartsWith($tempRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedScratch) -like "ods-voice-selection-*" -and
        (Test-Path -LiteralPath $resolvedScratch -PathType Container)) {
        Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
    }
}
