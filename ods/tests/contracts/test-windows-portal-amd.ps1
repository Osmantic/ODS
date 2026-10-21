# AMD GPU route of the Windows Portal setup (llama.cpp on Windows). No real
# GPU, download, scheduled task or model server is used.
param([switch]$SkipProcessFixtures)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1')
# PowerShell 7 on Linux (CI) has no Windows identities, and setup names its
# runtime tasks by the caller's SID. A fixed SID stands in for the caller there.
if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) {
    function Get-ODSPortalUserSid([string]$UserId) {
        if ($UserId) { return $UserId }
        return 'S-1-5-21-1000-1000-1000-1001'
    }
}
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$script:checks = 0
# Setup messages are captured so tests can read them; results print directly.
function Write-Host { param([Parameter(ValueFromRemainingArguments = $true)]$Text) $script:output += ,([string]($Text -join ' ')) }
function Out-Pass([string]$Message) { Microsoft.PowerShell.Utility\Write-Host "PASS $Message" }
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Out-Pass $Message
}

# --- Plan: which GPUs take the llama.cpp route, and with which model --------
$script:gpu = $null
$script:ramGB = 47
function Get-GpuInfo { return $script:gpu }
function Get-SystemRamGB { return $script:ramGB }
$env:MODEL_PROFILE = ''

$script:gpu = @{ Backend='nvidia'; Name='NVIDIA GeForce RTX 4070'; VramMB=12282; MemoryType='discrete' }
Check ($null -eq (Get-ODSPortalAmdPlan $sourceRoot)) 'NVIDIA keeps the in-WSL CUDA route'
$script:gpu = @{ Backend='none'; Name='None'; VramMB=0; MemoryType='none' }
Check ($null -eq (Get-ODSPortalAmdPlan $sourceRoot)) 'no GPU keeps the CPU route'
$script:gpu = @{ Backend='amd'; Name='AMD Radeon(TM) Graphics'; VramMB=512; MemoryType='discrete'; SystemRamGB=8 }
$script:ramGB = 8
$script:output = @()
Check ($null -eq (Get-ODSPortalAmdPlan $sourceRoot)) 'AMD GPU with too little memory keeps the CPU route'
Check (($script:output -join ' ') -match 'too little graphics memory') 'too-small AMD GPU says why it uses the CPU'
foreach ($memory in @(12, 16, 24, 31)) {
    $script:ramGB = $memory
    $script:output = @()
    Check ($null -eq (Get-ODSPortalAmdPlan $sourceRoot)) "512MB iGPU with ${memory}GB RAM takes CPU when the real catalog has no GPU fit"
    Check (($script:output -join ' ') -match 'Pixel will use the CPU route') 'iGPU CPU route is explicit and preserves Pixel'
}
$script:gpu.Name = 'Unrecognized AMD adapter name'
$script:ramGB = 16
Check ($null -eq (Get-ODSPortalAmdPlan $sourceRoot)) 'CPU recovery for low-memory AMD does not depend on an adapter-name allowlist'
$script:gpu = @{ Backend='amd'; Name='AMD Radeon RX 9070 XT'; VramMB=16304; Count=1; MemoryType='discrete'; SystemRamGB=47 }
$script:ramGB = 47
$plan = Get-ODSPortalAmdPlan $sourceRoot
Check ($plan.GpuName -eq 'AMD Radeon RX 9070 XT' -and $plan.VramMB -eq 16304) '16 GB AMD GPU takes the llama.cpp route'
Check ($plan.LinuxTier -eq '2' -and $plan.ContextSize -ge 32768) '16 GB AMD GPU maps to tier 2 with a long context'
Check ($plan.GgufFile -match '\.gguf$' -and $plan.GgufFile -cmatch '^[\x20-\x7e]+$' -and $plan.GgufUrl -match '^https://huggingface\.co/.+/resolve/[0-9a-f]{40}/' -and $plan.GgufSha256 -match '^[0-9a-f]{64}$') 'AMD model is a commit-pinned ASCII GGUF with a SHA-256'
$script:gpu = @{ Backend='amd'; Name='AMD Radeon 8060S Graphics'; VramMB=98304; Count=1; MemoryType='unified'; SystemRamGB=128 }
$script:ramGB = 128
$large = Get-ODSPortalAmdPlan $sourceRoot
Check ($large.LinuxTier -eq '4' -and $large.Tier -notmatch '^[1-4]$') 'Strix Halo class maps to the Linux top tier'

