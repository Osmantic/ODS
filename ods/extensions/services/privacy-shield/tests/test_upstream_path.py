"""Upstream URL joining for the HTTP and WebSocket proxy lanes.

The shipped Compose file sets ``TARGET_API_URL=${LLM_API_URL}/v1`` — an
OpenAI-style base that already carries the API version. Clients address the
shield like any OpenAI endpoint (``POST /v1/chat/completions``, per the
README), so naively appending the client path sent
``/v1/v1/chat/completions`` upstream and every documented request 404'd.
"""

import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_KEY = "test-shield-key-abcdef0123456789"
os.environ["SHIELD_API_KEY"] = TEST_KEY
os.environ.setdefault("PII_CACHE_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

import proxy  # noqa: E402

AUTH = {"Authorization": f"Bearer {TEST_KEY}"}


@pytest.mark.parametrize(
    ("base", "path", "expected"),
    [
        # Shipped Compose base + documented OpenAI-style client path.
        ("http://llama-server:8080/v1", "v1/chat/completions",
         "http://llama-server:8080/v1/chat/completions"),
        # Clients that already omit the version keep working.
        ("http://llama-server:8080/v1", "chat/completions",
         "http://llama-server:8080/v1/chat/completions"),
        ("http://llama-server:8080/v1/", "v1/models",
         "http://llama-server:8080/v1/models"),
        ("http://llama-server:8080/v1", "v1",
         "http://llama-server:8080/v1"),
        # A versioned base under another prefix (Lemonade's /api/v1).
        ("http://llama-server:8080/api/v1", "v1/chat/completions",
         "http://llama-server:8080/api/v1/chat/completions"),
        # An unversioned base is a transparent origin: pass the path through.
        ("http://127.0.0.1:9000", "v1/chat/completions",
         "http://127.0.0.1:9000/v1/chat/completions"),
        # Only a whole leading "v1" segment is a version, not a prefix match.
        ("http://llama-server:8080/v1", "v1beta/models",
         "http://llama-server:8080/v1/v1beta/models"),
    ],
)
def test_upstream_url_joins_version_once(monkeypatch, base, path, expected):
    monkeypatch.setattr(proxy, "TARGET_API_BASE", base)
    assert proxy._upstream_url(path) == expected


def test_documented_client_request_reaches_upstream_once(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(proxy, "TARGET_API_BASE", "http://llama-server:8080/v1")
    monkeypatch.setattr(
        proxy, "http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = TestClient(proxy.app).post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert seen["url"] == "http://llama-server:8080/v1/chat/completions"
