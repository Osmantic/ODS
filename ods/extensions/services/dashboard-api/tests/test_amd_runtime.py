"""Tests for the AMD runtime diagnostic endpoint (upstream llama-server)."""

import pytest

from routers import gpu as gpu_router


def _patch_probe(monkeypatch, health="reachable", version="unknown", warning=None, seen=None):
    def probe(url):
        if seen is not None:
            seen.append(url)
        return health, version, warning

    monkeypatch.setattr(gpu_router, "_probe_amd_health", probe)


def _amd_env(monkeypatch, **values):
    defaults = {
        "GPU_BACKEND": "amd",
        "AMD_INFERENCE_RUNTIME": "llama-server",
        "AMD_INFERENCE_BACKEND": "vulkan",
        "AMD_INFERENCE_LOCATION": "container",
        "AMD_INFERENCE_PORT": "8080",
        "AMD_INFERENCE_SUPPORTED_BACKENDS": "vulkan,rocm",
        "AMD_INFERENCE_RUNTIME_MODE": "linux-container",
        "AMD_INFERENCE_MANAGED": "true",
    }
    for key in ("NATIVE_LLM_CONTAINER_BASE_URL", "LEMONADE_CONTAINER_BASE_URL", "LLM_API_BASE_PATH"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**defaults, **values}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


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


def test_amd_runtime_linux_container_llama_server(monkeypatch, test_client):
    _amd_env(monkeypatch)
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["runtime"] == "llama-server"
    assert payload["location"] == "container"
    assert payload["runtimeMode"] == "linux-container"
    assert payload["managedByODS"] is True
    assert payload["selectedBackend"] == "vulkan"
    assert payload["supportedBackends"] == ["vulkan", "rocm"]
    assert payload["apiBase"] == "http://llama-server:8080/v1"
    assert payload["healthUrl"] == "http://llama-server:8080/health"
    assert payload["health"] == "reachable"
    assert payload["warnings"] == []


def test_unmigrated_lemonade_env_reads_as_llama_server(monkeypatch, test_client):
    # A pre-round-F .env: the retired runtime name and Lemonade's API path.
    _amd_env(monkeypatch, AMD_INFERENCE_RUNTIME="lemonade", AMD_INFERENCE_BACKEND="rocm",
             AMD_INFERENCE_SUPPORTED_BACKENDS="rocm", LLM_API_BASE_PATH="/api/v1")
    seen: list = []
    _patch_probe(monkeypatch, seen=seen)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    payload = response.json()
    assert payload["runtime"] == "llama-server"
    assert payload["apiBase"] == "http://llama-server:8080/v1"
    assert payload["healthUrl"] == "http://llama-server:8080/health"
    assert seen == ["http://llama-server:8080/health"]
    assert not hasattr(gpu_router, "_probe_external_lemonade")


@pytest.mark.parametrize(("values", "origin"), [
    # Legacy native Windows: the agent's own loopback server.
    ({"AMD_INFERENCE_RUNTIME_MODE": "windows-native-llama-server", "AMD_INFERENCE_PORT": "18080"},
     "http://host.docker.internal:18080"),
    # The WSL Portal: the server as containers reach it.
    ({"AMD_INFERENCE_RUNTIME_MODE": "windows-portal-llama-server", "AMD_INFERENCE_PORT": "13305",
      "NATIVE_LLM_CONTAINER_BASE_URL": "http://192.168.50.1:13305/v1"},
     "http://192.168.50.1:13305"),
    # Its one-release legacy key name.
    ({"AMD_INFERENCE_RUNTIME_MODE": "windows-portal-llama-server", "AMD_INFERENCE_PORT": "13305",
      "LEMONADE_CONTAINER_BASE_URL": "http://192.168.50.1:13306/api/v1"},
     "http://192.168.50.1:13306"),
])
def test_amd_runtime_windows_host_llama_server(monkeypatch, test_client, values, origin):
    _amd_env(monkeypatch, AMD_INFERENCE_LOCATION="host", AMD_INFERENCE_SUPPORTED_BACKENDS="vulkan", **values)
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"] == "llama-server"
    assert payload["location"] == "host"
    assert payload["runtimeMode"] == values["AMD_INFERENCE_RUNTIME_MODE"]
    assert payload["apiBase"] == origin + "/v1"
    assert payload["healthUrl"] == origin + "/health"
    assert payload["health"] == "reachable"


def test_amd_runtime_health_unreachable(monkeypatch, test_client):
    _amd_env(monkeypatch)
    _patch_probe(monkeypatch, health="unreachable", warning="health_unreachable")

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["health"] == "unreachable"
    assert payload["warnings"] == ["health_unreachable"]


def test_amd_runtime_uses_explicit_port(monkeypatch, test_client):
    _amd_env(monkeypatch, AMD_INFERENCE_PORT="18080")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    payload = response.json()
    assert payload["apiBase"] == "http://llama-server:18080/v1"
    assert payload["healthUrl"] == "http://llama-server:18080/health"
    assert payload["warnings"] == []


def test_amd_runtime_invalid_port_warns_and_falls_back(monkeypatch, test_client):
    _amd_env(monkeypatch, AMD_INFERENCE_PORT="not-a-port")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    payload = response.json()
    assert payload["apiBase"] == "http://llama-server:8080/v1"
    assert "amd_port_invalid" in payload["warnings"]


def test_amd_runtime_warns_when_capabilities_missing(monkeypatch, test_client):
    _amd_env(monkeypatch, AMD_INFERENCE_LOCATION="host", AMD_INFERENCE_SUPPORTED_BACKENDS=None,
             AMD_INFERENCE_RUNTIME_MODE=None, AMD_INFERENCE_MANAGED=None)
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    payload = response.json()
    assert payload["available"] is True
    assert payload["runtimeMode"] == "unknown"
    assert payload["managedByODS"] is False
    assert payload["selectedBackend"] == "vulkan"
    assert payload["supportedBackends"] == []
    assert "amd_supported_backends_env_missing" in payload["warnings"]
    assert "amd_runtime_mode_env_missing" in payload["warnings"]
    assert "amd_managed_env_missing" in payload["warnings"]


def test_amd_runtime_warns_when_selected_backend_not_supported(monkeypatch, test_client):
    _amd_env(monkeypatch, AMD_INFERENCE_SUPPORTED_BACKENDS="rocm")
    _patch_probe(monkeypatch)

    response = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers)

    payload = response.json()
    assert payload["selectedBackend"] == "vulkan"
    assert payload["supportedBackends"] == ["rocm"]
    assert "amd_selected_backend_not_supported" in payload["warnings"]


def test_amd_runtime_backend_is_never_read_from_retired_lemonade_keys(monkeypatch, test_client):
    _amd_env(monkeypatch, AMD_INFERENCE_BACKEND=None)
    monkeypatch.setenv("LEMONADE_LLAMACPP_BACKEND", "rocm")
    _patch_probe(monkeypatch)

    payload = test_client.get("/api/gpu/amd-runtime", headers=test_client.auth_headers).json()

    assert payload["selectedBackend"] == "unknown"
    assert "amd_backend_env_missing" in payload["warnings"]
