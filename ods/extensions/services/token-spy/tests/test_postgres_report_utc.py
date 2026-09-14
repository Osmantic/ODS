"""Calendar reports keep their UTC contract on non-UTC PostgreSQL servers."""
import asyncio
from datetime import date, datetime, time, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import sys
from uuid import uuid4

import httpx
import pytest

psycopg2 = pytest.importorskip("psycopg2")
pool = importlib.import_module("psycopg2.pool")
sql = importlib.import_module("psycopg2.sql")
SERVICE = Path(__file__).resolve().parents[1]


def load_module(filename):
    spec = importlib.util.spec_from_file_location(f"utc_report_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database():
    dsn = os.environ.get("TOKEN_SPY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set TOKEN_SPY_TEST_POSTGRES_DSN to opt into live PostgreSQL tests")
    db = load_module("db_postgres.py")
    db._tenant_id = uuid4()
    schema = f"token_spy_report_{uuid4().hex}"
    admin = psycopg2.connect(dsn, connect_timeout=5)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            cursor.execute("""
                CREATE TABLE agents (id UUID PRIMARY KEY, name TEXT);
                CREATE TABLE requests (
                    tenant_id UUID, agent_id UUID REFERENCES agents(id), timestamp TIMESTAMPTZ,
                    model TEXT, provider TEXT, input_tokens BIGINT, output_tokens BIGINT DEFAULT 0,
                    cache_read_tokens BIGINT DEFAULT 0, cache_write_tokens BIGINT DEFAULT 0,
                    estimated_cost_usd NUMERIC);
            """)
        yield db, admin, dsn, schema
    finally:
        if db._pool is not None:
            db._pool.closeall()
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


@pytest.mark.parametrize("server_timezone", ["UTC", "America/Los_Angeles", "Asia/Tokyo", "Europe/London"])
@pytest.mark.parametrize("start,end", [("2026-09-01", "2026-09-30"), ("2026-03-29", "2026-03-29")])
def test_http_report_uses_utc_boundaries_and_daily_buckets(database, monkeypatch, server_timezone, start, end):
    db, admin, dsn, schema = database
    db._pool = pool.ThreadedConnectionPool(1, 2, dsn, connect_timeout=5,
        options=f"-c search_path={schema},public -c timezone={server_timezone} -c statement_timeout=15000")
    first = datetime.combine(date.fromisoformat(start), time.min, tzinfo=timezone.utc)
    stop = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, tzinfo=timezone.utc)
    agent = uuid4()
    with admin.cursor() as cursor:
        cursor.execute("INSERT INTO agents VALUES (%s, 'Calendar Agent')", (agent,))
        for stamp, count in [(first - timedelta(microseconds=1), 1000), (first, 1),
                             (first + timedelta(hours=12), 2), (stop - timedelta(microseconds=1), 4), (stop, 1000)]:
            cursor.execute("""INSERT INTO requests (tenant_id, agent_id, timestamp, model, provider, input_tokens, estimated_cost_usd)
                VALUES (%s, %s, %s, 'fixture-model', 'fixture-provider', %s, %s)""",
                (db._tenant_id, agent, stamp, count, count / 10))
        cursor.execute("""INSERT INTO requests (tenant_id, agent_id, timestamp, model, input_tokens)
            VALUES (%s, %s, %s, 'other-tenant', 10000)""", (uuid4(), agent, first))
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "calendar-fixture-key")
    monkeypatch.setitem(sys.modules, "db_postgres", db)
    api = load_module("main.py")

    async def fetch_report():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://token-spy.test",
                headers={"Authorization": "Bearer calendar-fixture-key"}) as client:
            response = await client.get("/api/report", params={"start": start, "end": end})
            assert response.status_code == 200, response.text
            return response.json()
    report = asyncio.run(fetch_report())
    assert report["summary"]["requests"] == 3
    assert report["summary"]["input_tokens"] == 7
    assert report["summary"]["spend_usd"] == 0.7
    populated = {row["date"]: (row["input_tokens"], row["requests"]) for row in report["daily"] if row["requests"]}
    assert populated == ({start: (7, 3)} if start == end else {start: (3, 2), end: (4, 1)})
    assert [(row["service"], row["requests"], row["input_tokens"]) for row in report["services"]] == [("Calendar Agent", 3, 7)]
    # Reporting must not change the server/pool timezone for other callers.
    connection = db._get_conn()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TimeZone")
            assert cursor.fetchone()[0] == server_timezone
    finally:
        db._put_conn(connection)
