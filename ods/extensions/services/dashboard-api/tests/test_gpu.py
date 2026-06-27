"""Tests for gpu.py — tier classification, nvidia-smi parsing, Apple Silicon detection."""

import subprocess
from unittest.mock import MagicMock

import pytest

from gpu import (
    get_gpu_tier, get_gpu_info_nvidia, get_gpu_info_apple,
    get_gpu_info_amd, get_gpu_info, run_command,
)


# --- get_gpu_tier (pure function, no I/O) ---


class TestGetGpuTierDiscrete:
    """Discrete GPU tier boundaries."""

    @pytest.mark.parametrize("vram_gb,expected", [
        (4, "Minimal"),
        (7.9, "Minimal"),
        (8, "Entry"),
        (15.9, "Entry"),
        (16, "Standard"),
        (23.9, "Standard"),
        (24, "Prosumer"),
        (79.9, "Prosumer"),
        (80, "Professional"),
        (128, "Professional"),
    ])
    def test_tiers(self, vram_gb, expected):
        assert get_gpu_tier(vram_gb) == expected


class TestGetGpuTierUnified:
    """Strix Halo (unified memory) tier boundaries."""

    @pytest.mark.parametrize("vram_gb,expected", [
        (64, "Strix Halo Compact"),
        (89.9, "Strix Halo Compact"),
        (90, "Strix Halo 90+"),
        (96, "Strix Halo 90+"),
        (128, "Strix Halo 90+"),
    ])
    def test_tiers(self, vram_gb, expected):
        assert get_gpu_tier(vram_gb, memory_type="unified") == expected


# --- get_gpu_info_nvidia (mock subprocess) ---


class TestGetGpuInfoNvidia:

    def test_parses_valid_output(self, monkeypatch):
        csv = "NVIDIA GeForce RTX 4090, 2048, 24564, 35, 62, 285.5"
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))

        info = get_gpu_info_nvidia()
        assert info is not None
        assert info.name == "NVIDIA GeForce RTX 4090"
        assert info.memory_used_mb == 2048
        assert info.memory_total_mb == 24564
        assert info.utilization_percent == 35
        assert info.temperature_c == 62
        assert info.power_w == 285.5
        assert info.gpu_backend == "nvidia"

    def test_handles_na_power(self, monkeypatch):
        csv = "Tesla T4, 1024, 16384, 10, 45, [N/A]"
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))

        info = get_gpu_info_nvidia()
        assert info is not None
        assert info.power_w is None

    def test_returns_none_on_command_failure(self, monkeypatch):
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (False, ""))

        assert get_gpu_info_nvidia() is None

    def test_returns_none_on_empty_output(self, monkeypatch):
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, ""))

        assert get_gpu_info_nvidia() is None

    def test_returns_none_on_malformed_output(self, monkeypatch):
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, "garbage"))

        assert get_gpu_info_nvidia() is None

    def test_multi_gpu_aggregation(self, monkeypatch):
        csv = (
            "NVIDIA GeForce RTX 4090, 2048, 24564, 35, 62, 285.5\n"
            "NVIDIA GeForce RTX 4090, 4096, 24564, 50, 70, 300.0"
        )
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))

        info = get_gpu_info_nvidia()
        assert info is not None
        assert "× 2" in info.name
        assert info.memory_used_mb == 2048 + 4096
        assert info.memory_total_mb == 24564 * 2

    def test_unified_memory_fallback_for_na_memory(self, monkeypatch):
        # GB10 / GB200 unified-memory: nvidia-smi reports [N/A] for memory.
        csv = "NVIDIA GB10, [N/A], [N/A], 6, 43, 8.2"
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))
        monkeypatch.setattr("gpu._read_meminfo_mb", lambda: (12000, 124000))

        info = get_gpu_info_nvidia()
        assert info is not None
        assert info.name == "NVIDIA GB10"
        assert info.memory_used_mb == 12000
        assert info.memory_total_mb == 124000
        assert info.memory_type == "unified"
        assert info.utilization_percent == 6
        assert info.temperature_c == 43
        assert info.power_w == 8.2

    def test_unified_memory_fallback_skips_when_meminfo_unavailable(self, monkeypatch):
        csv = "NVIDIA GB10, [N/A], [N/A], 6, 43, 8.2"
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (True, csv))
        monkeypatch.setattr("gpu._read_meminfo_mb", lambda: None)

        assert get_gpu_info_nvidia() is None


