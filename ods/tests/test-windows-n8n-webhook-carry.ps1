[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve the production command signatures.')]
[CmdletBinding()]
param()
# Contract: a Windows installer rerun keeps the owner's N8N_WEBHOOK_URL
# (extensions/services/n8n/README.md tells owners to set their public URL),
# and a generated URL follows the resolved N8N_PORT. Same order as Linux
# phase 06 (#7396): explicit process value, then the previous .env, then
# http://localhost:<N8N_PORT>.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
# Only the hardware probes are stubbed; Resolve-WindowsODSPort is the real one.
function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget { @{Limit='4.0';Reservation='1.0';Available='4.0'} }
function Get-ODSDockerMemoryGB { 8 }
$tier = @{TierName='Fixture';LlmModel='fixture';GgufFile='fixture.gguf';MaxContext=8192}
$checks = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}
function Get-Assignments([string]$Path, [string]$Key) {
    @(Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_.StartsWith("$Key=", [StringComparison]::Ordinal) })
}
function Assert-Single([string]$Path, [string]$Expected, [string]$Context) {
    $key = $Expected.Split('=')[0]
    $found = @(Get-Assignments $Path $key)
    Assert-True (($found.Count -eq 1) -and ($found[0] -ceq $Expected)) `
        "${Context}: expected exactly '$Expected', found '$($found -join '|')'"
}
function Invoke-Generator([string]$Dir) {
    $null = New-ODSEnv -InstallDir $Dir -TierConfig $tier -Tier '1' -GpuBackend 'none' -SystemRamGB 8
}
function Set-EnvLine([string]$Path, [string]$Key, [string]$Line) {
    # Replace (or, with an empty line, remove) the key's line as an owner's editor would.
    $kept = @(Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { -not $_.StartsWith("$Key=", [StringComparison]::Ordinal) })
    if (-not [string]::IsNullOrEmpty($Line)) { $kept += $Line }
    [IO.File]::WriteAllText($Path, ($kept -join "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
}

function Set-ProcessEnv([string]$Name, [AllowNull()][string]$Value) {
    # [Environment]::SetEnvironmentVariable($Name, $null) keeps an empty value
    # under PowerShell, which the port resolver rightly refuses; remove it.
    if ([string]::IsNullOrEmpty($Value)) {
        if (Test-Path -LiteralPath "Env:$Name") { Remove-Item -LiteralPath "Env:$Name" }
    } else {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

$savedPort = [Environment]::GetEnvironmentVariable('N8N_PORT')
$savedUrl = [Environment]::GetEnvironmentVariable('N8N_WEBHOOK_URL')
Set-ProcessEnv 'N8N_PORT' $null
Set-ProcessEnv 'N8N_WEBHOOK_URL' $null
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-n8n-webhook-carry-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    $envPath = Join-Path $tempRoot '.env'

    # Fresh install: the generated default.
    Invoke-Generator $tempRoot
    Assert-Single $envPath 'N8N_PORT=5678' 'fresh'
    Assert-Single $envPath 'N8N_WEBHOOK_URL=http://localhost:5678' 'fresh'

    # The owner puts n8n behind an HTTPS proxy, as n8n/README.md says, then
    # reruns the installer twice.
    Set-EnvLine $envPath 'N8N_WEBHOOK_URL' 'N8N_WEBHOOK_URL=https://n8n.example.com/'
    foreach ($run in 1..2) {
        Invoke-Generator $tempRoot
        Assert-Single $envPath 'N8N_WEBHOOK_URL=https://n8n.example.com/' "public URL rerun $run"
    }

    # A quoted value is kept byte for byte.
    Set-EnvLine $envPath 'N8N_WEBHOOK_URL' 'N8N_WEBHOOK_URL="https://n8n.example.com/hooks"'
    Invoke-Generator $tempRoot
    Assert-Single $envPath 'N8N_WEBHOOK_URL="https://n8n.example.com/hooks"' 'quoted rerun'

    # A custom port with no saved URL: the generated URL follows N8N_PORT.
    Set-EnvLine $envPath 'N8N_PORT' 'N8N_PORT=15679'
    Set-EnvLine $envPath 'N8N_WEBHOOK_URL' $null
    Invoke-Generator $tempRoot
    Assert-Single $envPath 'N8N_PORT=15679' 'port-only rerun'
    Assert-Single $envPath 'N8N_WEBHOOK_URL=http://localhost:15679' 'port-only rerun'

    # An empty saved URL is not a choice; it gets the generated URL.
    Set-EnvLine $envPath 'N8N_WEBHOOK_URL' 'N8N_WEBHOOK_URL='
    Invoke-Generator $tempRoot
    Assert-Single $envPath 'N8N_WEBHOOK_URL=http://localhost:15679' 'empty saved URL'

    # An explicit process value wins over the saved URL, as on Linux and as
    # Compose itself prefers process values over --env-file.
    Set-EnvLine $envPath 'N8N_WEBHOOK_URL' 'N8N_WEBHOOK_URL=https://n8n.example.com/'
    Set-ProcessEnv 'N8N_WEBHOOK_URL' 'https://hooks.example.org/'
    Invoke-Generator $tempRoot
    Assert-Single $envPath 'N8N_WEBHOOK_URL=https://hooks.example.org/' 'process override'

    # A process port override drives the generated URL on a fresh install.
    Set-ProcessEnv 'N8N_WEBHOOK_URL' $null
    Set-ProcessEnv 'N8N_PORT' '15680'
    $freshDir = Join-Path $tempRoot 'fresh'
    New-Item -ItemType Directory -Path $freshDir | Out-Null
    Invoke-Generator $freshDir
    Assert-Single (Join-Path $freshDir '.env') 'N8N_PORT=15680' 'fresh with process port'
    Assert-Single (Join-Path $freshDir '.env') 'N8N_WEBHOOK_URL=http://localhost:15680' 'fresh with process port'
    Set-ProcessEnv 'N8N_PORT' $null

    # A multi-line process value would add lines to .env; refuse it.
    $before = [IO.File]::ReadAllText($envPath)
    Set-ProcessEnv 'N8N_WEBHOOK_URL' "https://n8n.example.com/`nWEBUI_AUTH=false"
    $refused = $false
    try { Invoke-Generator $tempRoot } catch { $refused = $_.Exception.Message -like '*N8N_WEBHOOK_URL*' }
    Assert-True $refused 'multi-line process N8N_WEBHOOK_URL was not refused by name'
    Assert-True ([IO.File]::ReadAllText($envPath) -ceq $before) 'refused N8N_WEBHOOK_URL changed .env'
} finally {
    Set-ProcessEnv 'N8N_PORT' $savedPort
    Set-ProcessEnv 'N8N_WEBHOOK_URL' $savedUrl
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}

Write-Output "[PASS] Windows installer rerun keeps N8N_WEBHOOK_URL and follows N8N_PORT ($checks checks)"
