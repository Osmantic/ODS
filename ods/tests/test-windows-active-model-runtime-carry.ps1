#Requires -Version 5.1
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve production command signatures.')]
[CmdletBinding()]
param(
    [string]$GeneratorPath = ''
)
<#
.SYNOPSIS
    Integration tests for LLAMA_ARG_MMPROJ and paired chat-template carry in
    New-ODSEnv, plus helper contracts for generated-value precedence.
.PARAMETER GeneratorPath
    Optional path to an env-generator.ps1 to test. Defaults to this repo's
    installers/windows/lib/env-generator.ps1. Point at an old-main copy to
    confirm the negative control fails the positive carry assertions.
#>

$ErrorActionPreference = 'Stop'
$script:CreatedTestDirs = @{}

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($GeneratorPath)) {
    $GeneratorPath = Join-Path $script:RepoRoot 'installers\windows\lib\env-generator.ps1'
}
if (-not (Test-Path -LiteralPath $GeneratorPath -PathType Leaf)) {
    throw "env-generator.ps1 not found at $GeneratorPath"
}
. $GeneratorPath

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

function Write-TestFile {
    param([string]$Path, [string]$Content)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
}

function New-TestProjector {
    param([string]$Dir, [string]$Name = 'mmproj-test.gguf', [int]$Size = 64)
    $path = Join-Path $Dir $Name
    $bytes = New-Object byte[] $Size
    for ($i = 0; $i -lt $Size; $i++) { $bytes[$i] = [byte](($i * 7 + 3) % 251) }
    [System.IO.File]::WriteAllBytes($path, $bytes)
    return $path
}

function New-TestWeights {
    param([string]$Dir, [string]$Name = 'test-model.gguf', [int]$Size = 128)
    $path = Join-Path $Dir $Name
    $bytes = New-Object byte[] $Size
    for ($i = 0; $i -lt $Size; $i++) { $bytes[$i] = [byte](($i * 11 + 5) % 251) }
    [System.IO.File]::WriteAllBytes($path, $bytes)
    return $path
}

function New-TestCatalog {
    param([string]$InstallDir, [string]$GgufFile, [string]$MmprojFile, [int]$MmprojSize)
    $catalog = [ordered]@{
        models = @(
            [ordered]@{
                id = 'test-model'
                gguf_file = $GgufFile
                mmproj_file = $MmprojFile
                mmproj_size_bytes = $MmprojSize
            }
        )
    }
    Write-TestFile -Path (Join-Path (Join-Path $InstallDir 'config') 'model-library.json') -Content (($catalog | ConvertTo-Json -Depth 6) + "`n")
}

function New-TestImports {
    param([string]$InstallDir, [string]$GgufFile, [string]$MmprojFile, [int]$MmprojSize)
    $imports = [ordered]@{
        models = @(
            [ordered]@{
                id = 'hf-test-model'
                source = 'huggingface'
                gguf_file = $GgufFile
                mmproj_file = $MmprojFile
                mmproj_size_bytes = $MmprojSize
            }
        )
    }
    Write-TestFile -Path (Join-Path (Join-Path $InstallDir 'data') 'model-imports.json') -Content (($imports | ConvertTo-Json -Depth 6) + "`n")
}

function New-TestStoreRegistry {
    param([string]$InstallDir, [string]$StoreId, [string]$HostPath)
    $registry = [ordered]@{
        schemaVersion = 1
        stores = @(
            [ordered]@{
                id = $StoreId
                hostPath = $HostPath
                containerPath = "/model-stores/$StoreId"
                profiles = @{}
            }
        )
    }
    Write-TestFile -Path (Join-Path (Join-Path $InstallDir 'data') 'model-stores.json') -Content (($registry | ConvertTo-Json -Depth 6) + "`n")
}

