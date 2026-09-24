"""Real PostgreSQL ingest/readback contracts; each test owns a disposable schema."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4

import httpx
import pytest

psycopg2 = pytest.importorskip("psycopg2")
pool = importlib.import_module("psycopg2.pool")
sql = importlib.import_module("psycopg2.sql")

SERVICE = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(f"{name}_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database():
    dsn = os.environ.get("TOKEN_SPY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set TOKEN_SPY_TEST_POSTGRES_DSN to opt into live PostgreSQL tests")
    schema = f"token_spy_identity_{uuid4().hex}"
    admin = psycopg2.connect(dsn, connect_timeout=5)
    admin.autocommit = True
    modules = []
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            cursor.execute("""
                CREATE TABLE tenants (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT,
                    slug TEXT UNIQUE, plan TEXT, deleted_at TIMESTAMPTZ);
                CREATE TABLE agents (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
                    name TEXT, slug VARCHAR(255), UNIQUE (tenant_id, slug));
                CREATE TABLE requests (
                    id UUID PRIMARY KEY, tenant_id UUID, agent_id UUID REFERENCES agents(id),
                    timestamp TIMESTAMPTZ DEFAULT now(), provider TEXT, model TEXT,
                    estimated_cost_usd NUMERIC, stop_reason TEXT);
            """)
            counters = """request_body_bytes message_count user_message_count assistant_message_count
                tool_count system_prompt_total_chars workspace_agents_chars workspace_soul_chars
                workspace_tools_chars workspace_identity_chars workspace_user_chars workspace_heartbeat_chars
                workspace_bootstrap_chars workspace_memory_chars skill_injection_chars base_prompt_chars
                conversation_history_chars input_tokens output_tokens cache_read_tokens cache_write_tokens
                duration_ms""".split()
            for column in counters:
                cursor.execute(sql.SQL("ALTER TABLE requests ADD COLUMN {} BIGINT").format(sql.Identifier(column)))

        def connect_module(tenant_slug="default"):
            module = load_module("identity_db", "db_postgres.py")
            module.SINGLE_TENANT_SLUG = tenant_slug
            module._pool = pool.ThreadedConnectionPool(1, 10, dsn, connect_timeout=5,
                options=f"-c search_path={schema},public -c lock_timeout=5000 -c statement_timeout=15000")
            modules.append(module)
            module.init_db()
            return module

        yield connect_module, admin
    finally:
        for module in modules:
            module._pool.closeall()
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


@pytest.mark.parametrize("names", [
    ("Research Bot", "research-bot"), ("Pixel", "pixel"), ("K", "\u212a"),
])
def test_ingest_keeps_colliding_names_separate_after_reconnect(database, monkeypatch, names):
    connect_module, admin = database
    db = connect_module()
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "identity-fixture-key")
    monkeypatch.setitem(sys.modules, "db_postgres", db)
    api = load_module("identity_api", "main.py")
    monkeypatch.setattr(api, "_db_available", True)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                base_url="http://token-spy.test", headers={"Authorization": "Bearer identity-fixture-key"}) as client:
            for index, name in enumerate(names):
                response = await client.post("/api/ingest/routed", json={
                    "agent": name, "model": "fixture-model", "provider_name": "local",
                    "path": "/v1/chat/completions", "input_tokens": 10 + index,
                })
                assert response.status_code == 202, response.text
            for index, name in enumerate(names):
                response = await client.get("/api/usage", params={"agent": name})
                assert response.status_code == 200, response.text
                assert [(row["agent"], row["input_tokens"]) for row in response.json()] == [(name, 10 + index)]
    asyncio.run(exercise())

    # A fresh process-local cache must retain the same persisted agent IDs.
    fresh = connect_module()
    for index, name in enumerate(names):
        fresh.log_usage({"agent": name, "model": "fixture-model", "input_tokens": 20 + index})
    with admin.cursor() as cursor:
        cursor.execute("SELECT a.name, count(DISTINCT a.id), count(r.id) FROM agents a JOIN requests r ON r.agent_id=a.id GROUP BY a.name")
        assert {name: (agents, rows) for name, agents, rows in cursor} == {name: (1, 2) for name in names}
    today = datetime.now(timezone.utc).date().isoformat()
    assert {row["service"]: row["requests"] for row in fresh.query_report(today, today)["services"]} == {name: 2 for name in names}


def test_existing_agent_id_and_slug_survive_new_name_collision(database):
    connect_module, admin = database
    db = connect_module()
    old_id = uuid4()
    with admin.cursor() as cursor:
        cursor.execute("INSERT INTO agents (id, tenant_id, name, slug) VALUES (%s, %s, %s, %s)",
                       (old_id, db._tenant_id, "Research Bot", "research-bot"))
    db.log_usage({"agent": "Research Bot"})
    db.log_usage({"agent": "research-bot"})
    with admin.cursor() as cursor:
        cursor.execute("SELECT id, name, slug FROM agents ORDER BY name")
        rows = cursor.fetchall()
    assert len(rows) == 2
    assert (old_id, "Research Bot", "research-bot") in rows


def test_simultaneous_first_writes_share_one_agent_across_pools(database):
    connect_module, admin = database
    workers = [connect_module() for _ in range(6)]
    start = threading.Barrier(len(workers))
    def write(db):
        start.wait(timeout=10)
        db.log_usage({"agent": "Concurrent Worker", "input_tokens": 7})
    with ThreadPoolExecutor(max_workers=len(workers)) as executor:
        list(executor.map(write, workers))
    with admin.cursor() as cursor:
        cursor.execute("SELECT count(*), count(DISTINCT agent_id), sum(input_tokens) FROM requests")
        assert cursor.fetchone() == (6, 1, 42)


def test_same_name_stays_scoped_to_its_tenant(database):
    connect_module, admin = database
    first, second = connect_module("first"), connect_module("second")
    first.log_usage({"agent": "Shared name", "input_tokens": 11})
    second.log_usage({"agent": "Shared name", "input_tokens": 22})
    assert [row["input_tokens"] for row in first.query_usage(agent="Shared name")] == [11]
    assert [row["input_tokens"] for row in second.query_usage(agent="Shared name")] == [22]
    with admin.cursor() as cursor:
        cursor.execute("SELECT count(DISTINCT agent_id) FROM requests")
        assert cursor.fetchone() == (2,)


def test_failed_creation_releases_transaction_without_caching_an_agent(database):
    connect_module, admin = database
    db = connect_module()
    with admin.cursor() as cursor:
        cursor.execute("ALTER TABLE agents ADD CONSTRAINT reject_fixture CHECK (name <> 'Rejected')")
    with pytest.raises(psycopg2.errors.CheckViolation):
        db.log_usage({"agent": "Rejected"})
    with admin.cursor() as cursor:
        cursor.execute("ALTER TABLE agents DROP CONSTRAINT reject_fixture")
    # Another pool can acquire the same identity lock after the failed insert.
    fresh = connect_module()
    fresh.log_usage({"agent": "Rejected", "input_tokens": 17})
    db.log_usage({"agent": "Rejected", "input_tokens": 23})
    with admin.cursor() as cursor:
        cursor.execute("SELECT count(*), count(DISTINCT agent_id), sum(input_tokens) FROM requests")
        assert cursor.fetchone() == (2, 1, 40)
