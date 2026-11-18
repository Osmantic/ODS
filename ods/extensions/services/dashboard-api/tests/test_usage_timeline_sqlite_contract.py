"""Exercise recorded SQLite receipts through the authenticated minute API."""

import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture
def recorded_usage(tmp_path, monkeypatch):
    service_dir = Path(__file__).resolve().parents[2] / "token-spy"
    spec = importlib.util.spec_from_file_location("timeline_sqlite_contract", service_dir / "db.py")
    db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(db)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "usage.db"))
    db.init_db()
    yield db
    db._get_conn().close()


def test_recorded_receipts_reach_authenticated_minute_api_without_private_fields(
    test_client, recorded_usage, monkeypatch,
):
    import routers.usage as route
    import usage_timeline

    db = recorded_usage
    now = datetime.now(timezone.utc)

    class ReceiptClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(usage_timeline, "datetime", ReceiptClock)
    stamp = now.replace(second=0, microsecond=0).isoformat()
    # Real writes include optional cache buckets, a discriminator and private
    # attribution. Neither helper-only fixtures nor a mocked final response
    # protect their conversion at the service boundary.
    for model, tokens in [("private-model-one", (100, 20, 30, 4)),
                          ("private-model-two", (50, 10, None, None))]:
        db.log_usage(dict(zip(
            ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"), tokens,
        ), agent="private-agent", model=model, estimated_cost_usd=0.25))
    conn = db._get_conn()
    conn.execute("UPDATE usage SET timestamp = ?", (stamp,))
    # The real recent-window query must exclude old persisted history.
    conn.execute("INSERT INTO usage(timestamp,agent,input_tokens,output_tokens) "
                 "VALUES('2000-01-01T00:00:00Z','private-old-agent',99999,99999)")
    conn.commit()
    rows = db.query_usage(hours=24, limit=10000)
    assert len(rows) == 2
    assert all(row["token_buckets_version"] == 1 for row in rows)
    raw = json.dumps(rows).encode()
    observed = []

    def token_spy_transport(request, timeout):
        observed.append(request)
        target = urlsplit(request.full_url)
        assert target.path == "/api/usage"
        assert parse_qs(target.query) == {"hours": ["24"], "limit": ["10000"]}
        assert request.get_header("Authorization") == "Bearer private-upstream-key"
        assert timeout == 4
        return io.BytesIO(raw)

    monkeypatch.setattr(route, "TOKEN_SPY_URL", "http://token-spy.invalid")
    monkeypatch.setattr(route, "_token_spy_api_key", lambda: "private-upstream-key")
    monkeypatch.setattr(route.urllib.request, "urlopen", token_spy_transport)
    assert test_client.get("/api/usage/timeline").status_code == 401
    assert observed == []
    response = test_client.get("/api/usage/timeline", headers=test_client.auth_headers)
    assert response.status_code == 200
    result = response.json()
    assert len(observed) == 1
    assert result["recorded_requests"] == 2
    assert result["invalid_records"] == 0
    assert result["truncated"] is False
    active = [point for point in result["points"] if point["requests"]]
    assert len(active) == 1
    assert {key: active[0][key] for key in (
        "requests", "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "total_tokens",
    )} == {"requests": 2, "input_tokens": 150, "output_tokens": 30,
           "cache_read_tokens": 30, "cache_write_tokens": 4, "total_tokens": 214}
    assert all("private" not in json.dumps(value) for value in result.values())


@pytest.mark.parametrize("raw", [b'private malformed JSON', b'{"private": "wrong envelope"}',
                                  b" " * (8 * 1024 * 1024 + 1)],
                         ids=["invalid-json", "wrong-envelope", "oversized"])
def test_minute_api_refuses_unusable_upstream_bytes(test_client, monkeypatch, raw):
    import routers.usage as route

    reads = []

    class Upstream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    monkeypatch.setattr(route, "_token_spy_api_key", lambda: "private-upstream-key")
    monkeypatch.setattr(route.urllib.request, "urlopen", lambda *args, **kwargs: Upstream(raw))
    response = test_client.get("/api/usage/timeline", headers=test_client.auth_headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "Minute-level token telemetry unavailable"}
    assert reads == [8 * 1024 * 1024 + 1]