# --- get_gpu_info_apple (mock subprocess) ---


class TestGetGpuInfoApple:

    def test_returns_none_on_non_darwin(self, monkeypatch):
        monkeypatch.setattr("gpu.platform.system", lambda: "Linux")
        assert get_gpu_info_apple() is None

    def test_parses_apple_silicon(self, monkeypatch):
        monkeypatch.setattr("gpu.platform.system", lambda: "Darwin")

        def mock_run_command(cmd, **kw):
            if "machdep.cpu.brand_string" in cmd:
                return True, "Apple M4 Max"
            if "hw.memsize" in cmd:
                return True, str(64 * 1024**3)  # 64 GB
            if cmd == ["vm_stat"]:
                return True, (
                    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                    "Pages active:                          500000.\n"
                    "Pages wired down:                      300000.\n"
                    "Pages occupied by compressor:          100000.\n"
                )
            return False, ""

        monkeypatch.setattr("gpu.run_command", mock_run_command)

        info = get_gpu_info_apple()
        assert info is not None
        assert info.name == "Apple M4 Max"
        assert info.memory_total_mb == 64 * 1024
        assert info.gpu_backend == "apple"
        assert info.memory_type == "unified"
        assert info.memory_used_mb > 0

    def test_returns_none_when_sysctl_fails(self, monkeypatch):
        monkeypatch.setattr("gpu.platform.system", lambda: "Darwin")
        monkeypatch.setattr("gpu.run_command", lambda cmd, **kw: (False, ""))
        assert get_gpu_info_apple() is None


# --- run_command ---


class TestRunCommand:

    def test_returns_success_and_output(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  hello world  "
        monkeypatch.setattr("gpu.subprocess.run", lambda *a, **kw: mock_result)

        ok, output = run_command(["echo", "hello"])
        assert ok is True
        assert output == "hello world"

    def test_returns_false_on_timeout(self, monkeypatch):
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=["slow"], timeout=5)
        monkeypatch.setattr("gpu.subprocess.run", raise_timeout)

        ok, output = run_command(["slow"], timeout=5)
        assert ok is False
        assert output == "timeout"

    def test_returns_false_on_file_not_found(self, monkeypatch):
        def raise_fnf(*a, **kw):
            raise FileNotFoundError("No such file: 'missing'")
        monkeypatch.setattr("gpu.subprocess.run", raise_fnf)

        ok, output = run_command(["missing"])
        assert ok is False
        assert "No such file" in output


# --- get_gpu_info_amd ---


