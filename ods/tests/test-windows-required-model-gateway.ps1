$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../installers/windows/lib/service-plan.ps1')

# Exercise route dependencies independently of the optional recommended group.
$cases = @(
    @{Name='NVIDIA switchboard'; Mode='enabled'; Backend='nvidia'; Cloud=$false; Required=$true},
    @{Name='CPU switchboard'; Mode='enabled'; Backend='cpu'; Cloud=$false; Required=$true},
    @{Name='normalized switchboard'; Mode=' ENABLED '; Backend='none'; Cloud=$false; Required=$true},
    @{Name='native AMD legacy'; Mode='legacy'; Backend='amd'; Cloud=$false; Required=$true},
    @{Name='native AMD observe'; Mode='observe'; Backend='amd'; Cloud=$false; Required=$true},
    @{Name='cloud legacy'; Mode='legacy'; Backend='none'; Cloud=$true; Required=$true},
    @{Name='cloud observe'; Mode='observe'; Backend='none'; Cloud=$true; Required=$true},
    @{Name='direct NVIDIA legacy'; Mode='legacy'; Backend='nvidia'; Cloud=$false; Required=$false},
    @{Name='direct CPU observe'; Mode='observe'; Backend='cpu'; Cloud=$false; Required=$false}
)
foreach ($case in $cases) {
    $plan = New-ODSWindowsServicePlan -EnableRecommended $false -SwitchboardMode $case.Mode `
        -CloudMode $case.Cloud -GpuBackend $case.Backend
    if ([bool]$plan['litellm'].Enabled -ne $case.Required) { throw "Wrong gateway selection: $($case.Name)" }
    foreach ($optional in @('token-spy','searxng','hermes','whisper','n8n','comfyui','pixel-edge','pixel-model-relay')) {
        if ($plan[$optional].Enabled) { throw "$($case.Name) enabled unrelated optional service $optional" }
    }
}
$recommended = New-ODSWindowsServicePlan -EnableRecommended $true
if (-not $recommended['litellm'].Enabled -or -not $recommended['token-spy'].Enabled) {
    throw 'Recommended service selection changed'
}
$legacy = New-ODSWindowsServicePlan -EnableRecommended $false
if ($legacy['litellm'].Enabled) { throw 'Existing caller defaults changed' }
Write-Host 'PASS: nine route cases preserve required gateways and optional service choices'
