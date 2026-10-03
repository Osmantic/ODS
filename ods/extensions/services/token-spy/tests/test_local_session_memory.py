"""Session-status HTTP fallback stays bounded for long local-model histories."""
import importlib.util
import json
import tracemalloc
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def local_status(monkeypatch, tmp_path):
    service = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(service))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "local-history-test-key")
    spec = importlib.util.spec_from_file_location("token_spy_local_history", service / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "AGENT_SESSION_DIRS", {"local-test": str(tmp_path)})
    monkeypatch.setattr(module, "_db_available", False)
    monkeypatch.setattr(module, "get_agent_setting", lambda *_: 200_000)
    client = TestClient(module.app)
    try:
        yield client, tmp_path
    finally:
        client.close()


def read_status(client):
    response = client.get("/api/session-status?agent=local-test",
                          headers={"Authorization": "Bearer local-history-test-key"})
    assert response.status_code == 200
    return response.json()


def test_large_local_history_has_bounded_peak_memory(local_status):
    client, directory = local_status
    path = directory / "current.jsonl"
    content = "x" * 1024
    row = json.dumps({"type": "message", "message": {"role": "user", "content": content}}) + "\n"
    with path.open("w") as stream:
        for _ in range(12_000):
            stream.write(row)
    read_status(client)  # Warm HTTP/import machinery outside the memory measurement.
    tracemalloc.start()
    try:
        result = read_status(client)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["current_session_turns"] == 12_000
    assert result["current_history_chars"] == 12_288_000
    assert result["total_lines"] == 12_000
    assert result["file_bytes"] == path.stat().st_size
    assert result["recommendation"] == "reset_recommended"
    # The ~13 MB history must not be retained in memory for a status poll.
    assert peak < 3_000_000, f"session-status allocated {peak:,} bytes"


def test_streamed_counts_include_blank_and_malformed_lines(local_status):
    client, directory = local_status
    rows = [
        {"type": "message", "message": {"role": "assistant", "content": "hello"}},
        {"type": "message", "message": json.dumps({"role": "tool", "content": "done"})},
        {"type": "message", "message": {"role": "user", "content": ["one", "two"]}},
        {"type": "metadata"},
    ]
    path = directory / "current.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\ninvalid\n\n")
    result = read_status(client)
    assert result["current_session_turns"] == 1
    assert result["current_history_chars"] == 15
    assert result["tool_results"] == 1
    assert result["total_lines"] == 6
    assert result["session_files"] == 1
    assert result["file_bytes"] == path.stat().st_size
    assert result["recommendation"] == "healthy"
