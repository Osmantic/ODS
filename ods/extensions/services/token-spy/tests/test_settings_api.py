"""Authenticated settings API contract tests."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent
if str(TOKEN_SPY_DIR) not in sys.path:
    sys.path.insert(0, str(TOKEN_SPY_DIR))
os.environ.setdefault("TOKEN_SPY_API_KEY", "settings-test-token")

import main as token_spy  # noqa: E402


@pytest.fixture()
def settings_client(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    baseline = copy.deepcopy(token_spy._DEFAULT_SETTINGS)
    baseline["agents"] = {"test-agent": {
        "session_char_limit": None,
        "poll_interval_minutes": None,
    }}
    settings_path.write_text(json.dumps(baseline), encoding="utf-8")
    monkeypatch.setattr(token_spy, "SETTINGS_PATH", str(settings_path))
    monkeypatch.setattr(token_spy, "TOKEN_SPY_API_KEY", "settings-test-token")
    timer_calls = []
    monkeypatch.setattr(token_spy, "_update_timer_interval", timer_calls.append)
    client = TestClient(token_spy.app, raise_server_exceptions=False)
    yield client, settings_path, baseline, timer_calls
    client.close()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON object"),
        ({"session_char_limit": "200000"}, "must be an integer"),
        ({"poll_interval_minutes": True}, "must be an integer"),
        ({"agents": []}, "agents must be a JSON object"),
        ({"agents": {"test-agent": {"session_char_limit": 9999}}}, "must be >= 10000"),
        ({"agents": {"test-agent": {"poll_interval_minutes": 61}}}, "must be 1-60"),
    ],
)
def test_invalid_settings_payloads_return_400_without_persisting(
    settings_client, payload, message,
):
    client, settings_path, baseline, timer_calls = settings_client

    response = client.post(
        "/api/settings",
        json=payload,
        headers={"Authorization": "Bearer settings-test-token"},
    )

    assert response.status_code == 400
    assert message in response.json()["error"]
    assert json.loads(settings_path.read_text(encoding="utf-8")) == baseline
    assert timer_calls == []


def test_valid_integer_settings_persist_and_refresh_the_timer(settings_client):
    client, settings_path, _, timer_calls = settings_client

    response = client.post(
        "/api/settings",
        json={
            "session_char_limit": 250000,
            "poll_interval_minutes": 7,
            "agents": {"test-agent": {"session_char_limit": None, "poll_interval_minutes": 3}},
        },
        headers={"Authorization": "Bearer settings-test-token"},
    )

    assert response.status_code == 200
    persisted = json.loads(settings_path.read_text(encoding="utf-8"))
    assert persisted["session_char_limit"] == 250000
    assert persisted["poll_interval_minutes"] == 7
    assert persisted["agents"]["test-agent"]["poll_interval_minutes"] == 3
    assert timer_calls == [7]
