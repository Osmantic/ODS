#Requires -Version 5.1
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve production command signatures.')]
[CmdletBinding()]
param()
<#
.SYNOPSIS
    Integration tests for LLAMA_ARG_UBATCH / LLAMA_ARG_FIT_TARGET carry in
    New-ODSEnv. Exercises the real generator; no direct helper tests.
#>

$ErrorActionPreference = 'Stop'
$script:CreatedTestDirs = @{}

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:GeneratorPath = Join-Path $script:RepoRoot 'installers\windows\lib\env-generator.ps1'
if (-not (Test-Path -LiteralPath $script:GeneratorPath -PathType Leaf)) {
    throw "env-generator.ps1 not found at $script:GeneratorPath"
}
. $script:GeneratorPath

# --- Stubs for hardware/port/log dependencies (signatures from source) ---
function Get-ODSDockerMemoryGB { return 0 }
function Get-LlamaCpuBudget { param([string]$GpuBackend) return @{ Limit = '4.0'; Reservation = '1.0'; Available = '4.0' } }
function Resolve-WindowsODSPort {
    param([string]$Name, [int]$DefaultPort, [hashtable]$ExistingEnv, [string]$InstallDir = '', [switch]$RequireValidProcessOverride)
    if ($ExistingEnv -and $ExistingEnv.ContainsKey($Name)) {
        $p = 0
        if ([int]::TryParse(([string]$ExistingEnv[$Name]).Trim(), [ref]$p) -and $p -ge 1 -and $p -le 65535) { return $p }
    }
    return $DefaultPort
}
function Write-AIWarn { param([string]$Message) }

if (-not (Get-Variable -Name ODS_VERSION -Scope Script -ErrorAction SilentlyContinue)) {
    $script:ODS_VERSION = 'test'
}

# --- Helpers ---
function New-TestTierConfig {
    param([string]$GgufFile = 'test-model.gguf')
    return @{
        TierName = 'Test'
        LlmModel = 'test-model'
        GgufFile = $GgufFile
        MaxContext = 4096
        RecommendationSource = 'installer_tier_map'
        RecommendationPolicy = 'tier-map'
        RecommendationConfidence = 'medium'
        RecommendationReason = 'test'
        RecommendationAlternatives = ''
    }
}

function New-TestInstallDir {
    $base = [System.IO.Path]::GetTempPath()
    $name = 'ods-test-' + [Guid]::NewGuid().ToString('N')
    $dir = Join-Path $base $name
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $script:CreatedTestDirs[[IO.Path]::GetFullPath($dir)] = $name
    return $dir
}