function New-TestChatTemplate {
    param([string]$InstallDir, [string]$OverrideId = 'test-fix', [string]$TemplateBody = '{{ messages }}')
    $root = Join-Path (Join-Path $InstallDir 'config') 'chat-templates'
    $vendorDir = Join-Path $root 'upstream-b9014'
    New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null
    $templateName = 'test-fix.jinja'
    $templatePath = Join-Path $vendorDir $templateName
    Write-TestFile -Path $templatePath -Content $TemplateBody
    $sha = (Get-FileHash -LiteralPath $templatePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest = "$sha  $templateName`n"
    Write-TestFile -Path (Join-Path $vendorDir 'SHA256SUMS') -Content $manifest
    Write-TestFile -Path (Join-Path $vendorDir 'LICENSE') -Content "MIT License`n`nCopyright (c) test`n"
    $index = [ordered]@{
        schemaVersion = 1
        overrides = @(
            [ordered]@{
                id = $OverrideId
                embeddedTemplateSha256 = ('a' * 64)
                file = "upstream-b9014/$templateName"
                fileSha256 = $sha
                builds = @('b9014')
                reason = 'test fixture'
            }
        )
    }
    Write-TestFile -Path (Join-Path $root 'index.json') -Content (($index | ConvertTo-Json -Depth 6) + "`n")
    return @{ OverrideId = $OverrideId; ContainerFile = "/chat-templates/upstream-b9014/$templateName"; Sha = $sha }
}

function Assert-Carried {
    param([string]$EnvPath, [string]$Key, [string]$Expected)
    $actual = Get-EnvValue -Path $EnvPath -Key $Key
    if ($actual -cne $Expected) { throw "expected $Key=$Expected, got '$actual'" }
    if ((Get-EnvValueCount -Path $EnvPath -Key $Key) -ne 1) { throw "$Key duplicated" }
}

function Assert-NotCarried {
    param([string]$EnvPath, [string]$Key)
    if ((Get-EnvValueCount -Path $EnvPath -Key $Key) -ne 0) {
        throw "expected no $Key, got '$((Get-EnvValue -Path $EnvPath -Key $Key))'"
    }
}

function Remove-TestDir {
    param([string]$Dir)
    if (-not (Test-Path -LiteralPath $Dir)) { return }
    Assert-StrictChild -Parent ([System.IO.Path]::GetTempPath()) -Child $Dir
    $resolved = (Resolve-Path -LiteralPath $Dir).Path
    if (-not $script:CreatedTestDirs.ContainsKey($resolved) -or
        (Split-Path -Leaf $resolved) -cne $script:CreatedTestDirs[$resolved] -or
        (Split-Path -Leaf $resolved) -cnotmatch '^ods-test-[a-f0-9]{32}$') {
        throw 'Refusing cleanup outside this test invocation'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

# --- Test 1: fresh generate, seed catalog + projector, rerun twice ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    Assert-StrictChild -Parent $dir -Child $envPath
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw 'fresh generate did not write .env' }
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'

    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize

    # A downloaded projector must not enable itself without a saved setting.
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'
    Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ' -Expected '/models/mmproj-test.gguf'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ' -Expected '/models/mmproj-test.gguf'
} finally { Remove-TestDir -Dir $dir }

# --- Test 2: HF import path and quoted saved values ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestImports -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    Write-TestFile (Join-Path $dir 'config/model-library.json') '{"models":[]}'
    Set-EnvLine $envPath 'GGUF_FILE' "'test-model.gguf'"
    Add-EnvLine $envPath "LLAMA_ARG_MMPROJ='/models/mmproj-test.gguf'"
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ' -Expected "'/models/mmproj-test.gguf'"
} finally { Remove-TestDir -Dir $dir }

# --- Test 3: registered non-default store ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $storeDir = Join-Path $dir 'external-store'
    New-Item -ItemType Directory -Path $storeDir -Force | Out-Null
    New-TestWeights -Dir $storeDir | Out-Null
    $proj = New-TestProjector -Dir $storeDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestStoreRegistry -InstallDir $dir -StoreId 'ext' -HostPath $storeDir
    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    Set-EnvLine -Path $envPath -Key 'ODS_ACTIVE_MODEL_STORE' -Value 'ext'
    Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ' -Expected '/models/mmproj-test.gguf'
} finally { Remove-TestDir -Dir $dir }

