[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve the production command signatures.')]
[CmdletBinding()]
param()
# Contract: a Windows installer rerun keeps the previous .env when the install
# path holds wildcard characters ([ ]), and reads it as UTF-8 under Windows
# PowerShell 5.1, whose Get-Content default is the ANSI code page.
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
function Get-Assignment([string]$Path, [string]$Key) {
    $lines = [IO.File]::ReadAllLines($Path, [Text.UTF8Encoding]::new($false))
    @($lines | Where-Object { $_.StartsWith("$Key=", [StringComparison]::Ordinal) })
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-env-[lab]-' + [guid]::NewGuid().ToString('N'))
$null = [IO.Directory]::CreateDirectory($tempRoot)
try {
    $envPath = Join-Path $tempRoot '.env'
    $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
    Assert-True ([IO.File]::Exists($envPath)) 'first run did not write .env under a bracketed install path'
    $secret = @(Get-Assignment $envPath 'WEBUI_SECRET')
    Assert-True ($secret.Count -eq 1) 'first run did not write WEBUI_SECRET'

    # An owner setting with non-ASCII text, appended as UTF-8 like the
    # installer writes the rest of the file.
    $voiceLine = "AUDIO_TTS_VOICE=Lena $([char]0x2014) caf$([char]0x00E9)"
    [IO.File]::AppendAllText($envPath, "`n$voiceLine`n", [Text.UTF8Encoding]::new($false))

    $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
    $rerunSecret = @(Get-Assignment $envPath 'WEBUI_SECRET')
    Assert-True (($rerunSecret.Count -eq 1) -and ($rerunSecret[0] -ceq $secret[0])) `
        "rerun replaced WEBUI_SECRET under a bracketed install path"
    $voice = @(Get-Assignment $envPath 'AUDIO_TTS_VOICE')
    Assert-True (($voice.Count -eq 1) -and ($voice[0] -ceq $voiceLine)) `
        "rerun did not keep the UTF-8 owner setting: found '$($voice -join '|')'"

    # Token Spy keeps its own copy of the key; reuse it when .env lost it.
    # The generator joins this path with backslashes, so check it on Windows.
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $keyFile = Join-Path $tempRoot 'data\token-spy\token-spy-api-key.txt'
        $null = [IO.Directory]::CreateDirectory((Split-Path -Parent $keyFile))
        [IO.File]::WriteAllText($keyFile, "token-spy-fixture-key`n")
        [IO.File]::Delete($envPath)
        $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
        $tokenSpy = @(Get-Assignment $envPath 'TOKEN_SPY_API_KEY')
        Assert-True (($tokenSpy.Count -eq 1) -and ($tokenSpy[0] -ceq 'TOKEN_SPY_API_KEY=token-spy-fixture-key')) `
            "rerun did not reuse Token Spy's key: found '$($tokenSpy -join '|')'"
    }
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
Write-Host "[PASS] Windows installer rerun keeps .env on bracketed paths and as UTF-8 ($checks checks)"
