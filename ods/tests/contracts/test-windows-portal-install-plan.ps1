$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-install-plan.ps1')
. (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-runtime-plan.ps1')
. (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-model-path.ps1')
$count = 0
function Check($Condition, $Message) {
    if (-not $Condition) { throw $Message }
    $script:count++
    Write-Host "PASS $Message"
}
function Reject($Parameters, $Message) {
    $rejected = $false
    try { $null = Get-ODSWindowsPortalInstallPlan $Parameters } catch { $rejected = $true }
    Check $rejected $Message
}
$plan = Get-ODSWindowsPortalInstallPlan @{}
Check (((Get-ODSWindowsPortalArguments @()) -join ' ') -ceq '--pixel --no-hermes') 'direct WSL entrypoint requires Pixel by default'
Check (((Get-ODSWindowsPortalArguments $null) -join ' ') -ceq '--pixel --no-hermes') 'omitting all command arguments does not pass an empty flag to Bash'
Check (((Get-ODSWindowsPortalArguments @('--all')) -join ' ') -ceq '--all --pixel --no-hermes') 'shell all installation does not implicitly enable Hermes'
Check (((Get-ODSWindowsPortalArguments @('--hermes')) -join ' ') -ceq '--hermes --pixel') 'explicit optional Hermes preserves Pixel'
$normalized = @(Get-ODSWindowsPortalArguments $plan.PassthroughArgs)
Check (($normalized -join ' ') -ceq ($plan.PassthroughArgs -join ' ')) 'root arguments are not duplicated during WSL delegation'
$rejected = $false
try { $null = Get-ODSWindowsPortalArguments @('--no-pixel') } catch { $rejected = $true }
Check $rejected 'WSL Portal entrypoint cannot silently omit Pixel'
$store = Get-ODSPortalModelStorePaths 'D:\ODS Models\data\models'
Check ($store.WslPath -ceq '/mnt/d/ODS Models/data/models') 'one Windows model directory maps to WSL without splitting spaces'
foreach ($invalid in @('\\wsl.localhost\Ubuntu\models', 'D:\', 'D:\models\..\other', 'D:\models\x:stream', 'relative/models')) {
    $rejected=$false
    try { $null=Get-ODSPortalModelStorePaths $invalid } catch { $rejected=$true }
    Check $rejected "unsafe or unsupported shared model path rejected: $invalid"
}
foreach ($backend in @('nvidia','amd','none')) {
    $placement = Get-ODSWindowsPortalRuntimePlan @{ Backend=$backend }
    Check ($placement.Agent -ceq 'pixel' -and $placement.AgentHost -ceq 'wsl') "$backend preserves Pixel on WSL"
    Check $placement.ManagedLocally "$backend retains ODS model ownership"
}
$placement = Get-ODSWindowsPortalRuntimePlan @{ Backend='amd'; MemoryType='discrete' }
Check ($placement.InferenceHost -ceq 'windows' -and $placement.Backend -ceq 'vulkan') 'discrete AMD uses the existing Windows Vulkan runtime'
$placement = Get-ODSWindowsPortalRuntimePlan @{ Backend='amd'; MemoryType='unified' }
Check ($placement.InferenceHost -ceq 'windows') 'AMD APU uses the same owned Windows runtime path'
$placement = Get-ODSWindowsPortalRuntimePlan @{ Backend='nvidia' }
Check ($placement.InferenceHost -ceq 'docker' -and $placement.Backend -ceq 'cuda') 'NVIDIA retains the existing Docker CUDA runtime'
$placement = Get-ODSWindowsPortalRuntimePlan @{ Backend='amd' } -Cloud
Check ($placement.Backend -ceq 'cloud' -and -not $placement.ManagedLocally) 'explicit cloud selection does not take over the GPU'
$rejected = $false
try { $null = Get-ODSWindowsPortalRuntimePlan @{ Backend='unknown' } } catch { $rejected=$true }
Check $rejected 'unknown detection cannot silently become CPU or external'
Check (($plan.PassthroughArgs -join ' ') -ceq '--pixel --no-hermes') 'default requires Pixel and disables implicit Hermes'
$plan = Get-ODSWindowsPortalInstallPlan @{ All=$true; NoComfyui=$true; NoLangfuse=$true }
Check (($plan.PassthroughArgs -join ' ') -ceq '--all --no-comfyui --no-langfuse --pixel --no-hermes') 'all respects explicit overrides and never substitutes Hermes'
$plan = Get-ODSWindowsPortalInstallPlan @{ Hermes=$true }
Check ($plan.PassthroughArgs -contains '--hermes' -and $plan.PassthroughArgs -notcontains '--no-hermes') 'Hermes is explicit alongside Pixel'
$plan = Get-ODSWindowsPortalInstallPlan @{ Hermes=$true; NoHermes=$true }
Check ($plan.PassthroughArgs[-1] -ceq '--no-hermes') 'explicit disable takes precedence'
$plan = Get-ODSWindowsPortalInstallPlan @{ DryRun=$true; Tier='2'; SummaryJsonPath='D:\ODS Reports\summary.json' }
Check ($plan.PassthroughArgs -contains '--dry-run') 'dry run is preserved'
Check ($plan.PassthroughArgs -contains '/mnt/d/ODS Reports/summary.json') 'Windows summary path translated without splitting spaces'
Check ($plan.PassthroughArgs -contains '2') 'tier forwarded'
Reject @{ InstallDir='D:\ODS' } 'native installation root is not silently reused in WSL'
Check ((Get-ODSWindowsPortalInstallPlan @{ Lan=$true }).PassthroughArgs -contains '--lan') 'explicit LAN setting forwarded to the Linux installer'
Reject @{ SummaryJsonPath='relative.json' } 'ambiguous summary path rejected'
# Exercise the actual parameter dictionary type used by the root script.
function BoundPlan { param([switch]$DryRun); Get-ODSWindowsPortalInstallPlan $PSBoundParameters }
Check ((BoundPlan -DryRun).PassthroughArgs -contains '--dry-run') 'PowerShell bound parameters supported'

# Launch the real entrypoint in a disposable tree with recording installers.
# This proves routing/exit propagation without touching Docker, WSL or models.
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-entrypoint-' + [guid]::NewGuid().ToString('N'))
$fixture = [IO.Path]::GetFullPath($fixture)
try {
    $nativeDirectory = Join-Path $fixture 'ods/installers/windows'
    New-Item -ItemType Directory -Path (Join-Path $nativeDirectory 'lib') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../../../install.ps1') -Destination $fixture
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../../installers/windows/lib/portal-install-plan.ps1') -Destination (Join-Path $nativeDirectory 'lib')
    @'
param([string]$Distro, [string[]]$PassthroughArgs)
Write-Output ('WSL:' + $Distro + ':' + ($PassthroughArgs -join '|'))
exit 23
'@ | Set-Content -LiteralPath (Join-Path $fixture 'ods/installers/windows.ps1')
    @'
param([switch]$DryRun)
Write-Output ('NATIVE:' + $DryRun.IsPresent)
exit 17
'@ | Set-Content -LiteralPath (Join-Path $nativeDirectory 'install-windows.ps1')
    $shell = (Get-Process -Id $PID).Path
    $output = & $shell -NoProfile -File (Join-Path $fixture 'install.ps1') -DryRun -Distro Ubuntu-24.04
    Check ($LASTEXITCODE -eq 23) 'WSL installer failure exit code preserved'
    Check ($output -contains 'WSL:Ubuntu-24.04:--dry-run|--pixel|--no-hermes') 'actual entrypoint delegates to WSL with required Pixel'
    $output = & $shell -NoProfile -File (Join-Path $fixture 'install.ps1') -NativeWindows -DryRun
    Check ($LASTEXITCODE -eq 17) 'native installer failure exit code preserved'
    Check ($output -contains 'NATIVE:True') 'explicit native mode forwards only its supported parameters'
} finally {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $fixture.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temporary directory' }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Write-Host "$count checks passed"
exit 0
