"""Authenticated summary counts retain history without recounting surviving files."""
import builtins
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def append_turns(path, count, role="user"):
    with path.open("a") as stream:
        for _ in range(count):
            stream.write(json.dumps({"type": "message", "message": {
                "role": role, "content": "fixture turn"}}) + "\n")


@pytest.fixture
def summary(monkeypatch, tmp_path):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "accumulator-fixture-key")
    spec = importlib.util.spec_from_file_location("token_spy_accumulator", service / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    directory = tmp_path / "sessions"
    directory.mkdir()
    monkeypatch.setattr(module, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setattr(module, "AGENT_SESSION_DIRS", {"local-agent": str(directory)})
    monkeypatch.setattr(module, "LOCAL_MODEL_AGENTS", {"local-agent"})
    monkeypatch.setattr(module, "_db_available", False)
    monkeypatch.setattr(module, "get_agent_setting", lambda *_: 200_000)
    client = TestClient(module.app)

    def read():
        response = client.get("/api/summary", headers={"Authorization": "Bearer accumulator-fixture-key"})
        assert response.status_code == 200
        rows = response.json()
        return next(row["turns"] for row in rows if row["agent"] == "local-agent")

    try:
        yield read, directory, tmp_path / "data" / "local-agent-accumulated-turns.json"
    finally:
        client.close()


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_partial_purge_never_recounts_surviving_turns(summary, role):
    read, directory, _ = summary
    append_turns(directory / "old.jsonl", 2, role)
    append_turns(directory / "current.jsonl", 3, role)
    assert read() == 5
    (directory / "old.jsonl").unlink()
    assert read() == 5
    append_turns(directory / "current.jsonl", 1, role)
    assert read() == 6
    append_turns(directory / "new.jsonl", 4, role)
    assert read() == 10
    assert read() == 10


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_interrupted_checkpoint_preserves_previous_total(summary, monkeypatch, failure):
    read, directory, state = summary
    append_turns(directory / "old.jsonl", 2)
    append_turns(directory / "current.jsonl", 3)
    assert read() == 5
    previous = state.read_bytes()
    (directory / "old.jsonl").unlink()
    append_turns(directory / "current.jsonl", 1)

    def fail_write(value, stream, *args, **kwargs):
        stream.write('{"total":')
        raise OSError("fixture interrupted write")

    def fail_replace(*args, **kwargs):
        raise OSError("fixture replacement unavailable")

    with monkeypatch.context() as patch:
        if failure == "write":
            patch.setattr(json, "dump", fail_write)
        else:
            import os
            patch.setattr(os, "replace", fail_replace)
        assert read() == 6
    assert state.read_bytes() == previous
    assert list(state.parent.iterdir()) == [state]
    assert read() == 6
    assert json.loads(state.read_text())["total"] == 6


@pytest.mark.parametrize("new_turns", [1, 2])
def test_new_file_is_counted_even_when_cleanup_offsets_its_growth(summary, new_turns):
    read, directory, _ = summary
    append_turns(directory / "old.jsonl", 2)
    append_turns(directory / "current.jsonl", 3)
    assert read() == 5
    (directory / "old.jsonl").unlink()
    append_turns(directory / "new.jsonl", new_turns)
    assert read() == 5 + new_turns


def test_legacy_total_is_preserved_when_establishing_file_checkpoints(summary):
    read, directory, state = summary
    append_turns(directory / "old.jsonl", 2)
    append_turns(directory / "current.jsonl", 3)
    state.parent.mkdir()
    state.write_text(json.dumps({"total": 100, "last_file_turns": 5}))
    assert read() == 100
    (directory / "old.jsonl").unlink()
    assert read() == 100
    append_turns(directory / "current.jsonl", 1)
    assert read() == 101


def test_failed_scan_does_not_replace_the_last_complete_checkpoint(summary, monkeypatch):
    read, directory, _ = summary
    append_turns(directory / "old.jsonl", 2)
    append_turns(directory / "current.jsonl", 3)
    assert read() == 5
    original_open = builtins.open

    def fail_current(path, *args, **kwargs):
        if str(path) == str(directory / "current.jsonl"):
            raise PermissionError("fixture temporarily unreadable")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", fail_current)
        assert read() == 5
    append_turns(directory / "current.jsonl", 1)
    assert read() == 6
