"""Physical host telemetry must not mix Mac capacity with VM usage."""
from unittest.mock import mock_open
import pytest
import host_metrics
import helpers
import gpu

@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(host_metrics, "_cached", (0, None))


def payload():
    return {"schema_version": "ods.host-system-metrics.v1", "platform": "Darwin",
            "cpu": {"percent": 0, "scope": "host"},
            "ram": {"used_gb": 13.1, "total_gb": 16, "percent": 81.8, "scope": "host"},
            "gpu": {"name": "Apple M4", "memory_total_mb": 16384,
                    "memory_used_mb": 8428, "utilization_percent": 99}}


def test_native_bridge_shared_and_zero_is_available(monkeypatch):
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        return payload()
    monkeypatch.setattr(host_metrics, "request_json", request)
    monkeypatch.setenv("GPU_BACKEND", "apple")
    monkeypatch.setattr(helpers.platform, "system", lambda: "Linux")
    monkeypatch.setattr("builtins.open", lambda *a, **kw: pytest.fail("must not read VM RAM"))
    assert helpers.get_cpu_metrics()["percent"] == 0
    assert helpers.get_ram_metrics()["used_gb"] == 13.1
    info = gpu.get_gpu_info_apple()
    assert info.utilization_percent == 99 and info.utilization_available
    assert info.memory_used_mb == 8428 and info.memory_usage_available
    assert not info.temperature_available
    assert len(calls) == 1


@pytest.mark.parametrize("bad", [None, {}, {"schema_version": "wrong"},
    {"schema_version": "ods.host-system-metrics.v1", "platform": "Windows"}])
def test_missing_or_old_agent_never_falls_back_to_vm(monkeypatch, bad):
    monkeypatch.setattr(host_metrics, "request_json", lambda *a, **kw: bad)
    monkeypatch.setenv("GPU_BACKEND", "apple")
    monkeypatch.setenv("HOST_RAM_GB", "16")
    monkeypatch.setattr(helpers.platform, "system", lambda: "Linux")
    assert helpers.get_cpu_metrics()["percent"] is None
    assert helpers.get_ram_metrics()["used_gb"] is None
    assert gpu.get_gpu_info_apple() is None


@pytest.mark.parametrize("bad", [-1, 101, float("nan"), float("inf"), True, "1"])
def test_invalid_values_are_unavailable(monkeypatch, bad):
    data = payload()
    data["cpu"]["percent"] = bad
    data["gpu"]["utilization_percent"] = bad
    data["ram"]["used_gb"] = 200
    monkeypatch.setattr(host_metrics, "request_json", lambda *a, **kw: data)
    result = host_metrics.apple_host_metrics()
    assert result["cpu"]["percent"] is None
    assert result["ram"]["used_gb"] is None
    assert result["gpu"]["utilization_percent"] is None


@pytest.mark.parametrize("release,scope", [("6.6.87.2-microsoft-standard-WSL2", "wsl"),
    ("6.12.5-linuxkit", "vm"), ("6.8.0-ubuntu", "host")])
def test_linux_scope_and_unmixed_capacity(monkeypatch, release, scope):
    monkeypatch.setattr(host_metrics.platform, "release", lambda: release)
    monkeypatch.setattr(helpers.platform, "system", lambda: "Linux")
    monkeypatch.setenv("GPU_BACKEND", "nvidia")
    monkeypatch.setenv("HOST_RAM_GB", "128")
    monkeypatch.setattr("builtins.open", mock_open(read_data="MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\n"))
    data = helpers.get_ram_metrics()
    assert data["scope"] == scope
    assert data["total_gb"] == 16 and data["used_gb"] == 8


def test_timeout_is_shared_unavailable_not_status_exception(monkeypatch):
    from host_agent_client import AgentTimeout
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 5
        raise AgentTimeout("offline")
    monkeypatch.setattr(host_metrics, "request_json", request)
    monkeypatch.setenv("GPU_BACKEND", "apple")
    monkeypatch.setattr(helpers.platform, "system", lambda: "Linux")
    assert helpers.get_cpu_metrics()["percent"] is None
    assert helpers.get_ram_metrics()["used_gb"] is None
    assert gpu.get_gpu_info_apple() is None
    assert len(calls) == 1


def test_first_cpu_sample_missing_data_and_measured_idle(monkeypatch):
    monkeypatch.delattr(helpers.get_cpu_metrics, "_prev", raising=False)
    monkeypatch.setattr("glob.glob", lambda pattern: [])
    monkeypatch.setattr("builtins.open", mock_open(read_data="cpu  100 0 100 800 0 0 0 0\n"))
    assert helpers._get_cpu_metrics_linux()["percent"] is None
    monkeypatch.setattr("builtins.open", mock_open(read_data="cpu  100 0 100 900 0 0 0 0\n"))
    assert helpers._get_cpu_metrics_linux()["percent"] == 0
    monkeypatch.setattr("builtins.open", mock_open(read_data="cpu bad 0 100 900 0 0 0 0\n"))
    assert helpers._get_cpu_metrics_linux()["percent"] is None
    monkeypatch.setattr("builtins.open", mock_open(read_data=""))
    assert helpers._get_ram_metrics_linux()["used_gb"] is None
    monkeypatch.setattr("builtins.open", lambda *a, **kw: (_ for _ in ()).throw(OSError("unavailable")))
    assert helpers._get_cpu_metrics_linux()["percent"] is None
    assert helpers._get_ram_metrics_linux()["percent"] is None
