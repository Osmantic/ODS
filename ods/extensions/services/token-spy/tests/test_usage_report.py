"""Token Spy usage report aggregation tests."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


def _stored_ts(dt: datetime) -> str:
    """Render a timestamp the way the usage.timestamp column stores it."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def load_sqlite_db(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        f"token_spy_sqlite_db_{uuid4().hex}",
        TOKEN_SPY_DIR / "db.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DB_PATH", str(tmp_path / "usage.db"))
    module._local.conn = None
    module.init_db()
    return module


def insert_usage(db, timestamp, **overrides):
    entry = {
        "agent": "Open WebUI",
        "model": "gpt-4o",
        "provider_name": "openai",
        "cost_source": "priced_from_tokens",
        "input_tokens": 1000,
        "output_tokens": 250,
        "cache_read_tokens": 100,
        "cache_write_tokens": 0,
        "estimated_cost_usd": 0.0125,
        "duration_ms": 100,
    }
    entry.update(overrides)
    db.log_usage(entry)
    conn = db._get_conn()
    conn.execute(
        "UPDATE usage SET timestamp = ? WHERE id = (SELECT MAX(id) FROM usage)",
        (timestamp,),
    )
    conn.commit()


def test_query_report_aggregates_real_usage_rows(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)

    insert_usage(db, "2026-05-01T10:00:00Z")
    insert_usage(
        db,
        "2026-05-02T10:00:00Z",
        agent="llama-server",
        model="qwen3.5-9b",
        provider_name="local",
        cost_source="local_zero_cost",
        input_tokens=600,
        output_tokens=300,
        cache_read_tokens=0,
        estimated_cost_usd=0,
    )
    insert_usage(db, "2026-04-30T23:59:59Z", estimated_cost_usd=99)

    report = db.query_report("2026-05-01", "2026-05-02")

    assert report["summary"]["requests"] == 2
    assert report["summary"]["spend_usd"] == 0.0125
    assert report["summary"]["input_tokens"] == 1600
    assert report["summary"]["output_tokens"] == 550
    assert report["summary"]["cache_read_tokens"] == 100
    assert report["summary"]["total_tokens"] == 2250
    assert report["summary"]["billing_providers"] == 1
    assert report["summary"]["local_providers"] == 1
    assert report["daily"][0]["date"] == "2026-05-01"
    assert report["daily"][0]["requests"] == 1
    assert report["daily"][1]["date"] == "2026-05-02"
    assert report["daily"][1]["requests"] == 1
    assert [row["model"] for row in report["models"]] == ["gpt-4o", "qwen3.5-9b"]
    assert {row["service"] for row in report["services"]} == {"Open WebUI", "llama-server"}


def test_query_report_degrades_old_zero_cost_rows_to_untracked(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)

    insert_usage(
        db,
        "2026-05-01T10:00:00Z",
        provider_name=None,
        cost_source=None,
        estimated_cost_usd=0,
    )

    report = db.query_report("2026-05-01", "2026-05-01")

    assert report["summary"]["untracked_providers"] == 1
    assert report["models"][0]["cost_source"] == "untracked"
    assert report["models"][0]["cost_usd"] == 0


def test_query_report_marks_old_positive_cost_rows_as_priced(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)

    insert_usage(
        db,
        "2026-05-01T10:00:00Z",
        provider_name=None,
        cost_source=None,
        estimated_cost_usd=1.5,
    )

    report = db.query_report("2026-05-01", "2026-05-01")

    assert report["summary"]["paid_cost_usd"] == 1.5
    assert report["models"][0]["cost_source"] == "priced_from_tokens"


def test_query_usage_excludes_rows_older_than_window(tmp_path, monkeypatch):
    """query_usage(hours=1) must not return a row from earlier the same day.

    The SQLite bound used to be datetime('now', ...) — a space-separated value
    that byte-sorts before the stored T-separated timestamps, so every row on
    the cutoff's calendar date leaked into the window regardless of time-of-day.
    """
    db = load_sqlite_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    # A row ~hours-away but on the cutoff's calendar date — the leak case.
    stale_same_day = cutoff.replace(hour=0, minute=0, second=1, microsecond=0)

    insert_usage(db, _stored_ts(now - timedelta(minutes=10)), agent="recent")
    insert_usage(db, _stored_ts(stale_same_day), agent="stale")

    agents = {r["agent"] for r in db.query_usage(hours=1)}
    assert "recent" in agents
    assert "stale" not in agents


def test_query_summary_respects_hours_window(tmp_path, monkeypatch):
    """query_summary(hours=1) aggregates only rows inside the window."""
    db = load_sqlite_db(tmp_path, monkeypatch)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    stale_same_day = cutoff.replace(hour=0, minute=0, second=1, microsecond=0)

    insert_usage(db, _stored_ts(now - timedelta(minutes=5)), agent="recent")
    insert_usage(db, _stored_ts(stale_same_day), agent="stale")

    agents = {r["agent"] for r in db.query_summary(hours=1)}
    assert agents == {"recent"}
