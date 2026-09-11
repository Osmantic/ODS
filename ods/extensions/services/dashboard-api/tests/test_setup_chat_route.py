"""Setup chat follows persisted inference settings without restarting the API."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import routers.setup as setup


@pytest.mark.parametrize("base,path,expected", [
    ("http://host.docker.internal:13305", "/api/v1", "http://host.docker.internal:13305/api/v1"),
    ("http://llama-server:8080/v1/", "/v1/", "http://llama-server:8080/v1"),
    ("http://lemonade:13305/api/v1/", "api/v1/", "http://lemonade:13305/api/v1"),
    ("http://external:1234", "v1", "http://external:1234/v1"),
    ("http://proxy:8080/custom/v2/", "custom/v2/", "http://proxy:8080/custom/v2"),
    ("", "/v1", "http://stale-container:8080/v1"),
])
def test_chat_uses_live_url_path_and_model(test_client, monkeypatch, tmp_path, base, path, expected):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("OLLAMA_URL", "http://stale-container:8080")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/stale")
    (tmp_path / ".env").write_text(f"LLM_API_URL={base}\nLLM_API_BASE_PATH={path}\nLLM_MODEL=current-model\n", encoding="utf-8")
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"choices": [{"message": {"content": "ready"}}]})
    post = MagicMock()
    post.__aenter__ = AsyncMock(return_value=response)
    post.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = post
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(setup.aiohttp, "ClientSession", lambda **kwargs: context)

    result = test_client.post("/api/chat", json={"message":"hello", "system":"Be brief"}, headers=test_client.auth_headers)
    assert result.status_code == 200
    assert result.json() == {"success": True, "response":"ready"}
    assert session.post.call_args.args == (expected + "/chat/completions",)
    assert session.post.call_args.kwargs["json"]["model"] == "current-model"

    (tmp_path / ".env").write_text("LLM_API_URL=http://replacement:8080/v1\nLLM_MODEL=replaced\n", encoding="utf-8")
    result = test_client.post("/api/chat", json={"message":"again", "system":"Be brief"}, headers=test_client.auth_headers)
    assert result.status_code == 200
    assert session.post.call_args.args == ("http://replacement:8080/v1/chat/completions",)
    assert session.post.call_args.kwargs["json"]["model"] == "replaced"
