"""Probe the real HTTP health boundary through the authenticated voice route."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, BrokenBarrierError, Thread
from unittest.mock import AsyncMock

import aiohttp
from fastapi import FastAPI
import httpx
import pytest

from routers import voice
from security import DASHBOARD_API_KEY


@pytest.mark.asyncio
@pytest.mark.parametrize("with_livekit", [False, True])
async def test_voice_status_overlaps_configured_http_probes(monkeypatch, with_livekit):
    services = ["whisper", "tts"] + (["livekit"] if with_livekit else [])
    rendezvous = Barrier(len(services))
    missed = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                rendezvous.wait(timeout=2)
            except BrokenBarrierError:
                missed.append(self.path)
            self.send_response(503 if self.path == "/livekit" else 200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("config.SERVICES", {
        name: {"name": name, "host": "127.0.0.1", "port": server.server_port, "health": f"/{name}"}
        for name in services
    })
    app = FastAPI()
    app.include_router(voice.router)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/voice/status", headers={
                    "Authorization": f"Bearer {DASHBOARD_API_KEY}"
                })
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status_code == 200
    assert missed == [], "Configured voice services were probed sequentially"
    assert response.json()["available"] is (not with_livekit)
    assert response.json()["services"] == {
        "stt": {"status": "healthy"},
        "tts": {"status": "healthy"},
        "livekit": {"status": "unhealthy" if with_livekit else "not_configured"},
    }


@pytest.mark.asyncio
async def test_failed_probe_preserves_other_service_verdicts(monkeypatch, caplog):
    from types import SimpleNamespace

    async def check(service, _config):
        if service == "whisper":
            raise RuntimeError("Probe fixture failure")
        return SimpleNamespace(status="healthy")

    monkeypatch.setattr("config.SERVICES", {"whisper": {"name": "STT"}, "tts": {"name": "TTS"}})
    monkeypatch.setattr("helpers.check_service_health", check)
    result = await voice.voice_status(api_key="test")
    assert result["available"] is False
    assert result["services"]["stt"]["status"] == "unavailable"
    assert result["services"]["tts"]["status"] == "healthy"
    assert "Health check failed for whisper" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_request_cancels_all_active_probes(monkeypatch):
    import asyncio

    ready = asyncio.Event()
    started, finished = set(), set()

    async def check(service, _config):
        started.add(service)
        if len(started) == 2:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.add(service)

    monkeypatch.setattr("config.SERVICES", {"whisper": {"name": "STT"}, "tts": {"name": "TTS"}})
    monkeypatch.setattr("helpers.check_service_health", check)
    request = asyncio.create_task(voice.voice_status(api_key="test"))
    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
    finally:
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    assert finished == {"whisper", "tts"}
