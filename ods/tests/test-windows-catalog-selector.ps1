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
        foreach ($case in @(@(6, 16384), @(8, 32768), @(16, 65536), @(24, 128000))) {
            $gpu.Backend = $backend
            $gpu.VramMB = $case[0] * 1024
            $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
            if ($resolved.LlmModel -ne "phi-4-mini" -or $resolved.MaxContext -ne $case[1]) {
                throw "Wrong Phi4 context on $backend with $($case[0]) GiB: $($resolved.MaxContext)"
            }
        }
        # A 4GB card cannot hold Phi-4 mini's weights, KV cache and compute
        # buffer after the WDDM reserve at any context: never pick a model
        # that would run partly on the CPU.
        $gpu.Backend = $backend
        $gpu.VramMB = 4096
        $spilled = $false
        try {
            $null = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
        } catch {
            if ($_.Exception.Message -notlike "*No catalog model fits*") { throw }
            $spilled = $true
        }
        if (-not $spilled) { throw "A 4GB $backend card was offered a model that cannot stay on the GPU" }
    }
    # The 8GB result keeps every layer on the GPU with a q8_0 KV cache.
    $gpu.Backend = "nvidia"
    $gpu.VramMB = 8192
    $resolved = Resolve-CatalogModelRecommendation -TierConfig $tierConfig.Clone() -Tier "1" -GpuInfo $gpu -SystemRamGB 32 -SourceRoot $tempRoot
    if ($resolved.LLAMA_ARG_CACHE_TYPE_K -ne "q8_0" -or $resolved.LLAMA_ARG_UBATCH -ne "256" -or $resolved.LLAMA_ARG_FIT_TARGET -ne "512" -or $resolved.LLAMA_ARG_FLASH_ATTN -ne "on") {
        throw "Phi4 on 8GB lacks its GPU residency settings: $($resolved.LLAMA_ARG_CACHE_TYPE_K)/$($resolved.LLAMA_ARG_UBATCH)/$($resolved.LLAMA_ARG_FIT_TARGET)"
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
