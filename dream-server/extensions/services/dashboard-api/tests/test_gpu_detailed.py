"""Tests for per-GPU detailed detection, topology file reading, and assignment decoding."""

import asyncio
import base64
import json
from collections import deque
from unittest.mock import patch

from gpu import (
    decode_gpu_assignment,
    get_gpu_info_amd_detailed,
    get_gpu_info_nvidia_detailed,
    read_gpu_topology,
)
from models import GPUInfo


# ============================================================================
# read_gpu_topology — reads config/gpu-topology.json
# ============================================================================

_SAMPLE_TOPOLOGY = {
    "vendor": "nvidia",
    "gpu_count": 2,
    "driver_version": "560.35.03",
    "mig_enabled": False,
    "numa": {"nodes": 1},
    "gpus": [
        {"index": 0, "name": "RTX 4090", "memory_gb": 24.0, "pcie_gen": "4", "pcie_width": "16", "uuid": "GPU-aaa"},
        {"index": 1, "name": "RTX 4090", "memory_gb": 24.0, "pcie_gen": "4", "pcie_width": "16", "uuid": "GPU-bbb"},
    ],
    "links": [
        {"gpu_a": 0, "gpu_b": 1, "link_type": "NV12", "link_label": "NVLink", "rank": 100},
    ],
}


class TestReadGpuTopology:
    def test_reads_valid_file(self, monkeypatch, tmp_path):
        topo_file = tmp_path / "config" / "gpu-topology.json"
        topo_file.parent.mkdir()
        topo_file.write_text(json.dumps(_SAMPLE_TOPOLOGY))
        monkeypatch.setenv("DREAM_INSTALL_DIR", str(tmp_path))

        result = read_gpu_topology()
        assert result is not None
        assert result["vendor"] == "nvidia"
        assert result["gpu_count"] == 2
        assert len(result["links"]) == 1
        assert result["links"][0]["link_type"] == "NV12"

    def test_returns_none_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DREAM_INSTALL_DIR", str(tmp_path))
        assert read_gpu_topology() is None

    def test_returns_none_on_invalid_json(self, monkeypatch, tmp_path):
        topo_file = tmp_path / "config" / "gpu-topology.json"
        topo_file.parent.mkdir()
        topo_file.write_text("not valid json {{{")
        monkeypatch.setenv("DREAM_INSTALL_DIR", str(tmp_path))
        assert read_gpu_topology() is None


# ============================================================================
# decode_gpu_assignment
# ============================================================================


def _make_assignment_b64(assignment: dict) -> str:
    return base64.b64encode(json.dumps(assignment).encode()).decode()


_SAMPLE_ASSIGNMENT = {
    "gpu_assignment": {
        "version": "1.0",
        "strategy": "dedicated",
        "services": {
            "llama_server": {
                "gpus": ["GPU-aaa", "GPU-bbb"],
                "parallelism": {
                    "mode": "tensor",
                    "tensor_parallel_size": 2,
                    "pipeline_parallel_size": 1,
                    "gpu_memory_utilization": 0.92,
                },
            },
            "whisper": {"gpus": ["GPU-ccc"]},
        },
    }
}


class TestDecodeGpuAssignment:
    def test_decodes_valid_b64(self, monkeypatch):
        b64 = _make_assignment_b64(_SAMPLE_ASSIGNMENT)
        monkeypatch.setenv("GPU_ASSIGNMENT_JSON_B64", b64)
        result = decode_gpu_assignment()
        assert result is not None
        assert result["gpu_assignment"]["strategy"] == "dedicated"

    def test_returns_none_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("GPU_ASSIGNMENT_JSON_B64", raising=False)
        assert decode_gpu_assignment() is None

    def test_returns_none_on_invalid_b64(self, monkeypatch):
        monkeypatch.setenv("GPU_ASSIGNMENT_JSON_B64", "!!!not_base64!!!")
        assert decode_gpu_assignment() is None

    def test_returns_none_on_invalid_json(self, monkeypatch):
        bad = base64.b64encode(b"not valid json {").decode()
        monkeypatch.setenv("GPU_ASSIGNMENT_JSON_B64", bad)
        assert decode_gpu_assignment() is None


