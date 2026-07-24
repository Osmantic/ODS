"""Tests for routers/voice.py — voice status endpoints."""

from unittest.mock import patch
from dataclasses import dataclass


@dataclass
class FakeHealth:
    status: str


def test_voice_status_requires_auth(test_client):
    resp = test_client.get("/api/voice/status")
    assert resp.status_code == 401


def test_voice_status_handles_dict_or_missing_status(test_client, monkeypatch):
    monkeypatch.setattr("config.SERVICES", {"whisper": {"host": "localhost"}})

    async def fake_health(svc_key, cfg):
        return {"status": "healthy"}

    with patch("helpers.check_service_health", side_effect=fake_health):
        resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["services"]["stt"]["status"] == "healthy"
