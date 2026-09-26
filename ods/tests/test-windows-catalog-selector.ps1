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
# Both model profiles: the gemma4 pass exercises the sliding-window KV terms
# (Gemma 4 E2B/E4B/26B-A4B), which the qwen pass never reaches.
$checked = @{ qwen = 0; gemma4 = 0 }
try {
    foreach ($modelProfile in @("qwen", "gemma4")) {
        foreach ($envelope in $envelopes) {
            if ([string]$envelope.ceiling -ne "0") { continue }
            # macOS picks go through select-model.py on the Mac; its unified-memory
            # coder-next swap for any tier is not a Windows policy.
            if ($envelope.backend -eq "apple") { continue }
            $expected = $golden.($envelope.id)
            if ($modelProfile -eq "gemma4") {
                $expected = $expected.gemma4
                if (-not $expected) { throw "Golden file has no gemma4 pick for $($envelope.id); run simulate-model-selection.py --update-golden" }
            }
            $backend = if ($envelope.backend -eq "cpu") { "none" } else { [string]$envelope.backend }
            $gpuInfo = @{ Backend = $backend; MemoryType = [string]$envelope.memory_type; VramMB = [int]$envelope.vram_mb }
            $env:HOST_ARCH = [string]$envelope.host_arch
            $tierConfig = @{ ModelProfileEffective = $modelProfile; LlmModel = "fallback"; GgufFile = "fallback.gguf"; MaxContext = 8192 }
            $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig -Tier ([string]$envelope.tier) `
                -GpuInfo $gpuInfo -SystemRamGB ([int]$envelope.ram_gb) -SourceRoot $repoRoot -MinContext 65536
            $pick = $idByGguf[[string]$resolved.GgufFile]
            $profile = if ($resolved.RuntimeProfile) { [string]$resolved.RuntimeProfile } else { $null }
            if ($pick -ne $expected.pick -or [int]$resolved.MaxContext -ne [int]$expected.context_length -or
                $profile -ne $expected.runtime_profile -or [string]$resolved.RecommendationPolicy -ne [string]$expected.policy) {
                throw "Windows $modelProfile selector diverges on $($envelope.id): $pick/$($resolved.MaxContext)/$profile/$($resolved.RecommendationPolicy); golden $($expected.pick)/$($expected.context_length)/$($expected.runtime_profile)/$($expected.policy)"
            }
            $checked[$modelProfile]++
        }
    }
} finally {
    $env:HOST_ARCH = $savedHostArch
}
foreach ($modelProfile in @("qwen", "gemma4")) {
    if ($checked[$modelProfile] -lt 30) { throw "Windows $modelProfile parity covered only $($checked[$modelProfile]) envelopes" }
}
Write-Host "[PASS] Windows selector matches select-model.py on $($checked.qwen) envelopes (qwen) and $($checked.gemma4) (gemma4)"

# Sliding-window terms (model_memory.sliding_window_kv_bytes_per_cell /
# sliding_window_cells): Gemma 3 4B holds full-context KV on 5 of 34 layers
# and a 1024-token window on the other 29, so 128K fits an 8 GB card.
# Expected values are model_memory.estimate_model_memory's for the same inputs.
$swaCases = @(
    @{ Id = "gemma3-4b-it-q4"; Context = 131072; Cache = "f16"; Parallel = 1; SwaKv = 0.17; Kv = 2.67; Device = 5.37; Total = 10.81 },
    @{ Id = "gemma4-e4b-q4"; Context = 65536; Cache = "q8_0"; Parallel = 2; SwaKv = 0.031; Kv = 0.562; Device = 5.62; Total = 6.95 }
)
foreach ($case in $swaCases) {
    $swaModel = $realCatalog.models | Where-Object { $_.id -eq $case.Id } | Select-Object -First 1
    $estimate = Get-CatalogMemoryEstimate -Model $swaModel -ContextLength $case.Context -CacheTypeK $case.Cache -CacheTypeV $case.Cache -Parallel $case.Parallel
    if ($estimate.Method -ne "architecture" -or $estimate.SwaKvGiB -ne $case.SwaKv -or $estimate.KvGiB -ne $case.Kv -or
        $estimate.DeviceGiB -ne $case.Device -or $estimate.TotalGiB -ne $case.Total) {
        throw "Sliding-window estimate for $($case.Id) diverges from model_memory: $($estimate.Method) swa $($estimate.SwaKvGiB) kv $($estimate.KvGiB) device $($estimate.DeviceGiB) total $($estimate.TotalGiB); expected swa $($case.SwaKv) kv $($case.Kv) device $($case.Device) total $($case.Total)"
    }
}
Write-Host "[PASS] Windows sliding-window KV matches model_memory (Gemma 3 4B at 128K: 5.37 GiB; Gemma 4 E4B, 2 slots, Q8 KV)"

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
