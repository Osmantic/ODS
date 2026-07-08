"""Tests for the AMD runtime diagnostic endpoint."""

from routers import gpu as gpu_router


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
    model_count=1,
):
    async def _fake_probe(_api_base, _api_path):
        return health, version, list(warnings or []), loaded_model, model_count

    monkeypatch.setattr(gpu_router, "_probe_external_lemonade", _fake_probe)


def test_amd_runtime_not_amd(monkeypatch, test_client):
    monkeypatch.setenv("GPU_BACKEND", "nvidia")

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "reason": "not_amd",
        "runtime": "none",
        "location": "none",
        "runtimeMode": "none",
        "managedByODS": False,
        "selectedBackend": "none",
        "supportedBackends": [],
        "defaultBackend": "none",
        "version": "unknown",
        "capabilities": [],
        "warnings": [],
    }


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
    assert payload["managedByODS"] is True
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
    assert payload["managedByODS"] is True
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
    _patch_external_probe(monkeypatch, version="10.2.0", loaded_model="Qwen3-0.6B-GGUF", model_count=2)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == "lemonade"
    assert payload["location"] == "host"
    assert payload["runtimeMode"] == "external-lemonade"
    assert payload["managedByODS"] is False
    assert payload["selectedBackend"] == "auto"
    assert payload["supportedBackends"] == ["auto"]
    assert payload["apiBase"] == "http://host.docker.internal:13305/api/v1"
    assert payload["healthUrl"] == "http://host.docker.internal:13305/api/v1/health"
    assert payload["version"] == "10.2.0"
    assert payload["loadedModel"] == "Qwen3-0.6B-GGUF"
    assert payload["modelCount"] == 2
    assert payload["capabilities"] == ["auto"]


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
    )

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"] == "unhealthy"
    assert payload["warnings"] == ["health_auth_rejected"]
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
    assert payload["managedByODS"] is True
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
    assert payload["managedByODS"] is False
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
