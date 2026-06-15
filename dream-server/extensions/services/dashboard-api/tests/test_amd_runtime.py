"""Tests for AMD runtime and external Lemonade diagnostic endpoint."""

import asyncio

import pytest

from routers import gpu as gpu_router
from lemonade_capabilities import ExternalLemonadeProbeResult, provider_capability_summary
from lemonade_client import LemonadeClientError


def _patch_probe(monkeypatch, health="reachable", version="unknown", warning=None):
    monkeypatch.setattr(
        gpu_router,
        "_probe_amd_health",
        lambda _url: (health, version, warning),
    )


def _patch_external_probe(
    monkeypatch,
    health="reachable",
    version="unknown",
    warnings=None,
    loaded_model="Qwen3-0.6B-GGUF",
    loaded_models=None,
    model_count=1,
    provider_capabilities=None,
):
    async def _fake_probe(_api_base, _api_path, **_kwargs):
        return (
            health,
            version,
            list(warnings or []),
            loaded_model,
            model_count,
            list(provider_capabilities or [
                {"name": "health", "status": "ok", "required": True, "detail": version},
                {"name": "models", "status": "ok", "required": True, "detail": str(model_count or 0)},
                {"name": "stats", "status": "ok", "required": False},
                {"name": "chat", "status": "ok", "required": True, "detail": loaded_model or "unknown"},
            ]),
            "passive",
            list(loaded_models or []),
        )

    monkeypatch.setattr(gpu_router, "_probe_external_lemonade", _fake_probe)


def _provider_capability(payload, name):
    for item in payload:
        if item["name"] == name:
            return item
    raise AssertionError(f"missing provider capability: {name}")


def test_clean_env_reads_install_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ENABLE_RAG", raising=False)
    monkeypatch.setattr(gpu_router, "INSTALL_DIR", str(tmp_path))
    gpu_router._env_file_cache.update({"path": None, "signature": None, "values": {}})
    (tmp_path / ".env").write_text("ENABLE_RAG=true\n", encoding="utf-8")

    assert gpu_router._clean_env("ENABLE_RAG") == "true"

    monkeypatch.setenv("ENABLE_RAG", "false")
    assert gpu_router._clean_env("ENABLE_RAG") == "false"


