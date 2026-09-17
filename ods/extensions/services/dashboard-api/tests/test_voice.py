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
    """Patch the helpers/config symbols voice_status imports at call time."""

    def _apply(configured, statuses):
        monkeypatch.setattr("config.SERVICES", _services(*configured))
        monkeypatch.setattr("helpers.check_service_health", _health(statuses))

    return _apply


class TestVoiceStatus:

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