# ============================================================================
# get_gpu_info_nvidia_detailed
# ============================================================================


class TestGetGpuInfoNvidiaDetailed:
    def test_parses_single_gpu(self, monkeypatch):
        csv = "0, GPU-abc123, NVIDIA GeForce RTX 4090, 2048, 24564, 35, 62, 285.5"
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))
        monkeypatch.delenv("GPU_ASSIGNMENT_JSON_B64", raising=False)

        result = get_gpu_info_nvidia_detailed()
        assert result is not None
        assert len(result) == 1
        g = result[0]
        assert g.index == 0
        assert g.uuid == "GPU-abc123"
        assert g.name == "NVIDIA GeForce RTX 4090"
        assert g.memory_used_mb == 2048
        assert g.memory_total_mb == 24564
        assert g.utilization_percent == 35
        assert g.temperature_c == 62
        assert g.power_w == 285.5
        assert g.assigned_services == []

    def test_parses_multi_gpu(self, monkeypatch):
        csv = (
            "0, GPU-aaa, NVIDIA GeForce RTX 4090, 2048, 24564, 35, 62, 285.5\n"
            "1, GPU-bbb, NVIDIA GeForce RTX 4090, 4096, 24564, 50, 70, 300.0"
        )
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))
        monkeypatch.delenv("GPU_ASSIGNMENT_JSON_B64", raising=False)

        result = get_gpu_info_nvidia_detailed()
        assert result is not None
        assert len(result) == 2
        assert result[0].index == 0
        assert result[1].index == 1

    def test_populates_assigned_services(self, monkeypatch):
        csv = (
            "0, GPU-aaa, RTX 4090, 2048, 24564, 35, 62, 285.5\n"
            "1, GPU-bbb, RTX 4090, 4096, 24564, 50, 70, 300.0\n"
            "2, GPU-ccc, RTX 4090, 1024, 24564, 10, 55, 200.0"
        )
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))
        b64 = _make_assignment_b64(_SAMPLE_ASSIGNMENT)
        monkeypatch.setenv("GPU_ASSIGNMENT_JSON_B64", b64)

        result = get_gpu_info_nvidia_detailed()
        assert result is not None
        gpu_by_uuid = {g.uuid: g for g in result}
        assert "llama_server" in gpu_by_uuid["GPU-aaa"].assigned_services
        assert "llama_server" in gpu_by_uuid["GPU-bbb"].assigned_services
        assert "whisper" in gpu_by_uuid["GPU-ccc"].assigned_services

    def test_handles_na_power(self, monkeypatch):
        csv = "0, GPU-abc, Tesla T4, 1024, 16384, 10, 45, [N/A]"
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))
        monkeypatch.delenv("GPU_ASSIGNMENT_JSON_B64", raising=False)

        result = get_gpu_info_nvidia_detailed()
        assert result is not None
        assert result[0].power_w is None

    def test_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (False, ""))
        assert get_gpu_info_nvidia_detailed() is None

    def test_returns_none_on_empty_output(self, monkeypatch):
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, ""))
        assert get_gpu_info_nvidia_detailed() is None

    def test_unified_memory_fallback_for_na_memory(self, monkeypatch):
        # GB10 / GB200 unified-memory: nvidia-smi reports [N/A] for memory.
        csv = "0, GPU-gb10-uuid, NVIDIA GB10, [N/A], [N/A], 6, 43, 8.2"
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))
        monkeypatch.setattr("gpu._read_meminfo_mb", lambda: (12000, 124000))
        monkeypatch.delenv("GPU_ASSIGNMENT_JSON_B64", raising=False)

        result = get_gpu_info_nvidia_detailed()
        assert result is not None and len(result) == 1
        g = result[0]
        assert g.name == "NVIDIA GB10"
        assert g.memory_used_mb == 12000
        assert g.memory_total_mb == 124000
        assert g.utilization_percent == 6
        assert g.temperature_c == 43
        assert g.power_w == 8.2


