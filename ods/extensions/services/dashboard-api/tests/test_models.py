"""Focused tests for the models router helpers."""

from __future__ import annotations

import importlib
import asyncio
import json
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest

from models import GPUInfo


def test_fetch_loaded_model_uses_configured_llm_url(monkeypatch):
    """Windows Lemonade exposes the runtime through LLM_URL, not llama-server DNS."""
    import routers.models as models_router

    seen_urls: list[str] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"model_loaded": "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"}

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            seen_urls.append(url)
            return _Response()

    monkeypatch.setenv("LLM_URL", "http://host.docker.internal:8080/api/v1")
    monkeypatch.setattr(models_router.httpx, "AsyncClient", _Client)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            models_router._fetch_llama_loaded_model("llama-server", 8080, "/api/v1")
        )
    finally:
        loop.close()

    assert result == "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    assert seen_urls == ["http://host.docker.internal:8080/api/v1/health"]


def test_default_model_discovery_timeout_covers_slow_local_runtime():
    import routers.models as models_router

    assert models_router._MODEL_DISCOVERY_TIMEOUT_SECONDS >= 10.0


def test_agent_model_status_collapses_concurrent_poll_bursts(monkeypatch):
    import routers.models as models_router

    calls = 0
    calls_lock = threading.Lock()

    def fake_request(method, path, *, timeout, payload=None):
        nonlocal calls
        assert method == "GET"
        assert path == "/v1/model/status"
        assert timeout == 5
        assert payload is None
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {"status": "downloading", "percent": 42}

    monkeypatch.setattr(models_router, "request_agent_json", fake_request)
    monkeypatch.setattr(models_router, "_AGENT_MODEL_STATUS_CACHE_TTL_SECONDS", 1.0)
    monkeypatch.setattr(models_router, "_agent_model_status_cache_at", 0.0)
    monkeypatch.setattr(models_router, "_agent_model_status_cache_value", None)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: models_router._get_agent_model_status(), range(16)))

    assert calls == 1
    assert results == [{"status": "downloading", "percent": 42}] * 16


def test_agent_model_status_and_actions_share_transport(monkeypatch):
    import routers.models as models_router

    calls = []

    def fake_request(method, path, *, timeout, payload=None):
        calls.append((method, path, timeout, payload))
        return {"status": "idle" if method == "GET" else "started"}

    monkeypatch.setattr(models_router, "request_agent_json", fake_request)
    monkeypatch.setattr(models_router, "_AGENT_MODEL_STATUS_CACHE_TTL_SECONDS", 0.0)
    monkeypatch.setattr(models_router, "_agent_model_status_cache_at", 0.0)

    assert models_router._get_agent_model_status() == {"status": "idle"}
    assert models_router._call_agent_model("/v1/model/download", {"model": "test"}) == {
        "status": "started"
    }
    assert calls == [
        ("GET", "/v1/model/status", 5, None),
        ("POST", "/v1/model/download", 30, {"model": "test"}),
    ]


def test_agent_activation_conflict_preserves_target(monkeypatch):
    import routers.models as models_router

    payload = {
        "error": "Another model activation is in progress",
        "activeModelId": "phi4-mini-q4",
    }

    def conflict(*_args, **_kwargs):
        raise models_router.AgentHTTPError(409, payload["error"], json.dumps(payload))

    monkeypatch.setattr(models_router, "request_agent_json", conflict)

    with pytest.raises(models_router.HTTPException) as exc_info:
        models_router._call_agent_model("/v1/model/activate", {"model_id": "phi4-mini-q4"}, timeout=600)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == payload


def test_fetch_loaded_model_falls_back_to_models_when_lemonade_health_empty(monkeypatch):
    import routers.models as models_router

    seen_urls: list[str] = []

    class _Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            seen_urls.append(url)
            if url.endswith("/health"):
                return _Response({"status": "ok", "model_loaded": None})
            return _Response({"data": [{"id": "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"}]})

    monkeypatch.setenv("LLM_URL", "http://host.docker.internal:8080")
    monkeypatch.setattr(models_router.httpx, "AsyncClient", _Client)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            models_router._fetch_llama_loaded_model("llama-server", 8080, "/api/v1")
        )
    finally:
        loop.close()

    assert result == "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    assert seen_urls == [
        "http://host.docker.internal:8080/api/v1/health",
        "http://host.docker.internal:8080/api/v1/models",
    ]


