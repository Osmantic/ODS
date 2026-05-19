import json

from helpers import record_model_performance
from models import GPUInfo
from performance_oracle import build_models_payload, evaluate_performance, rank_pre_download_models


def _gpu(name="NVIDIA GeForce RTX 4060", total_mb=8192):
    return GPUInfo(
        name=name,
        memory_used_mb=1024,
        memory_total_mb=total_mb,
        memory_percent=12.5,
        utilization_percent=0,
        temperature_c=40,
        gpu_backend="nvidia",
    )


def _model():
    return {
        "id": "qwen3.5-9b-q4",
        "name": "Qwen 3.5 9B",
        "gguf_file": "Qwen3.5-9B-Q4_K_M.gguf",
        "size_mb": 5760,
        "vram_required_gb": 8,
        "context_length": 32768,
        "specialty": "General",
        "description": "Test model",
        "quantization": "Q4_K_M",
        "llm_model_name": "qwen3.5-9b",
    }


def test_benchmark_required_without_measurement_or_evidence(data_dir, tmp_path):
    install_dir = tmp_path / "dream-server"
    (install_dir / "data" / "models").mkdir(parents=True)

    payload = build_models_payload(
        _gpu(),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[_model()],
        evidence=[],
    )

    perf = payload["models"][0]["performance"]
    assert perf["source"] == "benchmark_required"
    assert perf["tokensPerSec"] is None
    assert payload["currentModel"] is None


def test_exact_8gb_model_fits_marketing_8gb_gpu(data_dir, tmp_path):
    install_dir = tmp_path / "dream-server"
    (install_dir / "data" / "models").mkdir(parents=True)

    payload = build_models_payload(
        _gpu(total_mb=8188),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[_model()],
        evidence=[],
    )

    model = payload["models"][0]
    assert model["fitsVram"] is True
    assert model["performance"]["source"] == "benchmark_required"


def test_build_models_payload_uses_official_model_library(data_dir, tmp_path):
    install_dir = tmp_path / "dream-server"
    (install_dir / "data" / "models").mkdir(parents=True)
    (install_dir / "config").mkdir(parents=True)
    (install_dir / "config" / "model-library.json").write_text(json.dumps({
        "version": 2,
        "models": [
            {
                "id": "phi4-mini-q4",
                "name": "Phi-4 Mini",
                "gguf_file": "Phi-4-mini-instruct-Q4_K_M.gguf",
                "size_mb": 2490,
                "vram_required_gb": 4,
                "context_length": 128000,
                "quantization": "Q4_K_M",
                "specialty": "Balanced",
                "description": "Compact 128K model.",
                "llm_model_name": "phi-4-mini",
            },
            _model(),
        ],
    }), encoding="utf-8")

    payload = build_models_payload(_gpu(), None, 0, install_dir, data_dir, evidence=[])

    assert [model["id"] for model in payload["models"]] == ["phi4-mini-q4", "qwen3.5-9b-q4"]
    assert payload["models"][0]["gguf"] == "Phi-4-mini-instruct-Q4_K_M.gguf"
    assert payload["models"][0]["llmModelName"] == "phi-4-mini"


def test_installer_recommended_model_survives_bootstrap_env(data_dir, tmp_path):
    install_dir = tmp_path / "dream-server"
    (install_dir / "data" / "models").mkdir(parents=True)
    (install_dir / ".env").write_text(
        "LLM_MODEL=qwen3.5-2b\n"
        "GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf\n"
        "MODEL_RECOMMENDED_MODEL=qwen3.5-9b\n"
        "MODEL_RECOMMENDED_GGUF=Qwen3.5-9B-Q4_K_M.gguf\n"
        "MODEL_RECOMMENDED_CONTEXT=32768\n"
        "MODEL_RECOMMENDATION_SOURCE=installer_tier_map\n",
        encoding="utf-8",
    )
    catalog = [
        {
            "id": "qwen3.5-2b-q4",
            "name": "Qwen 3.5 2B",
            "gguf_file": "Qwen3.5-2B-Q4_K_M.gguf",
            "size_mb": 1500,
            "vram_required_gb": 3,
            "context_length": 8192,
            "quantization": "Q4_K_M",
            "specialty": "Fast",
            "description": "Bootstrap model",
            "llm_model_name": "qwen3.5-2b",
        },
        _model(),
    ]

    payload = build_models_payload(_gpu(), "qwen3.5-2b", 60, install_dir, data_dir, catalog=catalog, evidence=[])

    by_id = {model["id"]: model for model in payload["models"]}
    assert payload["currentModel"] == "qwen3.5-2b-q4"
    assert payload["configuredModel"] == "qwen3.5-9b-q4"
    assert by_id["qwen3.5-2b-q4"]["status"] == "loaded"
    assert by_id["qwen3.5-9b-q4"]["recommended"] is True
    assert by_id["qwen3.5-9b-q4"]["recommendation"]["source"] == "installer_tier_map"
    assert by_id["qwen3.5-9b-q4"]["recommendation"]["contextLength"] == 32768
    assert payload["recommendationAlternatives"][0]["id"] == "qwen3.5-9b-q4"