@pytest.mark.asyncio
async def test_external_lemonade_probe_is_single_flight(monkeypatch):
    calls = 0
    result = ExternalLemonadeProbeResult("reachable", "10.7.0", [], "model-a", 1, [], "passive", [])

    async def _fake_uncached(_api_base, _api_path, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return result

    monkeypatch.setattr(gpu_router, "_probe_external_lemonade_uncached", _fake_uncached)
    monkeypatch.setenv("DASHBOARD_LEMONADE_PROBE_TTL", "120")
    gpu_router._external_lemonade_probe_cache.update({"expires": 0.0, "updated": 0.0, "key": None, "value": None})

    responses = await asyncio.gather(
        gpu_router._probe_external_lemonade("http://lemonade:13305/api/v1", "/api/v1"),
        gpu_router._probe_external_lemonade("http://lemonade:13305/api/v1", "/api/v1"),
    )

    assert responses == [result, result]
    assert calls == 1


@pytest.mark.asyncio
async def test_forced_external_lemonade_probe_is_single_flight_for_concurrent_requests(monkeypatch):
    calls = 0
    result = ExternalLemonadeProbeResult("reachable", "10.7.0", [], "model-a", 1, [], "active", [])

    async def _fake_uncached(_api_base, _api_path, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return result

    monkeypatch.setattr(gpu_router, "_probe_external_lemonade_uncached", _fake_uncached)
    gpu_router._external_lemonade_probe_cache.update({"expires": 0.0, "updated": 0.0, "key": None, "value": None})

    responses = await asyncio.gather(
        gpu_router._probe_external_lemonade("http://lemonade:13305/api/v1", "/api/v1", active=True, force=True),
        gpu_router._probe_external_lemonade("http://lemonade:13305/api/v1", "/api/v1", active=True, force=True),
    )

    assert responses == [result, result]
    assert calls == 1


@pytest.mark.asyncio
async def test_active_probe_waiting_on_passive_probe_still_runs_actively(monkeypatch):
    calls = []

    async def _fake_uncached(_api_base, _api_path, **kwargs):
        mode = "active" if kwargs.get("active") else "passive"
        calls.append(mode)
        await asyncio.sleep(0.01)
        return ExternalLemonadeProbeResult("reachable", "10.7.0", [], "model-a", 1, [], mode, [])

    monkeypatch.setattr(gpu_router, "_probe_external_lemonade_uncached", _fake_uncached)
    gpu_router._external_lemonade_probe_cache.update({"expires": 0.0, "updated": 0.0, "key": None, "value": None})

    passive_task = asyncio.create_task(
        gpu_router._probe_external_lemonade("http://lemonade:13305/api/v1", "/api/v1")
    )
    await asyncio.sleep(0)
    active_result = await gpu_router._probe_external_lemonade(
        "http://lemonade:13305/api/v1",
        "/api/v1",
        active=True,
        force=True,
    )
    await passive_task

    assert calls == ["passive", "active"]
    assert active_result.probe_mode == "active"


@pytest.mark.asyncio
async def test_passive_external_lemonade_probe_never_triggers_inference(monkeypatch):
    calls = []

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            calls.append("health")
            return {
                "status": "ok",
                "version": "10.7.0",
                "model_loaded": "Whisper-Tiny",
                "all_models_loaded": [
                    {
                        "model_name": "Qwen3-0.6B-GGUF",
                        "type": "llm",
                        "device": "gpu",
                        "recipe": "llamacpp",
                    }
                ],
            }

        async def models(self):
            calls.append("models")
            return [
                {"id": "Whisper-Tiny"},
                {"id": "Qwen3-0.6B-GGUF", "labels": ["reasoning"]},
            ]

        async def stats(self):
            calls.append("stats")
            return {}

        async def chat_completion(self, *_args, **_kwargs):
            raise AssertionError("passive probe must not generate chat")

        async def embeddings(self, *_args, **_kwargs):
            raise AssertionError("passive probe must not generate embeddings")

        async def rerank(self, *_args, **_kwargs):
            raise AssertionError("passive probe must not run reranking")

        async def transcribe_wav(self, *_args, **_kwargs):
            raise AssertionError("passive probe must not run transcription")

        async def speech(self, *_args, **_kwargs):
            raise AssertionError("passive probe must not generate speech")

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "")
    monkeypatch.setenv("LLM_MODEL", "stale-tier-model")
    monkeypatch.setenv("LLM_BACKEND", "lemonade")
    monkeypatch.setenv("LLM_API_URL", "http://litellm:4000")

    _health, _version, warnings, loaded_model, _model_count, capabilities, probe_mode, loaded_models = (
        await gpu_router._probe_external_lemonade_uncached(
            "http://host.docker.internal:13305/api/v1",
            "/api/v1",
        )
    )

    assert calls == ["health", "models", "stats"]
    assert loaded_model == "Whisper-Tiny"
    assert warnings == ["chat_model_legacy_llm_model_ignored"]
    assert probe_mode == "passive"
    assert loaded_models == [
        {
            "modelName": "Qwen3-0.6B-GGUF",
            "type": "llm",
            "device": "gpu",
            "recipe": "llamacpp",
        }
    ]
    assert _provider_capability(capabilities, "chat")["detail"] == "Qwen3-0.6B-GGUF"
    assert _provider_capability(capabilities, "chat")["status"] == "ok"
    assert _provider_capability(capabilities, "gateway_chat")["status"] == "unverified"


@pytest.mark.asyncio
async def test_external_lemonade_probe_accepts_validated_legacy_llm_model_with_migration_warning(monkeypatch):
    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {
                "status": "ok",
                "version": "10.7.0",
                "all_models_loaded": [{"model_name": "legacy-chat", "type": "llm"}],
            }

        async def models(self):
            return [{"id": "legacy-chat", "labels": ["reasoning"]}]

        async def stats(self):
            return {}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "")
    monkeypatch.setenv("LLM_MODEL", "legacy-chat")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
    )

    assert result.warnings == ["chat_model_legacy_llm_model"]
    assert _provider_capability(result.capabilities, "chat") == {
        "name": "chat",
        "status": "ok",
        "required": True,
        "detail": "legacy-chat",
    }


@pytest.mark.asyncio
async def test_passive_external_lemonade_probe_does_not_claim_unloaded_model_ready(monkeypatch):
    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "all_models_loaded": []}

        async def models(self):
            return [{"id": "Qwen3-0.6B-GGUF", "downloaded": True}]

        async def stats(self):
            return {}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "Qwen3-0.6B-GGUF")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
    )

    assert result.loaded_models == []
    assert _provider_capability(result.capabilities, "chat")["status"] == "unverified"
    assert provider_capability_summary(result.capabilities) == (None, "unverified")


