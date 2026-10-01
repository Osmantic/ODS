"""Recovery contracts for Token Spy's persisted dynamic settings."""

from __future__ import annotations

import importlib
import json
import os

import pytest

os.environ.setdefault("TOKEN_SPY_API_KEY", "token-spy-test-key")

main = importlib.import_module("main")


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(main, "SETTINGS_PATH", str(path))
    monkeypatch.setattr(main, "AGENT_NAME", "test-agent")
    return path


@pytest.mark.parametrize("document", [None, [], "settings"])
def test_non_object_settings_fall_back_to_fresh_defaults(settings_file, document):
    settings_file.write_text(json.dumps(document), encoding="utf-8")

    settings = main.load_settings()

    assert settings["session_char_limit"] == 200_000
    assert settings["poll_interval_minutes"] == 5
    assert isinstance(settings["filters"], dict)
    assert settings["agents"]["test-agent"]["session_char_limit"] is None


@pytest.mark.parametrize(
    "document",
    [
        {"agents": []},
        {"agents": {"test-agent": "invalid"}},
        {"filters": []},
        {"filters": {"tools": []}},
        {"agents": {"test-agent": {"filters": ["invalid"]}}},
    ],
)
def test_invalid_settings_containers_are_repaired(settings_file, document):
    settings_file.write_text(json.dumps(document), encoding="utf-8")

    settings = main.load_settings()

    assert isinstance(settings["agents"], dict)
    assert isinstance(settings["agents"]["test-agent"], dict)
    assert isinstance(settings["filters"], dict)
    assert isinstance(main.get_filter_settings("test-agent"), dict)


def test_invalid_numeric_settings_fall_back_safely(settings_file):
    settings_file.write_text(
        json.dumps({
            "session_char_limit": "many",
            "poll_interval_minutes": 0,
            "agents": {
                "test-agent": {
                    "session_char_limit": 1,
                    "poll_interval_minutes": 61,
                }
            },
        }),
        encoding="utf-8",
    )

    settings = main.load_settings()

    assert settings["session_char_limit"] == 200_000
    assert settings["poll_interval_minutes"] == 5
    assert settings["agents"]["test-agent"]["session_char_limit"] is None
    assert settings["agents"]["test-agent"]["poll_interval_minutes"] is None


def test_fallback_settings_do_not_mutate_process_defaults(settings_file):
    first = main.load_settings()
    first["filters"]["tools"]["mode"] = "blocklist"
    first["filters"]["tools"]["allowlist"].append("mutated")
    first["agents"]["test-agent"]["session_char_limit"] = 10_000

    second = main.load_settings()

    assert second["filters"]["tools"]["mode"] == "allowlist"
    assert "mutated" not in second["filters"]["tools"]["allowlist"]
    assert second["agents"]["test-agent"]["session_char_limit"] is None