def test_fetch_loaded_model_prefers_configured_lemonade_gguf_over_catalog_first(
    monkeypatch,
    tmp_path,
):
    import routers.models as models_router

    seen_urls: list[str] = []
    install_dir = tmp_path / "ods"
    install_dir.mkdir()
    (install_dir / ".env").write_text(
        "GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf\n"
        "LLM_MODEL=qwen3.6-35b-a3b-ud-q4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(models_router, "INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(models_router, "_ENV_PATH", install_dir / ".env")

    class _Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            seen_urls.append(url)
            if url.endswith("/health"):
                return _Response({"status": "ok", "model_loaded": None})
            return _Response({
                "data": [
                    {"id": "Qwen3-Coder-Next-GGUF", "downloaded": True},
                    {
                        "id": "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                        "checkpoint": "C:\\users\\conta\\ods\\data\\models\\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                        "downloaded": True,
                    },
                ],
            })

    monkeypatch.setenv("LLM_URL", "http://host.docker.internal:8080")
    monkeypatch.setattr(models_router.httpx, "AsyncClient", _Client)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            models_router._fetch_llama_loaded_model("llama-server", 8080, "/api/v1")
        )
    finally:
        loop.close()

    assert result == "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    assert seen_urls == [
        "http://host.docker.internal:8080/api/v1/health",
        "http://host.docker.internal:8080/api/v1/models",
    ]