def test_pre_download_ranker_prefers_capable_8gb_model_over_bootstrap(data_dir):
    catalog = [
        {
            "id": "qwen3.5-2b-q4",
            "name": "Qwen 3.5 2B",
            "gguf_file": "Qwen3.5-2B-Q4_K_M.gguf",
            "size_mb": 1500,
            "vram_required_gb": 3,
            "context_length": 8192,
            "quantization": "Q4_K_M",
            "specialty": "Fast",
            "description": "Bootstrap model",
            "llm_model_name": "qwen3.5-2b",
        },
        _model(),
    ]

    ranked = rank_pre_download_models(catalog, _gpu(total_mb=8188), profile="qwen", limit=2)

    assert ranked[0]["id"] == "qwen3.5-9b-q4"


def test_pre_download_ranker_accounts_for_long_context_kv_on_4gb_gpu(data_dir, tmp_path):
    catalog = [
        {
            "id": "phi4-mini-q4",
            "name": "Phi-4 Mini",
            "gguf_file": "Phi-4-mini-instruct-Q4_K_M.gguf",
            "size_mb": 2490,
            "vram_required_gb": 4,
            "context_length": 128000,
            "quantization": "Q4_K_M",
            "specialty": "Balanced",
            "description": "Compact 128K model.",
            "llm_model_name": "phi-4-mini",
        },
        {
            "id": "qwen3.5-2b-q4",
            "name": "Qwen 3.5 2B",
            "family": "qwen",
            "gguf_file": "Qwen3.5-2B-Q4_K_M.gguf",
            "size_mb": 1500,
            "vram_required_gb": 3,
            "context_length": 8192,
            "quantization": "Q4_K_M",
            "specialty": "Fast",
            "description": "Bootstrap model",
            "llm_model_name": "qwen3.5-2b",
        },
    ]

    gpu = _gpu(total_mb=4096)
    ranked = rank_pre_download_models(catalog, gpu, profile="qwen", limit=2)

    assert ranked[0]["id"] == "qwen3.5-2b-q4"

    install_dir = tmp_path / "dream-server"
    (install_dir / "data" / "models").mkdir(parents=True)
    payload = build_models_payload(gpu, None, 0, install_dir, data_dir, catalog=catalog, evidence=[])
    by_id = {model["id"]: model for model in payload["models"]}
    assert by_id["phi4-mini-q4"]["fitsVram"] is False
    assert by_id["phi4-mini-q4"]["estimatedRequired"] > by_id["phi4-mini-q4"]["vramRequired"]


def test_pre_download_ranker_falls_back_to_smallest_model_without_gpu_info(data_dir):
    catalog = [
        _model(),
        {
            "id": "qwen3.6-35b-a3b-ud-q4",
            "name": "Qwen 3.6 35B-A3B",
            "family": "qwen",
            "gguf_file": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "size_mb": 21110,
            "vram_required_gb": 24,
            "context_length": 131072,
            "quantization": "UD-Q4_K_M",
            "specialty": "Quality",
            "description": "Large MoE model",
            "llm_model_name": "qwen3.6-35b-a3b",
        },
    ]

    ranked = rank_pre_download_models(catalog, None, profile="qwen", limit=2)

    assert [model["id"] for model in ranked] == ["qwen3.5-9b-q4"]


def test_pre_download_ranker_honors_gemma_profile(data_dir):
    catalog = [
        _model(),
        {
            "id": "gemma4-e4b-q4",
            "name": "Gemma 4 E4B",
            "family": "gemma4",
            "gguf_file": "gemma-4-E4B-it-Q4_K_M.gguf",
            "size_mb": 5340,
            "vram_required_gb": 8,
            "context_length": 32768,
            "quantization": "Q4_K_M",
            "specialty": "General",
            "description": "Gemma profile model",
            "llm_model_name": "gemma-4-e4b-it",
        },
    ]

    ranked = rank_pre_download_models(catalog, _gpu(total_mb=8188), profile="gemma4", limit=2)

    assert ranked[0]["id"] == "gemma4-e4b-q4"


