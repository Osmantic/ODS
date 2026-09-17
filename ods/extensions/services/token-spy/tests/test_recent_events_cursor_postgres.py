"""Postgres backend SSE cursor tests for /token_events."""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

psycopg2 = pytest.importorskip("psycopg2")

TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


def _connect():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5434"),
        dbname=os.environ.get("DB_NAME", "tokenspy"),
        user=os.environ.get("DB_USER", "tokenspy"),
        password=os.environ.get("DB_PASSWORD", ""),
    )


@pytest.fixture
def pg_db():
    try:
        conn = _connect()
    except psycopg2.OperationalError as exc:
        pytest.skip(f"no live postgres reachable for db_postgres tests: {exc}")

    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT, slug TEXT UNIQUE, plan TEXT, deleted_at TIMESTAMPTZ)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID, name TEXT, slug TEXT)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID, agent_id UUID, request_id TEXT, provider TEXT,
            model TEXT, input_tokens INT, output_tokens INT,
            estimated_cost_usd NUMERIC, "timestamp" TIMESTAMPTZ)
        """
    )
    cur.execute("TRUNCATE requests, agents, tenants CASCADE")
    cur.execute("INSERT INTO tenants (name, slug, plan) VALUES ('Default', 'default', 'free')")
    conn.commit()
    cur.close()
    conn.close()

    spec = importlib.util.spec_from_file_location(
        f"token_spy_postgres_db_{uuid4().hex}", TOKEN_SPY_DIR / "db_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.init_db()
    try:
        yield module
    finally:
        if module._pool is not None:
            module._pool.closeall()


def _insert_row(db, tenant_id, when, counter, row_id=None):
    conn = _connect()
    cur = conn.cursor()
    row_id = row_id or uuid4()
    cur.execute(
        """
        INSERT INTO requests (id, tenant_id, request_id, provider, model,
            input_tokens, output_tokens, estimated_cost_usd, timestamp)
        VALUES (%s, %s, %s, 'openai', 'gpt-4o', 100, 50, 0.01, %s)
        """,
        (row_id, tenant_id, f"req-{counter}", when),
    )
    conn.commit()
    cur.close()
    conn.close()
    return row_id


def test_initial_window_is_chronological_and_does_not_replay(pg_db):
    tenant_id = pg_db._tenant_id
    base = datetime(2026, 7, 27, tzinfo=timezone.utc)
    inserted = [
        _insert_row(pg_db, tenant_id, base + timedelta(seconds=i), i)
        for i in range(5)
    ]

    initial = pg_db.query_recent_events(limit=3, after_id=None)

    assert [event["id"] for event in initial] == inserted[-3:]
    assert [event["timestamp"] for event in initial] == sorted(
        event["timestamp"] for event in initial
    )
    assert pg_db.query_recent_events(
        limit=50, after_id=initial[-1]["_cursor"]
    ) == []


def test_forward_poll_delivers_every_row_inserted_after_connect(pg_db):
    tenant_id = pg_db._tenant_id
    base = datetime(2026, 7, 27, tzinfo=timezone.utc)
    counter = 0
    for _ in range(3):
        _insert_row(pg_db, tenant_id, base + timedelta(seconds=counter), counter)
        counter += 1

    initial = pg_db.query_recent_events(limit=50, after_id=None)
    cursor = initial[-1]["_cursor"]

    delivered = []
    live_inserted = []
    for _ in range(8):
        batch = [
            _insert_row(pg_db, tenant_id, base + timedelta(seconds=counter + i), counter + i)
            for i in range(3)
        ]
        counter += 3
        live_inserted.extend(batch)

        events = pg_db.query_recent_events(limit=50, after_id=cursor)
        for event in events:
            delivered.append(event["id"])
            cursor = event["_cursor"]

    assert delivered == live_inserted


def test_composite_cursor_survives_source_row_deletion(pg_db):
    tenant_id = pg_db._tenant_id
    base = datetime(2026, 7, 27, tzinfo=timezone.utc)
    cursor_id = _insert_row(pg_db, tenant_id, base, 0)
    cursor = pg_db.query_recent_events(limit=50, after_id=None)[-1]["_cursor"]

    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM requests WHERE id = %s", (cursor_id,))
    conn.commit()
    cur.close()
    conn.close()

    next_id = _insert_row(pg_db, tenant_id, base + timedelta(seconds=1), 1)
    events = pg_db.query_recent_events(limit=50, after_id=cursor)

    assert [event["id"] for event in events] == [next_id]


def test_equal_timestamps_page_deterministically_by_uuid(pg_db):
    tenant_id = pg_db._tenant_id
    when = datetime(2026, 7, 27, tzinfo=timezone.utc)
    row_ids = [UUID(int=value) for value in (3, 1, 2)]
    for counter, row_id in enumerate(row_ids):
        _insert_row(pg_db, tenant_id, when, counter, row_id=row_id)

    initial = pg_db.query_recent_events(limit=2, after_id=None)
    assert [event["id"] for event in initial] == [UUID(int=2), UUID(int=3)]
    assert pg_db.query_recent_events(
        limit=50, after_id=initial[-1]["_cursor"]
    ) == []


def test_legacy_uuid_cursor_is_tenant_scoped(pg_db):
    tenant_id = pg_db._tenant_id
    base = datetime(2026, 7, 27, tzinfo=timezone.utc)
    own_id = _insert_row(pg_db, tenant_id, base, 0)
    own_next_id = _insert_row(pg_db, tenant_id, base + timedelta(seconds=1), 1)

    assert [
        event["id"]
        for event in pg_db.query_recent_events(limit=50, after_id=own_id)
    ] == [own_next_id]

    conn = _connect()
    cur = conn.cursor()
    foreign_tenant_id = uuid4()
    foreign_id = uuid4()
    cur.execute(
        "INSERT INTO tenants (id, name, slug, plan) VALUES (%s, 'Other', 'other', 'free')",
        (foreign_tenant_id,),
    )
    cur.execute(
        """
        INSERT INTO requests (id, tenant_id, request_id, provider, model,
            input_tokens, output_tokens, estimated_cost_usd, timestamp)
        VALUES (%s, %s, 'foreign', 'openai', 'gpt-4o', 1, 1, 0, %s)
        """,
        (foreign_id, foreign_tenant_id, base),
    )
    conn.commit()
    cur.close()
    conn.close()

    assert pg_db.query_recent_events(limit=50, after_id=foreign_id) == []