# ============================================================================
# get_gpu_info_amd_detailed
# ============================================================================


class TestGetGpuInfoAmdDetailed:
    def _sysfs_values(self, card: str) -> dict:
        return {
            f"/sys/class/drm/{card}/device/mem_info_vram_total": str(16 * 1024**3),
            f"/sys/class/drm/{card}/device/mem_info_vram_used": str(4 * 1024**3),
            f"/sys/class/drm/{card}/device/mem_info_gtt_total": str(8 * 1024**3),
            f"/sys/class/drm/{card}/device/mem_info_gtt_used": str(1 * 1024**3),
            f"/sys/class/drm/{card}/device/gpu_busy_percent": "45",
            f"/sys/class/drm/{card}/device/product_name": f"AMD RX 7900 ({card})",
            f"/sys/class/drm/{card}/device/hwmon/hwmon0/temp1_input": "65000",
            f"/sys/class/drm/{card}/device/hwmon/hwmon0/power1_average": "200000000",
        }

    def test_single_amd_card(self, monkeypatch):
        """One AMD card returns a single IndividualGPU object."""
        sysfs = self._sysfs_values("card0")

        def mock_read_sysfs(path: str):
            if path.endswith("/vendor"):
                return "0x1002"
            return sysfs.get(path)

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr(
            "gpu._find_hwmon_dir",
            lambda base: f"{base}/hwmon/hwmon0",
        )

        with patch("glob.glob", return_value=["/sys/class/drm/card0/device"]):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        assert len(result) == 1
        assert result[0].index == 0
        assert result[0].uuid == "card0"
        assert result[0].memory_total_mb == 16 * 1024
        assert result[0].temperature_c == 65
        assert result[0].power_w == 200.0

    def test_returns_none_when_no_amd(self, monkeypatch):
        monkeypatch.setattr("gpu._read_sysfs", lambda p: None)
        with patch("glob.glob", return_value=[]):
            result = get_gpu_info_amd_detailed()
        assert result is None

    def test_multi_card_iteration(self, monkeypatch):
        """Two AMD cards each return valid data → two IndividualGPU objects."""
        cards = ["card0", "card1"]
        all_sysfs: dict = {}
        for card in cards:
            all_sysfs.update(self._sysfs_values(card))

        def mock_read_sysfs(path: str):
            if path.endswith("/vendor"):
                return "0x1002"
            return all_sysfs.get(path)

        def mock_hwmon(base: str):
            card = base.split("/")[-2]
            return f"/sys/class/drm/{card}/device/hwmon/hwmon0"

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", mock_hwmon)

        card_paths = [f"/sys/class/drm/{c}/device" for c in cards]
        with patch("glob.glob", return_value=card_paths):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        assert len(result) == 2
        assert result[0].index == 0
        assert result[1].index == 1
        assert result[0].uuid == "card0"
        assert result[1].uuid == "card1"
        assert result[0].memory_total_mb == 16 * 1024
        assert result[0].temperature_c == 65
        assert result[0].power_w == 200.0


# ============================================================================
# GPU history buffer (routers/gpu.py)
# ============================================================================


