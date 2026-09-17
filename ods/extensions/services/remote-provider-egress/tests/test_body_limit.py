"""ASGI-boundary tests for remote-provider egress request limits."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from starlette.requests import Request


SERVICE_DIR = Path(__file__).resolve().parents[1]
ODS_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SERVICE_DIR))
sys.path.insert(0, str(ODS_ROOT / "bin"))

from app import main as app_main  # noqa: E402


def _request(chunks: list[bytes], content_length: int | None = None):
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if chunks:
            body = chunks.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": headers,
    }
    return Request(scope, receive), lambda: calls


def _patch_route_boundary(monkeypatch) -> None:
    monkeypatch.setattr(app_main, "_load_route", lambda: {"transport": "direct"})
    monkeypatch.setattr(
        app_main,
        "validate_direct_provider_resolution",
        lambda route: ["203.0.113.10"],
    )
    monkeypatch.setattr(app_main, "read_provider_secret", lambda path: "provider-key")


def test_content_length_rejected_before_body_read(monkeypatch):
    _patch_route_boundary(monkeypatch)
    monkeypatch.setattr(app_main, "MAX_BODY_BYTES", 4)
    request, receive_calls = _request([b"12345"], content_length=5)

    response = asyncio.run(app_main.forward("v1/chat/completions", request))

    assert response.status_code == 413
    assert json.loads(response.body)["error"]["type"] == "payload_too_large"
    assert receive_calls() == 0


def test_chunked_body_stops_when_limit_is_crossed(monkeypatch):
    _patch_route_boundary(monkeypatch)
    monkeypatch.setattr(app_main, "MAX_BODY_BYTES", 4)
    request, receive_calls = _request([b"123", b"45", b"unread"])

    response = asyncio.run(app_main.forward("v1/chat/completions", request))

    assert response.status_code == 413
    assert json.loads(response.body)["error"]["type"] == "payload_too_large"
    assert receive_calls() == 2
