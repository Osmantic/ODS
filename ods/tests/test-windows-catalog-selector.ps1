$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $repoRoot "installers/windows/lib/tier-map.ps1")

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempRoot = Join-Path $tempBase ("ods-catalog-source-" + [Guid]::NewGuid().ToString("N"))

try {
    $configDir = New-Item -ItemType Directory -Path (Join-Path $tempRoot "config")
    $catalog = @{
        models = @(
            @{
                id = "curated-model"
                name = "Curated model"
                llm_model_name = "curated-model"
                family = "qwen"
                gguf_file = "curated.gguf"
                gguf_url = "https://huggingface.co/ods/curated/resolve/main/curated.gguf"
                size_mb = 1000
                vram_required_gb = 2
                context_length = 65536
                specialty = "General"
            },
            @{
                id = "imported-model"
                source = "huggingface"
                name = "Imported model"
                llm_model_name = "imported-model"
                family = "qwen"
                gguf_file = "imported.gguf"
                gguf_url = "https://huggingface.co/community/imported/resolve/main/imported.gguf"
                size_mb = 7000
                vram_required_gb = 7
                context_length = 131072
                specialty = "Code"
            }
        )
    }
    $catalog | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $configDir "model-library.json")

    $tierConfig = @{
        ModelProfileEffective = "qwen"
        LlmModel = "fallback-model"
        GgufFile = "fallback.gguf"
    }
    $gpu = @{
        Backend = "nvidia"
        MemoryType = "discrete"
        VramMB = 8192
    }
    $resolved = Resolve-CatalogModelRecommendation `
        -TierConfig $tierConfig `
        -Tier "1" `
        -GpuInfo $gpu `
        -SystemRamGB 32 `
        -SourceRoot $tempRoot

    if ($resolved.LlmModel -ne "curated-model" -or $resolved.GgufFile -ne "curated.gguf") {
        throw "Windows catalog selector chose a non-curated source: $($resolved.LlmModel)"
    }
    Write-Host "[PASS] Windows catalog selector excludes Hugging Face imports"
    $realCatalog = Get-Content (Join-Path $repoRoot "config/model-library.json") -Raw | ConvertFrom-Json
    $phi = $realCatalog.models | Where-Object { $_.id -eq "phi4-mini-q4" }
    if ((Get-CatalogModelEstimatedContextKvGB -Model $phi) -ne 15.62) {
        throw "Phi4 full-context KV allocation was underestimated"
    }
    # phi4-mini is no longer an install recommendation; a copy that is still
    # exercises the architecture-driven context step-down.
    $phiCandidate = $phi.PSObject.Copy()
    $phiCandidate.install_recommendation = $true
    @{ models = @($phiCandidate) } | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $configDir "model-library.json")
    foreach ($backend in @("nvidia", "amd", "sycl")) {
        foreach ($case in @(@(4, 8192), @(8, 32768), @(16, 65536), @(24, 128000))) {
            $gpu.Backend = $backend
            $gpu.VramMB = $case[0] * 1024
            $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
            if ($resolved.LlmModel -ne "phi-4-mini" -or $resolved.MaxContext -ne $case[1]) {
                throw "Wrong Phi4 context on $backend with $($case[0]) GiB: $($resolved.MaxContext)"
            }
        }
    }
    if ($phi.context_length -ne 128000) { throw "Context selection mutated the catalog" }
    $gpu.VramMB = 1024
    $rejected = $false
    try {
        $null = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
    } catch {
        if ($_.Exception.Message -notlike "*No catalog model fits*") { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw "Unsafe tier fallback survived no-fit selection" }
    $profileModel = [pscustomobject]@{
        runtime_profiles = @([pscustomobject]@{
            backend = "nvidia"; system_ram_min_gb = 16; system_ram_max_gb = 32
        })
    }
    $gpu.Backend = "nvidia"
    if (Get-CatalogRuntimeProfile -Model $profileModel -GpuInfo $gpu -SystemRamGB 8) { throw "Low RAM matched" }
    if (-not (Get-CatalogRuntimeProfile -Model $profileModel -GpuInfo $gpu -SystemRamGB 8 -IgnoreRamMinimum)) { throw "Low RAM lost safety anchor" }
    if (Get-CatalogRuntimeProfile -Model $profileModel -GpuInfo $gpu -SystemRamGB 64 -IgnoreRamMinimum) { throw "Larger host trapped in smaller profile" }
    Write-Host "[PASS] Windows architecture memory, adaptive context, and no-fit rejection"
} finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    if (
        $resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -like "ods-catalog-source-*"
    ) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# Parity with scripts/select-model.py over every unbounded envelope in
# tests/fixtures/model-selection-envelopes.json (golden: model-selection-golden.json).
# ---------------------------------------------------------------------------
$fixtureDir = Join-Path $repoRoot "tests/fixtures"
$envelopes = (Get-Content (Join-Path $fixtureDir "model-selection-envelopes.json") -Raw | ConvertFrom-Json).envelopes
$golden = Get-Content (Join-Path $fixtureDir "model-selection-golden.json") -Raw | ConvertFrom-Json
$realCatalog = Get-Content (Join-Path $repoRoot "config/model-library.json") -Raw | ConvertFrom-Json
$idByGguf = @{}
foreach ($entry in $realCatalog.models) { if ($entry.gguf_file) { $idByGguf[[string]$entry.gguf_file] = [string]$entry.id } }
$savedHostArch = $env:HOST_ARCH
$checked = 0
try {
    foreach ($envelope in $envelopes) {
        if ([string]$envelope.ceiling -ne "0") { continue }
        # macOS picks go through select-model.py on the Mac; its unified-memory
        # coder-next swap for any tier is not a Windows policy.
        if ($envelope.backend -eq "apple") { continue }
        $expected = $golden.($envelope.id)
        $backend = if ($envelope.backend -eq "cpu") { "none" } else { [string]$envelope.backend }
        $gpuInfo = @{ Backend = $backend; MemoryType = [string]$envelope.memory_type; VramMB = [int]$envelope.vram_mb }
        $env:HOST_ARCH = [string]$envelope.host_arch
        $tierConfig = @{ ModelProfileEffective = "qwen"; LlmModel = "fallback"; GgufFile = "fallback.gguf"; MaxContext = 8192 }
        $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig -Tier ([string]$envelope.tier) `
            -GpuInfo $gpuInfo -SystemRamGB ([int]$envelope.ram_gb) -SourceRoot $repoRoot -MinContext 65536
        $pick = $idByGguf[[string]$resolved.GgufFile]
        $profile = if ($resolved.RuntimeProfile) { [string]$resolved.RuntimeProfile } else { $null }
        if ($pick -ne $expected.pick -or [int]$resolved.MaxContext -ne [int]$expected.context_length -or
            $profile -ne $expected.runtime_profile -or [string]$resolved.RecommendationPolicy -ne [string]$expected.policy) {
            throw "Windows selector diverges on $($envelope.id): $pick/$($resolved.MaxContext)/$profile/$($resolved.RecommendationPolicy); golden $($expected.pick)/$($expected.context_length)/$($expected.runtime_profile)/$($expected.policy)"
        }
        $checked++
    }
} finally {
    $env:HOST_ARCH = $savedHostArch
}
if ($checked -lt 30) { throw "Windows parity covered only $checked envelopes" }
Write-Host "[PASS] Windows selector matches select-model.py on $checked envelopes"

