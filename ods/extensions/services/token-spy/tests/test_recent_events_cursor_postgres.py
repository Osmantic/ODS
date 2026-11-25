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
            cache_read_tokens INT, cache_write_tokens INT,
            request_body_bytes INT, message_count INT, user_message_count INT,
            assistant_message_count INT, tool_count INT,
            system_prompt_total_chars INT, workspace_agents_chars INT,
            workspace_soul_chars INT, workspace_tools_chars INT,
            workspace_identity_chars INT, workspace_user_chars INT,
            workspace_heartbeat_chars INT, workspace_bootstrap_chars INT,
            workspace_memory_chars INT, skill_injection_chars INT,
            base_prompt_chars INT, conversation_history_chars INT,
            duration_ms INT, stop_reason TEXT,
            estimated_cost_usd NUMERIC, "timestamp" TIMESTAMPTZ DEFAULT NOW())
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


@pytest.mark.parametrize("cached,written,expected", [(1536, 0, 2148), (2048, 0, 2148),
                                                     (100, 20, 732), (None, None, 612)])
def test_logged_cache_receipt_totals_in_initial_and_poll_events(pg_db, cached, written, expected):
    _insert_row(pg_db, pg_db._tenant_id, datetime.now(timezone.utc) - timedelta(days=1), 0)
    cursor = pg_db.query_recent_events()[-1]["_cursor"]
    # Use the actual PostgreSQL persistence producer, not a total-only fake.
    pg_db.log_usage({"agent": "cache-fixture", "model": "gpt-4o", "provider_name": "openai",
                     "input_tokens": 0 if cached == 2048 else 512, "output_tokens": 100,
                     "cache_read_tokens": cached, "cache_write_tokens": written})
    assert pg_db.query_recent_events(limit=1)[0]["total_tokens"] == expected
    assert pg_db.query_recent_events(after_id=cursor)[0]["total_tokens"] == expected


def test_legacy_rows_keep_event_semantics_through_additive_schema_upgrade(pg_db):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE requests DROP COLUMN IF EXISTS token_buckets_version")
            base = datetime.now(timezone.utc) - timedelta(days=1)
            for offset, input_tokens in enumerate((2048, 512, None)):
                cur.execute("INSERT INTO requests(id,tenant_id,provider,model,input_tokens,output_tokens,"
                            "cache_read_tokens,cache_write_tokens,estimated_cost_usd,timestamp) "
                            "VALUES(%s,%s,'openai','gpt-4o',%s,100,1536,0,0.1,%s)",
                            (uuid4(), pg_db._tenant_id, input_tokens, base + timedelta(seconds=offset)))
            cur.execute("SELECT input_tokens,output_tokens,cache_read_tokens,estimated_cost_usd FROM requests ORDER BY timestamp")
            before = cur.fetchall()
        conn.commit()
        pg_db.init_db()
        pg_db.init_db()
        with conn.cursor() as cur:
            cur.execute("SELECT input_tokens,output_tokens,cache_read_tokens,estimated_cost_usd FROM requests ORDER BY timestamp")
            assert cur.fetchall() == before
        rows = pg_db.query_recent_events()
        assert [row["total_tokens"] for row in rows] == [2148, 612, None]
        assert [row["total_tokens"] for row in pg_db.query_recent_events(after_id=rows[0]["_cursor"])] == [612, None]
        with conn.cursor() as cur:
            cur.execute("SELECT token_buckets_version FROM requests")
            assert [row[0] for row in cur.fetchall()] == [None] * 3
    finally:
        conn.close()


def test_public_events_preserve_legacy_and_new_receipt_totals(pg_db, monkeypatch):
    import asyncio
    import sys
    from test_openai_cache_accounting import load, public_events

    monkeypatch.syspath_prepend(str(TOKEN_SPY_DIR))
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "cache-fixture-key")
    monkeypatch.setitem(sys.modules, "db_postgres", pg_db)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO requests(id,tenant_id,provider,model,input_tokens,output_tokens,"
                        "cache_read_tokens,timestamp) VALUES(%s,%s,'openai','gpt-4o',2048,100,1536,%s)",
                        (uuid4(), pg_db._tenant_id, datetime.now(timezone.utc) - timedelta(days=1)))
        conn.commit()
        pg_db.log_usage({"agent": "new", "model": "gpt-4o", "input_tokens": 512,
                         "output_tokens": 100, "cache_read_tokens": 1536, "cache_write_tokens": 0})
        api = load("main.py")
        events = asyncio.run(public_events(api))
        assert [event["total_tokens"] for event in events] == [2148, 2148]
    finally:
        conn.close()


@pytest.mark.parametrize("preprovisioned", [True, False])
def test_dml_role_requires_owner_only_for_missing_marker(pg_db, monkeypatch, preprovisioned):
    from psycopg2 import sql

    conn = _connect()
    role = sql.Identifier("token_spy_dml_" + uuid4().hex)
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            cur.execute(sql.SQL("GRANT SELECT, INSERT ON tenants, agents, requests TO {}").format(role))
            if not preprovisioned:
                cur.execute("ALTER TABLE requests DROP COLUMN token_buckets_version")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET ROLE {}").format(role))
        conn.commit()
        monkeypatch.setattr(pg_db, "_get_conn", lambda: conn)
        monkeypatch.setattr(pg_db, "_put_conn", lambda _conn: None)
        if preprovisioned:
            pg_db.init_db()
            pg_db.log_usage({"agent": "dml-owner", "model": "gpt-4o", "input_tokens": 512,
                             "output_tokens": 100, "cache_read_tokens": 1536, "cache_write_tokens": 0})
            assert pg_db.query_recent_events()[0]["total_tokens"] == 2148
        else:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                pg_db.init_db()
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute(sql.SQL("DROP OWNED BY {}").format(role))
            cur.execute(sql.SQL("DROP ROLE {}").format(role))
        conn.commit()
        conn.close()


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
