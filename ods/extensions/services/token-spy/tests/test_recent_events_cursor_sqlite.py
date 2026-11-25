"""The shipped SQLite SSE stream must not replay or skip usage events."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SERVICE = Path(__file__).resolve().parents[1]


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("sqlite_cursor_fixture", SERVICE / "db.py")
    db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(db)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "usage.db"))
    db.init_db()
    yield db
    db._get_conn().close()


def insert(db, count):
    for _ in range(count):
        db.log_usage({"agent": "cursor-fixture", "model": "local", "input_tokens": 1})


@pytest.mark.parametrize("timestamps", ["equal", "backwards"])
def test_initial_window_returns_latest_ids_in_cursor_order(sqlite_db, timestamps):
    assert sqlite_db.query_recent_events(limit=3) == []
    insert(sqlite_db, 5)
    # Wall clocks can move backwards; the SQLite cursor follows insertion IDs.
    conn = sqlite_db._get_conn()
    if timestamps == "backwards":
        conn.execute("UPDATE usage SET timestamp = printf('2026-10-11T00:00:%02dZ', 10-id)")
    else:
        conn.execute("UPDATE usage SET timestamp = '2026-10-11T00:00:00Z'")
    conn.commit()
    rows = sqlite_db.query_recent_events(limit=3)
    assert [row["id"] for row in rows] == [3, 4, 5]
    assert sqlite_db.query_recent_events(limit=3, after_id=rows[-1]["id"]) == []


def test_forward_pages_deliver_every_unseen_event_once(sqlite_db):
    insert(sqlite_db, 1)
    cursor = sqlite_db.query_recent_events(limit=2)[-1]["id"]
    insert(sqlite_db, 7)
    received: list[int] = []
    while True:
        rows = sqlite_db.query_recent_events(limit=2, after_id=cursor)
        if not rows:
            break
        received.extend(row["id"] for row in rows)
        cursor = rows[-1]["id"]
    assert received == list(range(2, 9))


@pytest.mark.parametrize("cached,written,expected", [(1536, 0, 2148), (2048, 0, 2148),
                                                     (100, 20, 732), (None, None, 612)])
def test_event_totals_include_disjoint_cache_buckets(sqlite_db, cached, written, expected):
    sqlite_db.log_usage({"agent": "cache", "model": "gpt-4o", "input_tokens": 1,
                         "output_tokens": 1})
    cursor = sqlite_db.query_recent_events()[-1]["id"]
    sqlite_db.log_usage({"agent": "cache", "model": "gpt-4o",
                         "input_tokens": 0 if cached == 2048 else 512, "output_tokens": 100,
                         "cache_read_tokens": cached, "cache_write_tokens": written})
    assert sqlite_db.query_recent_events(limit=1)[0]["total_tokens"] == expected
    assert sqlite_db.query_recent_events(after_id=cursor)[0]["total_tokens"] == expected


def test_legacy_rows_keep_event_semantics_through_additive_schema_upgrade(sqlite_db):
    conn = sqlite_db._get_conn()
    if any(row[1] == "token_buckets_version" for row in conn.execute("PRAGMA table_info(usage)")):
        conn.execute("ALTER TABLE usage DROP COLUMN token_buckets_version")
    # Same provider, ambiguous historical formats: never infer/rewrite either.
    for input_tokens in (2048, 512, None):
        conn.execute("INSERT INTO usage(agent,model,provider_name,input_tokens,output_tokens,"
                     "cache_read_tokens,cache_write_tokens,estimated_cost_usd) "
                     "VALUES('legacy','gpt-4o','openai',?,100,1536,0,0.1)", (input_tokens,))
    conn.commit()
    before = conn.execute("SELECT input_tokens,output_tokens,cache_read_tokens,estimated_cost_usd FROM usage").fetchall()
    sqlite_db.init_db()
    sqlite_db.init_db()
    assert conn.execute("SELECT input_tokens,output_tokens,cache_read_tokens,estimated_cost_usd FROM usage").fetchall() == before
    rows = sqlite_db.query_recent_events()
    assert [row["total_tokens"] for row in rows] == [2148, 612, None]
    assert [row["total_tokens"] for row in sqlite_db.query_recent_events(after_id=rows[0]["id"])] == [612, None]
    assert [row[0] for row in conn.execute("SELECT token_buckets_version FROM usage")] == [None] * 3


def test_public_sse_generator_recovers_without_replay_or_loss(sqlite_db, monkeypatch):
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "cursor-fixture-key")
    spec = importlib.util.spec_from_file_location("token_spy_cursor_fixture", SERVICE / "main.py")
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    insert(sqlite_db, 3)
    calls = 0

    def query(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("private transient fixture failure")
        return sqlite_db.query_recent_events(**kwargs)

    async def no_delay(_seconds):
        await real_sleep(0)

    real_sleep = asyncio.sleep
    monkeypatch.setattr(service, "query_recent_events", query)
    monkeypatch.setattr(service.asyncio, "sleep", no_delay)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app), base_url="http://fixture") as client:
            assert (await client.get("/token_events")).status_code == 401
            assert (await client.get("/token_events", headers={"Authorization": "Bearer wrong"})).status_code == 403
        response = await service.token_events(None)
        stream = response.body_iterator
        try:
            initial = [json.loads((await anext(stream))[6:])["input_tokens"] for _ in range(3)]
            assert initial == [1, 1, 1]
            assert await anext(stream) == ":heartbeat\n\n"
            insert(sqlite_db, 55)
            assert (await anext(stream)).startswith("event: error\n")
            emitted = 0
            for batch in (50, 5):
                for _ in range(batch):
                    assert (await anext(stream)).startswith("data: ")
                    emitted += 1
                assert await anext(stream) == ":heartbeat\n\n"
            assert emitted == 55
            assert await anext(stream) == ":heartbeat\n\n"
            assert await anext(stream) == ":heartbeat\n\n"
        finally:
            await stream.aclose()

        # Exercise the authenticated route and StreamingResponse disconnect
        # cancellation, with a private ASGI connection and no server/host I/O.
        disconnected = asyncio.Event()
        sent = []
        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
            if message.get("body", b"").startswith(b":heartbeat"):
                disconnected.set()
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/token_events", "raw_path": b"/token_events", "query_string": b"",
            "headers": [(b"authorization", b"Bearer cursor-fixture-key")],
            "client": ("127.0.0.1", 1), "server": ("fixture", 80),
        }
        await asyncio.wait_for(service.app(scope, receive, send), timeout=2)
        assert sent[0]["status"] == 200
        assert sum(message.get("body", b"").startswith(b"data: ") for message in sent) == 50
        calls_after_close = calls
        await real_sleep(0)
        assert calls == calls_after_close

    asyncio.run(exercise())