class TestGpuHistoryBuffer:
    def test_history_starts_empty(self):
        from routers.gpu import _GPU_HISTORY
        # We can't guarantee clean state in a module-level deque across tests,
        # but we can verify the structure.
        assert isinstance(_GPU_HISTORY, deque)
        assert _GPU_HISTORY.maxlen == 60

    def test_history_endpoint_empty(self):
        """With empty history, endpoint returns empty timestamps and gpus."""
        import routers.gpu as gpu_mod
        saved = list(gpu_mod._GPU_HISTORY)
        gpu_mod._GPU_HISTORY.clear()
        try:
            result = asyncio.run(
                gpu_mod.gpu_history()
            )
            assert result == {"timestamps": [], "gpus": {}}
        finally:
            for item in saved:
                gpu_mod._GPU_HISTORY.append(item)

    def test_history_endpoint_with_data(self):
        """History endpoint correctly structures samples into per-GPU series."""
        import routers.gpu as gpu_mod
        saved = list(gpu_mod._GPU_HISTORY)
        gpu_mod._GPU_HISTORY.clear()
        try:
            for i in range(3):
                gpu_mod._GPU_HISTORY.append({
                    "timestamp": f"2026-03-25T00:00:0{i}Z",
                    "gpus": {
                        "0": {"utilization": 10 + i, "memory_percent": 20.0, "temperature": 60, "power_w": 200.0},
                        "1": {"utilization": 30 + i, "memory_percent": 40.0, "temperature": 70, "power_w": 300.0},
                    },
                })
            result = asyncio.run(
                gpu_mod.gpu_history()
            )
            assert len(result["timestamps"]) == 3
            assert "0" in result["gpus"]
            assert "1" in result["gpus"]
            assert result["gpus"]["0"]["utilization"] == [10, 11, 12]
            assert result["gpus"]["1"]["temperature"] == [70, 70, 70]
        finally:
            gpu_mod._GPU_HISTORY.clear()
            for item in saved:
                gpu_mod._GPU_HISTORY.append(item)

    def test_history_maxlen_rolls_over(self):
        """Buffer never exceeds 60 samples."""
        import routers.gpu as gpu_mod
        saved = list(gpu_mod._GPU_HISTORY)
        gpu_mod._GPU_HISTORY.clear()
        try:
            sample = {"timestamp": "t", "gpus": {"0": {"utilization": 0, "memory_percent": 0, "temperature": 0, "power_w": None}}}
            for _ in range(70):
                gpu_mod._GPU_HISTORY.append(sample)
            assert len(gpu_mod._GPU_HISTORY) == 60
        finally:
            gpu_mod._GPU_HISTORY.clear()
            for item in saved:
                gpu_mod._GPU_HISTORY.append(item)


# ============================================================================
# _get_raw_gpus — Apple Silicon dispatch (routers/gpu.py)
# ============================================================================


def _sample_apple_gpu_info() -> GPUInfo:
    return GPUInfo(
        name="Apple M3 Max",
        memory_used_mb=24000,
        memory_total_mb=65536,
        memory_percent=36.6,
        utilization_percent=0,
        temperature_c=0,
        power_w=None,
        memory_type="unified",
        gpu_backend="apple",
    )


class TestGetRawGpusApple:
    def test_apple_returns_single_entry(self, monkeypatch):
        """Apple backend wraps the single GPUInfo into a one-element IndividualGPU list."""
        import routers.gpu as gpu_mod
        monkeypatch.setattr(gpu_mod, "get_gpu_info_apple", lambda: _sample_apple_gpu_info())

        result = gpu_mod._get_raw_gpus("apple")
        assert result is not None
        assert len(result) == 1
        g = result[0]
        assert g.index == 0
        assert len(g.uuid) >= 8  # GPUCard.jsx calls uuid.slice(-8)
        assert g.name == "Apple M3 Max"
        assert g.memory_used_mb == 24000
        assert g.memory_total_mb == 65536
        assert g.memory_percent == 36.6
        assert g.utilization_percent == 0
        assert g.temperature_c == 0
        assert g.power_w is None
        assert g.assigned_services == []

    def test_apple_returns_none_when_detection_fails(self, monkeypatch):
        """Detection returning None propagates as None — endpoint will raise 503."""
        import routers.gpu as gpu_mod
        monkeypatch.setattr(gpu_mod, "get_gpu_info_apple", lambda: None)

        assert gpu_mod._get_raw_gpus("apple") is None