class TestGetGpuInfoAmd:

    def test_parses_discrete_gpu(self, monkeypatch):
        """Discrete GPU: 16 GB VRAM, small GTT."""
        monkeypatch.setattr("gpu._find_amd_gpu_sysfs", lambda: "/sys/class/drm/card0/device")
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: "/sys/class/drm/card0/device/hwmon/hwmon0")

        sysfs_values = {
            "/sys/class/drm/card0/device/mem_info_vram_total": str(16 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_vram_used": str(4 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_total": str(8 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_used": str(1 * 1024**3),
            "/sys/class/drm/card0/device/gpu_busy_percent": "45",
            "/sys/class/drm/card0/device/product_name": "AMD Radeon RX 7900 XTX",
            "/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input": "62000",
            "/sys/class/drm/card0/device/hwmon/hwmon0/power1_average": "200000000",
        }
        monkeypatch.setattr("gpu._read_sysfs", lambda path: sysfs_values.get(path))

        info = get_gpu_info_amd()
        assert info is not None
        assert info.name == "AMD Radeon RX 7900 XTX"
        assert info.memory_total_mb == 16 * 1024
        assert info.memory_used_mb == 4 * 1024
        assert info.memory_type == "discrete"
        assert info.utilization_percent == 45
        assert info.temperature_c == 62
        assert info.power_w == 200.0
        assert info.gpu_backend == "amd"

    def test_parses_unified_apu(self, monkeypatch):
        """APU: small VRAM + large GTT signals unified memory."""
        monkeypatch.setattr("gpu._find_amd_gpu_sysfs", lambda: "/sys/class/drm/card0/device")
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)

        sysfs_values = {
            "/sys/class/drm/card0/device/mem_info_vram_total": str(4 * 1024**3),      # small VRAM partition
            "/sys/class/drm/card0/device/mem_info_vram_used": str(1 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_total": str(96 * 1024**3),       # large GTT => unified
            "/sys/class/drm/card0/device/mem_info_gtt_used": str(20 * 1024**3),
            "/sys/class/drm/card0/device/gpu_busy_percent": "10",
            "/sys/class/drm/card0/device/product_name": None,
        }
        monkeypatch.setattr("gpu._read_sysfs", lambda path: sysfs_values.get(path))

        info = get_gpu_info_amd()
        assert info is not None
        assert info.memory_type == "unified"
        assert info.memory_total_mb == 96 * 1024
        assert info.memory_used_mb == 20 * 1024
        assert "Strix Halo" in info.name  # default name

    def test_reads_hwmon_temp_power(self, monkeypatch):
        """Ensure hwmon reads for temperature and power work."""
        monkeypatch.setattr("gpu._find_amd_gpu_sysfs", lambda: "/sys/class/drm/card0/device")
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: "/hw")

        sysfs_values = {
            "/sys/class/drm/card0/device/mem_info_vram_total": str(16 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_vram_used": str(2 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_total": str(8 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_used": str(0),
            "/sys/class/drm/card0/device/gpu_busy_percent": "20",
            "/sys/class/drm/card0/device/product_name": "Test GPU",
            "/hw/temp1_input": "75000",
            "/hw/power1_average": "150000000",
        }
        monkeypatch.setattr("gpu._read_sysfs", lambda path: sysfs_values.get(path))

        info = get_gpu_info_amd()
        assert info is not None
        assert info.temperature_c == 75
        assert info.power_w == 150.0

    def test_returns_none_when_no_device(self, monkeypatch):
        monkeypatch.setattr("gpu._find_amd_gpu_sysfs", lambda: None)
        assert get_gpu_info_amd() is None

    def test_returns_none_when_vram_missing(self, monkeypatch):
        monkeypatch.setattr("gpu._find_amd_gpu_sysfs", lambda: "/sys/class/drm/card0/device")
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)
        monkeypatch.setattr("gpu._read_sysfs", lambda path: None)

        assert get_gpu_info_amd() is None


# --- get_gpu_info_apple container path ---


class TestGetGpuInfoAppleContainer:

    def test_host_ram_gb_returns_gpu_info(self, monkeypatch):
        """Linux container with GPU_BACKEND=apple and HOST_RAM_GB=64."""
        monkeypatch.setattr("gpu.platform.system", lambda: "Linux")
        monkeypatch.setattr("gpu.os.environ", {
            "GPU_BACKEND": "apple",
            "HOST_RAM_GB": "64",
        })

        info = get_gpu_info_apple()
        assert info is not None
        assert info.memory_total_mb == 64 * 1024
        assert info.memory_type == "unified"
        assert info.gpu_backend == "apple"
        assert "64" in info.name

    def test_no_host_ram_returns_none(self, monkeypatch):
        """Linux container with GPU_BACKEND=apple but no HOST_RAM_GB."""
        monkeypatch.setattr("gpu.platform.system", lambda: "Linux")
        monkeypatch.setattr("gpu.os.environ", {
            "GPU_BACKEND": "apple",
        })

        assert get_gpu_info_apple() is None

    def test_invalid_host_ram_returns_none(self, monkeypatch):
        """Linux container with GPU_BACKEND=apple and invalid HOST_RAM_GB."""
        monkeypatch.setattr("gpu.platform.system", lambda: "Linux")
        monkeypatch.setattr("gpu.os.environ", {
            "GPU_BACKEND": "apple",
            "HOST_RAM_GB": "not-a-number",
        })

        assert get_gpu_info_apple() is None


# --- get_gpu_info dispatcher ---


class TestGetGpuInfoDispatcher:

    def _make_gpu_info(self, name, backend):
        from models import GPUInfo
        return GPUInfo(
            name=name, memory_used_mb=1024, memory_total_mb=8192,
            memory_percent=12.5, utilization_percent=50, temperature_c=60,
            gpu_backend=backend,
        )

    def test_nvidia_backend_tries_nvidia(self, monkeypatch):
        monkeypatch.setattr("gpu.os.environ", {"GPU_BACKEND": "nvidia"})
        gpu = self._make_gpu_info("RTX 4090", "nvidia")
        monkeypatch.setattr("gpu.get_gpu_info_nvidia", lambda: gpu)
        monkeypatch.setattr("gpu.get_gpu_info_amd", lambda: None)
        monkeypatch.setattr("gpu.get_gpu_info_apple", lambda: None)

        result = get_gpu_info()
        assert result is not None
        assert result.name == "RTX 4090"

    def test_amd_backend_tries_amd_first(self, monkeypatch):
        monkeypatch.setattr("gpu.os.environ", {"GPU_BACKEND": "amd"})
        gpu = self._make_gpu_info("Radeon RX 7900", "amd")
        monkeypatch.setattr("gpu.get_gpu_info_amd", lambda: gpu)
        monkeypatch.setattr("gpu.get_gpu_info_nvidia", lambda: None)
        monkeypatch.setattr("gpu.get_gpu_info_apple", lambda: None)

        result = get_gpu_info()
        assert result is not None
        assert result.name == "Radeon RX 7900"

    def test_apple_on_darwin_autodetects(self, monkeypatch):
        monkeypatch.setattr("gpu.os.environ", {"GPU_BACKEND": ""})
        monkeypatch.setattr("gpu.platform.system", lambda: "Darwin")
        gpu = self._make_gpu_info("Apple M4 Max", "apple")
        monkeypatch.setattr("gpu.get_gpu_info_nvidia", lambda: None)
        monkeypatch.setattr("gpu.get_gpu_info_amd", lambda: None)
        monkeypatch.setattr("gpu.get_gpu_info_apple", lambda: gpu)

        result = get_gpu_info()
        assert result is not None
        assert result.name == "Apple M4 Max"

    def test_falls_back_through_backends(self, monkeypatch):
        """nvidia backend -> nvidia fails -> amd fails -> still tries nvidia first then amd."""
        monkeypatch.setattr("gpu.os.environ", {"GPU_BACKEND": "nvidia"})
        monkeypatch.setattr("gpu.platform.system", lambda: "Linux")

        amd_gpu = self._make_gpu_info("AMD Fallback", "amd")
        monkeypatch.setattr("gpu.get_gpu_info_nvidia", lambda: None)
        monkeypatch.setattr("gpu.get_gpu_info_amd", lambda: amd_gpu)
        monkeypatch.setattr("gpu.get_gpu_info_apple", lambda: None)

        result = get_gpu_info()
        assert result is not None
        assert result.name == "AMD Fallback"

    def test_returns_none_when_nothing_found(self, monkeypatch):
        monkeypatch.setattr("gpu.os.environ", {"GPU_BACKEND": "nvidia"})
        monkeypatch.setattr("gpu.platform.system", lambda: "Linux")
        monkeypatch.setattr("gpu.get_gpu_info_nvidia", lambda: None)
        monkeypatch.setattr("gpu.get_gpu_info_amd", lambda: None)
        monkeypatch.setattr("gpu.get_gpu_info_apple", lambda: None)

        result = get_gpu_info()
        assert result is None