# --- Test 4: failure cases ---
$failureCases = @(
    @{ Name = 'changed model'; Gguf = 'other-model.gguf'; Store = 'default'; Gpu = 'nvidia'; Mode = 'local'; PrevGpu = 'nvidia'; PrevMode = 'local'; PrevLlm = 'llama-server'; ExpectCarry = $false },
    @{ Name = 'changed store'; Gguf = 'test-model.gguf'; Store = 'other'; Gpu = 'nvidia'; Mode = 'local'; PrevGpu = 'nvidia'; PrevMode = 'local'; PrevLlm = 'llama-server'; ExpectCarry = $false },
    @{ Name = 'changed mode'; Gguf = 'test-model.gguf'; Store = 'default'; Gpu = 'nvidia'; Mode = 'hybrid'; PrevGpu = 'nvidia'; PrevMode = 'local'; PrevLlm = 'llama-server'; ExpectCarry = $false },
    @{ Name = 'same model and engine on CPU'; Gguf = 'test-model.gguf'; Store = 'default'; Gpu = 'cpu'; Mode = 'local'; PrevGpu = 'nvidia'; PrevMode = 'local'; PrevLlm = 'llama-server'; ExpectCarry = $true },
    @{ Name = 'prior remote engine'; Gguf = 'test-model.gguf'; Store = 'default'; Gpu = 'nvidia'; Mode = 'local'; PrevGpu = 'nvidia'; PrevMode = 'local'; PrevLlm = 'litellm'; ExpectCarry = $false },
    @{ Name = 'cloud mode'; Gguf = 'test-model.gguf'; Store = 'default'; Gpu = 'nvidia'; Mode = 'cloud'; PrevGpu = 'nvidia'; PrevMode = 'cloud'; PrevLlm = 'litellm'; ExpectCarry = $false }
)
foreach ($case in $failureCases) {
    $dir = New-TestInstallDir
    try {
        $envPath = Join-Path $dir '.env'
        $tier = New-TestTierConfig -GgufFile $case.Gguf
        $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
        New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
        New-TestWeights -Dir $modelsDir | Out-Null
        $proj = New-TestProjector -Dir $modelsDir
        $projSize = ([System.IO.FileInfo]::new($proj)).Length
        New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
        $seed = @(
            'GGUF_FILE=test-model.gguf',
            "ODS_ACTIVE_MODEL_STORE=$($case.Store)",
            "GPU_BACKEND=$($case.PrevGpu)",
            "ODS_MODE=$($case.PrevMode)",
            "LLM_BACKEND=$($case.PrevLlm)",
            'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
        ) -join "`n"
        [System.IO.File]::WriteAllText($envPath, $seed + "`n", (New-Object System.Text.UTF8Encoding($false)))
        Invoke-Generate -InstallDir $dir -TierConfig $tier -GpuBackend $case.Gpu -ODSMode $case.Mode | Out-Null
        Assert-Carried -EnvPath $envPath -Key 'GPU_BACKEND' -Expected $case.Gpu
        if ($case.ExpectCarry) {
            Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ' -Expected '/models/mmproj-test.gguf'
        } else {
            Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'
        }
    } finally { Remove-TestDir -Dir $dir }
}

# --- Test 5: missing / truncated projector ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ' -Expected '/models/mmproj-test.gguf'
    # Truncate: size mismatch must drop.
    [System.IO.File]::WriteAllBytes($proj, (New-Object byte[] 8))
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'
    # Missing: drop.
    Remove-Item -LiteralPath $proj -Force
    Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'
} finally { Remove-TestDir -Dir $dir }

# --- Test 6: projector-as-shard / weight ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    $catalog = [ordered]@{
        models = @(
            [ordered]@{
                id = 'test-model'
                gguf_file = 'test-model.gguf'
                mmproj_file = 'mmproj-test.gguf'
                mmproj_size_bytes = $projSize
                gguf_parts = @(
                    [ordered]@{ file = 'mmproj-test.gguf'; size_bytes = $projSize }
                )
            }
        )
    }
    Write-TestFile -Path (Join-Path (Join-Path $dir 'config') 'model-library.json') -Content (($catalog | ConvertTo-Json -Depth 6) + "`n")
    Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'
} finally { Remove-TestDir -Dir $dir }