class TestGetRawGpusAmdHostRuntime:
    def test_native_amd_sysfs_result_wins(self, monkeypatch):
        """Native AMD metrics remain preferred when available."""
        import routers.gpu as gpu_mod
        from models import IndividualGPU

        native = [IndividualGPU(
            index=0,
            uuid="card0",
            name="AMD Radeon",
            memory_used_mb=1024,
            memory_total_mb=16384,
            memory_percent=6.25,
            utilization_percent=25,
            temperature_c=55,
            power_w=120.0,
            assigned_services=[],
        )]
        monkeypatch.setattr(gpu_mod, "get_gpu_info_amd_detailed", lambda: native)

        assert gpu_mod._get_raw_gpus("amd") == native

    def test_windows_host_lemonade_falls_back_without_sysfs(self, monkeypatch):
        """Windows-hosted Lemonade has no AMD sysfs inside Docker Desktop."""
        import routers.gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "get_gpu_info_amd_detailed", lambda: None)
        monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
        monkeypatch.setenv("AMD_INFERENCE_LOCATION", "host")
        monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "windows-legacy-lemonade")
        monkeypatch.setenv("AMD_INFERENCE_BACKEND", "vulkan")
        monkeypatch.setenv("GPU_COUNT", "1")
        monkeypatch.setenv("HOST_RAM_GB", "96")

        result = gpu_mod._get_raw_gpus("amd")

        assert result is not None
        assert len(result) == 1
        assert result[0].uuid == "amd-host-runtime-0"
        assert result[0].name == "AMD Lemonade host runtime (vulkan)"
        assert result[0].memory_total_mb == 96 * 1024
        assert result[0].assigned_services == ["llama-server"]

    def test_non_windows_amd_without_sysfs_still_returns_none(self, monkeypatch):
        """Linux/container AMD installs still fail loud when no GPU is visible."""
        import routers.gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "get_gpu_info_amd_detailed", lambda: None)
        monkeypatch.setenv("AMD_INFERENCE_RUNTIME", "lemonade")
        monkeypatch.setenv("AMD_INFERENCE_LOCATION", "container")
        monkeypatch.setenv("AMD_INFERENCE_RUNTIME_MODE", "linux-container")

        assert gpu_mod._get_raw_gpus("amd") is None


class TestGpuDetailedEndpointApple:
    def test_endpoint_returns_apple_aggregate(self, monkeypatch, test_client):
        """/api/gpu/detailed with GPU_BACKEND=apple returns 200 with single-GPU aggregate."""
        import routers.gpu as gpu_mod
        # Bypass the 3 s TTL cache so this test sees fresh data.
        gpu_mod._detailed_cache["expires"] = 0.0
        gpu_mod._detailed_cache["value"] = None

        monkeypatch.setenv("GPU_BACKEND", "apple")
        monkeypatch.setattr(gpu_mod, "get_gpu_info_apple", lambda: _sample_apple_gpu_info())
        monkeypatch.setattr(gpu_mod, "decode_gpu_assignment", lambda: None)

        try:
            response = test_client.get("/api/gpu/detailed", headers=test_client.auth_headers)
            assert response.status_code == 200
            body = response.json()
            assert body["backend"] == "apple"
            assert body["gpu_count"] == 1
            assert len(body["gpus"]) == 1
            assert body["gpus"][0]["name"] == "Apple M3 Max"
            assert body["gpus"][0]["index"] == 0
            assert len(body["gpus"][0]["uuid"]) >= 8
            assert body["aggregate"]["name"] == "Apple M3 Max"
            assert body["aggregate"]["gpu_backend"] == "apple"
        finally:
            gpu_mod._detailed_cache["expires"] = 0.0
            gpu_mod._detailed_cache["value"] = None
