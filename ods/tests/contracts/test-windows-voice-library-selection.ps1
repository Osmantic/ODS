$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $root "installers\windows\lib\service-plan.ps1")

function Assert-Selection([bool]$Condition, [string]$Message) {
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
    Assert-Selection (-not $fresh.Whisper -and -not $fresh.Tts) "Fresh Core selected voice"
    $full = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true
    Assert-Selection ($full.Whisper -and $full.Tts) "Fresh Full lost paired voice"

    Set-Content -LiteralPath (Join-Path $installDir ".env") -Value "ODS_MODE=local"
    $empty = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true
    Assert-Selection (-not $empty.Whisper -and -not $empty.Tts) "Existing Core surprise-enabled voice"
    $legacyFlags = Join-Path $installDir ".compose-flags"
    Set-Content -LiteralPath $legacyFlags -Value "-f docker-compose.base.yml -f extensions/services/whisper/compose.yaml -f extensions/services/tts/compose.yaml"
    $legacy = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false
    Assert-Selection ($legacy.Whisper -and $legacy.Tts) "Legacy paired voice flags were lost"
    Remove-Item -LiteralPath $legacyFlags -Force

    Set-Content -LiteralPath $whisperActive -Value "services: {}"
    Set-Content -LiteralPath "$ttsActive.disabled" -Value "services: {}"
    $dataDir = Join-Path $installDir "data\tts"
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $sentinel = Join-Path $dataDir "retained.txt"
    Set-Content -LiteralPath $sentinel -Value "keep"

    $whisperOnly = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true
    Assert-Selection ($whisperOnly.Whisper -and -not $whisperOnly.Tts) "Whisper-only Library selection was lost"
    $plan = New-ODSWindowsServicePlan -EnableVoice $true -EnableWhisper $whisperOnly.Whisper -EnableTts $whisperOnly.Tts
    Assert-Selection ($plan["whisper"].Enabled -and -not $plan["tts"].Enabled) "Service plan re-coupled voice"
    # A source refresh can restore compose.yaml beside a disabled marker.
    Set-Content -LiteralPath $ttsActive -Value "services: {}"
    $null = Set-ODSWindowsExtensionComposeState -ComposePath $ttsActive -Enabled $plan["tts"].Enabled
    Assert-Selection (-not (Test-Path -LiteralPath $ttsActive) -and
        (Test-Path -LiteralPath "$ttsActive.disabled")) "Source refresh re-enabled Kokoro"
    Assert-Selection ((Get-Content -LiteralPath $sentinel -Raw).Trim() -eq "keep") "Voice data changed"

    Move-Item -LiteralPath $whisperActive -Destination "$whisperActive.disabled"
    Move-Item -LiteralPath "$ttsActive.disabled" -Destination $ttsActive
    $ttsOnly = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true
    Assert-Selection (-not $ttsOnly.Whisper -and $ttsOnly.Tts) "Kokoro-only Library selection was lost"
    $explicit = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false -CliEnable $true
    Assert-Selection ($explicit.Whisper -and $explicit.Tts) "Explicit Voice did not enable both"
    $all = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $true -All $true
    Assert-Selection ($all.Whisper -and $all.Tts) "All did not enable both"
    $core = Resolve-ODSWindowsVoiceSelection -InstallDir $installDir -ComputedVoice $false -MenuExplicit $true
    Assert-Selection (-not $core.Whisper -and -not $core.Tts) "Explicit Core did not disable both"
    Write-Output "PASS: native Windows independent Voice Library selection and retention"
} finally {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ([IO.Path]::GetFullPath($scratch).StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    }
}
