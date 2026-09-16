"""Tests for routers/service_tests.py — the /api/test/* feature probes (#4174).

SuccessValidation.jsx and ods/tests/test-phase-c-p1.sh both fetch
/api/test/{llm,voice,rag,workflows}. Those routes did not exist, so the
dashboard's "Run live tests" reported failure on a healthy install.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from routers.service_tests import FEATURE_SERVICES, _probe_feature

FEATURES = ("llm", "voice", "rag", "workflows")
AUTH = {"Authorization": "Bearer test-key-12345"}


@pytest.fixture()
def client():
    import main as main_module
    return TestClient(main_module.app)


def _health(status):
    """A stand-in for check_service_health's return value."""
    class _Result:
        def __init__(self, s):
            self.status = s
    return _Result(status)


class TestRoutesExist:
    """The four routes the frontend and the shell contract expect."""

    @pytest.mark.parametrize("feature", FEATURES)
    def test_route_is_registered(self, client, feature):
        # Unauthenticated is fine here: anything but 404 proves the route
        # exists, which is exactly what #4174 was about.
        resp = client.get(f"/api/test/{feature}")
        assert resp.status_code != 404, f"/api/test/{feature} is missing"

    @pytest.mark.parametrize("feature", FEATURES)
    def test_route_requires_auth(self, client, feature):
        resp = client.get(f"/api/test/{feature}")
        assert resp.status_code in (401, 403), (
            f"/api/test/{feature} must be authenticated, got {resp.status_code}"
        )


class TestProbeVerdict:
    """_probe_feature's contract, which the dashboard reads directly."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("feature", FEATURES)
    async def test_all_healthy_is_success(self, feature):
        services = {sid: {"host": "h", "port": 1} for sid in FEATURE_SERVICES[feature]}
        with patch("config.SERVICES", services), \
             patch("helpers.check_service_health", AsyncMock(return_value=_health("healthy"))):
            result = await _probe_feature(feature)

        assert result["success"] is True
        # The dashboard reads `result.success ?? result.available`; both must agree.
        assert result["available"] is True
        assert result["error"] is None
        assert result["feature"] == feature

    @pytest.mark.asyncio
    async def test_unhealthy_service_fails_and_names_it(self):
        services = {"qdrant": {"host": "h", "port": 1}}
        with patch("config.SERVICES", services), \
             patch("helpers.check_service_health", AsyncMock(return_value=_health("stopped"))):
            result = await _probe_feature("rag")

        assert result["success"] is False
        assert result["available"] is False
        assert "qdrant" in result["error"]
        assert "stopped" in result["error"]

    @pytest.mark.asyncio
    async def test_absent_service_reports_not_installed(self):
        with patch("config.SERVICES", {}), \
             patch("helpers.check_service_health", AsyncMock(return_value=_health("healthy"))):
            result = await _probe_feature("workflows")

        assert result["success"] is False
        assert result["services"]["n8n"] == "not_configured"
        assert "not installed" in result["error"]

    @pytest.mark.asyncio
    async def test_voice_needs_both_stt_and_tts(self):
        """One healthy half of the voice stack is not a working voice feature."""
        services = {"whisper": {"host": "h", "port": 1}, "tts": {"host": "h", "port": 2}}

        async def _per_service(service_id, _config):
            return _health("healthy" if service_id == "whisper" else "stopped")

        with patch("config.SERVICES", services), \
             patch("helpers.check_service_health", AsyncMock(side_effect=_per_service)):
            result = await _probe_feature("voice")

        assert result["success"] is False
        assert result["services"]["whisper"] == "healthy"
        assert "tts" in result["error"]

    @pytest.mark.asyncio
    async def test_probe_failure_is_reported_not_raised(self):
        """An unreachable service is a verdict, not a 500."""
        services = {"llama-server": {"host": "h", "port": 1}}
        with patch("config.SERVICES", services), \
             patch("helpers.check_service_health", AsyncMock(side_effect=OSError("refused"))):
            result = await _probe_feature("llm")

        assert result["success"] is False
        assert result["services"]["llama-server"] == "unavailable"


class TestFeatureMapping:
    """The mapping the frontend's own service names imply."""

    def test_mapping_matches_frontend_expectations(self):
        assert FEATURE_SERVICES["llm"] == ("llama-server",)
        assert FEATURE_SERVICES["rag"] == ("qdrant",)
        assert FEATURE_SERVICES["workflows"] == ("n8n",)
        assert set(FEATURE_SERVICES["voice"]) == {"whisper", "tts"}
