"""The production poller must reset the local history it actually assessed."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def local_agent(monkeypatch, tmp_path):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "local-reset-fixture-key")
    spec = importlib.util.spec_from_file_location("token_spy_reset_target", service / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "AGENT_NAME", "proxy-agent")
    monkeypatch.setattr(module, "REMOTE_AGENTS", {})
    monkeypatch.setattr(module, "AGENT_SESSION_DIRS", {"local-agent": str(tmp_path)})
    monkeypatch.setattr(module, "get_agent_setting", lambda *_: 100)
    monkeypatch.setattr(module, "_last_auto_reset", {})
    for name, chars, timestamp in [("older-large", 1000, 100), ("current", 200, 200)]:
        path = tmp_path / f"{name}.jsonl"
        path.write_text(json.dumps({"type": "message", "message": {
            "role": "user", "content": "x" * chars}}) + "\n")
        os.utime(path, (timestamp, timestamp))
    (tmp_path / "sessions.json").write_text(json.dumps({
        "old": {"sessionId": "older-large"}, "new": {"sessionId": "current"}}))
    return module, tmp_path


def one_poll(module, monkeypatch):
    calls = 0

    async def pause(_delay):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "sleep", pause)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module._poll_remote_agents())


def test_local_poll_resets_newest_assessed_history(local_agent, monkeypatch):
    module, directory = local_agent
    one_poll(module, monkeypatch)
    assert (directory / "older-large.jsonl").exists()
    assert not (directory / "current.jsonl").exists()
    assert json.loads((directory / "sessions.json").read_text()) == {
        "old": {"sessionId": "older-large"}}
    assert "local-agent" in module._last_auto_reset


def test_disappearing_assessed_history_never_retargets_another_file(local_agent, monkeypatch):
    module, directory = local_agent
    read_status = module._get_local_session_status

    def disappear(*args, **kwargs):
        status = read_status(*args, **kwargs)
        (directory / "current.jsonl").unlink()
        return status

    monkeypatch.setattr(module, "_get_local_session_status", disappear)
    one_poll(module, monkeypatch)
    assert (directory / "older-large.jsonl").exists()
    assert "local-agent" not in module._last_auto_reset


def test_public_status_does_not_add_internal_reset_selection(local_agent):
    module, _ = local_agent
    assert not any(key.startswith("_") for key in module._get_local_session_status("local-agent"))


def test_manual_reset_keeps_largest_session_behavior(local_agent, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    module, directory = local_agent
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="older-large.jsonl\ncurrent.jsonl\nsessions.json\n"))
    result = module._kill_session("local-agent", reason="dashboard")
    assert result["session_id"] == "older-large"
    assert (directory / "current.jsonl").exists()
    assert not (directory / "older-large.jsonl").exists()
