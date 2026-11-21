import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from talk_store import TalkStore, StoreError


def make_store(tmp_path, name="s.db", clock=None, **kw):
    return TalkStore(tmp_path / name, clock=clock, **kw)


def test_allocate_and_get(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    snap = s.get("alice", cid)
    assert snap["state"] == "new"
    assert snap["stored_id"] is None
    assert snap["id"] == cid


def test_owner_isolation(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    with pytest.raises(StoreError) as e:
        s.get("bob", cid)
    assert e.value.code == "not_found"
    with pytest.raises(StoreError):
        s.claim_create("bob", cid)


def test_expiry(tmp_path):
    now = [1000]
    s = make_store(tmp_path, clock=lambda: now[0])
    cid = s.allocate("alice", 2000)
    now[0] = 2000
    with pytest.raises(StoreError) as e:
        s.get("alice", cid)
    assert e.value.code in {"expired", "not_found"}


def test_capacity_no_unexpired_eviction(tmp_path):
    s = make_store(tmp_path, max_global=2, max_per_owner=2)
    a = s.allocate("alice", 10**12)
    b = s.allocate("bob", 10**12)
    with pytest.raises(StoreError) as e:
        s.allocate("carol", 10**12)
    assert e.value.code == "capacity"
    assert s.get("alice", a)["id"] == a
    assert s.get("bob", b)["id"] == b


def test_per_owner_capacity(tmp_path):
    s = make_store(tmp_path, max_global=100, max_per_owner=1)
    s.allocate("alice", 10**12)
    with pytest.raises(StoreError) as e:
        s.allocate("alice", 10**12)
    assert e.value.code == "capacity"
    s.allocate("bob", 10**12)


def test_token_transitions(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    t1 = s.claim_create("alice", cid)
    with pytest.raises(StoreError):
        s.claim_create("alice", cid)
    s.finish_create("alice", cid, t1, "stored-1")
    assert s.get("alice", cid)["state"] == "ready"
    assert s.get("alice", cid)["stored_id"] == "stored-1"
    t2 = s.claim_turn("alice", cid)
    with pytest.raises(StoreError):
        s.claim_turn("alice", cid)
    s.submit_turn("alice", cid, t2)
    assert s.get("alice", cid)["state"] == "submitted"
    with pytest.raises(StoreError):
        s.release_prepared_turn("alice", cid, t2)
    s.finish_turn("alice", cid, t2)
    assert s.get("alice", cid)["state"] == "ready"


def test_release_prepared(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    t = s.claim_create("alice", cid)
    s.finish_create("alice", cid, t, "sid")
    t2 = s.claim_turn("alice", cid)
    s.release_prepared_turn("alice", cid, t2)
    assert s.get("alice", cid)["state"] == "ready"
    with pytest.raises(StoreError):
        s.release_prepared_turn("alice", cid, t2)


def test_finish_create_missing_stored_id(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    t = s.claim_create("alice", cid)
    with pytest.raises(StoreError):
        s.finish_create("alice", cid, t, None)
    with pytest.raises(StoreError):
        s.finish_create("alice", cid, t, "")


def test_wrong_token(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    t = s.claim_create("alice", cid)
    with pytest.raises(StoreError) as e:
        s.finish_create("alice", cid, "bogus", "sid")
    assert e.value.code == "conflict"
    s.finish_create("alice", cid, t, "sid")


def test_immutable_stored_id(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    t = s.claim_create("alice", cid)
    s.finish_create("alice", cid, t, "sid-1")
    t2 = s.claim_turn("alice", cid)
    s.submit_turn("alice", cid, t2)
    s.finish_turn("alice", cid, t2)
    assert s.get("alice", cid)["stored_id"] == "sid-1"


def test_native_identity_cannot_alias_another_owner(tmp_path):
    s = make_store(tmp_path)
    alice = s.allocate("alice", 10**12)
    token = s.claim_create("alice", alice)
    s.finish_create("alice", alice, token, "native-id")
    bob = s.allocate("bob", 10**12)
    token = s.claim_create("bob", bob)
    with pytest.raises(StoreError):
        s.finish_create("bob", bob, token, "native-id")
    assert s.get("bob", bob)["state"] == "creating"
    assert s.get("alice", alice)["stored_id"] == "native-id"


def test_mark_uncertain_blocks(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    t = s.claim_create("alice", cid)
    s.finish_create("alice", cid, t, "sid")
    t2 = s.claim_turn("alice", cid)
    s.submit_turn("alice", cid, t2)
    s.mark_uncertain("alice", cid, t2)
    assert s.get("alice", cid)["state"] == "uncertain"
    with pytest.raises(StoreError):
        s.claim_turn("alice", cid)
    with pytest.raises(StoreError):
        s.finish_turn("alice", cid, t2)


def test_concurrent_claim_exactly_one_wins(tmp_path):
    s = make_store(tmp_path)
    cid = s.allocate("alice", 10**12)
    t = s.claim_create("alice", cid)
    s.finish_create("alice", cid, t, "sid")
    barrier = threading.Barrier(8)
    results = []

    def worker():
        barrier.wait()
        try:
            tok = TalkStore(s.path).claim_turn("alice", cid)
            results.append(("ok", tok))
        except StoreError as e:
            results.append(("err", e.code))

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _: worker(), range(8)))
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1
    assert len(errs) == 7
    assert all(e[1] == "conflict" for e in errs)


def test_two_store_objects_share_sqlite(tmp_path):
    s1 = make_store(tmp_path)
    s2 = make_store(tmp_path)
    cid = s1.allocate("alice", 10**12)
    t = s1.claim_create("alice", cid)
    s1.finish_create("alice", cid, t, "sid")
    t2 = s2.claim_turn("alice", cid)
    s2.submit_turn("alice", cid, t2)
    assert s1.get("alice", cid)["state"] == "submitted"


def test_subprocess_restart_persists(tmp_path):
    db = tmp_path / "s.db"
    script = (
        "import sys\n"
        "from talk_store import TalkStore\n"
        "s = TalkStore(sys.argv[1])\n"
        "cid = s.allocate('alice', 10**12)\n"
        "t = s.claim_create('alice', cid)\n"
        "s.finish_create('alice', cid, t, 'sid')\n"
        "t2 = s.claim_turn('alice', cid)\n"
        "s.submit_turn('alice', cid, t2)\n"
        "print(cid, flush=True)\n"
        "import os; os._exit(0)\n"
    )
    out = (
        subprocess.check_output(
            [sys.executable, "-c", script, str(db)],
            cwd=str(Path(__file__).resolve().parent.parent),
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            },
            timeout=15,
        )
        .decode()
        .strip()
    )
    s = TalkStore(db)
    snap = s.get("alice", out)
    assert snap["state"] == "submitted"
    assert snap["stored_id"] == "sid"
    with pytest.raises(StoreError):
        s.claim_turn("alice", out)


@pytest.mark.parametrize("phase", ["creating", "preparing", "submitted", "uncertain"])
def test_abandoned_operation_never_times_out_into_replay(tmp_path, phase):
    now = [1000]
    s = make_store(tmp_path, clock=lambda: now[0])
    cid = s.allocate("alice", 100000)
    token = s.claim_create("alice", cid)
    if phase != "creating":
        s.finish_create("alice", cid, token, "stored")
        token = s.claim_turn("alice", cid)
    if phase in {"submitted", "uncertain"}:
        s.submit_turn("alice", cid, token)
    if phase == "uncertain":
        s.mark_uncertain("alice", cid, token)
    now[0] = 50000
    restarted = TalkStore(s.path, clock=lambda: now[0])
    assert restarted.get("alice", cid)["state"] == phase
    with pytest.raises(StoreError):
        restarted.claim_create("alice", cid)
    with pytest.raises(StoreError):
        restarted.claim_turn("alice", cid)


def test_expired_rows_cannot_exhaust_owner_capacity(tmp_path):
    now = [1000]
    s = make_store(tmp_path, clock=lambda: now[0], max_per_owner=1)
    # More than one cleanup batch, with the target owner's row at the end.
    for i in range(105):
        s.allocate(f"owner-{i}", 2000)
    old = s.allocate("alice", 2000)
    now[0] = 3000
    new = s.allocate("alice", 5000)
    assert new != old
    assert s.get("alice", new)["state"] == "new"


def test_corrupt_db(tmp_path):
    p = tmp_path / "bad.db"
    p.write_bytes(b"not a sqlite database at all" * 100)
    with pytest.raises(StoreError) as e:
        TalkStore(p)
    assert e.value.code == "unavailable"


def test_invalid_inputs(tmp_path):
    s = make_store(tmp_path)
    with pytest.raises(StoreError):
        s.allocate("", 10**12)
    with pytest.raises(StoreError):
        s.allocate("alice", 0)
    with pytest.raises(StoreError):
        s.allocate("alice", True)
    with pytest.raises(StoreError):
        s.get("alice", "nope")
