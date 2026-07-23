import json
from pathlib import Path

from helpers import record_model_performance
from models import GPUInfo
from performance_oracle import (
    build_models_payload,
    current_model_matches,
    evaluate_performance,
    load_evidence,
    model_compatibility_runtime_context,
    model_app_compatibility,
    model_publisher,
    rank_pre_download_models,
)


def _gpu(name="NVIDIA GeForce RTX 4060", total_mb=8192, backend="nvidia"):
    return GPUInfo(
        name=name,
        memory_used_mb=1024,
        memory_total_mb=total_mb,
        memory_percent=12.5,
        utilization_percent=0,
        temperature_c=40,
        gpu_backend=backend,
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


def _official_model_catalog():
    catalog_path = Path(__file__).resolve().parents[4] / "config" / "model-library.json"
    return json.loads(catalog_path.read_text(encoding="utf-8"))["models"]


def _compatibility_blocks_release_coverage(entry):
    status = str((entry or {}).get("status") or "").strip().lower()
    return status in {
        "blocked",
        "incompatible",
        "not_agent_viable",
        "not_recommended",
        "not_supported",
        "unsupported",
        "unsupported_until_revalidated",
    }


def test_current_model_matches_complete_phi_aliases_and_runtime_prefixes():
    catalog = {model["id"]: model for model in _official_model_catalog()}
    mini = catalog["phi4-mini-q4"]
    full = catalog["phi4-q4"]

    cases = [
        (mini, full, "phi4-mini-q4"),
        (mini, full, "Phi-4 Mini"),
        (mini, full, "phi-4-mini"),
        (mini, full, "Phi-4-mini-instruct-Q4_K_M.gguf"),
        (mini, full, "Phi-4-mini-instruct-Q4_K_M"),
        (mini, full, "extra.Phi-4-mini-instruct-Q4_K_M.gguf"),
        (mini, full, "user.Phi-4-mini-instruct-Q4_K_M.gguf"),
        (mini, full, "/models/Phi-4-mini-instruct-Q4_K_M.gguf"),
        (full, mini, "phi4-q4"),
        (full, mini, "Phi-4 14B"),
        (full, mini, "phi-4"),
        (full, mini, "phi-4-Q4_K_M.gguf"),
        (full, mini, "phi-4-Q4_K_M"),
        (full, mini, "extra.phi-4-Q4_K_M.gguf"),
        (full, mini, "user.phi-4-Q4_K_M.gguf"),
        (full, mini, r"C:\models\phi-4-Q4_K_M.gguf"),
    ]

    for expected, other, runtime_name in cases:
        assert current_model_matches(expected, runtime_name)
        assert not current_model_matches(other, runtime_name)

    assert current_model_matches(mini, None, mini["gguf_file"])
    assert not current_model_matches(full, None, mini["gguf_file"])
    assert not current_model_matches(full, "custom.phi-4")


def test_real_catalog_phi_models_have_exactly_one_loaded_identity(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    install_dir.mkdir()
    catalog = _official_model_catalog()

    for loaded_model, expected_id in [
        ("phi-4-mini", "phi4-mini-q4"),
        ("phi-4", "phi4-q4"),
    ]:
        payload = build_models_payload(
            _gpu(),
            loaded_model,
            0,
            install_dir,
            data_dir,
            catalog=catalog,
            evidence=[],
        )
        loaded_rows = [model["id"] for model in payload["models"] if model["status"] == "loaded"]

        assert loaded_rows == [expected_id]
        assert payload["currentModel"] == expected_id


def test_benchmark_required_without_measurement_or_evidence(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
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
    install_dir = tmp_path / "ods"
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
    install_dir = tmp_path / "ods"
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


def test_model_payload_projects_explicit_app_compatibility(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    catalog = [{
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
        "app_compatibility": {
            "openai_chat": {"status": "verified", "reason": "direct chat passed"},
            "agent_viability": {
                "status": "not_agent_viable",
                "reason": "Agent validation failed",
                "evidence": "fleet-run/example",
            },
            "hermes_talk": {"status": "unsupported_until_revalidated", "reason": "Talk proof failed"},
            "perplexica": {
                "status": "unsupported_until_revalidated",
                "reason": "Perplexica probe failed",
                "evidence": "fleet-run/perplexica",
            },
        },
    }]

    payload = build_models_payload(_gpu(), None, 0, install_dir, data_dir, catalog=catalog, evidence=[])

    compatibility = payload["models"][0]["appCompatibility"]
    assert compatibility["openaiChat"]["status"] == "verified"
    assert compatibility["agentViability"]["status"] == "not_agent_viable"
    assert compatibility["agentViability"]["reason"] == "Agent validation failed"
    assert compatibility["agentViability"]["evidence"] == "fleet-run/example"
    assert compatibility["hermesTalk"]["status"] == "unsupported_until_revalidated"
    assert compatibility["hermesTalk"]["reason"] == "Talk proof failed"
    assert compatibility["perplexica"]["status"] == "unsupported_until_revalidated"
    assert compatibility["perplexica"]["reason"] == "Perplexica probe failed"
    assert compatibility["perplexica"]["evidence"] == "fleet-run/perplexica"


def test_scoped_app_compatibility_applies_only_to_matching_runtime():
    model = {
        "id": "mistral-nemo-12b-instruct-q4",
        "app_compatibility": {
            "hermes_talk": {
                "status": "unsupported_until_revalidated",
                "reason": "Mistral Talk probe failed on Apple llama-server",
                "gpuBackendScope": ["apple"],
                "llmBackendScope": ["llama-server"],
            },
        },
    }

    apple_llama = model_app_compatibility(
        model,
        runtime_context={"gpuBackend": "apple", "llmBackend": "llama-server", "runtime": "llama-server"},
    )
    lemonade_amd = model_app_compatibility(
        model,
        runtime_context={"gpuBackend": "amd", "llmBackend": "lemonade", "runtime": "lemonade"},
    )

    assert apple_llama["hermesTalk"]["status"] == "unsupported_until_revalidated"
    assert apple_llama["agentViability"]["status"] == "not_agent_viable"
    assert lemonade_amd["hermesTalk"]["status"] == "unknown"
    assert lemonade_amd["agentViability"]["status"] == "unknown"


def test_host_scoped_app_compatibility_applies_only_to_matching_host():
    model = {
        "id": "granite4.1-3b-q4",
        "app_compatibility": {
            "hermes_talk": {
                "status": "unsupported_until_revalidated",
                "reason": "Granite Talk probe timed out on windows-laptop",
                "hostScope": ["windows-laptop"],
            },
        },
    }

    windows_laptop = model_app_compatibility(
        model,
        runtime_context={"host": "windows-laptop", "hosts": ["windows-laptop", "light-worker"]},
    )
    strixy = model_app_compatibility(
        model,
        runtime_context={"host": "strixy", "hosts": ["strixy"]},
    )

    assert windows_laptop["hermesTalk"]["status"] == "unsupported_until_revalidated"
    assert windows_laptop["agentViability"]["status"] == "not_agent_viable"
    assert strixy["hermesTalk"]["status"] == "unknown"
    assert strixy["agentViability"]["status"] == "unknown"


def test_model_payload_applies_scoped_app_compatibility_from_install_env(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    model = {
        "id": "mistral-nemo-12b-instruct-q4",
        "name": "Mistral Nemo 12B Instruct",
        "gguf_file": "Mistral-Nemo-Instruct-2407.Q4_K_M.gguf",
        "size_mb": 7477,
        "vram_required_gb": 12,
        "context_length": 128000,
        "quantization": "Q4_K_M",
        "specialty": "Quality",
        "description": "Mistral test model",
        "llm_model_name": "mistral-nemo-instruct-2407",
        "app_compatibility": {
            "hermes_talk": {
                "status": "unsupported_until_revalidated",
                "reason": "Mistral Talk probe failed on Apple llama-server",
                "gpuBackendScope": ["apple"],
                "llmBackendScope": ["llama-server"],
            },
        },
    }

    (install_dir / ".env").write_text("GPU_BACKEND=apple\nLLM_BACKEND=llama-server\n", encoding="utf-8")
    apple_payload = build_models_payload(
        _gpu(name="Apple M5 Max", total_mb=131072, backend="apple"),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[model],
        evidence=[],
    )

    (install_dir / ".env").write_text("GPU_BACKEND=amd\nLLM_BACKEND=lemonade\n", encoding="utf-8")
    lemonade_payload = build_models_payload(
        _gpu(name="AMD Strix Halo", total_mb=126976, backend="amd"),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[model],
        evidence=[],
    )

    assert apple_payload["models"][0]["appCompatibility"]["hermesTalk"]["status"] == (
        "unsupported_until_revalidated"
    )
    assert apple_payload["models"][0]["appCompatibility"]["agentViability"]["status"] == "not_agent_viable"
    assert lemonade_payload["models"][0]["appCompatibility"]["hermesTalk"]["status"] == "unknown"
    assert lemonade_payload["models"][0]["appCompatibility"]["agentViability"]["status"] == "unknown"


def test_model_payload_applies_host_scoped_app_compatibility_from_install_env(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    model = {
        "id": "granite4.1-3b-q4",
        "name": "Granite 4.1 3B",
        "gguf_file": "granite-4.1-3b-Q4_K_M.gguf",
        "size_mb": 2100,
        "vram_required_gb": 4,
        "context_length": 131072,
        "quantization": "Q4_K_M",
        "specialty": "Tool Use",
        "description": "Granite test model",
        "llm_model_name": "granite-4.1-3b",
        "app_compatibility": {
            "hermes_talk": {
                "status": "unsupported_until_revalidated",
                "reason": "Granite Talk probe timed out on windows-laptop",
                "hostScope": ["windows-laptop"],
            },
        },
    }

    (install_dir / ".env").write_text("ODS_FLEET_HOST_ID=windows-laptop\n", encoding="utf-8")
    windows_payload = build_models_payload(
        _gpu(),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[model],
        evidence=[],
    )

    (install_dir / ".env").write_text("ODS_FLEET_HOST_ID=strixy\n", encoding="utf-8")
    strixy_payload = build_models_payload(
        _gpu(),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[model],
        evidence=[],
    )

    assert model_compatibility_runtime_context(install_dir)["hosts"] == ["strixy"]
    assert windows_payload["models"][0]["appCompatibility"]["hermesTalk"]["status"] == (
        "unsupported_until_revalidated"
    )
    assert windows_payload["models"][0]["appCompatibility"]["agentViability"]["status"] == "not_agent_viable"
    assert strixy_payload["models"][0]["appCompatibility"]["hermesTalk"]["status"] == "unknown"
    assert strixy_payload["models"][0]["appCompatibility"]["agentViability"]["status"] == "unknown"


def test_real_catalog_gemma_perplexica_block_is_global():
    by_id = {model["id"]: model for model in _official_model_catalog()}
    model = by_id["gemma3-4b-it-q4"]

    windows_laptop = model_app_compatibility(
        model,
        runtime_context={"host": "windows-laptop", "hosts": ["windows-laptop"]},
    )
    strixy = model_app_compatibility(
        model,
        runtime_context={"host": "strixy", "hosts": ["strixy"]},
    )
    tower2 = model_app_compatibility(
        model,
        runtime_context={"host": "tower2", "hosts": ["tower2"]},
    )

    assert windows_laptop["perplexica"]["status"] == "unsupported_until_revalidated"
    assert strixy["perplexica"]["status"] == "unsupported_until_revalidated"
    assert tower2["perplexica"]["status"] == "unsupported_until_revalidated"


def test_measured_local_too_slow_blocks_agent_compatibility(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    model = {
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
    }
    record_model_performance(
        "phi-4-mini",
        "NVIDIA GeForce RTX 4060",
        "nvidia",
        0.5,
        model_id="phi4-mini-q4",
        gguf="Phi-4-mini-instruct-Q4_K_M.gguf",
        context_length=128000,
        vram_total_mb=8192,
    )

    payload = build_models_payload(
        _gpu(),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[model],
        evidence=[],
    )

    compatibility = payload["models"][0]["appCompatibility"]
    assert payload["models"][0]["performance"]["source"] == "measured_local"
    assert payload["models"][0]["tokensPerSec"] == 0.5
    assert compatibility["hermesTalk"]["status"] == "unsupported_until_revalidated"
    assert compatibility["agentViability"]["status"] == "not_agent_viable"
    assert "0.5 tok/s" in compatibility["agentViability"]["reason"]


def test_published_exact_too_slow_blocks_agent_compatibility(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    model = {
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
    }
    evidence = [{
        "model_id": "phi4-mini-q4",
        "model_names": ["phi-4-mini", "Phi-4-mini-instruct-Q4_K_M.gguf"],
        "quantization": "Q4_K_M",
        "backend": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 4060",
        "vram_gb": 8,
        "context_length": 128000,
        "runtime": "llama-server",
        "tokens_per_second": 0.5,
    }]

    payload = build_models_payload(
        _gpu(),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[model],
        evidence=evidence,
    )

    compatibility = payload["models"][0]["appCompatibility"]
    assert payload["models"][0]["performance"]["source"] == "published_exact"
    assert compatibility["hermesTalk"]["status"] == "unsupported_until_revalidated"
    assert compatibility["agentViability"]["status"] == "not_agent_viable"


def test_bundled_windows_laptop_phi_evidence_blocks_agent_compatibility(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    model = {
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
    }

    payload = build_models_payload(
        _gpu(name="NVIDIA GeForce RTX 5070 Laptop GPU", total_mb=8188),
        None,
        0,
        install_dir,
        data_dir,
        catalog=[model],
        evidence=load_evidence(),
    )

    compatibility = payload["models"][0]["appCompatibility"]
    assert payload["models"][0]["performance"]["source"] == "published_exact"
    assert payload["models"][0]["tokensPerSec"] == 0.5
    assert compatibility["hermesTalk"]["status"] == "unsupported_until_revalidated"
    assert compatibility["agentViability"]["status"] == "not_agent_viable"


def test_real_catalog_has_six_windows_8gb_release_swap_candidates(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    (install_dir / ".env").write_text("ODS_FLEET_HOST_ID=windows-laptop\n", encoding="utf-8")
    catalog = _official_model_catalog()

    payload = build_models_payload(
        _gpu(name="NVIDIA GeForce RTX 5070 Laptop GPU", total_mb=8188),
        "qwen3.5-9b",
        0,
        install_dir,
        data_dir,
        catalog=catalog,
        evidence=load_evidence(),
    )

    candidates = [
        model for model in payload["models"]
        if model["id"] != "qwen3.5-9b-q4"
        and model["status"] in {"available", "downloaded"}
        and model["fitsVram"] is not False
        and model["contextLength"] >= 64000
        and all(
            not _compatibility_blocks_release_coverage(entry)
            for entry in model["appCompatibility"].values()
        )
    ]
    candidate_ids = {model["id"] for model in candidates}
    by_id = {model["id"]: model for model in candidates}
    all_by_id = {model["id"]: model for model in payload["models"]}

    assert len(candidates) >= 6
    assert {
        "qwen3.5-4b-q4",
        "qwen3-4b-instruct-2507-q4",
        "qwen3-4b-128k-q4",
        "qwen2.5-coder-1.5b-128k-q4",
        "granite4.0-h-micro-q4",
        "granite4.0-h-tiny-q4",
    }.issubset(candidate_ids)
    assert by_id["qwen3.5-4b-q4"]["contextLength"] == 262144
    assert by_id["qwen3-4b-instruct-2507-q4"]["contextLength"] == 262144
    assert by_id["qwen3-4b-128k-q4"]["contextLength"] == 131072
    assert by_id["qwen2.5-coder-1.5b-128k-q4"]["contextLength"] == 131072
    assert all_by_id["falcon-h1-1.5b-instruct-q4"]["appCompatibility"]["hermesTalk"]["status"] == (
        "unsupported_until_revalidated"
    )
    assert all_by_id["falcon-h1-1.5b-instruct-q4"]["appCompatibility"]["agentViability"]["status"] == (
        "not_agent_viable"
    )
    assert all_by_id["falcon-h1-3b-instruct-q4"]["appCompatibility"]["hermesTalk"]["status"] == (
        "unsupported_until_revalidated"
    )
    assert all_by_id["falcon-h1-3b-instruct-q4"]["appCompatibility"]["agentViability"]["status"] == (
        "not_agent_viable"
    )
    assert all_by_id["qwen2.5-coder-1.5b-128k-q4"]["appCompatibility"]["hermesTalk"]["status"] == "unknown"
    assert all_by_id["phi3-mini-128k-q4"]["appCompatibility"]["hermesTalk"]["status"] == "unknown"
    assert all_by_id["phi3-mini-128k-q4"]["appCompatibility"]["perplexica"]["status"] == (
        "unsupported_until_revalidated"
    )
    assert all_by_id["granite4.1-3b-q4"]["appCompatibility"]["hermesTalk"]["status"] == (
        "unsupported_until_revalidated"
    )
    assert all_by_id["granite3.1-2b-instruct-q4"]["appCompatibility"]["perplexica"]["status"] == (
        "unsupported_until_revalidated"
    )
    assert all_by_id["granite4.0-h-1b-q4"]["appCompatibility"]["perplexica"]["status"] == "unknown"
    assert "granite3.1-2b-instruct-q4" not in candidate_ids
    assert "granite4.0-h-1b-q4" in candidate_ids
    assert "phi4-mini-q4" not in candidate_ids
    assert "gemma3-4b-it-q4" not in candidate_ids
    assert "falcon-h1-1.5b-instruct-q4" not in candidate_ids
    assert "falcon-h1-3b-instruct-q4" not in candidate_ids
    assert "granite4.1-3b-q4" not in candidate_ids
    assert "granite4.0-h-350m-q4" not in candidate_ids
    assert "granite4.0-1b-q4" not in candidate_ids
    assert "phi3-mini-128k-q4" not in candidate_ids
    assert "granite3.3-8b-instruct-q4" not in candidate_ids
    assert "smollm3-3b-q4" not in candidate_ids
    assert "qwen2.5-3b-instruct-q4" not in candidate_ids
    assert "qwen3-4b-q4" not in candidate_ids
    assert "qwen3-1.7b-q4" not in candidate_ids


def test_installer_recommended_model_survives_bootstrap_env(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    (install_dir / ".env").write_text(
        "LLM_MODEL=qwen3.5-2b\n"
        "GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf\n"
        "MODEL_RECOMMENDED_MODEL=qwen3.5-9b\n"
        "MODEL_RECOMMENDED_GGUF=Qwen3.5-9B-Q4_K_M.gguf\n"
        "MODEL_RECOMMENDED_CONTEXT=65536\n"
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
    assert payload["hermesMinimumContext"] == 65536
    assert payload["hermesTargetContext"] == 131072
    assert by_id["qwen3.5-2b-q4"]["status"] == "loaded"
    assert by_id["qwen3.5-9b-q4"]["contextLength"] == 65536
    assert by_id["qwen3.5-9b-q4"]["recommended"] is True
    assert by_id["qwen3.5-9b-q4"]["recommendation"]["source"] == "installer_tier_map"
    assert by_id["qwen3.5-9b-q4"]["recommendation"]["contextLength"] == 65536
    assert payload["recommendationAlternatives"][0]["id"] == "qwen3.5-9b-q4"


def test_configured_model_prefers_env_file_over_stale_process_env(data_dir, tmp_path, monkeypatch):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)
    (install_dir / ".env").write_text(
        "LLM_MODEL=phi-4-mini\n"
        "GGUF_FILE=Phi-4-mini-instruct-Q4_K_M.gguf\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "qwen3.6-35b-a3b")
    monkeypatch.setenv("GGUF_FILE", "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
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
            "id": "qwen3.6-35b-a3b-ud-q4",
            "name": "Qwen 3.6 35B A3B",
            "gguf_file": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "size_mb": 21500,
            "vram_required_gb": 24,
            "context_length": 65536,
            "quantization": "UD-Q4_K_M",
            "specialty": "Reasoning",
            "description": "Large reasoning model.",
            "llm_model_name": "qwen3.6-35b-a3b",
        },
    ]

    payload = build_models_payload(
        _gpu(),
        "Phi-4-mini-instruct-Q4_K_M",
        0,
        install_dir,
        data_dir,
        catalog=catalog,
        evidence=[],
    )

    by_id = {model["id"]: model for model in payload["models"]}
    assert payload["currentModel"] == "phi4-mini-q4"
    assert payload["configuredModel"] == "phi4-mini-q4"
    assert by_id["phi4-mini-q4"]["configured"] is True
    assert by_id["qwen3.6-35b-a3b-ud-q4"]["configured"] is False


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

    install_dir = tmp_path / "ods"
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


def test_windows_amd_host_runtime_uses_install_ram_when_gpu_probe_is_unavailable(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    install_dir.mkdir(parents=True)
    models_dir = data_dir / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf").write_text("placeholder", encoding="utf-8")
    (install_dir / ".env").write_text(
        "GPU_BACKEND=amd\n"
        "LLM_BACKEND=lemonade\n"
        "AMD_INFERENCE_RUNTIME=lemonade\n"
        "AMD_INFERENCE_LOCATION=host\n"
        "SYSTEM_RAM_GB=128\n"
        "MODEL_RECOMMENDATION_POLICY=context-aware-largest-capable-general-v1+unified-memory-coder-next-a3b-v1\n",
        encoding="utf-8",
    )
    catalog = [{
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
            "id": "amd-strix-halo-unified",
            "label": "AMD Strix Halo unified-memory profile",
            "backend": "amd",
            "memory_type": "unified",
            "vram_min_gb": 90,
            "system_ram_min_gb": 64,
            "estimated_required_gb": 44,
            "context_length": 131072,
            "fit_label": "Host AMD unified-memory fit",
        }],
    }]

    payload = build_models_payload(None, None, 0, install_dir, data_dir, catalog=catalog, evidence=[])

    model = payload["models"][0]
    assert payload["gpu"]["vramTotal"] == 128
    assert payload["gpu"]["vramFree"] == 128
    assert model["status"] == "downloaded"
    assert model["fitsVram"] is True
    assert model["runtimeProfile"]["id"] == "amd-strix-halo-unified"


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
    monkeypatch.setattr("performance_oracle.platform.machine", lambda: "x86_64")
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
    install_dir = tmp_path / "ods"
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


def test_implausible_live_counter_is_not_emitted_as_measured_speed(data_dir, tmp_path):
    install_dir = tmp_path / "ods"
    (install_dir / "data" / "models").mkdir(parents=True)

    payload = build_models_payload(
        _gpu(),
        "qwen3.5-9b",
        1_000_000,
        install_dir,
        data_dir,
        context_length=32768,
        catalog=[_model()],
        evidence=[],
    )

    loaded = payload["models"][0]
    assert loaded["status"] == "loaded"
    assert loaded["performance"]["source"] == "benchmark_required"
    assert loaded["tokensPerSec"] is None


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
    install_dir = tmp_path / "ods"
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


def test_polluted_history_is_ignored_in_favor_of_a_valid_exact_alias(
    data_dir, tmp_path, monkeypatch,
):
    import performance_oracle

    samples = iter([
        {"tokens_per_second": 1_000_000, "sample_count": 336},
        {"tokens_per_second": 240.5, "sample_count": 1},
    ])
    monkeypatch.setattr(
        performance_oracle,
        "get_recorded_model_performance",
        lambda *args, **kwargs: next(samples, None),
    )
    install_dir = tmp_path / "ods"
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

    assert payload["models"][0]["performance"]["source"] == "measured_local"
    assert payload["models"][0]["tokensPerSec"] == 240.5


def test_official_catalog_families_expose_their_real_hugging_face_identity():
    cases = {
        "Qwen 3.5 2B": ("Qwen", "Qwen"),
        "Phi-4 Mini": ("Microsoft", "microsoft"),
        "Granite 3.3 2B": ("IBM Granite", "ibm-granite"),
        "SmolLM3 3B": ("Hugging Face", "HuggingFaceTB"),
        "Gemma 3 4B": ("Google", "google"),
        "Falcon H1 7B": ("Technology Innovation Institute", "tiiuae"),
        "Ministral 3B": ("Mistral AI", "mistralai"),
        "Llama 3.2 3B": ("Meta", "meta-llama"),
        "DeepSeek R1 7B": ("DeepSeek", "deepseek-ai"),
    }

    for name, (publisher, author) in cases.items():
        assert model_publisher({"name": name}) == {
            "name": publisher,
            "huggingFaceAuthor": author,
        }


def test_publisher_identity_does_not_match_family_substrings_inside_other_words():
    assert model_publisher({"name": "Dolphin 2.9 Mixtral 8x7B"}) == {
        "name": "Mistral AI",
        "huggingFaceAuthor": "mistralai",
    }
    assert model_publisher({"name": "OpenPhind Code Model"}) is None


def test_calibrated_prediction_above_single_request_ceiling_requires_benchmark(data_dir):
    record_model_performance(
        "calibration-model",
        "NVIDIA GeForce RTX 4060",
        "nvidia",
        5_000,
        decode_read_mb=10_000,
        vram_total_mb=8192,
    )
    performance = evaluate_performance(
        {**_model(), "decode_read_mb": 1},
        _gpu(),
        {"quantization": "Q4_K_M", "readable": False},
        False,
        0,
        32768,
        {},
        [],
        True,
    )

    assert performance["source"] == "benchmark_required"


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


def test_published_exact_matches_gguf_stem_identity(data_dir):
    evidence = [{
        "model_id": "Qwen3.5-9B-Q4_K_M",
        "model_names": [],
        "quantization": "Q4_K_M",
        "backend": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 4060",
        "vram_gb": 8,
        "context_length": 32768,
        "tokens_per_second": 43.7,
        "source_url": "https://example.test/stem-bench",
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
    assert perf["tokensPerSec"] == 43.7
    assert perf["sourceUrl"] == "https://example.test/stem-bench"
