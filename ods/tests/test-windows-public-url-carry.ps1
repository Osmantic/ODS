[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve the production command signatures.')]
[CmdletBinding()]
param()
# Contract: a Windows installer rerun keeps the public-URL settings that
# docs/ODS-PROXY.md tells owners to put in .env (ODS_PUBLIC_URL,
# ODS_SERVICE_PUBLIC_URLS, <SERVICE>_PUBLIC_URL), as phase 06 does on Linux
# (ods_carry_public_url_env_keys, #7493).
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
    @(Get-Content -LiteralPath $Path | Where-Object { $_.StartsWith("$Key=", [StringComparison]::Ordinal) })
}

# Pure helper: last assignment wins, first-seen order, the new content wins,
# and only <KEY>_PUBLIC_URL / <KEY>_PUBLIC_URLS keys are carried.
$previous = @(
    'N8N_PUBLIC_URL=https://n8n-old.example.com'
    'ODS_PUBLIC_URL=https://ods.example.com'
    'N8N_PUBLIC_URL=https://n8n.example.com'
    'ODS_PUBLIC_URL_NOTE=not-a-public-url-key'
    'ods_public_url=wrong-case'
    '# COMFYUI_PUBLIC_URL=commented-out'
    'TTS_PUBLIC_URL=https://tts.example.com'
)
$lines = @(Get-ODSPublicUrlEnvLines -PreviousLines $previous -NewContent "WEBUI_SECRET=x`r`nTTS_PUBLIC_URL=from-template")
Assert-True (($lines -join '|') -ceq 'N8N_PUBLIC_URL=https://n8n.example.com|ODS_PUBLIC_URL=https://ods.example.com') `
    "helper carried unexpected lines: $($lines -join '|')"
Assert-True (@(Get-ODSPublicUrlEnvLines -PreviousLines @() -NewContent '').Count -eq 0) `
    'helper carried lines without a previous .env'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-public-url-carry-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    $envPath = Join-Path $tempRoot '.env'
    $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
    Assert-True (@(Get-Content -LiteralPath $envPath | Where-Object { $_ -cmatch '^[A-Z][A-Z0-9_]*_PUBLIC_URLS?=' }).Count -eq 0) `
        'fresh .env already writes a public-URL key'

    # The owner follows docs/ODS-PROXY.md, then reruns the installer.
    $owner = @(
        'ODS_PUBLIC_URL=https://ods.example.com'
        'ODS_SERVICE_PUBLIC_URLS={"open-webui":"https://chat.example.com","n8n":"https://n8n.example.com"}'
        'ODS_TALK_PUBLIC_URL=https://talk.example.com'
        'OPEN_WEBUI_PUBLIC_URL=https://chat.example.com'
    )
    # The generated .env has no trailing newline; start the owner's lines on
    # their own line, as an editor would.
    Add-Content -LiteralPath $envPath -Value (@('') + $owner)
    foreach ($run in 1..2) {
        $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
        foreach ($expected in $owner) {
            $key = $expected.Split('=')[0]
            $found = @(Get-Assignments $envPath $key)
            Assert-True (($found.Count -eq 1) -and ($found[0] -ceq $expected)) `
                "rerun ${run}: expected exactly '$expected', found '$($found -join '|')'"
        }
        Assert-True (@(Get-Content -LiteralPath $envPath | Where-Object { $_ -ceq '#=== Public URLs (kept from the previous .env) ===' }).Count -eq 1) `
            "rerun ${run}: public URLs header is missing or repeated"
    }
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}

Write-Output "[PASS] Windows installer rerun keeps owner public URLs ($checks checks)"
