$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/env-generator.ps1')
. (Join-Path $root 'installers/windows/lib/service-plan.ps1')

function Write-AIWarn { param([string]$Message) }
function Get-LlamaCpuBudget { @{ Limit = '4.0'; Reservation = '1.0'; Available = '4.0' } }
function Get-ODSDockerMemoryGB { 8 }
function Resolve-WindowsODSPort { param($Name, $DefaultPort, $ExistingEnv, $InstallDir); $DefaultPort }

$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods-core-chat-route-' + [guid]::NewGuid().ToString('N'))
$tier = @{ TierName = 'Fixture'; LlmModel = 'fixture'; GgufFile = 'fixture.gguf'; MaxContext = 8192 }
function Read-Route([string]$Path) {
    $result = @{}
    foreach ($line in Get-Content -LiteralPath (Join-Path $Path '.env')) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $result[$Matches[1]] = $Matches[2] }
    }
    return $result
}
function Assert-Route([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

try {
    $localCore = Join-Path $scratch 'local-core'
    New-Item -ItemType Directory -Path $localCore -Force | Out-Null
    New-ODSEnv -InstallDir $localCore -TierConfig $tier -Tier '1' -GpuBackend 'none' `
        -ODSMode 'local' -SystemRamGB 8 -EnableRecommended $false | Out-Null
    $coreEnv = Read-Route $localCore
    Assert-Route ($coreEnv['ODS_MODEL_SWITCHBOARD'] -eq 'enabled') 'Fresh Core lost model switching'
    Assert-Route ($coreEnv['OPEN_WEBUI_LLM_BASE_URL'] -eq 'http://litellm:4000') 'Fresh Core chat lost the switchboard gateway'
    Assert-Route ($coreEnv['OPEN_WEBUI_LLM_API_KEY'] -eq $coreEnv['LITELLM_KEY']) 'Fresh Core chat has the wrong gateway key'
    Assert-Route ($coreEnv['LLM_API_URL'] -eq 'http://llama-server:8080') 'Fresh Core lost local inference'
    $corePlan = New-ODSWindowsServicePlan -EnableRecommended $false `
        -SwitchboardMode (Get-ODSWindowsEffectiveSwitchboardMode -InstallDir $localCore)
    Assert-Route ($corePlan['litellm'].Enabled -and -not $corePlan['token-spy'].Enabled -and
        -not $corePlan['searxng'].Enabled) 'Fresh Core service plan disagrees with its chat route'

    # A retained switchboard -> observe transition must clear the old gateway
    # route before the service plan omits LiteLLM.
    $observe = Join-Path $scratch 'observe'
    New-Item -ItemType Directory -Path $observe -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $observe '.env'),
        "ODS_MODEL_SWITCHBOARD=observe`nOPEN_WEBUI_LLM_BASE_URL=http://litellm:4000`nOPEN_WEBUI_LLM_API_KEY=old-gateway-key`n")
    New-ODSEnv -InstallDir $observe -TierConfig $tier -Tier '1' -GpuBackend 'none' `
        -ODSMode 'local' -SystemRamGB 8 | Out-Null
    $observeEnv = Read-Route $observe
    $observePlan = New-ODSWindowsServicePlan -EnableRecommended $false `
        -SwitchboardMode (Get-ODSWindowsEffectiveSwitchboardMode -InstallDir $observe -RequestedMode 'enabled')
    Assert-Route (-not $observePlan['litellm'].Enabled -and
        [string]::IsNullOrWhiteSpace($observeEnv['OPEN_WEBUI_LLM_BASE_URL']) -and
        [string]::IsNullOrWhiteSpace($observeEnv['OPEN_WEBUI_LLM_API_KEY'])) 'Observe install retained an orphaned gateway route'

    $cloudCore = Join-Path $scratch 'cloud-core'
    New-Item -ItemType Directory -Path $cloudCore -Force | Out-Null
    New-ODSEnv -InstallDir $cloudCore -TierConfig $tier -Tier 'CLOUD' -GpuBackend 'none' `
        -ODSMode 'cloud' -SystemRamGB 8 -EnableRecommended $false | Out-Null
    $cloudEnv = Read-Route $cloudCore
    Assert-Route ($cloudEnv['LLM_API_URL'] -eq 'http://litellm:4000') 'Cloud Core lost its required gateway'
    Assert-Route ($cloudEnv['OPEN_WEBUI_LLM_BASE_URL'] -eq 'http://litellm:4000') 'Cloud Core chat has no selected provider'
    $cloudPlan = New-ODSWindowsServicePlan -EnableRecommended $false -CloudMode $true `
        -SwitchboardMode (Get-ODSWindowsEffectiveSwitchboardMode -InstallDir $cloudCore)
    Assert-Route ($cloudPlan['litellm'].Enabled -and -not $cloudPlan['token-spy'].Enabled) 'Cloud Core plan disagrees with its chat route'

    Write-Output 'PASS: Windows Core chat route and service selection agree across modes'
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing cleanup outside the temporary root'
    }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