# Hermes floor re-check (phases/03-features.ps1): the configured model's fit at 64K.
$gpu27 = @{ Backend = "nvidia"; MemoryType = "discrete"; VramMB = 20475 }
$config27 = @{ LlmModel = "qwen3.5-27b"; GgufFile = "Qwen3.5-27B-Q4_K_M.gguf"; MaxContext = 32768 }
if ((Test-CatalogModelContextFit -TierConfig $config27 -GpuInfo $gpu27 -SystemRamGB 64 -SourceRoot $repoRoot -ContextLength 65536) -ne $false) {
    throw "27B with F16 KV must not fit at 64K on a 20 GB card"
}
$gpu27.VramMB = 32607
if ((Test-CatalogModelContextFit -TierConfig $config27 -GpuInfo $gpu27 -SystemRamGB 61 -SourceRoot $repoRoot -ContextLength 65536) -ne $true) {
    throw "27B must fit at 64K on an RTX 5090"
}
# phi-4 at 64K would fit a 32 GB card by memory, but its native context is
# 16,384: llama.cpp caps the slot there, so the raise is never a fit.
$configPhi4 = @{ LlmModel = "phi-4"; GgufFile = "phi-4-Q4_K_M.gguf"; MaxContext = 16384 }
$phi4Catalog = (Get-Content (Join-Path $repoRoot "config\model-library.json") -Raw | ConvertFrom-Json).models |
    Where-Object { $_.id -eq "phi4-q4" } | Select-Object -First 1
$configPhi4.LlmModel = "$($phi4Catalog.llm_model_name)"
$configPhi4.GgufFile = "$($phi4Catalog.gguf_file)"
if ((Test-CatalogModelContextFit -TierConfig $configPhi4 -GpuInfo $gpu27 -SystemRamGB 61 -SourceRoot $repoRoot -ContextLength 65536) -ne $false) {
    throw "A context above the declared native maximum must never fit (phi-4 at 64K)"
}
if ((Test-CatalogModelContextFit -TierConfig $configPhi4 -GpuInfo $gpu27 -SystemRamGB 61 -SourceRoot $repoRoot -ContextLength 16384) -ne $true) {
    throw "phi-4 at its native 16K must fit on an RTX 5090"
}
$unknown = @{ LlmModel = "imported"; GgufFile = "not-in-catalog.gguf"; MaxContext = 32768 }
if ($null -ne (Test-CatalogModelContextFit -TierConfig $unknown -GpuInfo $gpu27 -SystemRamGB 61 -SourceRoot $repoRoot -ContextLength 65536)) {
    throw "A model outside the catalog must be unknown, not a verdict"
}
$gpu27.VramMB = 20475
$reselected = Resolve-CatalogModelRecommendation -TierConfig @{ ModelProfileEffective = "qwen" } -Tier "3" -GpuInfo $gpu27 `
    -SystemRamGB 64 -SourceRoot $repoRoot -MinContext 65536 -RequireMinContext
if ($reselected.RuntimeProfile -ne "nvidia-20gb-64k-q8-kv" -or [int]$reselected.MaxContext -ne 65536 -or $reselected.LLAMA_ARG_CACHE_TYPE_K -ne "q8_0") {
    throw "20 GB re-selection at the floor should use the 27B Q8 KV profile: $($reselected.RuntimeProfile)/$($reselected.MaxContext)"
}
$gpu27.VramMB = 2048
$refused = $false
try {
    $null = Resolve-CatalogModelRecommendation -TierConfig @{ ModelProfileEffective = "qwen" } -Tier "0" -GpuInfo $gpu27 `
        -SystemRamGB 16 -SourceRoot $repoRoot -MinContext 65536 -RequireMinContext
} catch {
    if ($_.Exception.Message -notlike "*at 65536 context*") { throw }
    $refused = $true
}
if (-not $refused) { throw "RequireMinContext must refuse when nothing fits at 64K" }
Write-Host "[PASS] Windows Hermes floor re-check: fit test, re-selection and refusal"
