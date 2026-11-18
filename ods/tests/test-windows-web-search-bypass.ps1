[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve production command signatures.')]
[CmdletBinding()]
param([switch]$CheckCompose)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget { @{Limit='4.0';Reservation='1.0';Available='4.0'} }
function Get-ODSDockerMemoryGB { 8 }
function Resolve-WindowsODSPort { param($Name,$DefaultPort,$ExistingEnv,$InstallDir); $DefaultPort }
$tier = @{TierName='Fixture';LlmModel='fixture';GgufFile='fixture.gguf';MaxContext=8192}
$key = 'BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL'
$previousProcessValue = [Environment]::GetEnvironmentVariable($key)
$previousProcessPresent = [Environment]::GetEnvironmentVariables('Process').Contains($key)
$checks = 0
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
$tempRoot = Join-Path $tempParent ('ods-search-bypass-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}
try {
    $cases = @(
        @{Name='default';Absent=$true;Prior=$null;Expected='false'},
        @{Name='owner-on-no-override';Absent=$true;Prior='true';Expected='true'},
        @{Name='owner-on-empty-process';Process='';Prior='true';Expected='true'},
        @{Name='process-on';Process='true';Prior=$null;Expected='true'},
        @{Name='process-off';Process='false';Prior=$null;Expected='false'},
        @{Name='owner-off';Process='true';Prior='false';Expected='false'},
        @{Name='owner-on';Process='false';Prior='true';Expected='true'},
        @{Name='quoted';Process='';Prior="' TRUE '";Expected='true'},
        @{Name='double-quoted';Process='true';Prior='"False"';Expected='false'},
        @{Name='blank';Process='true';Prior='';Expected='true'},
        @{Name='quoted-blank';Process='';Prior="''";Expected='false'},
        @{Name='quoted-blank-process';Process='true';Prior="''";Expected='true'},
        @{Name='owner-valid-process-bad';Process='invalid';Prior='false';Expected='false'},
        @{Name='bad-owner';Process='true';Prior='yes';Invalid=$true},
        @{Name='bad-process';Process='1';Prior=$null;Invalid=$true},
        @{Name='unmatched-quote';Process='';Prior="'true";Invalid=$true},
        @{Name='literal-expression';Process='';Prior='$(throw "evaluated")';Invalid=$true}
    )
    foreach ($case in $cases) {
        $caseDir = Join-Path $tempRoot $case.Name
        New-Item -ItemType Directory -Path $caseDir | Out-Null
        $envPath = Join-Path $caseDir '.env'
        if ($case.Absent) {
            [Environment]::SetEnvironmentVariable($key, [NullString]::Value, 'Process')
            Assert-True (-not [Environment]::GetEnvironmentVariables('Process').Contains($key)) "$($case.Name): override not removed"
        } else {
            [Environment]::SetEnvironmentVariable($key, $case.Process, 'Process')
        }
        if ($null -ne $case.Prior) { Set-Content -LiteralPath $envPath -Value "$key=$($case.Prior)" }
        $failed = $false
        try {
            $null = New-ODSEnv -InstallDir $caseDir -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
        } catch {
            $failed = $true
            Assert-True ($_.Exception.Message -eq "$key must be true or false.") "$($case.Name): unexpected rejection"
        }
        if ($case.Invalid) {
            Assert-True $failed "$($case.Name): invalid value accepted"
            continue
        }
        Assert-True (-not $failed) "$($case.Name): valid value rejected"
        foreach ($run in 1..2) {
            $assignments = @(Get-Content -LiteralPath $envPath | Where-Object { $_.StartsWith("$key=", [StringComparison]::Ordinal) })
            Assert-True ($assignments.Count -eq 1 -and $assignments[0] -ceq "$key=$($case.Expected)") "$($case.Name): value lost or duplicated"
            Assert-True ([Environment]::GetEnvironmentVariable($key) -ceq $case.Expected) "$($case.Name): child launch disagrees with saved choice"
            if ($CheckCompose) {
                # Real Compose interpolation, without pulling or starting services.
                $composePath = Join-Path $caseDir 'compose.yaml'
                $composeFixture = 'services: {probe: {image: alpine:3.20, environment: {BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL: "${BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL:-false}"}}}'
                Set-Content -LiteralPath $composePath -Value $composeFixture
                $rendered = & docker compose --project-name ods-search-fixture --env-file $envPath -f $composePath config --format json
                Assert-True ($LASTEXITCODE -eq 0) "$($case.Name): Compose config failed"
                $config = $rendered | ConvertFrom-Json
                Assert-True ($config.services.probe.environment.$key -ceq $case.Expected) "$($case.Name): Compose runtime disagrees with saved choice"
            }
            # Rerun with search disabled and the opposite process override;
            # the saved explicit owner value must still survive.
            $opposite = if ($case.Expected -eq 'true') { 'false' } else { 'true' }
            [Environment]::SetEnvironmentVariable($key, $opposite, 'Process')
            $null = New-ODSEnv -InstallDir $caseDir -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8 -EnableWebSearch $false
        }
    }
} finally {
    if ($previousProcessPresent) {
        [Environment]::SetEnvironmentVariable($key, $previousProcessValue, 'Process')
    } else {
        [Environment]::SetEnvironmentVariable($key, [NullString]::Value, 'Process')
    }
    $resolved = (Resolve-Path -LiteralPath $tempRoot).Path
    if ([IO.Path]::GetDirectoryName($resolved) -ne $tempParent -or
        [IO.Path]::GetFileName($resolved) -notlike 'ods-search-bypass-*') {
        throw 'Refusing fixture cleanup outside the verified temporary directory.'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Host "[PASS] Windows web search bypass persistence ($checks checks)"