@pytest.mark.asyncio
async def test_passive_external_lemonade_probe_does_not_claim_model_missing_when_catalog_fails(monkeypatch):
    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "all_models_loaded": []}

        async def models(self):
            raise LemonadeClientError("provider_error", "catalog unavailable", status_code=500)

        async def stats(self):
            return {}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "Qwen3-0.6B-GGUF")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
    )

    assert "chat_model_not_found" not in result.warnings
    assert _provider_capability(result.capabilities, "models")["status"] == "failed"
    assert _provider_capability(result.capabilities, "chat")["status"] == "unverified"
    assert _provider_capability(result.capabilities, "chat")["detail"] == "models_unavailable"


@pytest.mark.asyncio
async def test_external_lemonade_probe_rejects_semantically_invalid_health_payload(monkeypatch):
    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"data": ["not", "a", "health", "contract"]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
    )

    assert result.health == "unhealthy"
    assert result.warnings == ["health_invalid_response"]
    assert _provider_capability(result.capabilities, "health")["detail"] == "invalid_response"


@pytest.mark.asyncio
async def test_active_external_lemonade_probe_refreshes_loaded_models_after_inference(monkeypatch):
    health_calls = 0
    model_calls = 0

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            nonlocal health_calls
            health_calls += 1
            loaded = [] if health_calls == 1 else [
                {
                    "model_name": "Qwen3-0.6B-GGUF",
                    "type": "LLM",
                    "device": "gpu",
                    "backend_url": "http://127.0.0.1:8000",
                }
            ]
            return {"status": "ok", "version": "10.7.0", "all_models_loaded": loaded}

        async def models(self):
            nonlocal model_calls
            model_calls += 1
            models = [{"id": "Qwen3-0.6B-GGUF", "downloaded": True}]
            if model_calls > 1:
                models.append({"id": "Whisper-Tiny", "downloaded": True, "labels": ["transcription"]})
            return models

        async def stats(self):
            return {}

        async def chat_completion(self, *_args, **_kwargs):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "Qwen3-0.6B-GGUF")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
        active=True,
    )

    assert health_calls == 2
    assert model_calls == 2
    assert result.model_count == 2
    assert _provider_capability(result.capabilities, "models")["detail"] == "2"
    assert result.loaded_model == "Qwen3-0.6B-GGUF"
    assert result.loaded_models == [
        {
            "modelName": "Qwen3-0.6B-GGUF",
            "type": "llm",
            "device": "gpu",
            "backendUrl": "http://127.0.0.1:8000",
        }
    ]


@pytest.mark.asyncio
async def test_active_external_lemonade_probe_degrades_when_final_health_is_unhealthy(monkeypatch):
    health_calls = 0

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            nonlocal health_calls
            health_calls += 1
            if health_calls == 1:
                return {"status": "ok", "version": "10.7.0", "model_loaded": "chat-model"}
            return {"status": "starting", "version": "10.7.0"}

        async def models(self):
            return [{"id": "chat-model"}]

        async def stats(self):
            return {}

        async def chat_completion(self, *_args, **_kwargs):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "chat-model")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
        active=True,
    )

    assert result.health == "unhealthy"
    assert result.warnings == ["health_refresh_unhealthy"]
    assert _provider_capability(result.capabilities, "health_refresh") == {
        "name": "health_refresh",
        "status": "failed",
        "required": False,
        "detail": "starting",
    }
    assert provider_capability_summary(result.capabilities) == (True, "degraded")


@pytest.mark.asyncio
async def test_active_external_lemonade_probe_omits_stale_count_when_catalog_refresh_fails(monkeypatch):
    model_calls = 0

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "model_loaded": "chat-model"}

        async def models(self):
            nonlocal model_calls
            model_calls += 1
            if model_calls > 1:
                raise LemonadeClientError("provider_error", "catalog unavailable", status_code=500)
            return [{"id": "chat-model"}]

        async def stats(self):
            return {}

        async def chat_completion(self, *_args, **_kwargs):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "chat-model")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
        active=True,
    )

    assert result.model_count is None
    assert result.warnings == ["models_refresh_provider_error"]
    assert _provider_capability(result.capabilities, "models_refresh") == {
        "name": "models_refresh",
        "status": "failed",
        "required": False,
        "detail": "provider_error",
    }
    assert provider_capability_summary(result.capabilities) == (True, "degraded")


def test_amd_runtime_not_amd(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "nvidia")

    response = test_client.get("/api/providers/lemonade", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "reason": "not_amd",
        "runtime": "none",
        "location": "none",
        "runtimeMode": "none",
        "managedByDreamServer": False,
        "selectedBackend": "none",
        "supportedBackends": [],
        "defaultBackend": "none",
        "version": "unknown",
        "capabilities": [],
        "warnings": [],
    }


