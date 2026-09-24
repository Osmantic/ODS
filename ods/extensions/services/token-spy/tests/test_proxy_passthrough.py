"""HTTP-boundary contracts for Token Spy's authenticated catch-all proxy."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault("TOKEN_SPY_API_KEY", "token-spy-test-key")
sys.path.insert(0, str(TOKEN_SPY_DIR))

spec = importlib.util.spec_from_file_location("token_spy_proxy_test_app", TOKEN_SPY_DIR / "main.py")
token_spy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(token_spy)


class RecordingClient:
    def __init__(self) -> None:
        self.url = ""

    async def request(self, *, url: str, **_kwargs) -> httpx.Response:
        self.url = url
        return httpx.Response(200, json={"ok": True})


def test_catch_all_preserves_encoded_and_repeated_query_parameters(monkeypatch):
    upstream = RecordingClient()
    monkeypatch.setattr(token_spy, "_uses_openai_upstream", lambda: True)
    monkeypatch.setattr(token_spy, "get_moonshot_client", lambda: upstream)

    client = TestClient(token_spy.app)
    response = client.get(
        "/v1/models?trace=a%2Fb&trace=second",
        headers={"Authorization": "Bearer token-spy-test-key"},
    )

    assert response.status_code == 200
    assert upstream.url == "/v1/models?trace=a%2Fb&trace=second"
