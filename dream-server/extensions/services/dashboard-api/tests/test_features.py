"""Tests for features.py — calculate_feature_status with Apple Silicon fallback."""

import os
from unittest.mock import patch, AsyncMock

from routers.features import calculate_feature_status


class TestCalculateFeatureStatusDefaults:
    """calculate_feature_status uses .get() defaults for optional feature fields."""

    def test_missing_optional_fields_use_defaults(self):
        """A feature with only id, name, and requirements should not KeyError."""
        minimal_feature = {
            "id": "minimal",
            "name": "Minimal Feature",
            "requirements": {"vram_gb": 0, "services": [], "services_any": []},
        }
        result = calculate_feature_status(minimal_feature, [], None)

        assert result["id"] == "minimal"
        assert result["name"] == "Minimal Feature"
        assert result["description"] == ""
        assert result["icon"] == "Package"
        assert result["category"] == "other"
        assert result["setupTime"] == "Unknown"
        assert result["priority"] == 99


class TestCalculateFeatureStatusAppleFallback:

    def _make_feature(self, vram_gb=0):
        return {
            "id": "test-feat",
            "name": "Test Feature",
            "description": "A test feature",
            "icon": "Zap",
            "category": "inference",
            "setup_time": "5 min",
            "priority": 1,
            "requirements": {
                "vram_gb": vram_gb,
                "services": [],
                "services_any": [],
            },
            "enabled_services_all": ["required-svc"],
            "enabled_services_any": [],
        }

    def test_apple_fallback_uses_host_ram_when_gpu_info_none(self):
        """When GPU_BACKEND=apple and gpu_info is None, HOST_RAM_GB gates VRAM."""
        from routers.features import calculate_feature_status
        feature = self._make_feature(vram_gb=16)
        with patch.dict(os.environ, {"HOST_RAM_GB": "24", "GPU_BACKEND": "apple"}):
            with patch("routers.features.GPU_BACKEND", "apple"):
                result = calculate_feature_status(feature, [], None)
        assert result["requirements"]["vramOk"] is True
        assert result["status"] != "insufficient_vram"

    def test_apple_fallback_insufficient_when_ram_too_low(self):
        """When HOST_RAM_GB < feature vram_gb, feature is insufficient_vram."""
        from routers.features import calculate_feature_status
        feature = self._make_feature(vram_gb=32)
        with patch.dict(os.environ, {"HOST_RAM_GB": "16", "GPU_BACKEND": "apple"}):
            with patch("routers.features.GPU_BACKEND", "apple"):
                result = calculate_feature_status(feature, [], None)
        assert result["requirements"]["vramOk"] is False
        assert result["status"] == "insufficient_vram"

    def test_apple_fallback_not_triggered_on_linux(self):
        """HOST_RAM fallback does NOT apply on non-apple backends."""
        from routers.features import calculate_feature_status
        feature = self._make_feature(vram_gb=8)
        with patch.dict(os.environ, {"HOST_RAM_GB": "64", "GPU_BACKEND": "nvidia"}):
            with patch("routers.features.GPU_BACKEND", "nvidia"):
                result = calculate_feature_status(feature, [], None)
        # gpu_info is None, so gpu_vram_gb=0, which is < 8 → insufficient_vram on nvidia
        assert result["status"] == "insufficient_vram"


class TestApiFeaturesAppleFallback:
    """Tests for the endpoint-level Apple Silicon VRAM fallback in api_features()."""

    def test_api_features_apple_fallback_gpu_summary(self, test_client):
        """api_features() endpoint applies Apple Silicon HOST_RAM_GB fallback for GPU summary."""
        with patch.dict(os.environ, {"HOST_RAM_GB": "16", "GPU_BACKEND": "apple"}):
            with patch("routers.features.GPU_BACKEND", "apple"):
                with patch("routers.features.get_gpu_info", return_value=None):
                    with patch("helpers.get_all_services", new_callable=AsyncMock, return_value=[]):
                        response = test_client.get(
                            "/api/features",
                            headers=test_client.auth_headers,
                        )
        assert response.status_code == 200
        data = response.json()
        assert data["gpu"]["vramGb"] == 16.0


# --- calculate_feature_status general cases ---


