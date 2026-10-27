"""Tests for Windows Desktop runtime profile, Win32 metrics, and zero-subprocess GPU telemetry."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

import config
import gpu
import helpers
import host_agent_client
import main
from models import IndividualGPU


def test_runtime_profile_resolution_and_validation(tmp_path):
    assert config.resolve_runtime_profile({}) == "compose"
    assert config.resolve_runtime_profile({"ODS_RUNTIME_PROFILE": "compose"}) == "compose"
    assert config.resolve_runtime_profile({"ODS_RUNTIME_PROFILE": "windows-desktop"}) == "windows-desktop"

    with pytest.raises(RuntimeError, match="Unsupported ODS_RUNTIME_PROFILE"):
        config.resolve_runtime_profile({"ODS_RUNTIME_PROFILE": "invalid-profile"})

    # windows-desktop requires an absolute ODS_DATA_DIR and non-empty DASHBOARD_API_KEY
    with pytest.raises(RuntimeError, match="ODS_DATA_DIR must be set to an absolute path"):
        config.validate_windows_desktop_env({"DASHBOARD_API_KEY": "secret"})

    with pytest.raises(RuntimeError, match="ODS_DATA_DIR must be set to an absolute path"):
        config.validate_windows_desktop_env({
            "ODS_DATA_DIR": "relative/data",
            "DASHBOARD_API_KEY": "secret",
        })

    with pytest.raises(RuntimeError, match="DASHBOARD_API_KEY must be set"):
        config.validate_windows_desktop_env({
            "ODS_DATA_DIR": str(tmp_path.resolve()),
            "DASHBOARD_API_KEY": "",
        })

    # Valid windows-desktop environment passes cleanly
    config.validate_windows_desktop_env({
        "ODS_DATA_DIR": str(tmp_path.resolve()),
        "DASHBOARD_API_KEY": "valid-desktop-key",
    })


def test_windows_desktop_service_filtering_keeps_only_loopback():
    services = {
        "llama-server": {"host": "llama-server", "port": 8080, "external_port": 8080, "external_link": True},
        "litellm": {"host": "litellm", "port": 4000, "external_port": 4000},
        "qdrant": {"host": "qdrant", "port": 6333, "external_port": 6333},
        "opencode": {"host": "localhost", "port": 4096, "external_port": 4096},
        "pixel-agent": {"host": "127.0.0.1", "port": 3100, "external_port": 3100},
    }
    config._apply_windows_desktop_services_filter(
        services,
        {"ODS_NATIVE_LLM_URL": "http://127.0.0.1:8085"},
    )
    assert set(services.keys()) == {"llama-server", "opencode", "pixel-agent"}
    assert services["llama-server"]["host"] == "127.0.0.1"
    assert services["llama-server"]["port"] == 8085
    assert services["llama-server"]["external_link"] is False
    assert services["llama-server"]["type"] == "native"


def test_host_agent_disabled_when_agent_url_empty(monkeypatch):
    monkeypatch.setattr(host_agent_client, "AGENT_URL", "")
    monkeypatch.setattr(host_agent_client, "_sync_client", None)
    monkeypatch.setattr(host_agent_client, "_async_client", None)

    with pytest.raises(host_agent_client.AgentUnavailable, match="disabled"):
        host_agent_client.request_json("GET", "/v1/service/health", timeout=1)


def test_gpu_run_command_spawns_zero_subprocesses_in_windows_desktop(monkeypatch):
    monkeypatch.setattr(gpu, "WINDOWS_DESKTOP_PROFILE", True)
    called = False

    def _forbid_subprocess(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess.run must not be called in windows-desktop profile")

    monkeypatch.setattr(gpu.subprocess, "run", _forbid_subprocess)
    ok, msg = gpu.run_command(["nvidia-smi"])
    assert ok is False
    assert "disabled" in msg
    assert called is False


@pytest.mark.asyncio
async def test_preflight_docker_not_applicable_in_windows_desktop(monkeypatch):
    monkeypatch.setattr(main, "WINDOWS_DESKTOP_PROFILE", True)
    resp = await main.preflight_docker()
    assert resp["available"] is False
    assert resp["applicable"] is False


def test_directx_user_gpu_preferences_parsing_and_matching(monkeypatch):
    monkeypatch.delenv("ODS_WINDOWS_GPU_PREFERENCE", raising=False)

    assert gpu.parse_directx_gpu_preference_string("GpuPreference=2;") == 2
    assert gpu.parse_directx_gpu_preference_string("AppStatus=0;GpuPreference=1;") == 1
    assert gpu.parse_directx_gpu_preference_string("SwapEffectUpgradeEnable=0;GpuPreference=0;") == 0
    assert gpu.parse_directx_gpu_preference_string("AppStatus=4096;") is None
    assert gpu.parse_directx_gpu_preference_string("") is None

    # Default when registry has no matching entry: GPU-first (high_performance)
    pref_default = gpu.read_windows_user_gpu_preference(registry_entries={})
    assert pref_default["mode"] == "high_performance"
    assert pref_default["preferDiscreteGpu"] is True
    assert pref_default["source"] == "default_gpu_first"

    # Match by basename (ODS.exe set to Power Saving in Windows Graphics Settings)
    pref_power = gpu.read_windows_user_gpu_preference(
        registry_entries={r"C:\Users\Owner\AppData\Local\ODS\bin\ODS.exe": "AppStatus=0;GpuPreference=1;"}
    )
    assert pref_power["mode"] == "power_saving"
    assert pref_power["preferDiscreteGpu"] is False
    assert pref_power["preferenceCode"] == 1
    assert pref_power["source"] == "directx_registry"

    # Match by exact candidate path (High Performance)
    target_exe = r"C:\Users\Owner\AppData\Local\ODS\llama.cpp\llama-server.exe"
    pref_high = gpu.read_windows_user_gpu_preference(
        exe_candidates=[target_exe],
        registry_entries={target_exe: "GpuPreference=2;"},
    )
    assert pref_high["mode"] == "high_performance"
    assert pref_high["preferDiscreteGpu"] is True
    assert pref_high["preferenceCode"] == 2


def test_windows_native_gpu_selection_honors_user_gpu_preference(monkeypatch):
    monkeypatch.setattr(gpu.platform, "system", lambda: "Windows")

    discrete_nvidia = IndividualGPU(
        index=0,
        uuid="GPU-12345678",
        name="NVIDIA GeForce RTX 3060",
        memory_used_mb=512,
        memory_total_mb=12288,
        memory_percent=4.2,
        utilization_percent=12,
        temperature_c=46,
        power_w=18.5,
        memory_type="discrete",
    )
    integrated_intel = IndividualGPU(
        index=1,
        uuid="win-intel-0001",
        name="Intel(R) UHD Graphics",
        memory_used_mb=0,
        memory_total_mb=1024,
        memory_percent=0.0,
        utilization_percent=0,
        temperature_c=0,
        memory_type="unified",
        memory_usage_available=False,
        utilization_available=False,
        temperature_available=False,
    )

    monkeypatch.setattr(gpu, "get_gpu_info_windows_nvml_detailed", lambda: [discrete_nvidia])
    monkeypatch.setattr(
        gpu,
        "get_gpu_info_windows_registry_detailed",
        lambda: [("nvidia", discrete_nvidia), ("intel", integrated_intel)],
    )

    # 1. High Performance (default): chooses discrete NVIDIA GPU
    monkeypatch.setattr(
        gpu,
        "read_windows_user_gpu_preference",
        lambda: {"mode": "high_performance", "preferDiscreteGpu": True},
    )
    res_high = gpu.get_gpu_info_windows_native_detailed()
    assert res_high is not None
    assert res_high[0] == "nvidia"
    assert res_high[1][0].name == "NVIDIA GeForce RTX 3060"

    # 2. Power Saving (Windows Graphics Settings GpuPreference=1): switches to integrated GPU
    monkeypatch.setattr(
        gpu,
        "read_windows_user_gpu_preference",
        lambda: {"mode": "power_saving", "preferDiscreteGpu": False},
    )
    res_power = gpu.get_gpu_info_windows_native_detailed()
    assert res_power is not None
    assert res_power[0] == "intel"
    assert res_power[1][0].name == "Intel(R) UHD Graphics"


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows kernel32 APIs required")
def test_windows_kernel32_cpu_and_ram_metrics_live():
    cpu = helpers.get_cpu_metrics()
    assert cpu["source"] == "windows-kernel32"
    assert cpu["scope"] == "host"
    assert isinstance(cpu["percent"], float)
    assert 0.0 <= cpu["percent"] <= 100.0

    ram = helpers.get_ram_metrics()
    assert ram["source"] == "windows-kernel32"
    assert ram["scope"] == "host"
    assert isinstance(ram["total_gb"], float) and ram["total_gb"] > 0
    assert isinstance(ram["used_gb"], float) and ram["used_gb"] > 0
    assert isinstance(ram["percent"], float) and 0.0 < ram["percent"] <= 100.0
