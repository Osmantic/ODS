param([string]$InstallerPath = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $InstallerPath) { $InstallerPath = Join-Path $root 'installers/windows/install-windows.ps1' }
. (Join-Path $root 'installers/windows/lib/service-plan.ps1')
. (Join-Path $root 'installers/windows/lib/retained-extensions.ps1')
$pythonFile = (Get-Command python -ErrorAction Stop).Source
function Resolve-ODSHostAgentPython { @{ FilePath = $pythonFile; PrefixArgs = @() } }
function Write-AI { param($Message) }
function Write-AIWarn { param($Message) }
function Test-ODSWindowsWhisperCudaSupported { $true }
function Assert-True { param($Condition, $Message) if (-not $Condition) { throw $Message } }
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-retained-' + [Guid]::NewGuid().ToString('N'))
$installDir = Join-Path $fixture ('install space ' + [char]0x03bb)
try {
    New-Item -ItemType Directory -Path (Join-Path $installDir 'scripts') -Force | Out-Null
    foreach ($name in @('windows-retained-extensions.py', 'resolve-compose-stack.sh', 'compose-cache-policy.py')) {
        Copy-Item -LiteralPath (Join-Path $root "scripts/$name") -Destination (Join-Path $installDir 'scripts')
    }
    foreach ($name in @('docker-compose.base.yml', 'docker-compose.nvidia.yml', 'docker-compose.cpu.yml', 'docker-compose.tier0.yml', 'docker-compose.override.yml')) {
        [IO.File]::WriteAllText((Join-Path $installDir $name), 'services: {dashboard: {image: dashboard}}')
    }
    $flags = @('--env-file', '.env', '-f', 'docker-compose.base.yml')
    # Empty paths must not even resolve Python.
    function Resolve-ODSHostAgentPython { throw 'Unexpected Python dependency on a fresh install' }
    Assert-True ((Add-ODSWindowsRetainedExtensions -InstallDir $installDir -ComposeFlags $flags) -join ' ' -eq ($flags -join ' ')) 'Fresh selection changed'
    function Resolve-ODSHostAgentPython { @{ FilePath = $pythonFile; PrefixArgs = @() } }
    New-Item -ItemType Directory -Path (Join-Path $installDir 'data/user-extensions') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'extensions/library/services/actual-budget') -Destination (Join-Path $installDir 'data/user-extensions') -Recurse
    $cloudMode = $false; $gpuInfo = @{ Backend = 'nvidia'; Count = 1 }; $selectedTier = '0'
    $enableRecommended = $false; $script:gpuPassthroughFailed = $false
    foreach ($name in @('enableVoice','enableWorkflows','enableRag','enableHermes','enableComfyui',
        'enableDeepResearch','enablePrivacyShield','enableBraveSearch','enableODSProxy','enableRemoteAccess')) {
        Set-Variable -Name $name -Value $false
    }
    $source = [IO.File]::ReadAllText($InstallerPath)
    $start = $source.IndexOf('        $composeFlags = @("--env-file"')
    $end = $source.IndexOf('        # Validate compose files exist before launching', $start)
    Assert-True ($start -gt 0 -and $end -gt $start) 'Installer Compose assembly boundary missing'
    . ([scriptblock]::Create($source.Substring($start, $end - $start)))
    Assert-True ($composeFlags -contains 'data/user-extensions/actual-budget/compose.yaml') 'Retained ActualBudget omitted: up --remove-orphans would remove its container'
    Assert-True ($composeFlags[0] -eq '--env-file' -and $composeFlags[1] -eq '.env') 'Explicit .env lost'
    Assert-True ($composeFlags[-1] -eq 'docker-compose.override.yml' -and $composeFlags[-3] -eq 'docker-compose.tier0.yml') 'Owner/tier override order changed'
    Assert-True ($composeFlags[5] -eq 'docker-compose.nvidia.yml') 'Windows backend selection changed'
    $recipe = Join-Path $installDir 'data/user-extensions/actual-budget/compose.yaml'
    $gpuOverlay = Join-Path (Split-Path -Parent $recipe) 'compose.nvidia.yaml'
    [IO.File]::WriteAllText($gpuOverlay, 'services: {actual-budget: {environment: {GPU_FIXTURE: yes}}}')
    $script:gpuPassthroughFailed = $true
    . ([scriptblock]::Create($source.Substring($start, $end - $start)))
    Assert-True ($composeFlags[5] -eq 'docker-compose.cpu.yml') 'CPU fallback selection lost'
    Assert-True (-not ($composeFlags -contains 'data/user-extensions/actual-budget/compose.nvidia.yaml')) 'CPU fallback selected a user GPU overlay'
    $script:gpuPassthroughFailed = $false
    Move-Item -LiteralPath $recipe -Destination "$recipe.disabled"
    . ([scriptblock]::Create($source.Substring($start, $end - $start)))
    Assert-True (-not ($composeFlags -contains 'data/user-extensions/actual-budget/compose.yaml')) 'Disabled user recipe re-enabled'
    Move-Item -LiteralPath "$recipe.disabled" -Destination $recipe
    [IO.File]::WriteAllText($recipe, 'services: {actual-budget: {image: app, privileged: true}}')
    $rejected = $false
    try { $null = Add-ODSWindowsRetainedExtensions -InstallDir $installDir -ComposeFlags $flags }
    catch { $rejected = $true }
    Assert-True $rejected 'Unsafe retained recipe reached Compose'
    Assert-True ($source.IndexOf('Add-ODSWindowsRetainedExtensions', $start) -lt $source.IndexOf('$flagsFile = Join-Path')) 'Validation follows cache write'
    Write-Host '[PASS] Actual installer assembly retains ActualBudget, preserves state/order, rejects unsafe recipes, and needs no Python on fresh installs'
} finally {
    $resolved = [IO.Path]::GetFullPath($fixture)
    $parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if ($resolved.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved).StartsWith('ods-retained-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}
exit 0
