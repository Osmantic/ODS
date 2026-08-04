"""Token Spy SSE event cursor tests (sqlite backend).

query_recent_events(after_id=...) is the forward cursor behind the
/token_events SSE stream. main.py polls it every 2s, streams the rows, and
passes the highest id back as after_id. The cursor must advance so each row
is streamed exactly once.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from uuid import uuid4


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


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


def _log(db, n):
    for _ in range(n):
        db.log_usage({
            "agent": "Open WebUI", "model": "gpt-4o",
            "input_tokens": 1, "output_tokens": 1, "estimated_cost_usd": 0.0,
        })


def _advance(events, last_id):
    """Cursor advance exactly as main.py's event_stream does."""
    batch_max = max(
        (e["id"] for e in events if e.get("id") is not None),
        default=None,
    )
    return batch_max if batch_max is not None else last_id


def _drain(db, page=50, rounds=10):
    """Poll until dry, returning every id emitted across all polls."""
    last_id = None
    emitted = []
    for _ in range(rounds):
        events = db.query_recent_events(limit=page, after_id=last_id)
        emitted.extend(e["id"] for e in events)
        new_last = _advance(events, last_id)
        if new_last == last_id:
            break
        last_id = new_last
    return emitted


def test_each_event_is_streamed_once(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)
    _log(db, 5)

    emitted = _drain(db)

    assert sorted(emitted) == [1, 2, 3, 4, 5]
    assert len(emitted) == len(set(emitted)), f"duplicate events streamed: {emitted}"


def test_cursor_does_not_walk_backwards_on_the_initial_batch(tmp_path, monkeypatch):
    """The initial (after_id=None) batch is newest-first; the cursor must jump
    to the max id, not the last (oldest) row."""
    db = load_sqlite_db(tmp_path, monkeypatch)
    _log(db, 5)

    first = db.query_recent_events(limit=50, after_id=None)
    cursor = _advance(first, None)

    assert cursor == 5
    # Nothing new since — the next poll must be empty, not a re-send.
    assert db.query_recent_events(limit=50, after_id=cursor) == []


def test_new_events_after_idle_polls_are_delivered_in_order(tmp_path, monkeypatch):
    db = load_sqlite_db(tmp_path, monkeypatch)
    _log(db, 3)
    cursor = _advance(db.query_recent_events(limit=50, after_id=None), None)

    # A few empty polls, then three new rows arrive.
    assert db.query_recent_events(limit=50, after_id=cursor) == []
    _log(db, 3)

    batch = db.query_recent_events(limit=50, after_id=cursor)
    assert [e["id"] for e in batch] == [4, 5, 6]


def test_forward_burst_larger_than_one_page_drains_without_skips(tmp_path, monkeypatch):
    """Once the stream is tailing, a burst bigger than one page must drain
    across polls with no gaps and no repeats.

    (The initial backlog is intentionally capped at one page — that is not
    what this covers; this is the live forward tail.)"""
    db = load_sqlite_db(tmp_path, monkeypatch)
    _log(db, 3)
    cursor = _advance(db.query_recent_events(limit=50, after_id=None), None)

    _log(db, 120)  # 4..123 arrive while we tail

    emitted = []
    last_id = cursor
    for _ in range(10):
        batch = db.query_recent_events(limit=50, after_id=last_id)
        if not batch:
            break
        emitted.extend(e["id"] for e in batch)
        last_id = _advance(batch, last_id)

    assert emitted == list(range(4, 124))  # every new row, in order, once
