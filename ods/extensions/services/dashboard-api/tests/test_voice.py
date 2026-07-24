"""Tests for routers/voice.py — the voice services status endpoint (stub).

The endpoint fans out over the STT (whisper), TTS and optional LiveKit
services using the shared ``check_service_health`` infrastructure, then
aggregates the per-service statuses into a single ``available`` flag.

Mocked surfaces:
  * helpers.check_service_health — stand-in for the live health probe.
  * config.SERVICES — the service registry the endpoint reads.

Both are imported *inside* the request handler, so the patches target the
source modules (``helpers`` / ``config``) rather than the router module.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_voice_status_requires_auth(test_client):
    resp = test_client.get("/api/voice/status")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _health(status):
    """Minimal stand-in for a ServiceStatus — the router only reads .status."""
    return SimpleNamespace(status=status)


def _patch_services(monkeypatch, present, health_map=None, raise_for=None):
    """Wire config.SERVICES and helpers.check_service_health.

    *present* is the set of service keys that exist in SERVICES.
    *health_map* maps a service key to the status string it reports.
    *raise_for* is a set of service keys whose health probe raises.
    """
    import config
    import helpers

    health_map = health_map or {}
    raise_for = raise_for or set()

    services = {key: {"name": key, "port": 1} for key in present}
    monkeypatch.setattr(config, "SERVICES", services)

    async def fake_health(key, cfg):
        if key in raise_for:
            raise RuntimeError("probe blew up")
        return _health(health_map.get(key, "healthy"))

    monkeypatch.setattr(helpers, "check_service_health", AsyncMock(side_effect=fake_health))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_all_services_healthy(test_client, monkeypatch):
    _patch_services(monkeypatch, present={"whisper", "tts", "livekit"})

    resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["services"]["stt"]["status"] == "healthy"
    assert body["services"]["tts"]["status"] == "healthy"
    assert body["services"]["livekit"]["status"] == "healthy"
    assert body["message"] == "All voice services operational"


def test_one_service_unhealthy_flips_available(test_client, monkeypatch):
    _patch_services(
        monkeypatch,
        present={"whisper", "tts", "livekit"},
        health_map={"tts": "down"},
    )

    resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["services"]["tts"]["status"] == "down"
    assert body["message"] == "Some voice services unavailable"


def test_missing_service_is_not_configured(test_client, monkeypatch):
    # whisper present, tts absent, livekit absent
    _patch_services(monkeypatch, present={"whisper"})

    resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["services"]["stt"]["status"] == "healthy"
    assert body["services"]["tts"]["status"] == "not_configured"
    assert body["services"]["livekit"]["status"] == "not_configured"
    # not_configured is not healthy → overall unavailable
    assert body["available"] is False


def test_health_probe_failure_reports_unavailable(test_client, monkeypatch):
    # whisper probe raises → endpoint stays up and reports "unavailable"
    _patch_services(
        monkeypatch,
        present={"whisper", "tts"},
        raise_for={"whisper"},
    )

    resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["services"]["stt"]["status"] == "unavailable"
    assert body["services"]["tts"]["status"] == "healthy"
    assert body["available"] is False


def test_livekit_probe_failure_reports_unavailable(test_client, monkeypatch):
    _patch_services(
        monkeypatch,
        present={"whisper", "tts", "livekit"},
        raise_for={"livekit"},
    )

    resp = test_client.get("/api/voice/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["services"]["livekit"]["status"] == "unavailable"
    assert body["available"] is False