def test_amd_runtime_route_remains_compat_alias(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "nvidia")

    canonical = test_client.get("/api/providers/lemonade", headers=test_client.auth_headers)
    legacy = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert canonical.status_code == 200
    assert legacy.status_code == 200
    assert legacy.json() == canonical.json()


def test_external_lemonade_provider_contract_is_not_amd_only(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "nvidia")
    monkeypatch.setenv("DREAM_MODE", "lemonade")
    monkeypatch.setenv("LLM_BACKEND", "lemonade")
    monkeypatch.setenv("LEMONADE_EXTERNAL", "true")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "auto")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "13305")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "auto")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "external-lemonade")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "false")
    monkeypatch.setenv("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:13305")
    _patch_external_probe(monkeypatch, version="10.7.0")

    response = test_client.get("/api/providers/lemonade", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload.get("reason") is None
    assert payload["runtime"] == "lemonade"
    assert payload["location"] == "host"
    assert payload["runtimeMode"] == "external-lemonade"
    assert payload["managedByDreamServer"] is False
    assert payload["selectedBackend"] == "auto"
    assert payload["supportedBackends"] == ["auto"]
    assert payload["providerStatus"] == "ready"
    assert payload["providerProbeMode"] == "passive"
    assert payload["warnings"] == []


def test_amd_runtime_linux_container_lemonade(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "container")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "8080")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "linux-container")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "true")
    monkeypatch.setenv("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:13305")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["runtime"] == "lemonade"
    assert payload["location"] == "container"
    assert payload["runtimeMode"] == "linux-container"
    assert payload["managedByDreamServer"] is True
    assert payload["selectedBackend"] == "rocm"
    assert payload["supportedBackends"] == ["rocm"]
    assert payload["defaultBackend"] == "rocm"
    assert payload["apiBase"] == "http://llama-server:8080/api/v1"
    assert payload["healthUrl"] == "http://llama-server:8080/api/v1/health"
    assert payload["health"] == "reachable"
    assert payload["capabilities"] == ["rocm"]
    assert payload["warnings"] == []


def test_amd_runtime_windows_host_lemonade(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "vulkan")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "8080")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "vulkan")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "windows-legacy-lemonade")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "true")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_probe(monkeypatch, version="10.0.0")

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == "lemonade"
    assert payload["location"] == "host"
    assert payload["runtimeMode"] == "windows-legacy-lemonade"
    assert payload["managedByDreamServer"] is True
    assert payload["selectedBackend"] == "vulkan"
    assert payload["supportedBackends"] == ["vulkan"]
    assert payload["apiBase"] == "http://host.docker.internal:8080/api/v1"
    assert payload["healthUrl"] == "http://host.docker.internal:8080/api/v1/health"
    assert payload["version"] == "10.0.0"
    assert payload["capabilities"] == ["vulkan"]


def test_amd_runtime_external_lemonade_uses_container_base_url(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "auto")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "13305")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "auto")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "external-lemonade")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "false")
    monkeypatch.setenv("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:13305/api/v1")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_external_probe(
        monkeypatch,
        version="10.2.0",
        loaded_model="Qwen3-0.6B-GGUF",
        loaded_models=[{"modelName": "Qwen3-0.6B-GGUF", "type": "llm", "device": "gpu"}],
        model_count=2,
    )

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == "lemonade"
    assert payload["location"] == "host"
    assert payload["runtimeMode"] == "external-lemonade"
    assert payload["managedByDreamServer"] is False
    assert payload["selectedBackend"] == "auto"
    assert payload["supportedBackends"] == ["auto"]
    assert payload["apiBase"] == "http://host.docker.internal:13305/api/v1"
    assert payload["healthUrl"] == "http://host.docker.internal:13305/api/v1/health"
    assert payload["version"] == "10.2.0"
    assert payload["loadedModel"] == "Qwen3-0.6B-GGUF"
    assert payload["loadedModels"] == [{"modelName": "Qwen3-0.6B-GGUF", "type": "llm", "device": "gpu"}]
    assert payload["modelCount"] == 2
    assert payload["providerReady"] is True
    assert payload["providerStatus"] == "ready"
    assert payload["providerProbeMode"] == "passive"
    assert payload["providerCapabilities"] == [
        {"name": "health", "status": "ok", "required": True, "detail": "10.2.0"},
        {"name": "models", "status": "ok", "required": True, "detail": "2"},
        {"name": "stats", "status": "ok", "required": False},
        {"name": "chat", "status": "ok", "required": True, "detail": "Qwen3-0.6B-GGUF"},
    ]
    assert payload["capabilities"] == ["auto"]