# --- Test 7: extension manifest cannot smuggle stale projector ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $extDir = Join-Path (Join-Path (Join-Path $dir 'extensions') 'services') 'evil'
    New-Item -ItemType Directory -Path $extDir -Force | Out-Null
    $manifestBody = "env_vars:`n  - key: LLAMA_ARG_MMPROJ`n  - key: MODEL_CHAT_TEMPLATE_OVERRIDE`n  - key: LLAMA_ARG_CHAT_TEMPLATE_FILE`n  - key: MY_EXT_KEY`n"
    Write-TestFile -Path (Join-Path $extDir 'manifest.yaml') -Content $manifestBody
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_MMPROJ=/models/stale.gguf'
    Add-EnvLine -Path $envPath -Line 'MODEL_CHAT_TEMPLATE_OVERRIDE=stale'
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_CHAT_TEMPLATE_FILE=/chat-templates/stale.jinja'
    Add-EnvLine -Path $envPath -Line 'MY_EXT_KEY=hello'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'
    Assert-NotCarried -EnvPath $envPath -Key 'MODEL_CHAT_TEMPLATE_OVERRIDE'
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_CHAT_TEMPLATE_FILE'
    Assert-Carried -EnvPath $envPath -Key 'MY_EXT_KEY' -Expected 'hello'
} finally { Remove-TestDir -Dir $dir }

# --- Test 8: paired chat template carry ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    $tpl = New-TestChatTemplate -InstallDir $dir
    Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
    Add-EnvLine -Path $envPath -Line "MODEL_CHAT_TEMPLATE_OVERRIDE=$($tpl.OverrideId)"
    Add-EnvLine -Path $envPath -Line "LLAMA_ARG_CHAT_TEMPLATE_FILE=$($tpl.ContainerFile)"
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-Carried -EnvPath $envPath -Key 'MODEL_CHAT_TEMPLATE_OVERRIDE' -Expected $tpl.OverrideId
    Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_CHAT_TEMPLATE_FILE' -Expected $tpl.ContainerFile
    Assert-Carried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ' -Expected '/models/mmproj-test.gguf'
} finally { Remove-TestDir -Dir $dir }

# --- Test 9: bad sha / half / unknown / no-mix ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    $tpl = New-TestChatTemplate -InstallDir $dir
    # Bad sha: corrupt the template file after index was written.
    $templatePath = Join-Path (Join-Path (Join-Path (Join-Path $dir 'config') 'chat-templates') 'upstream-b9014') 'test-fix.jinja'
    Write-TestFile -Path $templatePath -Content 'tampered'
    Add-EnvLine -Path $envPath -Line "MODEL_CHAT_TEMPLATE_OVERRIDE=$($tpl.OverrideId)"
    Add-EnvLine -Path $envPath -Line "LLAMA_ARG_CHAT_TEMPLATE_FILE=$($tpl.ContainerFile)"
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'MODEL_CHAT_TEMPLATE_OVERRIDE'
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_CHAT_TEMPLATE_FILE'
    # Half: only override set, no template file.
    $tpl = New-TestChatTemplate -InstallDir $dir
    Add-EnvLine -Path $envPath -Line "MODEL_CHAT_TEMPLATE_OVERRIDE=$($tpl.OverrideId)"
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'MODEL_CHAT_TEMPLATE_OVERRIDE'
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_CHAT_TEMPLATE_FILE'
    # Unknown id.
    Add-EnvLine -Path $envPath -Line 'MODEL_CHAT_TEMPLATE_OVERRIDE=unknown-id'
    Add-EnvLine -Path $envPath -Line 'LLAMA_ARG_CHAT_TEMPLATE_FILE=/chat-templates/upstream-b9014/test-fix.jinja'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'MODEL_CHAT_TEMPLATE_OVERRIDE'
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_CHAT_TEMPLATE_FILE'
    # Missing override: an old file path alone must not be carried.
    $tpl = New-TestChatTemplate -InstallDir $dir
    Add-EnvLine -Path $envPath -Line "LLAMA_ARG_CHAT_TEMPLATE_FILE=$($tpl.ContainerFile)"
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_CHAT_TEMPLATE_FILE'
} finally { Remove-TestDir -Dir $dir }

