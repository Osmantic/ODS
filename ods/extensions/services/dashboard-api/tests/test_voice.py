"""Tests for routers/voice.py — voice services availability verdict.

/api/voice/status is what the first-boot Success Validation card re-runs for
its "Voice I/O" check (SuccessValidation.jsx reads `result.available`), so the
verdict has to describe the voice stack ODS actually installed.
"""

from types import SimpleNamespace

import pytest

from routers.voice import voice_status


def _health(statuses):
    """check_service_health stub returning a status per service id."""

    async def _check(service_id, config, **kwargs):
        return SimpleNamespace(status=statuses[service_id])

    return _check


def _services(*service_ids):
    return {sid: {"name": sid, "port": 1234} for sid in service_ids}


@pytest.fixture()
def voice_env(monkeypatch):
    """Patch the live lookup and health probe used by voice_status."""

    def _apply(configured, statuses):
        services = _services(*configured)
        monkeypatch.setattr(
            "routers.voice.load_enabled_service_config",
            lambda service_id: services.get(service_id),
        )
        monkeypatch.setattr("helpers.check_service_health", _health(statuses))

    return _apply


class TestVoiceStatus:

    @pytest.mark.asyncio
    async def test_tts_follows_library_marker_without_api_restart(
        self, tmp_path, monkeypatch,
    ):
        import config

        for service_id in ("whisper", "tts"):
            service_dir = tmp_path / service_id
            service_dir.mkdir()
            (service_dir / "manifest.yaml").write_text(
                "schema_version: ods.services.v1\n"
                "service:\n"
                f"  id: {service_id}\n"
                f"  name: Test {service_id}\n"
                "  type: docker\n"
                "  gpu_backends: [all]\n"
                f"  default_host: {service_id}\n"
                "  port: 8880\n"
                "  compose_file: compose.yaml\n",
                encoding="utf-8",
            )
        (tmp_path / "whisper" / "compose.yaml").write_text(
            "services: {whisper: {image: test/whisper}}\n", encoding="utf-8",
        )
        selected = tmp_path / "tts" / "compose.yaml"
        disabled = tmp_path / "tts" / "compose.yaml.disabled"
        disabled.write_text("services: {tts: {image: test/kokoro}}\n", encoding="utf-8")
        monkeypatch.setattr(config, "EXTENSIONS_DIR", tmp_path)
        monkeypatch.setattr(
            "helpers.check_service_health",
            _health({"whisper": "healthy", "tts": "healthy"}),
        )

        before = await voice_status(api_key="test")
        assert before["available"] is False
        assert before["services"]["tts"]["status"] == "not_configured"

        disabled.rename(selected)
        enabled = await voice_status(api_key="test")
        assert enabled["available"] is True
        assert enabled["services"]["tts"]["status"] == "healthy"

        selected.rename(disabled)
        after = await voice_status(api_key="test")
        assert after["available"] is False
        assert after["services"]["tts"]["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_available_when_stt_and_tts_are_healthy(self, voice_env):
        """LiveKit ships uninstalled; its absence must not veto the verdict."""
        voice_env(
            configured=("whisper", "tts"),
            statuses={"whisper": "healthy", "tts": "healthy"},
        )

        result = await voice_status(api_key="test")

        assert result["available"] is True
        assert result["services"]["livekit"]["status"] == "not_configured"
        assert result["message"] == "All voice services operational"

    @pytest.mark.asyncio
    async def test_unavailable_when_a_required_service_is_down(self, voice_env):
        voice_env(
            configured=("whisper", "tts"),
            statuses={"whisper": "healthy", "tts": "down"},
        )

        result = await voice_status(api_key="test")

        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_unavailable_when_a_required_service_is_missing(self, voice_env):
        voice_env(configured=("tts",), statuses={"tts": "healthy"})

        result = await voice_status(api_key="test")

        assert result["available"] is False
        assert result["services"]["stt"]["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_installed_livekit_still_has_to_be_healthy(self, voice_env):
        """Opting in to the optional service opts in to its health too."""
        voice_env(
            configured=("whisper", "tts", "livekit"),
            statuses={"whisper": "healthy", "tts": "healthy", "livekit": "down"},
        )

        result = await voice_status(api_key="test")

        assert result["available"] is False
        assert result["services"]["livekit"]["status"] == "down"

    @pytest.mark.asyncio
    async def test_available_with_a_healthy_livekit(self, voice_env):
        voice_env(
            configured=("whisper", "tts", "livekit"),
            statuses={"whisper": "healthy", "tts": "healthy", "livekit": "healthy"},
        )

        result = await voice_status(api_key="test")

        assert result["available"] is True