def test_already_active_model_uses_env_file_before_stale_process_env(
    monkeypatch,
    tmp_path,
):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    (install_dir / ".env").write_text(
        "GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf\n"
        "LLM_MODEL=qwen3.6-35b-a3b\n",
        encoding="utf-8",
    )
    model_file = data_dir / "models" / "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("model", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "qwen3.5-2b")
    monkeypatch.setattr(
        models_router,
        "_fetch_loaded_model_sync",
        lambda: "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
    )
    monkeypatch.setattr(models_router, "_loaded_model_backend_ready_sync", lambda _model: True)

    already_active, loaded_model = models_router._already_active_model(
        "qwen3.6-35b-a3b-ud-q4",
        {
            "id": "qwen3.6-35b-a3b-ud-q4",
            "gguf_file": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "llm_model_name": "qwen3.6-35b-a3b",
        },
    )

    assert already_active is True
    assert loaded_model == "extra.Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"


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

    install_dir = tmp_path / "ods"
    data_dir = install_dir / "data"
    data_dir.mkdir(parents=True)
    (install_dir / ".env").write_text("ODS_MODE=local\n", encoding="utf-8")
    monkeypatch.setattr(helpers, "_PERF_FILE", data_dir / "model_performance.json")
    monkeypatch.setattr(models_router, "INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(models_router, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(models_router, "_LIBRARY_PATH", install_dir / "config" / "model-library.json")
    monkeypatch.setattr(models_router, "_MODELS_DIR", data_dir / "models")
    monkeypatch.setattr(models_router, "_ENV_PATH", install_dir / ".env")
    monkeypatch.setattr(models_router, "ODS_MODE_EFFECTIVE", "local")
    return models_router, install_dir, data_dir


@pytest.mark.parametrize("mode", ["local", "hybrid", "lemonade"])
def test_model_activation_mode_policy_allows_matching_local_modes(mode):
    import routers.models as models_router

    assert models_router._model_activation_mode_denial(mode, mode) is None


@pytest.mark.parametrize(
    ("effective_mode", "configured_mode", "expected_code", "expected_reason"),
    [
        ("cloud", "cloud", "local_mode_required", "effective_mode_not_local"),
        ("unknown", "local", "ods_mode_unknown", "mode_unknown"),
        ("local", "invalid", "ods_mode_unknown", "mode_unknown"),
        ("cloud", "local", "ods_mode_mismatch", "mode_mismatch"),
        ("local", "cloud", "ods_mode_mismatch", "mode_mismatch"),
    ],
)
def test_load_model_rejects_unsafe_mode_before_lookup_or_agent_call(
    test_client,
    monkeypatch,
    tmp_path,
    effective_mode,
    configured_mode,
    expected_code,
    expected_reason,
):
    models_router, install_dir, _data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    (install_dir / ".env").write_text(
        f"ODS_MODE={configured_mode}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(models_router, "ODS_MODE_EFFECTIVE", effective_mode)
    monkeypatch.setattr(
        models_router,
        "_call_agent_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe activation reached host agent")
        ),
    )

    response = test_client.post(
        "/api/models/not-installed/load",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    message = detail.pop("message")
    assert message.startswith("Local model activation is unavailable")
    assert detail == {
        "error": "local_mode_required",
        "code": expected_code,
        "reason": expected_reason,
        "effectiveMode": models_router.normalize_ods_mode(effective_mode),
        "configuredMode": models_router.normalize_ods_mode(configured_mode),
        "requestedModelId": "not-installed",
    }


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
        "ODS_MODE=cloud\n"
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
    assert resp.json()["odsMode"] == "local"
    assert resp.json()["configuredMode"] == "cloud"
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


def test_load_model_noops_when_requested_model_already_loaded(test_client, monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
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
    (data_dir / "models" / "Qwen3.5-9B-Q4_K_M.gguf").write_text("model", encoding="utf-8")
    (install_dir / ".env").write_text(
        "ODS_MODE=local\n"
        "LLM_MODEL=qwen3.5-9b\n"
        "GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(models_router, "_fetch_loaded_model_sync", lambda: "extra.Qwen3.5-9B-Q4_K_M.gguf")
    monkeypatch.setattr(models_router, "_loaded_model_backend_ready_sync", lambda loaded: True)

    def fail_agent_call(*_args, **_kwargs):
        raise AssertionError("already-active model should not call host-agent activate")

    monkeypatch.setattr(models_router, "_call_agent_model", fail_agent_call)

    resp = test_client.post("/api/models/qwen3.5-9b-q4/load", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "already_active",
        "model_id": "qwen3.5-9b-q4",
        "loadedModel": "extra.Qwen3.5-9B-Q4_K_M.gguf",
    }


def test_load_model_delegates_when_live_backend_reports_different_model(test_client, monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
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
    (data_dir / "models" / "Qwen3.5-9B-Q4_K_M.gguf").write_text("model", encoding="utf-8")
    (install_dir / ".env").write_text(
        "ODS_MODE=local\n"
        "LLM_MODEL=qwen3.5-9b\n"
        "GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(models_router, "_fetch_loaded_model_sync", lambda: "other-model.gguf")
    monkeypatch.setattr(
        models_router,
        "_call_agent_model",
        lambda path, body, timeout=30: {"status": "activated", "path": path, "body": body, "timeout": timeout},
    )

    resp = test_client.post("/api/models/qwen3.5-9b-q4/load", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "activated",
        "path": "/v1/model/activate",
        "body": {"model_id": "qwen3.5-9b-q4"},
        "timeout": 2700,
    }


def test_load_model_delegates_when_loaded_backend_is_not_ready(test_client, monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
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
    (data_dir / "models" / "Qwen3.5-9B-Q4_K_M.gguf").write_text("model", encoding="utf-8")
    (install_dir / ".env").write_text(
        "ODS_MODE=local\n"
        "LLM_MODEL=qwen3.5-9b\n"
        "GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(models_router, "_fetch_loaded_model_sync", lambda: "extra.Qwen3.5-9B-Q4_K_M.gguf")
    monkeypatch.setattr(models_router, "_loaded_model_backend_ready_sync", lambda loaded: False)
    monkeypatch.setattr(
        models_router,
        "_call_agent_model",
        lambda path, body, timeout=30: {"status": "activated", "path": path, "body": body, "timeout": timeout},
    )

    resp = test_client.post("/api/models/qwen3.5-9b-q4/load", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "activated",
        "path": "/v1/model/activate",
        "body": {"model_id": "qwen3.5-9b-q4"},
        "timeout": 2700,
    }


def test_load_model_delegates_local_gguf_without_catalog_entry(test_client, monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [])
    (data_dir / "models" / "OpenAI-20B-NEO-CODE-DI-Uncensored-Q8_0.gguf").write_text(
        "model",
        encoding="utf-8",
    )
    (install_dir / ".env").write_text(
        "ODS_MODE=local\nMAX_CONTEXT=65536\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        models_router,
        "_call_agent_model",
        lambda path, body, timeout=30: {"status": "activated", "path": path, "body": body, "timeout": timeout},
    )

    resp = test_client.post(
        "/api/models/OpenAI-20B-NEO-CODE-DI-Uncensored-Q8_0/load",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "activated",
        "path": "/v1/model/activate",
        "body": {"model_id": "OpenAI-20B-NEO-CODE-DI-Uncensored-Q8_0"},
        "timeout": 2700,
    }


def test_local_gguf_scan_keeps_mixed_case_and_skips_empty(monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [])
    (data_dir / "models" / "MixedCaseModel.GGUF").write_text("model", encoding="utf-8")
    (data_dir / "models" / "empty.gguf").write_text("", encoding="utf-8")
    (data_dir / "models" / "partial.gguf.part").write_text("partial", encoding="utf-8")

    assert models_router._scan_downloaded_models() == {
        "MixedCaseModel.GGUF": len("model"),
    }


def test_download_status_prefers_host_agent_normalized_status(test_client, monkeypatch, tmp_path):
    models_router, _install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    status_path = data_dir / "model-download-status.json"
    status_path.write_text(
        json.dumps({
            "status": "downloading",
            "model": "Phi-4-mini-instruct-Q4_K_M.gguf",
            "bytesDownloaded": 0,
            "bytesTotal": 2491874272,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        models_router,
        "_get_agent_model_status",
        lambda: {
            "status": "failed",
            "model": "Phi-4-mini-instruct-Q4_K_M.gguf",
            "error": "Model download is not running; previous download was interrupted.",
        },
    )

    resp = test_client.get("/api/models/download-status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert "not running" in resp.json()["error"]


def test_load_model_resolves_local_gguf_by_stem_with_mixed_case_extension(
    test_client,
    monkeypatch,
    tmp_path,
):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [])
    (data_dir / "models" / "MixedCaseModel.GGUF").write_text("model", encoding="utf-8")
    monkeypatch.setattr(
        models_router,
        "_call_agent_model",
        lambda path, body, timeout=30: {"status": "activated", "path": path, "body": body, "timeout": timeout},
    )

    resp = test_client.post(
        "/api/models/MixedCaseModel/load",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    assert models_router._find_loadable_model("MixedCaseModel")["gguf_file"] == "MixedCaseModel.GGUF"
    assert resp.json() == {
        "status": "activated",
        "path": "/v1/model/activate",
        "body": {"model_id": "MixedCaseModel"},
        "timeout": 2700,
    }


def test_local_gguf_model_uses_safe_logical_id_for_spaced_filename(monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [])
    (data_dir / "models" / "My Custom Model.Q8_0.GGUF").write_text("model", encoding="utf-8")

    model = models_router._find_loadable_model("My Custom Model.Q8_0")

    assert model["gguf_file"] == "My Custom Model.Q8_0.GGUF"
    assert model["id"] == "My-Custom-Model.Q8_0"
    assert model["llm_model_name"] == "My-Custom-Model.Q8_0"


def test_local_gguf_ui_id_loads_and_deletes_spaced_filename(test_client, monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [])
    gguf = data_dir / "models" / "My Custom Model.Q8_0.GGUF"
    gguf.write_text("model", encoding="utf-8")
    calls = []

    def agent_call(path, body, timeout=30):
        calls.append((path, body, timeout))
        return {"status": "ok"}

    monkeypatch.setattr(models_router, "_call_agent_model", agent_call)

    load_response = test_client.post(
        "/api/models/My-Custom-Model.Q8_0/load",
        headers=test_client.auth_headers,
    )
    delete_response = test_client.delete(
        "/api/models/My-Custom-Model.Q8_0",
        headers=test_client.auth_headers,
    )

    assert load_response.status_code == 200
    assert delete_response.status_code == 200
    assert calls == [
        ("/v1/model/activate", {"model_id": "My-Custom-Model.Q8_0"}, 2700),
        ("/v1/model/delete", {"gguf_file": "My Custom Model.Q8_0.GGUF"}, 30),
    ]


def test_delete_local_gguf_rejects_path_separators(test_client, monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [])
    (data_dir / "models" / "nested.gguf").write_text("model", encoding="utf-8")
    monkeypatch.setattr(
        models_router,
        "_call_agent_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe delete reached host agent")),
    )

    response = test_client.delete(
        "/api/models/..%5Cnested",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 404


def test_load_model_rejects_local_gguf_path_separators(test_client, monkeypatch, tmp_path):
    models_router, install_dir, data_dir = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install_dir, [])
    (data_dir / "models" / "nested.gguf").write_text("model", encoding="utf-8")

    resp = test_client.post(
        "/api/models/..%5Cnested/load",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 404
