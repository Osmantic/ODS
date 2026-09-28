"""Regression tests for cloud/remote inference mode in /api/status.

These tests exercise _build_api_status and the /api/status fallback path
without any network calls, without touching the host filesystem, and without
relying on the global llama metrics cache.
"""

from unittest.mock import AsyncMock

import pytest

import main
from main import _build_api_status
from models import BootstrapStatus, DiskUsage, GPUInfo, ModelInfo


@pytest.fixture()
def stable_env(monkeypatch):
    """Pin env reads so normalize/read_live_env_value return exact values."""
    monkeypatch.setattr(main, "read_live_env_value", lambda key, default=None: {
        "ODS_MODE": "local",
    }.get(key, default))
    monkeypatch.setattr(main, "normalize_ods_mode", lambda value: (value or "local").strip().lower())
    return monkeypatch


@pytest.fixture()
def disk_usage():
    return DiskUsage(path="/", used_gb=100.0, total_gb=500.0, percent=20.0)


def _patch_common(monkeypatch, *, gpu=None, loaded_model=None, model_info=None,
                  llama_metrics=None, llama_context=None, remote_runtime=None,
                  cloud_mode=False, disk_usage=None):
    """Patch all _build_api_status dependencies deterministically."""
    monkeypatch.setattr(main, "get_gpu_info", lambda: gpu)
    monkeypatch.setattr(main, "get_all_services", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "get_model_info", lambda: model_info)
    monkeypatch.setattr(main, "get_bootstrap_status", lambda: BootstrapStatus(active=False))
    monkeypatch.setattr(main, "get_loaded_model", AsyncMock(return_value=loaded_model))
    monkeypatch.setattr(main, "get_llama_metrics", AsyncMock(return_value=llama_metrics or {}))
    monkeypatch.setattr(main, "get_llama_context_size", AsyncMock(return_value=llama_context))
    monkeypatch.setattr(main, "get_uptime", lambda: 0)
    monkeypatch.setattr(main, "get_cpu_metrics", lambda: {"percent": 10.0, "temp_c": 40})
    monkeypatch.setattr(main, "get_ram_metrics", lambda: {"used_gb": 4.0, "total_gb": 16.0, "percent": 25.0})
    monkeypatch.setattr(main, "get_disk_usage", lambda: disk_usage or DiskUsage(path="/", used_gb=0.0, total_gb=0.0, percent=0.0))
    monkeypatch.setattr(main, "_get_services", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "_get_dashboard_remote_runtime", AsyncMock(return_value=remote_runtime))
    monkeypatch.setattr(main, "read_live_env_value", lambda key, default=None: "cloud" if key == "ODS_MODE" and cloud_mode else "local" if key == "ODS_MODE" else default)


@pytest.mark.asyncio
async def test_remote_runtime_suppresses_local_gpu_and_llama(monkeypatch, stable_env, disk_usage):
    gpu = GPUInfo(
        name="Intel Arc A770", memory_used_mb=4096, memory_total_mb=16384,
        memory_percent=25.0, utilization_percent=30, temperature_c=60,
        gpu_backend="intel", gpu_count=2,
    )
    llama_metrics = AsyncMock(return_value={"tokens_per_second": 99.0, "lifetime_tokens": 12345})
    llama_context = AsyncMock(return_value=4096)
    _patch_common(
        monkeypatch, gpu=gpu, loaded_model="local-model",
        model_info=ModelInfo(name="local-model", size_gb=8.0, context_length=8192),
        llama_metrics={"tokens_per_second": 99.0, "lifetime_tokens": 12345},
        llama_context=4096,
        remote_runtime={"source": "remote-provider", "model": "remote-model", "contextLength": 131072},
        disk_usage=disk_usage,
    )
    monkeypatch.setattr(main, "get_llama_metrics", llama_metrics)
    monkeypatch.setattr(main, "get_llama_context_size", llama_context)

    result = await _build_api_status()

    assert result["gpu"] is None
    assert result["tier"] == "Cloud"
    assert result["inferenceMode"] == "remote"
    assert result["inferenceSource"] == "remote-provider"
    assert result["currentModel"] == "remote-model"
    assert result["loadedModel"] is None
    assert result["configuredModel"] == "local-model"
    llama_metrics.assert_not_called()
    llama_context.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_mode_suppresses_local_gpu_and_llama(monkeypatch, stable_env, disk_usage):
    gpu = GPUInfo(
        name="Intel Arc A770", memory_used_mb=4096, memory_total_mb=16384,
        memory_percent=25.0, utilization_percent=30, temperature_c=60,
        gpu_backend="intel", gpu_count=2,
    )
    llama_metrics = AsyncMock(return_value={"tokens_per_second": 99.0, "lifetime_tokens": 12345})
    llama_context = AsyncMock(return_value=4096)
    _patch_common(
        monkeypatch, gpu=gpu, loaded_model="local-model",
        model_info=ModelInfo(name="local-model", size_gb=8.0, context_length=8192),
        llama_metrics={"tokens_per_second": 99.0, "lifetime_tokens": 12345},
        llama_context=4096,
        remote_runtime=None,
        cloud_mode=True,
        disk_usage=disk_usage,
    )
    monkeypatch.setattr(main, "get_llama_metrics", llama_metrics)
    monkeypatch.setattr(main, "get_llama_context_size", llama_context)

    result = await _build_api_status()

    assert result["gpu"] is None
    assert result["tier"] == "Cloud"
    assert result["inferenceMode"] == "cloud"
    assert result["inferenceSource"] == "cloud-mode"
    assert result["loadedModel"] is None
    llama_metrics.assert_not_called()
    llama_context.assert_not_called()


