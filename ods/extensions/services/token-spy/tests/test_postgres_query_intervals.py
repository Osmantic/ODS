"""PostgreSQL query contracts that do not require a live database."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from uuid import UUID, uuid4


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


def _load_postgres_module(monkeypatch):
    psycopg2 = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object
    extras.register_uuid = lambda: None

    class ThreadedConnectionPool:
        pass

    psycopg2.pool = types.SimpleNamespace(
        ThreadedConnectionPool=ThreadedConnectionPool
    )
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)

    spec = importlib.util.spec_from_file_location(
        f"token_spy_postgres_intervals_{uuid4().hex}",
        TOKEN_SPY_DIR / "db_postgres.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingCursor:
    def __init__(self) -> None:
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self) -> None:
        self.cursor_instance = RecordingCursor()

    def cursor(self, **_kwargs):
        return self.cursor_instance


def test_hour_windows_use_a_bound_postgres_interval_multiplier(monkeypatch):
    db = _load_postgres_module(monkeypatch)
    connection = RecordingConnection()
    db._tenant_id = UUID(int=1)
    monkeypatch.setattr(db, "_get_conn", lambda: connection)
    monkeypatch.setattr(db, "_put_conn", lambda _connection: None)

    assert db.query_usage(agent="agent-a", hours=7, limit=3) == []
    assert db.query_summary(hours=11) == []

    data_queries = [
        (sql, params)
        for sql, params in connection.cursor_instance.calls
        if "FROM requests" in sql
    ]
    assert len(data_queries) == 2
    assert all("(%s * INTERVAL '1 hour')" in sql for sql, _ in data_queries)
    assert all("INTERVAL '%s hours'" not in sql for sql, _ in data_queries)
    assert data_queries[0][1] == [UUID(int=1), 7, "agent-a", 3]
    assert data_queries[1][1] == (UUID(int=1), 11)
