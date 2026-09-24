import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOKEN_SPY_DIR))

import main  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, "TOKEN_SPY_API_KEY", "test-token-spy-key")

    def unexpected_client():
        raise AssertionError("invalid request reached the upstream client")

    monkeypatch.setattr(main, "get_http_client", unexpected_client)
    monkeypatch.setattr(main, "get_moonshot_client", unexpected_client)
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize("payload", [[], "text", 42, None])
def test_anthropic_proxy_rejects_non_object_json(client, payload):
    response = client.post(
        "/v1/messages",
        headers={
            "Authorization": "Bearer test-token-spy-key",
            "Content-Type": "application/json",
        },
        content=json.dumps(payload),
    )

    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Request body must be a JSON object",
        },
    }


@pytest.mark.parametrize("payload", [[], "text", 42, None])
def test_openai_proxy_rejects_non_object_json(client, payload):
    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer test-token-spy-key",
            "Content-Type": "application/json",
        },
        content=json.dumps(payload),
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "message": "Request body must be a JSON object",
            "type": "invalid_request_error",
            "param": None,
            "code": None,
        },
    }
