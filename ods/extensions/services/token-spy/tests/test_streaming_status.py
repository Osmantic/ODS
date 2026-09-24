"""HTTP status passthrough contracts for Token Spy streaming proxies."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


class FakeUpstream:
    status_code = 429

    def __init__(self):
        self.closed = False

    async def aiter_bytes(self):
        yield b'{"error":{"message":"rate limited"}}'

    async def aiter_lines(self):
        yield '{"error":{"message":"rate limited"}}'


    async def aclose(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.upstream = FakeUpstream()

    def build_request(self, method, path, **kwargs):
        return method, path, kwargs

    async def send(self, request, *, stream):
        assert stream is True
        return self.upstream


@pytest.fixture()
def token_spy(monkeypatch):
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "stream-status-key")
    monkeypatch.setenv("API_PROVIDER", "local")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.syspath_prepend(str(TOKEN_SPY_DIR))
    spec = importlib.util.spec_from_file_location(
        f"token_spy_stream_status_{uuid4().hex}",
        TOKEN_SPY_DIR / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.get_filter_settings = Mock(return_value={"enabled": False})
    module._log_entry = Mock()
    return module


@pytest.mark.parametrize(
    ("path", "body", "client_getter"),
    [
        (
            "/v1/messages",
            {"model": "test-model", "messages": [], "stream": True},
            "get_http_client",
        ),
        (
            "/v1/chat/completions",
            {"model": "test-model", "messages": [], "stream": True},
            "get_moonshot_client",
        ),
    ],
)
def test_streaming_proxy_preserves_upstream_error_status(
    token_spy, path, body, client_getter
):
    upstream_client = FakeClient()
    setattr(token_spy, client_getter, Mock(return_value=upstream_client))
    client = TestClient(token_spy.app)

    response = client.post(
        path,
        json=body,
        headers={"Authorization": "Bearer stream-status-key"},
    )

    assert response.status_code == 429
    assert "rate limited" in response.text
    assert upstream_client.upstream.closed is True