function Assert-StrictChild {
    param([string]$Parent, [string]$Child)
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    $childFull = [System.IO.Path]::GetFullPath($Child)
    if (-not $childFull.StartsWith($parentFull + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path '$childFull' is not a strict child of '$parentFull'"
    }
}

function Invoke-Generate {
    param(
        [string]$InstallDir,
        [hashtable]$TierConfig,
        [string]$GpuBackend = 'nvidia',
        [string]$ODSMode = 'local'
    )
    return New-ODSEnv -InstallDir $InstallDir -TierConfig $TierConfig -Tier '1' `
        -GpuBackend $GpuBackend -ODSMode $ODSMode -SystemRamGB 16
}

function Get-EnvValue {
    param([string]$Path, [string]$Key)
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    $assignments = @($lines | Where-Object { $_ -cmatch ('^' + [regex]::Escape($Key) + '=') })
    if ($assignments.Count -eq 0) { return $null }
    return ($assignments[-1] -replace ('^' + [regex]::Escape($Key) + '='), '')
}

function Get-EnvValueCount {
    param([string]$Path, [string]$Key)
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    return @($lines | Where-Object { $_ -cmatch ('^' + [regex]::Escape($Key) + '=') }).Count
}

function Add-EnvLine {
    param([string]$Path, [string]$Line)
    $existing = [System.IO.File]::ReadAllText($Path)
    $nl = if ($existing.Contains("`r`n")) { "`r`n" } else { "`n" }
    [System.IO.File]::WriteAllText($Path, $existing + $nl + $Line + $nl, (New-Object System.Text.UTF8Encoding($false)))
}

function Set-EnvLine {
    param([string]$Path, [string]$Key, [string]$Value)
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    $out = New-Object 'System.Collections.Generic.List[string]'
    $replaced = $false
    foreach ($line in $lines) {
        if ($line -cmatch ('^' + [regex]::Escape($Key) + '=')) {
            if (-not $replaced) { $out.Add("$Key=$Value"); $replaced = $true }
        } else {
            $out.Add($line)
        }
    }
    if (-not $replaced) { $out.Add("$Key=$Value") }
    [System.IO.File]::WriteAllLines($Path, $out, (New-Object System.Text.UTF8Encoding($false)))
}

# --- Test 1: fresh generate, append tuning, rerun, expect kept exactly once ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    Assert-StrictChild -Parent $dir -Child $envPath
    if ([System.IO.Path]::GetFileName($envPath) -ne '.env') { throw 'unexpected leaf' }

    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw 'fresh generate did not write .env' }

    $stable = @{}
    foreach ($key in @('DASHBOARD_API_KEY','WEBUI_SECRET_KEY','GGUF_FILE','CTX_SIZE','GPU_BACKEND','ODS_MODE','LLM_BACKEND')) {
        $stable[$key] = Get-EnvValue -Path $envPath -Key $key
    }
    if ((Get-EnvValueCount $envPath 'LLAMA_ARG_UBATCH') -ne 0 -or (Get-EnvValueCount $envPath 'LLAMA_ARG_FIT_TARGET') -ne 0) { throw 'fresh install emitted tuning' }
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_UBATCH=128'
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_FIT_TARGET=1024'

    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null

    $ubatch = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_UBATCH'
    $fit = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_FIT_TARGET'
    if ($ubatch -ne '128') { throw "expected LLAMA_ARG_UBATCH=128, got '$ubatch'" }
    if ($fit -ne '1024') { throw "expected LLAMA_ARG_FIT_TARGET=1024, got '$fit'" }
    if ((Get-EnvValueCount -Path $envPath -Key 'LLAMA_ARG_UBATCH') -ne 1) { throw 'LLAMA_ARG_UBATCH duplicated' }
    if ((Get-EnvValueCount -Path $envPath -Key 'LLAMA_ARG_FIT_TARGET') -ne 1) { throw 'LLAMA_ARG_FIT_TARGET duplicated' }
    foreach ($key in $stable.Keys) {
        if ((Get-EnvValue -Path $envPath -Key $key) -cne $stable[$key]) { throw "rerun changed unrelated setting $key" }
    }
    if ((Get-EnvValue $envPath 'CTX_SIZE') -ne '4096') { throw 'context changed' }
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    if ((Get-EnvValueCount $envPath 'LLAMA_ARG_UBATCH') -ne 1 -or (Get-EnvValue $envPath 'LLAMA_ARG_UBATCH') -ne '128') { throw 'second rerun lost or duplicated tuning' }

} finally {
    if (Test-Path -LiteralPath $dir) {
        Assert-StrictChild -Parent ([System.IO.Path]::GetTempPath()) -Child $dir
        $resolved = (Resolve-Path -LiteralPath $dir).Path
        if (-not $script:CreatedTestDirs.ContainsKey($resolved) -or
            (Split-Path -Leaf $resolved) -cne $script:CreatedTestDirs[$resolved] -or
            (Split-Path -Leaf $resolved) -cnotmatch '^ods-test-[a-f0-9]{32}$') {
            throw 'Refusing cleanup outside this test invocation'
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

# --- Table-driven cases ---
$cases = @(
    @{ Name = 'quoted identities'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = "' 128 '"; Fit = '1024'; ExpectUbatch = '128'; ExpectFit = '1024'; QuoteIdentity = $true },
    @{ Name = 'inclusive lower bounds'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '1'; Fit = '512'; ExpectUbatch = '1'; ExpectFit = '512' },
    @{ Name = 'inclusive upper bounds'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '512'; Fit = '65536'; ExpectUbatch = '512'; ExpectFit = '65536' },
    @{ Name = 'invalid model store'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = '../other'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null },
    @{ Name = 'literal command rejected'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128; Write-Output bad'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = '1024' },
    @{ Name = 'valid single quotes'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = "'128'"; Fit = "'1024'"; ExpectUbatch = '128'; ExpectFit = '1024' },
    @{ Name = 'valid double quotes'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '"256"'; Fit = '"2048"'; ExpectUbatch = '256'; ExpectFit = '2048' },
    @{ Name = 'invalid ubatch leading zero'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '0128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = '1024' },
    @{ Name = 'invalid fit leading zero'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '01024'; ExpectUbatch = '128'; ExpectFit = $null },
    @{ Name = 'ubatch out of range high'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '513'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = '1024' },
    @{ Name = 'fit out of range low'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '511'; ExpectUbatch = '128'; ExpectFit = $null },
    @{ Name = 'same cpu backend'; Gpu = 'cpu'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = '128'; ExpectFit = '1024' },
    @{ Name = 'same none backend'; Gpu = 'none'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = '128'; ExpectFit = '1024' },
    @{ Name = 'changed model'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'other-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null },
    @{ Name = 'changed backend'; Gpu = 'cpu'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null; PrevGpu = 'nvidia' },
    @{ Name = 'amd forbidden'; Gpu = 'amd'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null; PrevGpu = 'amd' },
    @{ Name = 'hybrid mode'; Gpu = 'nvidia'; Mode = 'hybrid'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null },
    @{ Name = 'cloud mode'; Gpu = 'nvidia'; Mode = 'cloud'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null },
    @{ Name = 'unknown gpu matching previous'; Gpu = 'vulkan'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null; PrevGpu = 'vulkan' },
    @{ Name = 'prior remote engine'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null; PrevLlm = 'litellm' },
    @{ Name = 'prior vllm engine'; Gpu = 'nvidia'; Mode = 'local'; Gguf = 'test-model.gguf'; Store = 'default'; Ubatch = '128'; Fit = '1024'; ExpectUbatch = $null; ExpectFit = $null; PrevLlm = 'vllm' }
)

foreach ($case in $cases) {
    $dir = New-TestInstallDir
    try {
        $envPath = Join-Path $dir '.env'
        Assert-StrictChild -Parent $dir -Child $envPath

        $tier = New-TestTierConfig -GgufFile $case.Gguf
        $prevGpu = if ($case.ContainsKey('PrevGpu')) { $case.PrevGpu } else { $case.Gpu }
        $prevLlm = if ($case.ContainsKey('PrevLlm')) { $case.PrevLlm } else { 'llama-server' }

        # Seed a previous .env with the identity we want to test against.
        $seed = @(
            "GGUF_FILE=test-model.gguf",
            "ODS_ACTIVE_MODEL_STORE=$($case.Store)",
            "GPU_BACKEND=$prevGpu",
            "ODS_MODE=$($case.Mode)",
            "LLM_BACKEND=$prevLlm",
            "LLAMA_ARG_UBATCH=$($case.Ubatch)",
            "LLAMA_ARG_FIT_TARGET=$($case.Fit)"
        ) -join "`n"
        [System.IO.File]::WriteAllText($envPath, $seed + "`n", (New-Object System.Text.UTF8Encoding($false)))
        if ($case.QuoteIdentity) {
            foreach ($key in @('GGUF_FILE','GPU_BACKEND','ODS_MODE','LLM_BACKEND','ODS_ACTIVE_MODEL_STORE')) {
                Set-EnvLine $envPath $key ('"' + (Get-EnvValue $envPath $key) + '"')
            }
        }

        Invoke-Generate -InstallDir $dir -TierConfig $tier -GpuBackend $case.Gpu -ODSMode $case.Mode | Out-Null

        $ubatch = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_UBATCH'
        $fit = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_FIT_TARGET'
        if ($null -eq $case.ExpectUbatch) {
            if ($null -ne $ubatch) { throw "[$($case.Name)] expected no LLAMA_ARG_UBATCH, got '$ubatch'" }
        } else {
            if ($ubatch -ne $case.ExpectUbatch) { throw "[$($case.Name)] expected LLAMA_ARG_UBATCH=$($case.ExpectUbatch), got '$ubatch'" }
        }
        if ($null -eq $case.ExpectFit) {
            if ($null -ne $fit) { throw "[$($case.Name)] expected no LLAMA_ARG_FIT_TARGET, got '$fit'" }
        } else {
            if ($fit -ne $case.ExpectFit) { throw "[$($case.Name)] expected LLAMA_ARG_FIT_TARGET=$($case.ExpectFit), got '$fit'" }
        }
    } finally {
        if (Test-Path -LiteralPath $dir) {
            Assert-StrictChild -Parent ([System.IO.Path]::GetTempPath()) -Child $dir
            $resolved = (Resolve-Path -LiteralPath $dir).Path
        if (-not $script:CreatedTestDirs.ContainsKey($resolved) -or
            (Split-Path -Leaf $resolved) -cne $script:CreatedTestDirs[$resolved] -or
            (Split-Path -Leaf $resolved) -cnotmatch '^ods-test-[a-f0-9]{32}$') {
            throw 'Refusing cleanup outside this test invocation'
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}

# --- Last assignment wins ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    Assert-StrictChild -Parent $dir -Child $envPath
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_UBATCH=64'
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_UBATCH=128'
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_FIT_TARGET=512'
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_FIT_TARGET=1024'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $ubatch = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_UBATCH'
    $fit = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_FIT_TARGET'
    if ($ubatch -ne '128') { throw "last-wins ubatch expected 128, got '$ubatch'" }
    if ($fit -ne '1024') { throw "last-wins fit expected 1024, got '$fit'" }
} finally {
    if (Test-Path -LiteralPath $dir) {
        Assert-StrictChild -Parent ([System.IO.Path]::GetTempPath()) -Child $dir
        $resolved = (Resolve-Path -LiteralPath $dir).Path
        if (-not $script:CreatedTestDirs.ContainsKey($resolved) -or
            (Split-Path -Leaf $resolved) -cne $script:CreatedTestDirs[$resolved] -or
            (Split-Path -Leaf $resolved) -cnotmatch '^ods-test-[a-f0-9]{32}$') {
            throw 'Refusing cleanup outside this test invocation'
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

# --- Extension claims reserved keys cannot bypass bounds ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    Assert-StrictChild -Parent $dir -Child $envPath
    $extDir = Join-Path (Join-Path (Join-Path $dir 'extensions') 'services') 'evil'
    New-Item -ItemType Directory -Path $extDir -Force | Out-Null
    $manifest = Join-Path $extDir 'manifest.yaml'
    $manifestBody = @"
env_vars:
  - key: LLAMA_ARG_UBATCH
  - key: LLAMA_ARG_FIT_TARGET
  - key: MY_EXT_KEY
"@
    [System.IO.File]::WriteAllText($manifest, $manifestBody, (New-Object System.Text.UTF8Encoding($false)))

    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_UBATCH=9999'
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_FIT_TARGET=999999'
    Add-EnvLine -Path $envPath -Line 'MY_EXT_KEY=hello'

    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null

    $ubatch = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_UBATCH'
    $fit = Get-EnvValue -Path $envPath -Key 'LLAMA_ARG_FIT_TARGET'
    $ext = Get-EnvValue -Path $envPath -Key 'MY_EXT_KEY'
    if ($null -ne $ubatch) { throw "extension bypass: LLAMA_ARG_UBATCH should be rejected, got '$ubatch'" }
    if ($null -ne $fit) { throw "extension bypass: LLAMA_ARG_FIT_TARGET should be rejected, got '$fit'" }
    if ($ext -ne 'hello') { throw "extension carry broken: MY_EXT_KEY expected 'hello', got '$ext'" }
    Add-EnvLine $envPath 'LLAMA_ARG_UBATCH=128'
    Add-EnvLine $envPath 'LLAMA_ARG_FIT_TARGET=1024'
    Invoke-Generate -InstallDir $dir -TierConfig (New-TestTierConfig -GgufFile 'other.gguf') | Out-Null
    if ((Get-EnvValueCount $envPath 'LLAMA_ARG_UBATCH') -ne 0 -or (Get-EnvValueCount $envPath 'LLAMA_ARG_FIT_TARGET') -ne 0) {
        throw 'extension carry bypassed the changed-model gate'
    }
} finally {
    if (Test-Path -LiteralPath $dir) {
        Assert-StrictChild -Parent ([System.IO.Path]::GetTempPath()) -Child $dir
        $resolved = (Resolve-Path -LiteralPath $dir).Path
        if (-not $script:CreatedTestDirs.ContainsKey($resolved) -or
            (Split-Path -Leaf $resolved) -cne $script:CreatedTestDirs[$resolved] -or
            (Split-Path -Leaf $resolved) -cnotmatch '^ods-test-[a-f0-9]{32}$') {
            throw 'Refusing cleanup outside this test invocation'
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

Write-Host "PASS: container tuning reruns, $($cases.Count) identity/value cases, duplicate and extension cases"
