"""Tests for AMD multi-GPU detection, hwmon helpers, and topology-aware naming."""

from unittest.mock import patch

from gpu import (
    _find_hwmon_power,
    _find_hwmon_temp,
    get_gpu_info_amd,
    get_gpu_info_amd_detailed,
)


# ============================================================================
# _find_hwmon_temp — label-based discovery (junction > edge > temp1)
# ============================================================================


class TestFindHwmonTemp:
    def test_prefers_junction_over_edge(self, monkeypatch, tmp_path):
        """MI300X pattern: temp1 unavailable, temp2=junction is preferred."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()
        (hwmon / "temp1_label").write_text("mem\n")
        (hwmon / "temp1_input").write_text("50000\n")
        (hwmon / "temp2_label").write_text("junction\n")
        (hwmon / "temp2_input").write_text("72000\n")
        (hwmon / "temp3_label").write_text("edge\n")
        (hwmon / "temp3_input").write_text("65000\n")

        monkeypatch.setattr("gpu._read_sysfs", lambda path: open(path).read().strip()
                            if (tmp_path / "hwmon0" / path.split("/")[-1]).exists()
                            else None)

        result = _find_hwmon_temp(str(hwmon))
        # junction (72000) is preferred over edge (65000)
        assert result == 72

    def test_falls_back_to_edge(self, monkeypatch, tmp_path):
        """When no junction label, use edge."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()
        (hwmon / "temp1_label").write_text("edge\n")
        (hwmon / "temp1_input").write_text("65000\n")

        result = _find_hwmon_temp(str(hwmon))
        assert result == 65

    def test_falls_back_to_temp1_input(self, monkeypatch, tmp_path):
        """When no labels exist, fall back to temp1_input."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()
        (hwmon / "temp1_input").write_text("55000\n")
        # No label files at all

        result = _find_hwmon_temp(str(hwmon))
        assert result == 55

    def test_returns_zero_when_nothing_available(self, tmp_path):
        """When no temp files exist, return 0."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()

        result = _find_hwmon_temp(str(hwmon))
        assert result == 0


# ============================================================================
# _find_hwmon_power — power1_average → power1_input fallback
# ============================================================================