# --- Test 10: preserve unrelated credentials and tuning hashes ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $stable = @{}
    foreach ($key in @('DASHBOARD_API_KEY','WEBUI_SECRET_KEY','GGUF_FILE','CTX_SIZE','GPU_BACKEND','ODS_MODE','LLM_BACKEND')) {
        $stable[$key] = Get-EnvValue -Path $envPath -Key $key
    }
    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    foreach ($key in $stable.Keys) {
        if ((Get-EnvValue -Path $envPath -Key $key) -cne $stable[$key]) { throw "rerun changed unrelated setting $key" }
    }
} finally { Remove-TestDir -Dir $dir }

# --- Test 11: fresh without old keys emits nothing ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_MMPROJ'
    Assert-NotCarried -EnvPath $envPath -Key 'MODEL_CHAT_TEMPLATE_OVERRIDE'
    Assert-NotCarried -EnvPath $envPath -Key 'LLAMA_ARG_CHAT_TEMPLATE_FILE'
} finally { Remove-TestDir -Dir $dir }

# Invalid saved settings and metadata must never re-enable a catalog projector.
$rejectedCases = @(
    @{ Name='explicit empty'; Change={ param($d,$e,$m) Set-EnvLine $e 'LLAMA_ARG_MMPROJ' '' } },
    @{ Name='different saved projector'; Change={ param($d,$e,$m) Set-EnvLine $e 'LLAMA_ARG_MMPROJ' '/models/other.gguf' } },
    @{ Name='saved traversal'; Change={ param($d,$e,$m) Set-EnvLine $e 'LLAMA_ARG_MMPROJ' '/models/../mmproj-test.gguf' } },
    @{ Name='missing model'; Change={ param($d,$e,$m) Remove-Item -LiteralPath (Join-Path $m 'test-model.gguf') -Force } },
    @{ Name='malformed catalog'; Change={ param($d,$e,$m) Write-TestFile (Join-Path $d 'config/model-library.json') '{broken' } },
    @{ Name='invalid size'; Change={ param($d,$e,$m)
        $p=Join-Path $d 'config/model-library.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json
        $j.models[0].mmproj_size_bytes='not-a-size'; Write-TestFile $p ($j | ConvertTo-Json -Depth 8)
    } },
    @{ Name='duplicate active model'; Change={ param($d,$e,$m)
        $p=Join-Path $d 'config/model-library.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json
        $j.models=@($j.models[0],$j.models[0]); Write-TestFile $p ($j | ConvertTo-Json -Depth 8)
    } },
    @{ Name='model used as projector'; Change={ param($d,$e,$m)
        New-TestCatalog $d 'test-model.gguf' 'test-model.gguf' 128
        Set-EnvLine $e 'LLAMA_ARG_MMPROJ' '/models/test-model.gguf'
    } },
    @{ Name='unregistered store'; Change={ param($d,$e,$m) Set-EnvLine $e 'ODS_ACTIVE_MODEL_STORE' 'absent' } },
    @{ Name='malformed store schema'; Change={ param($d,$e,$m)
        Set-EnvLine $e 'ODS_ACTIVE_MODEL_STORE' 'ext'
        Write-TestFile (Join-Path $d 'data/model-stores.json') '{"schemaVersion":"bad","stores":[]}'
    } }
)
foreach ($case in $rejectedCases) {
    $dir=New-TestInstallDir
    try {
        $envPath=Join-Path $dir '.env'; $tier=New-TestTierConfig
        Invoke-Generate $dir $tier | Out-Null
        $modelsDir=Join-Path $dir 'data/models'
        New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
        New-TestWeights $modelsDir | Out-Null
        New-TestProjector $modelsDir | Out-Null
        New-TestCatalog $dir 'test-model.gguf' 'mmproj-test.gguf' 64
        Add-EnvLine $envPath 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf'
        & $case.Change $dir $envPath $modelsDir
        Invoke-Generate $dir $tier | Out-Null
        Assert-NotCarried $envPath 'LLAMA_ARG_MMPROJ'
    } catch { throw "Rejected case '$($case.Name)' failed: $($_.Exception.Message)" }
    finally { Remove-TestDir $dir }
}

