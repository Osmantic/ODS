"""Focused tests for the models router helpers."""

from __future__ import annotations

import importlib
import json
import sys
import types
from unittest.mock import AsyncMock

from models import GPUInfo


def test_get_gpu_vram_returns_none_on_nvml_error(monkeypatch):
    """Operational NVML failures should degrade to unknown GPU rather than 500."""

    class FakeNVMLError(Exception):
        pass

    def _raise_nvml_error():
        raise FakeNVMLError("driver not loaded")

    real_gpu = sys.modules.get("gpu")
    real_pynvml = sys.modules.get("pynvml")

    monkeypatch.setitem(sys.modules, "gpu", types.SimpleNamespace(get_gpu_info=_raise_nvml_error))
    monkeypatch.setitem(sys.modules, "pynvml", types.SimpleNamespace(NVMLError=FakeNVMLError))

    import routers.models as models_router

    importlib.reload(models_router)
    assert models_router._get_gpu_vram() is None

    if real_gpu is None:
        monkeypatch.delitem(sys.modules, "gpu", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "gpu", real_gpu)

    if real_pynvml is None:
        monkeypatch.delitem(sys.modules, "pynvml", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "pynvml", real_pynvml)

    importlib.reload(models_router)


def _write_model_library(install_dir, models):
    config_dir = install_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "model-library.json").write_text(
        json.dumps({"version": 2, "models": models}),
        encoding="utf-8",
    )
    (install_dir / "data" / "models").mkdir(parents=True)


