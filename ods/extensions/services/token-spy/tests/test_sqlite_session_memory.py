"""Real SQLite session polling retains only its summary and rolling window."""

import importlib.util
import tracemalloc
from uuid import uuid4

from fastapi.testclient import TestClient
from test_usage_report import TOKEN_SPY_DIR, load_sqlite_db


def test_session_http_memory_does_not_follow_turn_count(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)
    conn = db._get_conn()
    conn.executemany(
        "INSERT INTO usage(agent,conversation_history_chars,estimated_cost_usd) VALUES(?,?,?)",
        (("memory-test", 3000 + n, 0.125) for n in range(30_000)),
    )
    conn.commit()
    monkeypatch.syspath_prepend(str(TOKEN_SPY_DIR))
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "sqlite-memory-fixture")
    spec = importlib.util.spec_from_file_location(f"session_api_{uuid4().hex}", TOKEN_SPY_DIR / "main.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "query_session_status", db.query_session_status)
    monkeypatch.setattr(api, "_db_available", True)
    monkeypatch.setattr(api, "REMOTE_AGENTS", {})
    monkeypatch.setattr(api, "get_agent_setting", lambda *_: 200_000)
    api.app.dependency_overrides[api.verify_api_key] = lambda: "fixture"
    client = TestClient(api.app)
    try:
        client.get("/api/session-status", params={"agent": "missing"})
        tracemalloc.start()
        try:
            response = client.get("/api/session-status", params={"agent": "memory-test"})
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    finally:
        client.close()
    assert response.status_code == 200
    result = response.json()
    assert result["current_session_turns"] == 30_000
    assert result["current_history_chars"] == 32_999
    assert result["cost_since_last_reset"] == 3750
    assert result["avg_cost_last_5"] == 0.125
    print(f"SQLite session HTTP peak: {peak:,} bytes")
    assert peak < 2 * 1024 * 1024


def test_resets_clear_cost_and_rolling_cache_window(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)
    conn = db._get_conn()
    turns = [(5000, 10, 0, 1000), (6000, 20, 0, 1000), (1000, None, None, None),
             (2000, 0.25, 100, 0), (3000, 0.5, 100, 100)]
    conn.executemany(
        "INSERT INTO usage(agent,conversation_history_chars,estimated_cost_usd,cache_read_tokens,cache_write_tokens) VALUES('reset-test',?,?,?,?)",
        turns,
    )
    conn.commit()
    result = db.query_session_status("reset-test")
    assert result["current_session_turns"] == result["turns_since_last_reset"] == 3
    assert result["cost_since_last_reset"] == 0.75
    assert result["last_turn_cost"] == 0.5
    assert result["avg_cost_last_5"] == 0.25
    assert result["cache_write_pct_last_5"] == 0.3333
    assert result["recommendation"] == "cache_unstable"
    assert db.query_session_status("missing")["recommendation"] == "no_data"