@pytest.mark.asyncio
async def test_local_mode_preserves_gpu_and_llama(monkeypatch, stable_env, disk_usage):
    gpu = GPUInfo(
        name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
        memory_percent=8.3, utilization_percent=35, temperature_c=62,
        gpu_backend="nvidia",
    )
    _patch_common(
        monkeypatch, gpu=gpu, loaded_model="local-model",
        model_info=ModelInfo(name="local-model", size_gb=8.0, context_length=8192),
        llama_metrics={"tokens_per_second": 25.0, "lifetime_tokens": 1000},
        llama_context=8192,
        remote_runtime=None,
        cloud_mode=False,
        disk_usage=disk_usage,
    )

    result = await _build_api_status()

    assert result["gpu"] is not None
    assert result["gpu"]["name"] == "RTX 4090"
    assert result["tier"] == "Prosumer"
    assert result["inferenceMode"] == "local"
    assert result["inferenceSource"] == "local-runtime"
    assert result["currentModel"] == "local-model"
    assert result["loadedModel"] == "local-model"


@pytest.mark.asyncio
async def test_remote_source_outranks_local_configured_env(monkeypatch, stable_env, disk_usage):
    """Even if ODS_MODE=local, a proven remote_runtime must win."""
    gpu = GPUInfo(
        name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
        memory_percent=8.3, utilization_percent=35, temperature_c=62,
        gpu_backend="nvidia",
    )
    _patch_common(
        monkeypatch, gpu=gpu, loaded_model="local-model",
        model_info=ModelInfo(name="local-model", size_gb=8.0, context_length=8192),
        llama_metrics={"tokens_per_second": 25.0, "lifetime_tokens": 1000},
        llama_context=8192,
        remote_runtime={"source": "remote-provider", "model": "remote-model", "contextLength": 131072},
        cloud_mode=False,
        disk_usage=disk_usage,
    )

    result = await _build_api_status()

    assert result["inferenceMode"] == "remote"
    assert result["inferenceSource"] == "remote-provider"
    assert result["gpu"] is None
    assert result["tier"] == "Cloud"


@pytest.mark.asyncio
async def test_configured_only_cloud_model_remains_unavailable(monkeypatch, stable_env, disk_usage):
    """A configured-only model must never be reported as available/loaded."""
    _patch_common(
        monkeypatch, gpu=None, loaded_model=None,
        model_info=ModelInfo(name="configured-only", size_gb=8.0, context_length=8192),
        llama_metrics={},
        llama_context=None,
        remote_runtime=None,
        cloud_mode=True,
        disk_usage=disk_usage,
    )

    result = await _build_api_status()

    assert result["inferenceMode"] == "cloud"
    assert result["inferenceSource"] == "cloud-mode"
    assert result["currentModel"] is None
    assert result["loadedModel"] is None
    assert result["configuredModel"] == "configured-only"


@pytest.mark.asyncio
async def test_fallback_cloud_mode_suppresses_cached_local_metrics(monkeypatch, stable_env):
    """When /api/status handler fails in cloud mode, cached local llama metrics
    must not leak into the fallback payload."""
    monkeypatch.setattr(main, "read_live_env_value", lambda key, default=None: {
        "ODS_MODE": "cloud",
    }.get(key, default))
    monkeypatch.setattr(main, "normalize_ods_mode", lambda value: (value or "local").strip().lower())
    monkeypatch.setattr(main, "get_cached_llama_metrics", lambda: {"tokens_per_second": 42.0, "lifetime_tokens": 999})

    async def boom(*args, **kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr(main, "_build_api_status", boom)

    from fastapi.testclient import TestClient
    client = TestClient(main.app, raise_server_exceptions=True)
    client.auth_headers = {"Authorization": "Bearer test-key-12345"}

    response = client.get("/api/status", headers=client.auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "Cloud"
    assert body["inferenceMode"] == "cloud"
    assert body["inferenceSource"] == "cloud-mode"
    assert body["inference"]["tokensPerSecond"] is None
    assert body["inference"]["lifetimeTokens"] is None


@pytest.mark.asyncio
async def test_fallback_local_mode_preserves_cached_metrics(monkeypatch, stable_env):
    """Ordinary local fallback must still surface cached llama metrics."""
    monkeypatch.setattr(main, "read_live_env_value", lambda key, default=None: {
        "ODS_MODE": "local",
    }.get(key, default))
    monkeypatch.setattr(main, "normalize_ods_mode", lambda value: (value or "local").strip().lower())
    monkeypatch.setattr(main, "get_cached_llama_metrics", lambda: {"tokens_per_second": 42.0, "lifetime_tokens": 999})

    async def boom(*args, **kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr(main, "_build_api_status", boom)

    from fastapi.testclient import TestClient
    client = TestClient(main.app, raise_server_exceptions=True)
    client.auth_headers = {"Authorization": "Bearer test-key-12345"}

    response = client.get("/api/status", headers=client.auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "Unknown"
    assert body["inferenceMode"] == "local"
    assert body["inferenceSource"] == "unknown"
    assert body["inference"]["tokensPerSecond"] == 42.0
    assert body["inference"]["lifetimeTokens"] == 999
