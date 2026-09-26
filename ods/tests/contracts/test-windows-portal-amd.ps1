# AMD GPU route of the Windows Portal setup. No real GPU, download, MSI,
# scheduled task or Lemonade server is used.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1')
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

# --- Plan: which GPUs take the Lemonade route, and with which model ---------
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
$script:gpu = @{ Backend='amd'; Name='AMD Radeon RX 9070 XT'; VramMB=16304; Count=1; MemoryType='discrete'; SystemRamGB=47 }
$script:ramGB = 47
$plan = Get-ODSPortalAmdPlan $sourceRoot
Check ($plan.GpuName -eq 'AMD Radeon RX 9070 XT' -and $plan.VramMB -eq 16304) '16 GB AMD GPU takes the Lemonade route'
Check ($plan.LinuxTier -eq '2' -and $plan.ContextSize -ge 32768) '16 GB AMD GPU maps to tier 2 with a long context'
Check ($plan.GgufFile -match '\.gguf$' -and $plan.GgufUrl -match '^https://huggingface\.co/.+/resolve/[0-9a-f]{40}/' -and $plan.GgufSha256 -match '^[0-9a-f]{64}$') 'AMD model is a commit-pinned GGUF with a SHA-256'
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
    $dir = Get-ODSPortalLemonadeModel $modelPlan
    Check ($script:curlCalls -eq 1 -and (Get-Content -LiteralPath (Join-Path $dir 'Model-Q4.gguf') -Raw) -eq $payload) 'model downloads into the Windows Lemonade models folder'
    Check (-not (Test-Path -LiteralPath (Join-Path $dir 'Model-Q4.gguf.partial'))) 'finished download leaves no partial file'
    $null = Get-ODSPortalLemonadeModel $modelPlan
    Check ($script:curlCalls -eq 1) 'a verified model is not downloaded again'
    $script:curlBody = 'corrupted'
    $badPlan = [pscustomobject]@{ GgufFile='Other.gguf'; GgufUrl='https://example.invalid/Other.gguf'; GgufSha256=$payloadSha }
    $message = ''
    try { $null = Get-ODSPortalLemonadeModel $badPlan } catch { $message = $_.Exception.Message }
    Check ($message -match 'does not match its checksum' -and -not (Test-Path -LiteralPath (Join-Path $dir 'Other.gguf'))) 'checksum mismatch stops and keeps no bad model'
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    Remove-Item -LiteralPath $fixture -Recurse -Force
}

# --- Lemonade install: found, declined ---------------------------------------
function Resolve-ODSLemonadeExe([string]$ExecutableName) { return $script:foundExe }
function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) { $script:confirmed++; return $script:accept }
$script:confirmed = 0
$script:foundExe = 'C:\Users\u\AppData\Local\lemonade_server\bin\lemonade-server.exe'
Check ((Install-ODSPortalLemonade $sourceRoot $false) -eq $script:foundExe -and $script:confirmed -eq 0) 'installed Lemonade is reused without asking'
$script:foundExe = $null
$script:accept = $false
Check ($null -eq (Install-ODSPortalLemonade $sourceRoot $false) -and $script:confirmed -eq 1) 'declining the Lemonade install changes nothing'

# --- Orchestration: load before Linux, CPU fallback, newer Lemonade ------------
$script:calls = [Collections.Generic.List[string]]::new()
$script:installResult = 'C:\lemonade\bin\lemonade-server.exe'
$script:modern = $false
$script:healthy = $true
function Install-ODSPortalLemonade([string]$SourceRoot, [bool]$NonInteractive) { return $script:installResult }
function Get-ODSPortalLemonadeModel($Plan) { $script:calls.Add('model'); return 'C:\models' }
function Get-ODSLemonadeLaunchContract { param($ExecutablePath, $Port, $ModelsDir, $ContextSize) return [pscustomobject]@{ Modern=$script:modern; Version=[Version]'10.0.0'; Port=$Port; ContextSize=$ContextSize } }
function Register-ODSPortalLemonadeTask($Contract) { $script:calls.Add('task:' + $Contract.Port) }
function Wait-ODSPortalLemonadeHealth([int]$Port, [int]$Seconds) { $script:calls.Add('health'); return $script:healthy }
function Resolve-ODSLemonadeModelId { param($Port, $GgufFile) return 'extra.' + $GgufFile }
function Set-ODSLemonadeLoadedModel { param($Port, $ModelId, $ContextSize, $TimeoutSec) $script:calls.Add("load:${ModelId}:${ContextSize}:${TimeoutSec}") }
$env:AMD_INFERENCE_PORT = ''
$result = @(Initialize-ODSPortalAmdLemonade $plan $sourceRoot $false)
Check (($result -join ' ') -eq "--lemonade-url http://localhost:8080 --lemonade-model extra.$($plan.GgufFile) --lemonade-gpu-name AMD Radeon RX 9070 XT --lemonade-gpu-vram-mb 16304") 'ready Lemonade returns the Linux route arguments'
Check (($script:calls -join ',') -eq "model,task:8080,health,load:extra.$($plan.GgufFile):$($plan.ContextSize):900") 'model is downloaded, served, and loaded with its context before Linux runs'
$script:installResult = $null
Check (@(Initialize-ODSPortalAmdLemonade $plan $sourceRoot $false).Count -eq 0) 'declined Lemonade returns no Linux arguments (CPU route)'
$script:installResult = 'C:\lemonade\bin\lemonade-server.exe'
$script:modern = $true
$message = ''
try { $null = Initialize-ODSPortalAmdLemonade $plan $sourceRoot $false } catch { $message = $_.Exception.Message }
Check ($message -match 'newer than') 'a newer Lemonade release stops with instructions'
$script:modern = $false
$script:healthy = $false
$message = ''
try { $null = Initialize-ODSPortalAmdLemonade $plan $sourceRoot $false } catch { $message = $_.Exception.Message }
Check ($message -match 'did not answer') 'Lemonade that never becomes healthy stops setup'

Out-Pass "Passed $script:checks Windows Portal AMD contracts."
