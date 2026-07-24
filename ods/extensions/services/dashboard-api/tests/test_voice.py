"""Tests for voice status endpoints."""

from types import SimpleNamespace


def test_voice_status_available_when_required_services_healthy_without_livekit(
    test_client,
    monkeypatch,
):
    """LiveKit is optional and must not make healthy STT/TTS unavailable."""
    import config
    import helpers

    monkeypatch.setattr(
        config,
        "SERVICES",
        {
            "whisper": {"name": "Whisper", "port": 8000},
            "tts": {"name": "Kokoro", "port": 8880},
        },
    )

    async def healthy(service_id, cfg):
        return SimpleNamespace(status="healthy")

    monkeypatch.setattr(helpers, "check_service_health", healthy)

    resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["available"] is True
    assert payload["services"]["stt"]["status"] == "healthy"
    assert payload["services"]["tts"]["status"] == "healthy"
    assert payload["services"]["livekit"]["status"] == "not_configured"
    assert payload["message"] == "All voice services operational"


def test_voice_status_unavailable_when_configured_livekit_is_unhealthy(
    test_client,
    monkeypatch,
):
    """A configured LiveKit service still participates in health aggregation."""
    import config
    import helpers

    monkeypatch.setattr(
        config,
        "SERVICES",
        {
            "whisper": {"name": "Whisper", "port": 8000},
            "tts": {"name": "Kokoro", "port": 8880},
            "livekit": {"name": "LiveKit", "port": 7880},
        },
    )

    async def health(service_id, cfg):
        status = "unhealthy" if service_id == "livekit" else "healthy"
        return SimpleNamespace(status=status)

    monkeypatch.setattr(helpers, "check_service_health", health)

    resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["available"] is False
    assert payload["services"]["stt"]["status"] == "healthy"
    assert payload["services"]["tts"]["status"] == "healthy"
    assert payload["services"]["livekit"]["status"] == "unhealthy"
    assert payload["message"] == "Some voice services unavailable"