def test_amd_runtime_external_lemonade_normalizes_api_suffix_case(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "auto")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "auto")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "external-lemonade")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "false")
    monkeypatch.setenv("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:13305/API/V1")
    monkeypatch.setenv("LEMONADE_API_BASE_PATH", "/api/v1")
    _patch_external_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["apiBase"] == "http://host.docker.internal:13305/api/v1"


def test_amd_runtime_prefers_lemonade_api_base_path(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "auto")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "13305")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "auto")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "external-lemonade")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "false")
    monkeypatch.setenv("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:13305")
    monkeypatch.setenv("LEMONADE_API_BASE_PATH", "/v1")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_external_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["apiBase"] == "http://host.docker.internal:13305/v1"
    assert response.json()["healthUrl"] == "http://host.docker.internal:13305/v1/health"


def test_amd_runtime_active_probe_is_explicit_post(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "auto")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "13305")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "auto")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "external-lemonade")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "false")
    monkeypatch.setenv("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:13305")
    calls = []

    async def _fake_probe(_api_base, _api_path, **kwargs):
        calls.append(kwargs)
        return (
            "reachable",
            "10.7.0",
            [],
            "Qwen3-0.6B-GGUF",
            1,
            [{"name": "chat", "status": "ok", "required": True, "detail": "Qwen3-0.6B-GGUF"}],
            "active" if kwargs.get("active") else "passive",
            [{"modelName": "Qwen3-0.6B-GGUF", "type": "llm"}],
        )

    monkeypatch.setattr(gpu_router, "_probe_external_lemonade", _fake_probe)

    passive = test_client.get("/api/providers/lemonade", headers=test_client.auth_headers)
    blocked = test_client.post("/api/providers/lemonade/probe", headers=test_client.auth_headers)
    active = test_client.post(
        "/api/providers/lemonade/probe",
        headers={**test_client.auth_headers, "X-Requested-With": "DreamServerDashboard"},
    )
    legacy_active = test_client.post(
        "/api/gpu/amd-runtime/probe",
        headers={**test_client.auth_headers, "X-Requested-With": "DreamServerDashboard"},
    )

    assert passive.status_code == 200
    assert blocked.status_code == 403
    assert active.status_code == 200
    assert legacy_active.status_code == 200
    assert passive.json()["providerProbeMode"] == "passive"
    assert active.json()["providerProbeMode"] == "active"
    assert legacy_active.json()["providerProbeMode"] == "active"
    assert calls == [
        {"active": False, "force": False},
        {"active": True, "force": True},
        {"active": True, "force": True},
    ]


def test_amd_runtime_external_lemonade_surfaces_adapter_warnings(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "auto")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "13305")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "auto")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "external-lemonade")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "false")
    monkeypatch.setenv("LEMONADE_CONTAINER_BASE_URL", "http://host.docker.internal:13305/api/v1")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_external_probe(
        monkeypatch,
        health="unhealthy",
        version="unknown",
        warnings=["health_auth_rejected"],
        loaded_model=None,
        model_count=None,
        provider_capabilities=[{"name": "health", "status": "failed", "required": True, "detail": "auth_rejected"}],
    )

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"] == "unhealthy"
    assert payload["providerReady"] is False
    assert payload["providerStatus"] == "blocked"
    assert payload["warnings"] == ["health_auth_rejected"]
    assert payload["providerCapabilities"] == [
        {"name": "health", "status": "failed", "required": True, "detail": "auth_rejected"}
    ]
    assert "loadedModel" not in payload
    assert "modelCount" not in payload


def test_amd_runtime_windows_host_llama_server_fallback(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "llama-server")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "vulkan")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "8080")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "vulkan")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "windows-llama-server-fallback")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "true")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/v1")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == "llama-server"
    assert payload["location"] == "host"
    assert payload["runtimeMode"] == "windows-llama-server-fallback"
    assert payload["managedByDreamServer"] is True
    assert payload["selectedBackend"] == "vulkan"
    assert payload["supportedBackends"] == ["vulkan"]
    assert payload["apiBase"] == "http://host.docker.internal:8080/v1"
    assert payload["healthUrl"] == "http://host.docker.internal:8080/health"
    assert payload["health"] == "reachable"
    assert payload["capabilities"] == ["vulkan"]


def test_amd_runtime_health_unreachable(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "container")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "8080")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "linux-container")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "true")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_probe(monkeypatch, health="unreachable", warning="health_unreachable")

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["health"] == "unreachable"
    assert payload["warnings"] == ["health_unreachable"]


def test_amd_runtime_uses_explicit_port(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "container")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "18080")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "linux-container")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "true")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["apiBase"] == "http://llama-server:18080/api/v1"
    assert payload["healthUrl"] == "http://llama-server:18080/api/v1/health"
    assert payload["warnings"] == []