# --- Model download: checksum verified, existing good file reused ----------
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-amd-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previousLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $fixture
try {
    $payload = 'fake gguf bytes'
    $payloadFile = Join-Path $fixture 'payload'
    [IO.File]::WriteAllText($payloadFile, $payload)
    $payloadSha = (Get-FileHash -LiteralPath $payloadFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $script:curlCalls = 0
    $script:curlBody = $payload
    function curl.exe {
        $script:curlCalls++
        $out = $args[[array]::IndexOf($args, '--output') + 1]
        [IO.File]::WriteAllText($out, $script:curlBody)
        $global:LASTEXITCODE = 0
    }
    $modelPlan = [pscustomobject]@{ GgufFile='Model-Q4.gguf'; GgufUrl='https://example.invalid/Model-Q4.gguf'; GgufSha256=$payloadSha }
    $dir = Get-ODSPortalModel $modelPlan
    Check ($dir -eq (Get-ODSPortalModelsDir) -and $script:curlCalls -eq 1 -and (Get-Content -LiteralPath (Join-Path $dir 'Model-Q4.gguf') -Raw) -eq $payload) 'model downloads into the ODS Windows model store'
    Check (-not (Test-Path -LiteralPath (Join-Path $dir 'Model-Q4.gguf.partial'))) 'finished download leaves no partial file'
    $null = Get-ODSPortalModel $modelPlan
    Check ($script:curlCalls -eq 1) 'a verified model is not downloaded again'
    $script:curlBody = 'corrupted'
    $badPlan = [pscustomobject]@{ GgufFile='Other.gguf'; GgufUrl='https://example.invalid/Other.gguf'; GgufSha256=$payloadSha }
    $message = ''
    try { $null = Get-ODSPortalModel $badPlan } catch { $message = $_.Exception.Message }
    Check ($message -match 'does not match its checksum' -and -not (Test-Path -LiteralPath (Join-Path $dir 'Other.gguf'))) 'checksum mismatch stops and keeps no bad model'

    # --- Port: a busy 8080 moves to the next quiet port, never Lemonade's ------
    $script:busy = @{}
    function Get-ODSPortalPortOwner([int]$Port) { return $script:busy[$Port] }
    function Test-ODSPortalPortBindable([int]$Port) { return $true }
    $env:AMD_INFERENCE_PORT = ''
    Check ((Select-ODSPortalRuntimePort) -eq 8080) 'a free machine uses the pinned port 8080'
    $script:busy = @{ 8080 = 'AgentService' }
    Check ((Select-ODSPortalRuntimePort) -eq 18080) 'port 8080 used by another program moves llama.cpp to 18080, not to a Lemonade default'
    $script:busy = @{ 8080 = 'a'; 18080 = 'd'; 28080 = 'e' }
    $message = ''
    try { $null = Select-ODSPortalRuntimePort } catch { $message = $_.Exception.Message }
    Check ($message -match '8080 \(a\).+28080 \(e\)' -and $message -match 'AMD_INFERENCE_PORT' -and $message -notmatch '13305') 'no free port names every busy candidate and the override'
    $script:busy = @{ 9999 = 'Other' }
    $env:AMD_INFERENCE_PORT = '9999'
    $message = ''
    try { $null = Select-ODSPortalRuntimePort } catch { $message = $_.Exception.Message }
    Check ($message -match "9999 is already used by 'Other'") 'a busy AMD_INFERENCE_PORT stops with its owner'
    $script:busy = @{}
    Check ((Select-ODSPortalRuntimePort) -eq 9999) 'a free AMD_INFERENCE_PORT is used as given'
    $env:AMD_INFERENCE_PORT = ''

    # --- A Lemonade the user installed is neither reused nor required ----------
    $userLemonade = Join-Path (Join-Path (Join-Path $fixture 'lemonade_server') 'bin') 'LemonadeServer.exe'
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $userLemonade) -Force
    [IO.File]::WriteAllText($userLemonade, 'user lemonade')
    function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction, $ErrorVariable) }
    function Get-ODSNativeLlamaPin { param($SourceRoot) [pscustomobject]@{ ReleaseTag = 'b9014'; Build = 9014; Asset = 'llama-b9014-bin-win-vulkan-x64.zip'
        Sha256 = ('a' * 64); Size = 33541404; Url = 'https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-vulkan-x64.zip'; Source = 'fixture' } }
    $script:prompts = @()
    function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) { $script:prompts += $Message; return $false }
    $script:output = @()
    Check ($null -eq (Initialize-ODSPortalAmdRuntime $plan $sourceRoot $false 'Ubuntu-24.04' '/home/user/ods')) 'declining llama.cpp keeps the CPU route even when Lemonade is installed'
    Check ($script:prompts.Count -eq 1 -and $script:prompts[0] -match 'llama\.cpp' -and $script:prompts[0] -notmatch 'Lemonade' -and
        -not (($script:output -join ' ') -match 'Lemonade')) 'an installed Lemonade is never reused, adopted or offered as the GPU route'
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    Remove-Item -LiteralPath $fixture -Recurse -Force
}

Out-Pass "Passed $script:checks Windows Portal AMD contracts."
& (Join-Path $PSScriptRoot 'test-windows-portal-llama-restart.ps1') -SkipProcessFixtures:$SkipProcessFixtures
& (Join-Path $PSScriptRoot 'test-windows-portal-model-control.ps1')
& (Join-Path $PSScriptRoot 'test-windows-portal-amd-recovery.ps1')
& (Join-Path $PSScriptRoot 'test-windows-portal-llama-migration.ps1')
