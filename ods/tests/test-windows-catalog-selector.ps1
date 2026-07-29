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
} finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    if (
        $resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -like "ods-catalog-source-*"
    ) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