def test_amd_runtime_invalid_port_warns_and_falls_back(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "container")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "not-a-port")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "linux-container")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "true")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["apiBase"] == "http://llama-server:8080/api/v1"
    assert "amd_port_invalid" in payload["warnings"]


def test_amd_runtime_warns_when_capabilities_missing(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "vulkan")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "8080")
    monkeypatch.delenv("AMD_INFERENCE_SUPPORTED_BACKENDS", raising=False)
    monkeypatch.delenv("AMD_INFERENCE_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("AMD_INFERENCE_MANAGED", raising=False)
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["runtimeMode"] == "unknown"
    assert payload["managedByDreamServer"] is False
    assert payload["selectedBackend"] == "vulkan"
    assert payload["supportedBackends"] == []
    assert payload["capabilities"] == []
    assert "amd_supported_backends_env_missing" in payload["warnings"]
    assert "amd_runtime_mode_env_missing" in payload["warnings"]
    assert "amd_managed_env_missing" in payload["warnings"]


def test_amd_runtime_warns_when_selected_backend_not_supported(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "amd")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
    monkeypatch.setenv("AMD_INFERENCE_BACKEND", "vulkan")
    monkeypatch.setenv("AMD_INFERENCE_LOCATION", "container")
    monkeypatch.setenv("AMD_INFERENCE_PORT", "8080")
    monkeypatch.setenv("AMD_INFERENCE_SUPPORTED_BACKENDS", "rocm")
    monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "linux-container")
    monkeypatch.setenv("AMD_INFERENCE_MANAGED", "true")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/api/v1")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["selectedBackend"] == "vulkan"
    assert payload["supportedBackends"] == ["rocm"]
    assert "amd_selected_backend_not_supported" in payload["warnings"]


@pytest.mark.asyncio
async def test_external_lemonade_probe_checks_models_stats_and_chat(monkeypatch):
    calls = []

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            calls.append("health")
            return {"status": "ok", "version": "10.7.0", "model_loaded": "Qwen3-0.6B-GGUF"}

        async def models(self):
            calls.append("models")
            return [{"id": "Qwen3-0.6B-GGUF"}]

        async def stats(self):
            calls.append("stats")
            return {"tokens_per_second": 42}

        async def chat_completion(self, model, messages, **kwargs):
            calls.append(("chat", model, messages, kwargs))
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "Qwen3-0.6B-GGUF")
    monkeypatch.setenv("LLM_BACKEND", "")

    health, version, warnings, loaded_model, model_count, provider_capabilities, probe_mode, loaded_models = (
        await gpu_router._probe_external_lemonade_uncached(
            "http://host.docker.internal:13305/api/v1",
            "/api/v1",
            active=True,
        )
    )

    assert health == "reachable"
    assert version == "10.7.0"
    assert warnings == []
    assert loaded_model == "Qwen3-0.6B-GGUF"
    assert model_count == 1
    assert probe_mode == "active"
    assert loaded_models == []
    assert _provider_capability(provider_capabilities, "health")["status"] == "ok"
    assert _provider_capability(provider_capabilities, "models") == {
        "name": "models",
        "status": "ok",
        "required": True,
        "detail": "1",
    }
    assert _provider_capability(provider_capabilities, "stats") == {
        "name": "stats",
        "status": "ok",
        "required": False,
    }
    assert _provider_capability(provider_capabilities, "chat") == {
        "name": "chat",
        "status": "ok",
        "required": True,
        "detail": "Qwen3-0.6B-GGUF",
    }
    assert _provider_capability(provider_capabilities, "embeddings") == {
        "name": "embeddings",
        "status": "skipped",
        "required": False,
        "detail": "not_selected",
    }
    assert _provider_capability(provider_capabilities, "rerank") == {
        "name": "rerank",
        "status": "skipped",
        "required": False,
        "detail": "not_selected",
    }
    assert _provider_capability(provider_capabilities, "stt") == {
        "name": "stt",
        "status": "skipped",
        "required": False,
        "detail": "not_selected",
    }
    assert _provider_capability(provider_capabilities, "tts") == {
        "name": "tts",
        "status": "skipped",
        "required": False,
        "detail": "not_selected",
    }
    assert calls[0:3] == ["health", "models", "stats"]
    assert calls[3][0:2] == ("chat", "Qwen3-0.6B-GGUF")
    assert calls[3][3]["max_tokens"] == 1
    assert calls[4] == "health"
    assert calls[5] == "models"