# The shipped index contract is all-or-nothing, including unrelated bad entries.
$templateCases=@('wrong schema','duplicate id','invalid entry','duplicate build','missing field')
foreach ($case in $templateCases) {
    $dir=New-TestInstallDir
    try {
        $envPath=Join-Path $dir '.env'; $tier=New-TestTierConfig
        Invoke-Generate $dir $tier | Out-Null
        $modelsDir=Join-Path $dir 'data/models'; New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
        New-TestWeights $modelsDir | Out-Null
        New-TestCatalog $dir 'test-model.gguf' 'mmproj-test.gguf' 64
        $tpl=New-TestChatTemplate $dir
        Add-EnvLine $envPath ('MODEL_CHAT_TEMPLATE_OVERRIDE='+$tpl.OverrideId)
        Add-EnvLine $envPath ('LLAMA_ARG_CHAT_TEMPLATE_FILE='+$tpl.ContainerFile)
        $indexPath=Join-Path $dir 'config/chat-templates/index.json'
        $index=Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json
        switch($case) {
            'wrong schema' { $index.schemaVersion='bad' }
            'duplicate id' { $index.overrides=@($index.overrides[0],$index.overrides[0]) }
            'invalid entry' { $index.overrides+=@([pscustomobject]@{id='broken'}) }
            'duplicate build' { $index.overrides[0].builds=@('b9014','b9014') }
            'missing field' { $index.overrides[0].PSObject.Properties.Remove('reason') }
        }
        Write-TestFile $indexPath ($index | ConvertTo-Json -Depth 8)
        Invoke-Generate $dir $tier | Out-Null
        Assert-NotCarried $envPath 'MODEL_CHAT_TEMPLATE_OVERRIDE'
        Assert-NotCarried $envPath 'LLAMA_ARG_CHAT_TEMPLATE_FILE'
    } finally { Remove-TestDir $dir }
}

