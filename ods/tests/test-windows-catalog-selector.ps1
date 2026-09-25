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
    @{ models = @($phi) } | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $configDir "model-library.json")
    foreach ($backend in @("nvidia", "amd", "sycl")) {
        foreach ($case in @(@(4, 8192), @(6, 16384), @(8, 32768), @(16, 65536), @(24, 128000))) {
            $gpu.Backend = $backend
            $gpu.VramMB = $case[0] * 1024
            $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
            if ($resolved.LlmModel -ne "phi-4-mini" -or $resolved.MaxContext -ne $case[1]) {
                throw "Wrong Phi4 context on $backend with $($case[0]) GiB: $($resolved.MaxContext)"
            }
        }
    }
    # A 4GB card is below the calibrated range: Phi-4 mini at 8K is predicted
    # to spill, but the estimate never changes the pick there. It keeps its
    # declared settings and the reason says the placement is reported.
    $gpu.Backend = "nvidia"
    $gpu.VramMB = 4096
    $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
    if ($resolved.LLAMA_ARG_UBATCH -or $resolved.LLAMA_ARG_FIT_TARGET -or $resolved.RecommendationReason -notlike "*does not stay fully on this GPU*") {
        throw "4GB Phi4 should keep its declared settings and report the spill: $($resolved.RecommendationReason)"
    }
    # The 8GB result keeps every layer on the GPU with a q8_0 KV cache.
    $gpu.VramMB = 8192
    $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
    if ($resolved.LLAMA_ARG_CACHE_TYPE_K -ne "q8_0" -or $resolved.LLAMA_ARG_UBATCH -ne "256" -or $resolved.LLAMA_ARG_FIT_TARGET -ne "512" -or $resolved.LLAMA_ARG_FLASH_ATTN -ne "on") {
        throw "Phi4 on 8GB lacks its GPU residency settings: $($resolved.LLAMA_ARG_CACHE_TYPE_K)/$($resolved.LLAMA_ARG_UBATCH)/$($resolved.LLAMA_ARG_FIT_TARGET)"
    }
    # Memory other processes hold (a desktop drawn on the GPU, another GPU
    # app) never changes the pick; the settings shrink around it, and when
    # even the smallest allowed settings spill the reason says so.
    $gpu.UsedMB = 1700
    $busy = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
    # q4_0 alone makes room, so prompt processing keeps ubatch 256.
    if ($busy.LlmModel -ne "phi-4-mini" -or $busy.MaxContext -ne 32768 -or $busy.LLAMA_ARG_CACHE_TYPE_K -ne "q4_0" -or $busy.LLAMA_ARG_UBATCH -ne "256") {
        throw "1.7GB in use was not planned around: $($busy.LlmModel) $($busy.MaxContext) $($busy.LLAMA_ARG_CACHE_TYPE_K) $($busy.LLAMA_ARG_UBATCH)"
    }
    $gpu.UsedMB = 4000
    $busy = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
    $gpu.Remove("UsedMB")
    if ($busy.LlmModel -ne "phi-4-mini" -or $busy.MaxContext -ne 32768 -or $busy.LLAMA_ARG_UBATCH -ne "128") {
        throw "4GB in use changed the pick or was not planned around: $($busy.LlmModel) $($busy.MaxContext) $($busy.LLAMA_ARG_UBATCH)"
    }
    if ($busy.RecommendationReason -notlike "*Other processes hold 4000 MiB*") {
        throw "Best-effort settings were not reported: $($busy.RecommendationReason)"
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