@pytest.mark.asyncio
async def test_external_lemonade_probe_classifies_chat_failure(monkeypatch):
    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0"}

        async def models(self):
            return [{"id": "Qwen3-0.6B-GGUF"}]

        async def stats(self):
            raise LemonadeClientError("not_found", "stats endpoint not implemented", status_code=404)

        async def chat_completion(self, *_args, **_kwargs):
            raise LemonadeClientError("not_found", "model missing", status_code=404)

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LLM_BACKEND", "")

    health, version, warnings, loaded_model, model_count, provider_capabilities, probe_mode, loaded_models = (
        await gpu_router._probe_external_lemonade_uncached(
            "http://host.docker.internal:13305/api/v1",
            "/api/v1",
            active=True,
        )
    )

    assert health == "reachable"
    assert version == "10.7.0"
    assert loaded_model is None
    assert model_count == 1
    assert probe_mode == "active"
    assert loaded_models == []
    assert warnings == ["chat_not_found"]
    assert _provider_capability(provider_capabilities, "stats") == {
        "name": "stats",
        "status": "unsupported",
        "required": False,
        "detail": "not_found",
    }
    assert _provider_capability(provider_capabilities, "chat") == {
        "name": "chat",
        "status": "failed",
        "required": True,
        "detail": "not_found",
        "recoveryHint": (
            "Set LEMONADE_MODEL to a model id returned by Lemonade /models, "
            "then retry the readiness probe."
        ),
    }


@pytest.mark.asyncio
async def test_external_lemonade_probe_rejects_empty_chat_content(monkeypatch):
    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "model_loaded": "Qwen3-0.6B-GGUF"}

        async def models(self):
            return [{"id": "Qwen3-0.6B-GGUF"}]

        async def stats(self):
            return {}

        async def chat_completion(self, *_args, **_kwargs):
            return {"choices": [{"message": {"content": ""}}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "Qwen3-0.6B-GGUF")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
        active=True,
    )

    assert "chat_invalid_response" in result.warnings
    assert _provider_capability(result.capabilities, "chat") == {
        "name": "chat",
        "status": "failed",
        "required": True,
        "detail": "invalid_response",
        "recoveryHint": (
            "Set LEMONADE_MODEL to a model id returned by Lemonade /models, "
            "then retry the readiness probe."
        ),
    }
    assert provider_capability_summary(result.capabilities) == (False, "blocked")


@pytest.mark.asyncio
async def test_external_lemonade_probe_marks_rag_embeddings_as_dream_owned(monkeypatch):
    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "model_loaded": "Qwen3-0.6B-GGUF"}

        async def models(self):
            return [{"id": "Qwen3-0.6B-GGUF"}]

        async def stats(self):
            return {}

        async def chat_completion(self, *_args, **_kwargs):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("ENABLE_RAG", "true")
    monkeypatch.setenv("EMBEDDING_URL", "http://embeddings:80")
    monkeypatch.setenv("LLM_BACKEND", "")

    health, _version, warnings, _loaded_model, _model_count, provider_capabilities, probe_mode, _loaded_models = (
        await gpu_router._probe_external_lemonade_uncached(
            "http://host.docker.internal:13305/api/v1",
            "/api/v1",
        )
    )

    assert health == "reachable"
    assert probe_mode == "passive"
    assert warnings == []
    assert _provider_capability(provider_capabilities, "embeddings") == {
        "name": "embeddings",
        "status": "skipped",
        "required": False,
        "detail": "handled_by_embeddings_service",
    }


