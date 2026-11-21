"""Seed/verify real TalkStore state through the filesystem backup roundtrip."""

import json
from pathlib import Path
import sqlite3
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions/services/dashboard-api"))
from talk_store import StoreError, TalkStore  # noqa: E402

action, root = sys.argv[1], Path(sys.argv[2])
directory = root / "data/talk"
path = directory / "conversations.sqlite3"
manifest = directory / "fixture-expected.json"
owner = "backup-test-owner"

if action == "create":
    store = TalkStore(path)
    expected = []
    for state in ("ready", "uncertain"):
        cid = store.allocate(owner, int(time.time()) + 86400)
        store.finish_create(owner, cid, store.claim_create(owner, cid), "native-" + state)
        if state == "uncertain":
            token = store.claim_turn(owner, cid)
            store.submit_turn(owner, cid, token)
            store.mark_uncertain(owner, cid, token)
        expected.append(store.get(owner, cid))
    manifest.write_text(json.dumps(expected))
elif action == "verify":
    # Check modes before constructing TalkStore, which enforces them itself.
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    store = TalkStore(path)
    expected = json.loads(manifest.read_text())
    assert {row["state"] for row in expected} == {"ready", "uncertain"}
    for snapshot in expected:
        cid = snapshot["id"]
        assert store.get(owner, cid) == snapshot
        try:
            store.get("different-owner", cid)
        except StoreError as error:
            assert error.code == "not_found"
        else:
            raise AssertionError("Restored conversation lost owner binding")
        if snapshot["state"] == "uncertain":
            try:
                store.claim_turn(owner, cid)
            except StoreError as error:
                assert error.code == "conflict"
            else:
                raise AssertionError("Restore reopened an uncertain turn")
else:
    raise ValueError(action)