def test_pre_download_ranker_allows_8gb_nvidia_runtime_profile(monkeypatch):
    monkeypatch.setattr("performance_oracle._system_ram_gb", lambda: 31)
    catalog = [
        _model(),
        {
            "id": "qwen3.6-35b-a3b-ud-q4",
            "name": "Qwen 3.6 35B-A3B",
            "family": "qwen",
            "gguf_file": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "size_mb": 21110,
            "vram_required_gb": 24,
            "context_length": 131072,
            "quantization": "UD-Q4_K_M",
            "specialty": "Quality",
            "description": "Large MoE model",
            "llm_model_name": "qwen3.6-35b-a3b",
            "runtime_profiles": [{
                "id": "nvidia-8gb-qwen36-35b-a3b-turboquant",
                "label": "Advanced 8GB NVIDIA TurboQuant MoE offload",
                "backend": "nvidia",
                "host_arch": ["amd64"],
                "memory_type": "discrete",
                "vram_min_gb": 7.5,
                "vram_max_gb": 12.5,
                "system_ram_min_gb": 31,
                "estimated_required_gb": 8,
                "context_length": 65536,
                "fit_label": "Advanced 8GB TurboQuant fit",
                "env": {"LLAMA_ARG_N_CPU_MOE": "30"},
            }],
        },
    ]

    ranked = rank_pre_download_models(catalog, _gpu(total_mb=8188), profile="qwen", limit=2)

    assert ranked[0]["id"] == "qwen3.6-35b-a3b-ud-q4"
    assert ranked[0]["_runtime_profile"]["id"] == "nvidia-8gb-qwen36-35b-a3b-turboquant"


def test_measured_local_from_live_loaded_model(data_dir, tmp_path):
    install_dir = tmp_path / "dream-server"
    (install_dir / "data" / "models").mkdir(parents=True)

    payload = build_models_payload(
        _gpu(),
        "qwen3.5-9b",
        41.8,
        install_dir,
        data_dir,
        context_length=32768,
        catalog=[_model()],
        evidence=[],
    )

    loaded = payload["models"][0]
    assert payload["currentModel"] == "qwen3.5-9b-q4"
    assert loaded["status"] == "loaded"
    assert loaded["performance"]["source"] == "measured_local"
    assert loaded["tokensPerSec"] == 41.8


def test_predicted_calibrated_requires_local_sample(data_dir, tmp_path):
    record_model_performance(
        "qwen3.5-4b",
        "NVIDIA GeForce RTX 4060",
        "nvidia",
        80.0,
        model_id="qwen3.5-4b-q4",
        gguf="Qwen3.5-4B-Q4_K_M.gguf",
        context_length=16384,
        decode_read_mb=2870,
        vram_total_mb=8192,
    )
    install_dir = tmp_path / "dream-server"
    (install_dir / "data" / "models").mkdir(parents=True)

    payload = build_models_payload(
        _gpu(),
        None,
        0,
        install_dir,
        data_dir,
        context_length=32768,
        catalog=[_model()],
        evidence=[],
    )

    perf = payload["models"][0]["performance"]
    assert perf["source"] == "predicted_calibrated"
    assert perf["tokensPerSec"] is not None
    assert perf["confidence"] == "low"


def test_published_exact_requires_matching_signature(data_dir):
    evidence = [{
        "model_id": "qwen3.5-9b-q4",
        "model_names": ["qwen3.5-9b", "Qwen3.5-9B-Q4_K_M.gguf"],
        "quantization": "Q4_K_M",
        "backend": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 4060",
        "vram_gb": 8,
        "context_length": 32768,
        "tokens_per_second": 44.2,
        "source_url": "https://example.test/bench",
    }]

    perf = evaluate_performance(
        _model(),
        _gpu(),
        {"quantization": "Q4_K_M", "readable": False},
        False,
        0,
        32768,
        {},
        evidence,
        True,
    )

    assert perf["source"] == "published_exact"
    assert perf["tokensPerSec"] == 44.2
    assert perf["sourceUrl"] == "https://example.test/bench"