class TestFindHwmonPower:
    def test_reads_power1_average(self, tmp_path):
        """Standard case: power1_average available."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()
        (hwmon / "power1_average").write_text("200000000\n")

        result = _find_hwmon_power(str(hwmon))
        assert result == 200.0

    def test_falls_back_to_power1_input(self, tmp_path):
        """MI300X pattern: power1_average unavailable, power1_input exists."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()
        (hwmon / "power1_input").write_text("150000000\n")
        # No power1_average

        result = _find_hwmon_power(str(hwmon))
        assert result == 150.0

    def test_returns_none_when_nothing_available(self, tmp_path):
        """No power files → None."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()

        result = _find_hwmon_power(str(hwmon))
        assert result is None

    def test_prefers_average_over_input(self, tmp_path):
        """When both exist, power1_average takes precedence."""
        hwmon = tmp_path / "hwmon0"
        hwmon.mkdir()
        (hwmon / "power1_average").write_text("250000000\n")
        (hwmon / "power1_input").write_text("150000000\n")

        result = _find_hwmon_power(str(hwmon))
        assert result == 250.0


# ============================================================================
# get_gpu_info_amd — aggregate AMD GPU metrics
# ============================================================================


class TestGetGpuInfoAmdMultiGpu:
    def test_discrete_gpu_uses_vram(self, monkeypatch):
        """Discrete GPU: VRAM >> GTT → uses VRAM for memory metrics."""
        monkeypatch.setattr("gpu._find_amd_gpu_sysfs", lambda: "/sys/class/drm/card0/device")
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)

        sysfs_values = {
            "/sys/class/drm/card0/device/mem_info_vram_total": str(24 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_vram_used": str(8 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_total": str(16 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_used": str(2 * 1024**3),
            "/sys/class/drm/card0/device/gpu_busy_percent": "60",
            "/sys/class/drm/card0/device/product_name": "AMD Radeon RX 7900 XTX",
        }
        monkeypatch.setattr("gpu._read_sysfs", lambda path: sysfs_values.get(path))

        info = get_gpu_info_amd()
        assert info is not None
        assert info.memory_type == "discrete"
        assert info.memory_total_mb == 24 * 1024
        assert info.memory_used_mb == 8 * 1024
        assert info.name == "AMD Radeon RX 7900 XTX"
        assert info.gpu_backend == "amd"

    def test_no_redundant_power_overwrite(self, monkeypatch):
        """Verify power reading is not overwritten by redundant code (S2 fix)."""
        monkeypatch.setattr("gpu._find_amd_gpu_sysfs", lambda: "/sys/class/drm/card0/device")

        # Simulate MI300X: power1_average unavailable, power1_input = 150W
        sysfs_values = {
            "/sys/class/drm/card0/device/mem_info_vram_total": str(192 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_vram_used": str(10 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_total": str(16 * 1024**3),
            "/sys/class/drm/card0/device/mem_info_gtt_used": str(0),
            "/sys/class/drm/card0/device/gpu_busy_percent": "5",
            "/sys/class/drm/card0/device/product_name": "AMD Instinct MI300X",
        }

        def mock_read_sysfs(path):
            return sysfs_values.get(path)

        def mock_find_hwmon(base):
            return "/fake/hwmon0"

        def mock_find_hwmon_temp(hwmon):
            return 72

        def mock_find_hwmon_power(hwmon):
            return 150.0  # power1_input fallback

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", mock_find_hwmon)
        monkeypatch.setattr("gpu._find_hwmon_temp", mock_find_hwmon_temp)
        monkeypatch.setattr("gpu._find_hwmon_power", mock_find_hwmon_power)

        info = get_gpu_info_amd()
        assert info is not None
        # S2 fix: power should come from _find_hwmon_power (150W), not overwritten
        assert info.power_w == 150.0


# ============================================================================
# get_gpu_info_amd_detailed — per-GPU enumeration with topology
# ============================================================================


class TestGetGpuInfoAmdDetailedMultiGpu:
    def _make_sysfs(self, cards: list[str], vram_gb: int = 16) -> dict:
        sysfs = {}
        for card in cards:
            base = f"/sys/class/drm/{card}/device"
            sysfs.update({
                f"{base}/mem_info_vram_total": str(vram_gb * 1024**3),
                f"{base}/mem_info_vram_used": str(4 * 1024**3),
                f"{base}/mem_info_gtt_total": str(8 * 1024**3),
                f"{base}/mem_info_gtt_used": str(1 * 1024**3),
                f"{base}/gpu_busy_percent": "45",
                f"{base}/product_name": f"AMD RX 7900 ({card})",
            })
        return sysfs

    def _make_topology(self, gpu_count: int, link_type: str = "XGMI") -> dict:
        gpus = []
        for i in range(gpu_count):
            gpus.append({
                "index": i,
                "name": f"AMD Instinct MI300X GPU{i}",
                "memory_gb": 192.0,
                "pcie_gen": "5",
                "pcie_width": "x16",
                "uuid": f"MI300X-UUID-{i}",
                "gfx_version": "gfx942",
                "render_node": str(128 + i),
                "memory_type": "discrete",
                "pci_bdf": f"0000:f{9+i*2}:00.0",
            })
        links = []
        for i in range(gpu_count):
            for j in range(i + 1, gpu_count):
                links.append({
                    "gpu_a": i, "gpu_b": j,
                    "link_type": link_type, "link_label": link_type,
                    "rank": 90 if link_type == "XGMI" else 40,
                })
        return {"vendor": "amd", "gpu_count": gpu_count, "gpus": gpus, "links": links}

    def test_4gpu_with_topology_names(self, monkeypatch):
        """4 AMD GPUs with topology file → names come from topology (C3 fix)."""
        cards = ["card0", "card1", "card2", "card3"]
        sysfs = self._make_sysfs(cards, vram_gb=192)
        topo = self._make_topology(4)

        def mock_read_sysfs(path):
            if path.endswith("/vendor"):
                return "0x1002"
            if path.endswith("/product_name"):
                return None  # MI300X: product_name unavailable
            return sysfs.get(path)

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)
        monkeypatch.setattr("gpu.read_gpu_topology", lambda: topo)
        monkeypatch.setattr("gpu.decode_gpu_assignment", lambda: None)

        card_paths = [f"/sys/class/drm/{c}/device" for c in cards]
        with patch("glob.glob", return_value=card_paths):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        assert len(result) == 4
        # Names should come from topology, not sysfs (C3 fix)
        assert result[0].name == "AMD Instinct MI300X GPU0"
        assert result[3].name == "AMD Instinct MI300X GPU3"
        # UUIDs should come from topology
        assert result[0].uuid == "MI300X-UUID-0"
        assert result[1].uuid == "MI300X-UUID-1"

    def test_4gpu_without_topology_falls_back_to_sysfs(self, monkeypatch):
        """Without topology file, names and UUIDs come from sysfs."""
        cards = ["card0", "card1", "card2", "card3"]
        sysfs = self._make_sysfs(cards)

        def mock_read_sysfs(path):
            if path.endswith("/vendor"):
                return "0x1002"
            return sysfs.get(path)

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)
        monkeypatch.setattr("gpu.read_gpu_topology", lambda: None)
        monkeypatch.setattr("gpu.decode_gpu_assignment", lambda: None)

        card_paths = [f"/sys/class/drm/{c}/device" for c in cards]
        with patch("glob.glob", return_value=card_paths):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        assert len(result) == 4
        assert result[0].name == "AMD RX 7900 (card0)"
        assert result[0].uuid == "card0"  # No topology → card index as UUID

    def test_filters_non_amd_vendor(self, monkeypatch):
        """Non-0x1002 vendor cards (XCP virtual cards) are filtered out."""
        def mock_read_sysfs(path):
            if path == "/sys/class/drm/card0/device/vendor":
                return "0x1002"
            if path == "/sys/class/drm/card1/device/vendor":
                return ""  # XCP card: empty vendor
            if path == "/sys/class/drm/card2/device/vendor":
                return "0x1002"
            base = path.rsplit("/", 1)[0]
            card = base.split("/")[-2]
            if "mem_info_vram_total" in path:
                return str(16 * 1024**3)
            if "mem_info_vram_used" in path:
                return str(4 * 1024**3)
            if "mem_info_gtt" in path:
                return str(8 * 1024**3)
            if "gpu_busy_percent" in path:
                return "10"
            if "product_name" in path:
                return f"AMD GPU ({card})"
            return None

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)
        monkeypatch.setattr("gpu.read_gpu_topology", lambda: None)
        monkeypatch.setattr("gpu.decode_gpu_assignment", lambda: None)

        card_paths = [
            "/sys/class/drm/card0/device",
            "/sys/class/drm/card1/device",
            "/sys/class/drm/card2/device",
        ]
        with patch("glob.glob", return_value=card_paths):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        assert len(result) == 2  # card1 (XCP) filtered out
        assert result[0].index == 0
        assert result[1].index == 1

    def test_service_assignment_mapping(self, monkeypatch):
        """GPU assignment JSON maps services to GPUs by UUID."""
        cards = ["card0", "card1"]
        sysfs = self._make_sysfs(cards)
        topo = self._make_topology(2, link_type="PCIE")
        assignment = {
            "gpu_assignment": {
                "version": "1.0",
                "strategy": "dedicated",
                "services": {
                    "llama_server": {"gpus": ["MI300X-UUID-0", "MI300X-UUID-1"]},
                    "whisper": {"gpus": ["MI300X-UUID-1"]},
                },
            }
        }

        def mock_read_sysfs(path):
            if path.endswith("/vendor"):
                return "0x1002"
            return sysfs.get(path)

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)
        monkeypatch.setattr("gpu.read_gpu_topology", lambda: topo)
        monkeypatch.setattr("gpu.decode_gpu_assignment", lambda: assignment)

        card_paths = [f"/sys/class/drm/{c}/device" for c in cards]
        with patch("glob.glob", return_value=card_paths):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        gpu_by_uuid = {g.uuid: g for g in result}
        assert "llama_server" in gpu_by_uuid["MI300X-UUID-0"].assigned_services
        assert "llama_server" in gpu_by_uuid["MI300X-UUID-1"].assigned_services
        assert "whisper" in gpu_by_uuid["MI300X-UUID-1"].assigned_services

    def test_hwmon_temp_and_power_per_gpu(self, monkeypatch):
        """Each GPU gets independent hwmon readings."""
        cards = ["card0", "card1"]
        sysfs = self._make_sysfs(cards)
        # Temperatures and power values per hwmon path
        hwmon_temps = {}
        hwmon_powers = {}

        def mock_read_sysfs(path):
            if path.endswith("/vendor"):
                return "0x1002"
            return sysfs.get(path)

        def mock_hwmon(base):
            card = base.split("/")[-2]
            hwmon_path = f"/fake/hwmon/{card}"
            hwmon_temps[hwmon_path] = 72 if card == "card0" else 68
            hwmon_powers[hwmon_path] = 150.0 if card == "card0" else 130.0
            return hwmon_path

        def mock_temp(hwmon_dir):
            return hwmon_temps.get(hwmon_dir, 0)

        def mock_power(hwmon_dir):
            return hwmon_powers.get(hwmon_dir)

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", mock_hwmon)
        monkeypatch.setattr("gpu._find_hwmon_temp", mock_temp)
        monkeypatch.setattr("gpu._find_hwmon_power", mock_power)
        monkeypatch.setattr("gpu.read_gpu_topology", lambda: None)
        monkeypatch.setattr("gpu.decode_gpu_assignment", lambda: None)

        card_paths = [f"/sys/class/drm/{c}/device" for c in cards]
        with patch("glob.glob", return_value=card_paths):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        assert len(result) == 2
        assert result[0].temperature_c == 72
        assert result[0].power_w == 150.0
        assert result[1].temperature_c == 68
        assert result[1].power_w == 130.0

    def test_skips_card_with_missing_vram(self, monkeypatch):
        """Cards with no VRAM data are skipped (not crash)."""
        def mock_read_sysfs(path):
            if path.endswith("/vendor"):
                return "0x1002"
            if "card0" in path and "mem_info_vram_total" in path:
                return None  # Missing VRAM
            if "card1" in path and "mem_info_vram_total" in path:
                return str(16 * 1024**3)
            if "mem_info_vram_used" in path:
                return str(4 * 1024**3)
            if "mem_info_gtt" in path:
                return str(8 * 1024**3)
            if "gpu_busy_percent" in path:
                return "10"
            if "product_name" in path:
                return "Test GPU"
            return None

        monkeypatch.setattr("gpu._read_sysfs", mock_read_sysfs)
        monkeypatch.setattr("gpu._find_hwmon_dir", lambda base: None)
        monkeypatch.setattr("gpu.read_gpu_topology", lambda: None)
        monkeypatch.setattr("gpu.decode_gpu_assignment", lambda: None)

        card_paths = [
            "/sys/class/drm/card0/device",
            "/sys/class/drm/card1/device",
        ]
        with patch("glob.glob", return_value=card_paths):
            result = get_gpu_info_amd_detailed()

        assert result is not None
        assert len(result) == 1  # card0 skipped, card1 present
        assert result[0].index == 1  # Keeps enumeration index (card1)
