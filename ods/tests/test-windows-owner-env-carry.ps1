[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve the production command signatures.')]
[CmdletBinding()]
param()
# Contract: a Windows installer rerun keeps the settings ODS tells owners to
# put in .env (N8N_API_KEY, HF_TOKEN, ...), as phase 06 does on Linux.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget { @{Limit='4.0';Reservation='1.0';Available='4.0'} }
function Get-ODSDockerMemoryGB { 8 }
function Resolve-WindowsODSPort { param($Name,$DefaultPort,$ExistingEnv,$InstallDir); $DefaultPort }
$tier = @{TierName='Fixture';LlmModel='fixture';GgufFile='fixture.gguf';MaxContext=8192}
$checks = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}
function Get-Assignments([string]$Path, [string]$Key) {
    @(Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_.StartsWith("$Key=", [StringComparison]::Ordinal) })
}

# Both installers must carry the same keys.
$linuxLib = Get-Content -Raw -LiteralPath (Join-Path $root 'installers/lib/extension-env-carry.sh')
$linuxBlock = [regex]::Match($linuxLib, '(?ms)^ODS_OWNER_ENV_KEYS=\((.*?)^\)')
Assert-True $linuxBlock.Success 'Linux ODS_OWNER_ENV_KEYS list not found'
$linuxKeys = @($linuxBlock.Groups[1].Value -split '\s+' | Where-Object { $_ })
Assert-True (($linuxKeys -join ',') -ceq ($script:ODS_OWNER_ENV_KEYS -join ',')) `
    "Windows owner keys differ from Linux: $($script:ODS_OWNER_ENV_KEYS -join ',') vs $($linuxKeys -join ',')"

# Pure helper: last assignment wins, keys match exactly, the template wins.
$previous = @(
    'N8N_API_KEY=n8n-api-first'
    'N8N_API_KEY=n8n-api-current'
    "HF_TOKEN='hf_owner token'"
    'hf_token=wrong-case'
    'HF_TOKEN_EXTRA=not-listed'
    'AUDIO_TTS_VOICE=af_bella'
)
$lines = @(Get-ODSCarriedEnvLines -PreviousLines $previous -NewContent "WEBUI_SECRET=x`r`nAUDIO_TTS_VOICE=from-template" `
    -Keys $script:ODS_OWNER_ENV_KEYS)
Assert-True (($lines -join '|') -ceq "N8N_API_KEY=n8n-api-current|HF_TOKEN='hf_owner token'") `
    "helper carried unexpected lines: $($lines -join '|')"
Assert-True (@(Get-ODSCarriedEnvLines -PreviousLines @() -NewContent '' -Keys $script:ODS_OWNER_ENV_KEYS).Count -eq 0) `
    'helper carried lines without a previous .env'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-owner-env-carry-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    $envPath = Join-Path $tempRoot '.env'
    $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
    # The generator itself must not write a listed key; an old value would
    # then override its choice.
    foreach ($key in $script:ODS_OWNER_ENV_KEYS) {
        Assert-True ((Get-Assignments $envPath $key).Count -eq 0) "fresh .env already writes owner key $key"
    }

    # The owner follows ODS's instructions and edits .env, then reruns the installer.
    $owner = @(
        'N8N_API_KEY=n8n-api-first'
        'N8N_API_KEY=n8n-api-current'
        "HF_TOKEN='hf_owner token'"
        'LLAMA_ARC_IMAGE=ghcr.io/example/arc@sha256:abc'
        'AUDIO_TTS_VOICE=af_bella'
        'HF_TOKEN_EXTRA=not-listed'
    )
    Add-Content -LiteralPath $envPath -Value $owner
    foreach ($run in 1..2) {
        $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
        foreach ($expected in @('N8N_API_KEY=n8n-api-current', "HF_TOKEN='hf_owner token'",
                'LLAMA_ARC_IMAGE=ghcr.io/example/arc@sha256:abc', 'AUDIO_TTS_VOICE=af_bella')) {
            $key = $expected.Split('=')[0]
            $found = @(Get-Assignments $envPath $key)
            Assert-True (($found.Count -eq 1) -and ($found[0] -ceq $expected)) `
                "rerun ${run}: expected exactly '$expected', found '$($found -join '|')'"
        }
        Assert-True ((Get-Assignments $envPath 'HF_TOKEN_EXTRA').Count -eq 0) "rerun ${run}: an unlisted key was carried"
        Assert-True (@(Get-Content -LiteralPath $envPath | Where-Object { $_ -ceq '#=== Owner settings (kept from the previous .env) ===' }).Count -eq 1) `
            "rerun ${run}: owner settings header is missing or repeated"
    }
    # Keep the owner's model-profile opt-in or opt-out across repeated installs.
    foreach ($mode in @('off', 'observe', 'enabled')) {
        $expectedProfile = "ODS_MODEL_PROFILES=$mode"
        [IO.File]::AppendAllText($envPath, "`n$expectedProfile`n", [Text.UTF8Encoding]::new($false))
        foreach ($run in 1..2) {
            $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
            $found = @(Get-Assignments $envPath 'ODS_MODEL_PROFILES')
            Assert-True (($found.Count -eq 1) -and ($found[0] -ceq $expectedProfile)) `
                "Model profile mode $mode changed on rerun $run"
        }
    }
    # The private writer emits UTF-8 without a BOM. PS5.1 otherwise reads it as
    # the Windows ANSI codepage, corrupting non-ASCII owner settings on rerun.
    # Construct codepoints so this test's own script encoding is irrelevant.
    $voice = 'voice-' + [char]0x58F0 + [char]0x97F3
    $expectedVoice = "AUDIO_TTS_VOICE=$voice"
    [IO.File]::AppendAllText($envPath, "`n$expectedVoice`n", [Text.UTF8Encoding]::new($false))
    foreach ($run in 1..2) {
        $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
        $found = @(Get-Assignments $envPath 'AUDIO_TTS_VOICE')
        Assert-True (($found.Count -eq 1) -and ($found[0] -ceq $expectedVoice)) `
            "UTF-8 voice changed on rerun $run"
    }
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
Write-Host "[PASS] Windows installer rerun keeps owner .env settings ($checks checks)"