@pytest.mark.asyncio
async def test_external_lemonade_probe_treats_provider_endpoint_urls_as_lemonade_owned(monkeypatch):
    calls = []

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "model_loaded": "chat-model"}

        async def models(self):
            return [{"id": "chat-model"}, {"id": "embed-model", "labels": ["embeddings"]}]

        async def stats(self):
            return {}

        async def chat_completion(self, *_args, **_kwargs):
            return {"choices": [{"message": {"content": "ok"}}]}

        async def embeddings(self, model, text):
            calls.append(("embeddings", model, text))
            return {"data": [{"embedding": [0.1]}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LEMONADE_MODEL", "chat-model")
    monkeypatch.setenv("ENABLE_RAG", "true")
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("EMBEDDING_URL", "http://host.docker.internal:13305/api/v1/embeddings")
    monkeypatch.setenv("LLM_BACKEND", "")

    result = await gpu_router._probe_external_lemonade_uncached(
        "http://host.docker.internal:13305/api/v1",
        "/api/v1",
        active=True,
    )

    assert result.warnings == []
    assert _provider_capability(result.capabilities, "embeddings") == {
        "name": "embeddings",
        "status": "ok",
        "required": True,
        "detail": "embed-model",
    }
    assert calls == [("embeddings", "embed-model", "ping")]


@pytest.mark.asyncio
async def test_external_lemonade_probe_checks_litellm_gateway_chat(monkeypatch):
    chat_roots = []

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "model_loaded": "Qwen3-0.6B-GGUF"}

        async def models(self):
            return [{"id": "Qwen3-0.6B-GGUF"}]

        async def stats(self):
            return {}

        async def chat_completion(self, model, *_args, **_kwargs):
            chat_roots.append((self.settings.api_root, self.settings.api_key, model))
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LLM_BACKEND", "lemonade")
    monkeypatch.setenv("LLM_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_KEY", "sk-dream")
    monkeypatch.setenv("LEMONADE_API_KEY", "")
    monkeypatch.setenv("LITELLM_LEMONADE_API_KEY", "")

    _health, _version, warnings, _loaded_model, _model_count, provider_capabilities, probe_mode, _loaded_models = (
        await gpu_router._probe_external_lemonade_uncached(
            "http://host.docker.internal:13305/api/v1",
            "/api/v1",
            active=True,
        )
    )

    assert warnings == []
    assert probe_mode == "active"
    assert _provider_capability(provider_capabilities, "gateway_chat") == {
        "name": "gateway_chat",
        "status": "ok",
        "required": True,
        "detail": "default",
    }
    assert chat_roots == [
        ("http://litellm:4000/v1", "sk-dream", "default"),
        ("http://host.docker.internal:13305/api/v1", "", "Qwen3-0.6B-GGUF"),
    ]


@pytest.mark.asyncio
async def test_external_lemonade_probe_checks_selected_provider_capabilities(monkeypatch):
    calls = []

    class FakeLemonadeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def health(self):
            return {"status": "ok", "version": "10.7.0", "model_loaded": "chat-model"}

        async def models(self):
            return [{"id": "chat-model"}]

        async def stats(self):
            return {}

        async def chat_completion(self, model, *_args, **_kwargs):
            calls.append(("chat", model))
            return {"choices": [{"message": {"content": "ok"}}]}

        async def embeddings(self, model, text):
            calls.append(("embeddings", model, text))
            return {"data": [{"embedding": [0.1]}]}

        async def rerank(self, model, query, documents):
            calls.append(("rerank", model, query, documents))
            return {"results": [{"index": 0, "relevance_score": 1.0}]}

        async def transcribe_wav(self, model, wav_bytes, **kwargs):
            calls.append(("stt", model, len(wav_bytes), kwargs))
            return {"text": ""}

        async def speech(self, model, text, **kwargs):
            calls.append(("tts", model, text, kwargs))
            return b"RIFF"

    monkeypatch.setattr(gpu_router, "LemonadeClient", FakeLemonadeClient)
    monkeypatch.setenv("LLM_BACKEND", "")
    monkeypatch.setenv("LEMONADE_EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("LEMONADE_RERANK_MODEL", "rerank-model")
    monkeypatch.setenv("LEMONADE_STT_MODEL", "stt-model")
    monkeypatch.setenv("LEMONADE_TTS_MODEL", "tts-model")
    monkeypatch.setenv("AUDIO_TTS_VOICE", "af_heart")

    _health, _version, warnings, _loaded_model, _model_count, provider_capabilities, probe_mode, _loaded_models = (
        await gpu_router._probe_external_lemonade_uncached(
            "http://host.docker.internal:13305/api/v1",
            "/api/v1",
            active=True,
        )
    )

    assert warnings == []
    assert probe_mode == "active"
    assert _provider_capability(provider_capabilities, "embeddings") == {
        "name": "embeddings",
        "status": "ok",
        "required": True,
        "detail": "embed-model",
    }
    assert _provider_capability(provider_capabilities, "rerank") == {
        "name": "rerank",
        "status": "ok",
        "required": True,
        "detail": "rerank-model",
    }
    assert _provider_capability(provider_capabilities, "stt") == {
        "name": "stt",
        "status": "ok",
        "required": True,
        "detail": "stt-model",
    }
    assert _provider_capability(provider_capabilities, "tts") == {
        "name": "tts",
        "status": "ok",
        "required": True,
        "detail": "tts-model",
    }
    assert ("embeddings", "embed-model", "ping") in calls
    assert any(call[0:2] == ("rerank", "rerank-model") for call in calls)
    assert any(call[0:2] == ("stt", "stt-model") and call[2] > 44 for call in calls)
    assert ("tts", "tts-model", "ping", {"voice": "af_heart"}) in calls
    assert [call[0] for call in calls] == ["embeddings", "rerank", "stt", "tts", "chat"]