def _patch_model_router_paths(monkeypatch, tmp_path):
    import helpers
    import routers.models as models_router

    install_dir = tmp_path / "dream-server"
    data_dir = install_dir / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(helpers, "_PERF_FILE", data_dir / "model_performance.json")
    monkeypatch.setattr(models_router, "INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(models_router, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(models_router, "_LIBRARY_PATH", install_dir / "config" / "model-library.json")
    monkeypatch.setattr(models_router, "_MODELS_DIR", data_dir / "models")
    monkeypatch.setattr(models_router, "_ENV_PATH", install_dir / ".env")
    return models_router, install_dir, data_dir


def _gpu():
    return GPUInfo(
        name="NVIDIA GeForce RTX 4060",
        memory_used_mb=1024,
        memory_total_mb=8192,
        memory_percent=12.5,
        utilization_percent=0,
        temperature_c=40,
        gpu_backend="nvidia",
    )


def test_api_models_returns_full_catalog_without_fake_tokens(test_client, monkeypatch, tmp_path):
    models_router, install_dir, _data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [
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
            "tokens_per_sec_estimate": 130,
            "llm_model_name": "phi-4-mini",
        },
        {
            "id": "deepseek-r1-7b-q4",
            "name": "DeepSeek R1 7B",
            "gguf_file": "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
            "size_mb": 4680,
            "vram_required_gb": 7,
            "context_length": 32768,
            "quantization": "Q4_K_M",
            "specialty": "Reasoning",
            "description": "Reasoning model.",
            "tokens_per_sec_estimate": 80,
            "llm_model_name": "deepseek-r1-distill-qwen-7b",
        },
    ])
    monkeypatch.setattr(models_router, "get_gpu_info", lambda: _gpu())
    monkeypatch.setattr(models_router, "get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(models_router, "get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
    monkeypatch.setattr(models_router, "get_llama_context_size", AsyncMock(return_value=None))

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    payload = resp.json()
    assert [model["id"] for model in payload["models"]] == ["phi4-mini-q4", "deepseek-r1-7b-q4"]
    assert payload["models"][0]["tokensPerSec"] is None
    assert payload["models"][0]["tokensPerSecEstimate"] == 130
    assert payload["models"][0]["performance"]["source"] == "benchmark_required"


def test_api_models_falls_back_to_loaded_model_probe(test_client, monkeypatch, tmp_path):
    models_router, install_dir, _data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [{
        "id": "qwen3.5-9b-q4",
        "name": "Qwen 3.5 9B",
        "gguf_file": "Qwen3.5-9B-Q4_K_M.gguf",
        "size_mb": 5760,
        "vram_required_gb": 8,
        "context_length": 32768,
        "quantization": "Q4_K_M",
        "specialty": "General",
        "description": "Balanced default.",
        "llm_model_name": "qwen3.5-9b",
    }])
    monkeypatch.setattr(models_router, "get_gpu_info", lambda: _gpu())
    monkeypatch.setattr(models_router, "get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(models_router, "_fetch_llama_loaded_model", AsyncMock(return_value="Qwen3.5-9B-Q4_K_M.gguf"))
    monkeypatch.setattr(models_router, "get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 33.0, "lifetime_tokens": 0}))
    monkeypatch.setattr(models_router, "get_llama_context_size", AsyncMock(return_value=32768))
    monkeypatch.setattr(models_router, "SERVICES", {"llama-server": {"host": "localhost", "port": 8080}})

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["currentModel"] == "qwen3.5-9b-q4"
    assert payload["loadedModel"] == "Qwen3.5-9B-Q4_K_M.gguf"
    assert payload["models"][0]["performance"]["source"] == "measured_local"


def test_api_models_marks_installer_configured_model(test_client, monkeypatch, tmp_path):
    models_router, install_dir, _data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [{
        "id": "qwen3.5-9b-q4",
        "name": "Qwen 3.5 9B",
        "gguf_file": "Qwen3.5-9B-Q4_K_M.gguf",
        "size_mb": 5760,
        "vram_required_gb": 8,
        "context_length": 32768,
        "quantization": "Q4_K_M",
        "specialty": "General",
        "description": "Balanced default.",
        "llm_model_name": "qwen3.5-9b",
    }])
    (install_dir / ".env").write_text(
        "LLM_MODEL=qwen3.5-9b\n"
        "GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(models_router, "get_gpu_info", lambda: _gpu())
    monkeypatch.setattr(models_router, "get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(models_router, "get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
    monkeypatch.setattr(models_router, "get_llama_context_size", AsyncMock(return_value=None))

    resp = test_client.get("/api/models", headers=test_client.auth_headers)

    assert resp.status_code == 200
    model = resp.json()["models"][0]
    assert resp.json()["configuredModel"] == "qwen3.5-9b-q4"
    assert model["recommended"] is True
    assert model["configured"] is True
    assert model["recommendation"]["source"] == "installer_configured"
    assert "Benchmark" in model["performanceLabel"]


def test_benchmark_endpoint_rejects_not_loaded_model(test_client, monkeypatch, tmp_path):
    models_router, install_dir, _data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [{
        "id": "qwen3.5-9b-q4",
        "name": "Qwen 3.5 9B",
        "gguf_file": "Qwen3.5-9B-Q4_K_M.gguf",
        "size_mb": 5760,
        "vram_required_gb": 8,
        "context_length": 32768,
        "quantization": "Q4_K_M",
        "specialty": "General",
        "description": "Balanced default.",
        "llm_model_name": "qwen3.5-9b",
    }])
    monkeypatch.setattr(models_router, "get_gpu_info", lambda: _gpu())
    monkeypatch.setattr(models_router, "get_loaded_model", AsyncMock(return_value="other-model"))
    monkeypatch.setattr(models_router, "_fetch_llama_loaded_model", AsyncMock(return_value="other-model"))
    monkeypatch.setattr(models_router, "get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
    monkeypatch.setattr(models_router, "get_llama_context_size", AsyncMock(return_value=32768))
    monkeypatch.setattr(models_router, "SERVICES", {"llama-server": {"host": "localhost", "port": 8080}})

    resp = test_client.post(
        "/api/models/qwen3.5-9b-q4/benchmark",
        headers=test_client.auth_headers,
        json={"max_tokens": 64},
    )

    assert resp.status_code == 409
    assert "Load the model" in resp.json()["detail"]
