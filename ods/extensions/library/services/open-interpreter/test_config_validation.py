"""Tests for LLM_API_URL validation and what /health discloses."""

import importlib
import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# server.py calls DATA_DIR.mkdir() at import time against the hardcoded
# container path /app/data, which does not exist outside the container.
with patch.object(pathlib.Path, "mkdir"):
    import server


@pytest.fixture
def client():
    return TestClient(server.app)


def reload_with(url):
    """Re-import server with LLM_API_URL set, as the container would at boot."""
    with patch.dict("os.environ", {"LLM_API_URL": url}), \
         patch.object(pathlib.Path, "mkdir"):
        return importlib.reload(server)


# --- accepted configurations ------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://llama-server:8000",
    "https://llama-server:8000",
    "http://127.0.0.1:8080",
    "http://llama-server:8000/v1",
    "https://example.com",
])
def test_accepts_valid_urls(url):
    assert server._validated_llm_api_url(url) == url


def test_boot_uses_the_configured_url():
    module = reload_with("http://llama-server:9999/v1")
    try:
        assert module.LLM_API_URL == "http://llama-server:9999/v1"
    finally:
        reload_with("http://localhost:8000")


# --- rejected configurations ------------------------------------------------

SCHEME_ERROR = "http:// or https://"
HOST_ERROR = "must include a host"


@pytest.mark.parametrize("url, expected", [
    ("llama-server:8000", SCHEME_ERROR),    # missing scheme
    ("//llama-server:8000", SCHEME_ERROR),  # protocol-relative
    ("htp://llama-server", SCHEME_ERROR),   # typo
    ("file:///etc/passwd", SCHEME_ERROR),
    ("ftp://llama-server", SCHEME_ERROR),
    ("", SCHEME_ERROR),
    ("http://", HOST_ERROR),                # scheme but no host
])
def test_rejects_malformed_urls(url, expected):
    with pytest.raises(ValueError) as exc:
        server._validated_llm_api_url(url)

    message = str(exc.value)
    assert expected in message
    assert "LLM_API_URL" in message  # the message must name the setting to fix


def test_a_bad_url_stops_the_container_at_boot():
    """The failure must happen at import, not on the first request."""
    with pytest.raises(ValueError):
        reload_with("not-a-url")
    reload_with("http://localhost:8000")


# --- what /health discloses -------------------------------------------------

def test_health_does_not_disclose_the_backend_url(client):
    body = client.get("/health").json()

    assert "llm_url" not in body
    assert not any("localhost" in str(v) for v in body.values())


def test_health_would_not_leak_url_credentials():
    module = reload_with("http://admin:hunter2@llama-server:8000")
    try:
        body = TestClient(module.app).get("/health").json()
        assert "hunter2" not in str(body)
    finally:
        reload_with("http://localhost:8000")


def test_health_still_reports_liveness(client):
    """The compose healthcheck only needs a 200."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_stays_unauthenticated(client):
    """Regression guard: the container healthcheck sends no credentials."""
    assert client.get("/health").status_code == 200


def test_chat_routes_remain_authenticated(client):
    for route in ("/chat", "/chat/stream"):
        assert client.post(route, json={"message": "hi"}).status_code == 403