# --- Helper contract: malformed HF imports, HF-only active record, NewContent wins ---
$dir = New-TestInstallDir
try {
    $envPath = Join-Path $dir '.env'
    $tier = New-TestTierConfig
    Invoke-Generate -InstallDir $dir -TierConfig $tier | Out-Null
    $modelsDir = Join-Path (Join-Path $dir 'data') 'models'
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    New-TestWeights -Dir $modelsDir | Out-Null
    $proj = New-TestProjector -Dir $modelsDir
    $projSize = ([System.IO.FileInfo]::new($proj)).Length
    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    Write-TestFile (Join-Path $dir 'data/model-imports.json') '{broken'
    $prev = @{
        GGUF_FILE = 'test-model.gguf'
        ODS_MODE = 'local'
        LLM_BACKEND = 'llama-server'
        LLAMA_ARG_MMPROJ = '/models/mmproj-test.gguf'
    }
    $lines = @(Get-ODSCarriedActiveModelRuntimeLines -PreviousEnv $prev -NewContent '' `
        -CurrentGgufFile 'test-model.gguf' -CurrentModelStore 'default' `
        -CurrentOdsMode 'local' -CurrentLlmBackend 'llama-server' -InstallDir $dir)
    if ($lines -cnotcontains 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf') { throw 'malformed imports must not drop catalog projector' }

    Write-TestFile (Join-Path $dir 'config/model-library.json') '{"models":[]}'
    New-TestImports -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    $lines = @(Get-ODSCarriedActiveModelRuntimeLines -PreviousEnv $prev -NewContent '' `
        -CurrentGgufFile 'test-model.gguf' -CurrentModelStore 'default' `
        -CurrentOdsMode 'local' -CurrentLlmBackend 'llama-server' -InstallDir $dir)
    if ($lines -cnotcontains 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf') { throw 'valid HF-only record must carry before corruption' }
    Write-TestFile (Join-Path $dir 'data/model-imports.json') '{broken'
    $lines = @(Get-ODSCarriedActiveModelRuntimeLines -PreviousEnv $prev -NewContent '' `
        -CurrentGgufFile 'test-model.gguf' -CurrentModelStore 'default' `
        -CurrentOdsMode 'local' -CurrentLlmBackend 'llama-server' -InstallDir $dir)
    if ($lines -ccontains 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf') { throw 'HF-only active record with malformed imports must drop projector' }

    New-TestCatalog -InstallDir $dir -GgufFile 'test-model.gguf' -MmprojFile 'mmproj-test.gguf' -MmprojSize $projSize
    $tpl = New-TestChatTemplate -InstallDir $dir
    $prev = @{
        GGUF_FILE = 'test-model.gguf'
        ODS_MODE = 'local'
        LLM_BACKEND = 'llama-server'
        LLAMA_ARG_MMPROJ = '/models/mmproj-test.gguf'
        MODEL_CHAT_TEMPLATE_OVERRIDE = $tpl.OverrideId
        LLAMA_ARG_CHAT_TEMPLATE_FILE = $tpl.ContainerFile
    }
    $newContent = "LLAMA_ARG_MMPROJ=/models/new.gguf`n"
    $lines = @(Get-ODSCarriedActiveModelRuntimeLines -PreviousEnv $prev -NewContent $newContent `
        -CurrentGgufFile 'test-model.gguf' -CurrentModelStore 'default' `
        -CurrentOdsMode 'local' -CurrentLlmBackend 'llama-server' -InstallDir $dir)
    if ($lines -ccontains 'LLAMA_ARG_MMPROJ=/models/mmproj-test.gguf') { throw 'new generated projector must prevent carry' }
    if ($lines -cnotcontains ('MODEL_CHAT_TEMPLATE_OVERRIDE=' + $tpl.OverrideId)) { throw 'template pair should still carry when only projector is regenerated' }
    if ($lines -cnotcontains ('LLAMA_ARG_CHAT_TEMPLATE_FILE=' + $tpl.ContainerFile)) { throw 'template pair should still carry when only projector is regenerated' }

    $newContent = "MODEL_CHAT_TEMPLATE_OVERRIDE=new-id`n"
    $lines = @(Get-ODSCarriedActiveModelRuntimeLines -PreviousEnv $prev -NewContent $newContent `
        -CurrentGgufFile 'test-model.gguf' -CurrentModelStore 'default' `
        -CurrentOdsMode 'local' -CurrentLlmBackend 'llama-server' -InstallDir $dir)
    if ($lines -ccontains ('MODEL_CHAT_TEMPLATE_OVERRIDE=' + $tpl.OverrideId)) { throw 'new override half must suppress old override' }
    if ($lines -ccontains ('LLAMA_ARG_CHAT_TEMPLATE_FILE=' + $tpl.ContainerFile)) { throw 'new override half must suppress old template file' }

    $newContent = "LLAMA_ARG_CHAT_TEMPLATE_FILE=/chat-templates/new.jinja`n"
    $lines = @(Get-ODSCarriedActiveModelRuntimeLines -PreviousEnv $prev -NewContent $newContent `
        -CurrentGgufFile 'test-model.gguf' -CurrentModelStore 'default' `
        -CurrentOdsMode 'local' -CurrentLlmBackend 'llama-server' -InstallDir $dir)
    if ($lines -ccontains ('MODEL_CHAT_TEMPLATE_OVERRIDE=' + $tpl.OverrideId)) { throw 'new template-file half must suppress old override' }
    if ($lines -ccontains ('LLAMA_ARG_CHAT_TEMPLATE_FILE=' + $tpl.ContainerFile)) { throw 'new template-file half must suppress old template file' }
} finally { Remove-TestDir -Dir $dir }

Write-Host "PASS: active model runtime carry; catalog/import/store reruns, $($failureCases.Count) identity cases, $($rejectedCases.Count) invalid artifact cases, $($templateCases.Count) invalid index cases"
