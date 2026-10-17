"""The shipped Compose environment must proxy to the local runtime.

compose.yaml used to set only ``OLLAMA_URL``, which Token Spy never reads, so
a default install fell through to ``API_PROVIDER=anthropic`` and forwarded
``/v1/chat/completions`` to https://api.anthropic.com instead of llama-server.
"""
import importlib.util
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

SERVICE = Path(__file__).resolve().parents[1]
API_KEY = "compose-fixture-key"
_DEFAULTED = re.compile(r"\$\{[A-Z0-9_]+:-([^}]*)\}")


def compose_environment():
    """Environment a default install gets: every ``${VAR:-x}`` resolves to x."""
    text = (SERVICE / "compose.yaml").read_text(encoding="utf-8")
    block = text.split("environment:", 1)[1].split("deploy:", 1)[0]
    env = {}
    for line in block.splitlines():
        match = re.match(r"\s*-\s*([A-Z0-9_]+)=(.*)$", line)
        if match:
            env[match.group(1)] = _DEFAULTED.sub(r"\1", match.group(2)).strip()
    return env


def load(filename):
    spec = importlib.util.spec_from_file_location(f"compose_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(tmp_path, monkeypatch):
    for key in ("API_PROVIDER", "UPSTREAM_BASE_URL", "UPSTREAM_API_KEY",
                "OPENAI_UPSTREAM", "ANTHROPIC_UPSTREAM", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in compose_environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TOKEN_SPY_API_KEY", API_KEY)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.syspath_prepend(str(SERVICE))
    db = load("db.py")
    db.DB_PATH = str(tmp_path / "usage.db")
    monkeypatch.setitem(sys.modules, "db", db)
    db.init_db()
    module = load("main.py")
    module.SETTINGS_PATH = str(tmp_path / "settings.json")
    yield module
    if getattr(db._local, "conn", None) is not None:
        db._local.conn.close()


def test_compose_environment_selects_local_openai_upstream(api):
    assert api.API_PROVIDER == "local"
    assert api.OPENAI_UPSTREAM == "http://llama-server:8080"
    assert api._uses_openai_upstream()
    assert api._provider_uses_local_runtime(api.API_PROVIDER)


def test_compose_chat_completion_reaches_llama_server_without_upstream_key(api):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        })

    api._openai_client = httpx.AsyncClient(
        base_url=api.OPENAI_UPSTREAM, transport=httpx.MockTransport(handler),
    )

    response = TestClient(api.app).post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        content=json.dumps({"model": "local", "messages": [{"role": "user", "content": "hi"}]}),
    )

    assert response.status_code == 200, response.text
    assert seen["url"] == "http://llama-server:8080/v1/chat/completions"
    # Token Spy's own client credential must never be forwarded upstream.
    assert seen["authorization"] is None
