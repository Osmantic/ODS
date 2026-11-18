[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve the production command signatures.')]
[CmdletBinding()]
param(
    [string]$OdsDir = '',
    [switch]$KeepFixture
)
# Contract: a Windows installer rerun keeps the settings installed extensions
# own (their manifest env_vars), as phase 06 does on Linux. The new template
# wins even when empty; secrets are never regenerated.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OdsDir)) { $OdsDir = $root }
. (Join-Path $OdsDir 'installers/windows/lib/env-generator.ps1')
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

# The real LibreChat manifest and Compose file must be present.
$librechatManifest = Join-Path $OdsDir 'extensions/library/services/librechat/manifest.yaml'
$librechatCompose = Join-Path $OdsDir 'extensions/library/services/librechat/compose.yaml'
Assert-True (Test-Path -LiteralPath $librechatManifest -PathType Leaf) 'LibreChat manifest missing'
Assert-True (Test-Path -LiteralPath $librechatCompose -PathType Leaf) 'LibreChat compose missing'
$braveManifest = Join-Path $OdsDir 'extensions/services/brave-search/manifest.yaml'
Assert-True (Test-Path -LiteralPath $braveManifest -PathType Leaf) 'Brave Search manifest missing'

# Fixture root must live under this checkout's tests/.tmp.
$testsTmp = Join-Path (Join-Path $OdsDir 'tests') '.tmp'
if (-not (Test-Path -LiteralPath $testsTmp -PathType Container)) {
    New-Item -ItemType Directory -Path $testsTmp -Force | Out-Null
}
$tempRoot = Join-Path $testsTmp ('ods-ext-env-carry-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    $resolvedRoot = (Resolve-Path -LiteralPath $tempRoot).Path
    $resolvedParent = (Resolve-Path -LiteralPath $testsTmp).Path
    $sep = [IO.Path]::DirectorySeparatorChar
    Assert-True ($resolvedRoot.StartsWith($resolvedParent + $sep, [StringComparison]::OrdinalIgnoreCase)) `
        "fixture root $resolvedRoot is not under $resolvedParent"

    # Copy the real LibreChat extension into data/user-extensions/librechat.
    $userExtRoot = Join-Path (Join-Path $tempRoot 'data') 'user-extensions'
    $librechatDir = Join-Path $userExtRoot 'librechat'
    New-Item -ItemType Directory -Path $librechatDir -Force | Out-Null
    Copy-Item -LiteralPath $librechatManifest -Destination (Join-Path $librechatDir 'manifest.yaml')
    Copy-Item -LiteralPath $librechatCompose -Destination (Join-Path $librechatDir 'compose.yaml')

    # Bundled Brave Search fixture with the actual manifest copied.
    $braveDir = Join-Path (Join-Path (Join-Path $tempRoot 'extensions') 'services') 'brave-search'
    New-Item -ItemType Directory -Path $braveDir -Force | Out-Null
    Copy-Item -LiteralPath $braveManifest -Destination (Join-Path $braveDir 'manifest.yaml')

    # Uninstalled library decoy: must NOT be read, and must not carry a stray key.
    $decoyDir = Join-Path (Join-Path (Join-Path (Join-Path $tempRoot 'extensions') 'library') 'services') 'decoy'
    New-Item -ItemType Directory -Path $decoyDir -Force | Out-Null
    $decoyContent = @'
schema_version: ods.services.v1
service:
  id: decoy
  env_vars:
    - key: DECOY_KEY
'@
    Set-Content -LiteralPath (Join-Path $decoyDir 'manifest.yaml') -Value $decoyContent -Encoding UTF8

    $envPath = Join-Path $tempRoot '.env'
    $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
    foreach ($key in @('JWT_SECRET','JWT_REFRESH_SECRET','LIBRECHAT_MONGO_PASSWORD','LIBRECHAT_MEILI_KEY','CREDS_KEY','CREDS_IV','BRAVE_SEARCH_API_KEY','DECOY_KEY')) {
        Assert-True ((Get-Assignments $envPath $key).Count -eq 0) "fresh .env already writes extension key $key"
    }

    # The extension setup hook appends its secrets; the owner reruns the installer.
    $extension = @(
        'JWT_SECRET=first-synthetic'
        'JWT_SECRET=current-synthetic'
        "CREDS_KEY='synthetic quoted value'"
        'CREDS_IV=11111111111111111111111111111111'
        'LIBRECHAT_MONGO_PASSWORD=synthetic-mongo'
        'LIBRECHAT_MEILI_KEY=synthetic-meili'
        'JWT_REFRESH_SECRET=synthetic-refresh'
        'BRAVE_SEARCH_API_KEY=synthetic-brave'
        'JWT_SECRET_EXTRA=not-listed'
        'DECOY_KEY=should-not-carry'
    )
    Add-Content -LiteralPath $envPath -Value $extension
    foreach ($run in 1..2) {
        $null = New-ODSEnv -InstallDir $tempRoot -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
        foreach ($expected in @('JWT_SECRET=current-synthetic', "CREDS_KEY='synthetic quoted value'",
                'CREDS_IV=11111111111111111111111111111111', 'LIBRECHAT_MONGO_PASSWORD=synthetic-mongo',
                'LIBRECHAT_MEILI_KEY=synthetic-meili', 'JWT_REFRESH_SECRET=synthetic-refresh',
                'BRAVE_SEARCH_API_KEY=synthetic-brave')) {
            $key = $expected.Split('=')[0]
            $found = @(Get-Assignments $envPath $key)
            Assert-True (($found.Count -eq 1) -and ($found[0] -ceq $expected)) `
                "rerun ${run}: expected exactly '$expected', found '$($found -join '|')"
        }
        Assert-True ((Get-Assignments $envPath 'JWT_SECRET_EXTRA').Count -eq 0) "rerun ${run}: an unlisted key was carried"
        Assert-True ((Get-Assignments $envPath 'DECOY_KEY').Count -eq 0) "rerun ${run}: uninstalled library decoy key was carried"
        Assert-True (@(Get-Content -LiteralPath $envPath | Where-Object { $_ -ceq '#=== Installed extensions (kept from the previous .env) ===' }).Count -eq 1) `
            "rerun ${run}: extension settings header is missing or repeated"
    }
    $librechatKeys = @(Get-ODSExtensionEnvKeys -ManifestPaths @($librechatManifest))
    foreach ($expectedKey in @('JWT_SECRET','JWT_REFRESH_SECRET','LIBRECHAT_MONGO_PASSWORD','LIBRECHAT_MEILI_KEY','CREDS_KEY','CREDS_IV')) {
        Assert-True ($librechatKeys -ccontains $expectedKey) "LibreChat manifest key $expectedKey not parsed"
    }
    $braveKeys = @(Get-ODSExtensionEnvKeys -ManifestPaths @($braveManifest))
    Assert-True ($braveKeys -ccontains 'BRAVE_SEARCH_API_KEY') 'Brave Search manifest key not parsed'
    # Invalid / out-of-section manifest keys are ignored (fixture under tests/.tmp).
    $badManifest = Join-Path $tempRoot 'bad-manifest.yaml'
    $badContent = @'
schema_version: ods.services.v1
service:
  id: fixture
  env_vars:
    - key: GOOD_KEY
      required: true
    - key: lower_key
    - key: "QUOTED_KEY"
features:
  - key: OUT_OF_SECTION
'@
    Set-Content -LiteralPath $badManifest -Value $badContent -Encoding UTF8
    $badKeys = @(Get-ODSExtensionEnvKeys -ManifestPaths @($badManifest))
    Assert-True ($badKeys -ccontains 'GOOD_KEY') 'valid manifest key not parsed'
    Assert-True ($badKeys -ccontains 'QUOTED_KEY') 'quoted manifest key not parsed'
    Assert-True (-not ($badKeys -ccontains 'lower_key')) 'lowercase manifest key was parsed'
    Assert-True (-not ($badKeys -ccontains 'OUT_OF_SECTION')) 'out-of-section manifest key was parsed'

    # Pure helper: last assignment wins, keys match exactly, template wins.
    $previous = @(
        'JWT_SECRET=first'
        'JWT_SECRET=current'
        "CREDS_KEY='quoted value'"
        'CREDS_IV='
        'jwt_secret=wrong-case'
        'JWT_SECRET_EXTRA=not-listed'
        'LIBRECHAT_MONGO_PASSWORD=from-template'
    )
    $lines = @(Get-ODSCarriedEnvLines -PreviousLines $previous -NewContent "WEBUI_SECRET=x`r`nLIBRECHAT_MONGO_PASSWORD=new-template" `
        -Keys @('JWT_SECRET','CREDS_KEY','CREDS_IV','LIBRECHAT_MONGO_PASSWORD'))
    Assert-True (($lines -join '|') -ceq "JWT_SECRET=current|CREDS_KEY='quoted value'|CREDS_IV=") `
        "helper carried unexpected lines: $($lines -join '|')"

    # Pure helper: template wins even when the template value is empty.
    $emptyLines = @(Get-ODSCarriedEnvLines -PreviousLines @('CREDS_IV=previous') `
        -NewContent "CREDS_IV=`r`nOTHER=x" -Keys @('CREDS_IV'))
    Assert-True ($emptyLines.Count -eq 0) "helper did not let empty template win: $($emptyLines -join '|')"
} finally {
    if ($KeepFixture) {
        Write-Host "[INFO] Fixture preserved at $tempRoot"
    } else {
        $resolvedRoot = (Resolve-Path -LiteralPath $tempRoot).Path
        $resolvedParent = (Resolve-Path -LiteralPath $testsTmp).Path
        $sep = [IO.Path]::DirectorySeparatorChar
        if ($resolvedRoot.StartsWith($resolvedParent + $sep, [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
            Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
        }
    }
}
Write-Host "[PASS] Windows installer rerun keeps installed extension .env settings ($checks checks)"
