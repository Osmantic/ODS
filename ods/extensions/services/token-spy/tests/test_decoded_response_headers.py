"""Response-header contracts for decoded Token Spy proxy bodies."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent
DECODED_BODY = b'{"ok":true}'


class FakeResponse:
    content = DECODED_BODY
    status_code = 200
    headers = {
        "content-encoding": "gzip",
        "content-length": "99",
        "content-type": "application/json",
        "connection": "keep-alive",
        "transfer-encoding": "chunked",
        "x-upstream-request-id": "req-123",
    }

    def json(self):
        return {}


class FakeClient:
    async def request(self, *args, **kwargs):
        return FakeResponse()


@pytest.fixture()
def token_spy(monkeypatch):
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "decoded-headers-key")
    monkeypatch.setenv("API_PROVIDER", "local")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.syspath_prepend(str(TOKEN_SPY_DIR))
    spec = importlib.util.spec_from_file_location(
        f"token_spy_decoded_headers_{uuid4().hex}",
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
            {"model": "test-model", "messages": [], "stream": False},
            "get_http_client",
        ),
        (
            "/v1/chat/completions",
            {"model": "test-model", "messages": [], "stream": False},
            "get_moonshot_client",
        ),
        ("/v1/models", None, "get_moonshot_client"),
    ],
)
def test_decoded_body_drops_stale_representation_headers(
    token_spy, path, body, client_getter
):
    setattr(token_spy, client_getter, Mock(return_value=FakeClient()))
    client = TestClient(token_spy.app)
    headers = {"Authorization": "Bearer decoded-headers-key"}
    if body is None:
        response = client.get(path, headers=headers)
    else:
        response = client.post(path, json=body, headers=headers)

    assert response.status_code == 200
    assert response.content == DECODED_BODY
    assert "content-encoding" not in response.headers
    assert "connection" not in response.headers
    assert "transfer-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(DECODED_BODY))
    assert response.headers["x-upstream-request-id"] == "req-123"
