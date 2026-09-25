from datetime import datetime, timezone
from unittest.mock import mock_open
import pytest
import host_metrics
import helpers
import gpu
from routers import gpu as router


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(host_metrics, "_windows_cached", (0, None))
    monkeypatch.setattr(host_metrics.platform, "release", lambda: "6.6-microsoft-standard-WSL2")
    monkeypatch.setattr(helpers.platform, "system", lambda: "Linux")
    monkeypatch.setenv("GPU_BACKEND", "cpu")


def payload():
    return {"schema_version": "ods.host-system-metrics.v1", "platform": "Windows",
        "sampledAt": datetime.now(timezone.utc).isoformat(),
        "cpu": {"percent": 86, "scope": "host"},
        "ram": {"used_gb": 34.1, "total_gb": 95.8, "percent": 35.7, "scope": "host"},
        "gpus": [{"name": "AMD Radeon(TM) 8060S Graphics", "uuid": "luid_0x00000000_0x0001696c",
            "memory_total_mb": 32768, "memory_used_mb": 25566, "memory_type": "unified",
            "memory_scope": "dedicated", "utilization_percent": 24, "backend": "amd"}]}


def test_cpu_ram_gpu_share_native_sample_even_with_external_cpu_backend(monkeypatch):
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 9
        return payload()
    monkeypatch.setattr(host_metrics, "request_json", request)
    assert helpers.get_cpu_metrics()["percent"] == 86
    assert helpers.get_cpu_metrics()["source"] == "windows-cim"
    assert helpers.get_ram_metrics()["total_gb"] == 95.8
    info = gpu.get_gpu_info_wsl_host_detailed()[0]
    assert info.memory_total_mb == 32768 and info.utilization_percent == 24
    assert info.memory_usage_available and not info.temperature_available
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_detailed_route_reports_real_adapter_backend(monkeypatch):
    monkeypatch.setattr(host_metrics, "request_json", lambda *a, **kw: payload())
    monkeypatch.setattr(router, "get_gpu_info_nvidia_detailed", lambda: None)
    monkeypatch.setattr(router, "get_gpu_info_amd_detailed", lambda: None)
    monkeypatch.setattr(router, "decode_gpu_assignment", lambda: None)
    monkeypatch.setattr(router, "_live_env_value", lambda key: "")
    result = await router._read_detailed_gpu_status()
    assert result.backend == "amd" and result.gpu_count == 1
    assert result.aggregate.utilization_percent == 24
    assert result.gpus[0].uuid == "luid_0x00000000_0x0001696c"


def test_failed_native_bridge_keeps_honest_wsl_scope(monkeypatch):
    from host_agent_client import AgentTimeout
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        raise AgentTimeout("offline")
    monkeypatch.setattr(host_metrics, "request_json", request)
    monkeypatch.setattr("builtins.open", mock_open(read_data="MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\n"))
    result = helpers.get_ram_metrics()
    assert result["scope"] == "wsl" and result["total_gb"] == 16
    assert gpu.get_gpu_info_wsl_host_detailed() is None
    assert len(calls) == 1


@pytest.mark.parametrize("timestamp", [None, "", "bad", "2020-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"])
def test_stale_or_missing_source_time_not_reported_as_current(monkeypatch, timestamp):
    data = payload()
    data["sampledAt"] = timestamp
    monkeypatch.setattr(host_metrics, "request_json", lambda *a, **kw: data)
    assert host_metrics.windows_host_metrics() == {"cpu": None, "ram": None, "gpus": []}


def test_missing_counters_remain_unavailable_and_zero_is_valid(monkeypatch):
    data = payload()
    data["cpu"]["percent"] = 0
    data["gpus"][0].update(memory_used_mb=None, utilization_percent=None)
    monkeypatch.setattr(host_metrics, "request_json", lambda *a, **kw: data)
    assert helpers.get_cpu_metrics()["percent"] == 0
    info = gpu.get_gpu_info_wsl_host_detailed()[0]
    assert not info.memory_usage_available and not info.utilization_available
    assert not info.temperature_available