class TestCalculateFeatureStatusGeneral:

    def _make_feature(self, vram_gb=0, services=None, services_any=None,
                      enabled_all=None, enabled_any=None):
        return {
            "id": "test-feat",
            "name": "Test Feature",
            "description": "A test feature",
            "icon": "Zap",
            "category": "inference",
            "setup_time": "5 min",
            "priority": 1,
            "requirements": {
                "vram_gb": vram_gb,
                "services": services or [],
                "services_any": services_any or [],
            },
            "enabled_services_all": enabled_all if enabled_all is not None else (services or []),
            "enabled_services_any": enabled_any if enabled_any is not None else (services_any or []),
        }

    def _make_service_status(self, sid, status="healthy"):
        from models import ServiceStatus
        return ServiceStatus(
            id=sid, name=sid, port=8080, external_port=8080, status=status,
        )

    def test_enabled_when_all_services_healthy(self):
        from routers.features import calculate_feature_status
        from models import GPUInfo

        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        feature = self._make_feature(vram_gb=8, services=["llama-server"],
                                     enabled_all=["llama-server"])
        services = [self._make_service_status("llama-server", "healthy")]

        with patch("routers.features.GPU_BACKEND", "nvidia"):
            result = calculate_feature_status(feature, services, gpu)
        assert result["status"] == "enabled"
        assert result["enabled"] is True

    def test_preserves_launch_and_enabled_service_metadata(self):
        from routers.features import calculate_feature_status
        from models import GPUInfo

        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        feature = self._make_feature(
            vram_gb=8,
            services=["llama-server"],
            enabled_all=["llama-server"],
        )
        feature["launch"] = {"type": "service", "service": "open-webui"}
        services = [self._make_service_status("llama-server", "healthy")]

        with patch("routers.features.GPU_BACKEND", "nvidia"):
            result = calculate_feature_status(feature, services, gpu)

        assert result["enabledServicesAll"] == ["llama-server"]
        assert result["enabledServicesAny"] == []
        assert result["launch"] == {"type": "service", "service": "open-webui"}

    def test_insufficient_vram(self):
        from routers.features import calculate_feature_status
        from models import GPUInfo

        gpu = GPUInfo(
            name="GTX 1050", memory_used_mb=1024, memory_total_mb=4096,
            memory_percent=25.0, utilization_percent=10, temperature_c=50,
            gpu_backend="nvidia",
        )
        # enabled_all must reference a service not in the service list so
        # is_enabled is False, allowing the vram check to be reached.
        feature = self._make_feature(vram_gb=16, services=[],
                                     enabled_all=["llama-server"])

        with patch("routers.features.GPU_BACKEND", "nvidia"):
            result = calculate_feature_status(feature, [], gpu)
        assert result["status"] == "insufficient_vram"
        assert result["requirements"]["vramOk"] is False

    def test_services_needed_when_deps_missing(self):
        from routers.features import calculate_feature_status
        from models import GPUInfo

        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        feature = self._make_feature(vram_gb=8, services=["whisper", "tts"],
                                     enabled_all=["whisper", "tts"])
        services = [self._make_service_status("whisper", "healthy")]

        with patch("routers.features.GPU_BACKEND", "nvidia"):
            result = calculate_feature_status(feature, services, gpu)
        assert result["status"] == "services_needed"
        assert "tts" in result["requirements"]["servicesMissing"]

    def test_available_when_vram_ok_but_not_enabled(self):
        from routers.features import calculate_feature_status
        from models import GPUInfo

        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        feature = self._make_feature(vram_gb=8, services=[],
                                     enabled_all=["some-service"])
        services = []

        with patch("routers.features.GPU_BACKEND", "nvidia"):
            result = calculate_feature_status(feature, services, gpu)
        assert result["status"] == "available"


# --- /api/features/{feature_id}/enable ---


class TestFeatureEnableInstructions:

    def test_returns_instructions_for_known_feature(self, test_client, monkeypatch):
        test_features = [
            {"id": "chat", "name": "Chat", "description": "AI Chat",
             "icon": "MessageSquare", "category": "inference",
             "setup_time": "1 min", "priority": 1,
             "requirements": {"vram_gb": 0, "services": [], "services_any": []},
             "enabled_services_all": [], "enabled_services_any": []}
        ]
        monkeypatch.setattr("routers.features.FEATURES", test_features)

        resp = test_client.get(
            "/api/features/chat/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["featureId"] == "chat"
        assert "instructions" in data
        assert "steps" in data["instructions"]

    def test_instruction_links_use_request_host(self, test_client, monkeypatch):
        test_features = [
            {"id": "documents", "name": "Documents", "description": "Document Q&A",
             "icon": "FileText", "category": "productivity",
             "setup_time": "2 min", "priority": 3,
             "requirements": {"vram_gb": 0, "services": [], "services_any": []},
             "enabled_services_all": [], "enabled_services_any": []}
        ]
        monkeypatch.setattr("routers.features.FEATURES", test_features)
        monkeypatch.setattr(
            "routers.features.SERVICES",
            {"open-webui": {"external_port": 3000}},
        )

        resp = test_client.get(
            "/api/features/documents/enable",
            headers={**test_client.auth_headers, "host": "dashboard.dream.local:3001"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["instructions"]["links"][0] == {
            "label": "Open Chat",
            "url": "http://dashboard.dream.local:3000",
        }

    def test_lan_web_instructions_are_explicit_about_dream_proxy(self, test_client, monkeypatch):
        test_features = [
            {"id": "lan-web", "name": "LAN web entry", "description": "LAN entry",
             "icon": "Globe", "category": "networking",
             "setup_time": "Ready", "priority": 1,
             "requirements": {"vram_gb": 0, "services": [], "services_any": []},
             "enabled_services_all": ["dream-proxy"], "enabled_services_any": []}
        ]
        monkeypatch.setattr("routers.features.FEATURES", test_features)
        monkeypatch.setattr(
            "routers.features.SERVICES",
            {"dream-proxy": {"external_port": 80}},
        )

        resp = test_client.get(
            "/api/features/lan-web/enable",
            headers={**test_client.auth_headers, "host": "dashboard.dream.local:3001"},
        )

        assert resp.status_code == 200
        data = resp.json()
        steps = " ".join(data["instructions"]["steps"])
        assert "dream-proxy" in steps
        assert "port 80" in steps
        assert data["instructions"]["links"][0] == {
            "label": "Open LAN entry",
            "url": "http://dashboard.dream.local:80",
        }

    def test_404_for_unknown_feature(self, test_client, monkeypatch):
        monkeypatch.setattr("routers.features.FEATURES", [])

        resp = test_client.get(
            "/api/features/nonexistent/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 404
