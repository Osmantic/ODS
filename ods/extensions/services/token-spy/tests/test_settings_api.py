"""The settings API carries no poll interval, including one saved by an older version."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx
import pytest

SERVICE = Path(__file__).resolve().parents[1]
API_KEY = "settings-fixture-key"


def load(filename):
    spec = importlib.util.spec_from_file_location(f"settings_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", API_KEY)
    monkeypatch.setenv("AGENT_NAME", "local-agent")
    db = load("db.py")
    db.DB_PATH = str(tmp_path / "usage.db")
    monkeypatch.setitem(sys.modules, "db", db)
    module = load("main.py")
    module.SETTINGS_PATH = str(tmp_path / "settings.json")
    return module


def settings_request(api, method, body=None):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://token-spy",
                                     headers={"Authorization": f"Bearer {API_KEY}"}) as client:
            return await client.request(method, "/api/settings", json=body)
    response = asyncio.run(send())
    assert response.status_code == 200, response.text
    return response.json()


def saved(api):
    return json.loads(Path(api.SETTINGS_PATH).read_text(encoding="utf-8"))


def test_fresh_settings_have_no_poll_interval(api):
    settings = settings_request(api, "GET")
    assert settings["session_char_limit"] == 200_000
    assert "poll_interval_minutes" not in settings
    assert settings["agents"] == {
        "local-agent": {"session_char_limit": None, "_effective_session_char_limit": 200_000},
    }


def test_poll_interval_saved_by_an_older_version_is_dropped(api):
    Path(api.SETTINGS_PATH).write_text(json.dumps({
        "session_char_limit": 150_000,
        "poll_interval_minutes": 5,
        "agents": {
            "local-agent": {"session_char_limit": None, "poll_interval_minutes": 2},
            "other-agent": {"session_char_limit": 90_000, "poll_interval_minutes": 7},
        },
    }), encoding="utf-8")

    settings = settings_request(api, "GET")
    assert "poll_interval_minutes" not in settings
    assert settings["session_char_limit"] == 150_000
    assert settings["agents"]["local-agent"] == {
        "session_char_limit": None, "_effective_session_char_limit": 150_000,
    }
    assert settings["agents"]["other-agent"] == {
        "session_char_limit": 90_000, "_effective_session_char_limit": 90_000,
    }

    settings_request(api, "POST", {"session_char_limit": 160_000})
    assert saved(api) == {
        **api._DEFAULT_SETTINGS,
        "session_char_limit": 160_000,
        "agents": {
            "local-agent": {"session_char_limit": None},
            "other-agent": {"session_char_limit": 90_000},
        },
    }


def test_posted_poll_interval_is_not_stored(api):
    settings = settings_request(api, "POST", {
        "poll_interval_minutes": 3,
        "agents": {"local-agent": {"session_char_limit": 50_000, "poll_interval_minutes": 3}},
    })
    assert "poll_interval_minutes" not in settings
    assert settings["agents"]["local-agent"] == {
        "session_char_limit": 50_000, "_effective_session_char_limit": 50_000,
    }
    stored = saved(api)
    assert "poll_interval_minutes" not in stored
    assert stored["agents"] == {"local-agent": {"session_char_limit": 50_000}}
