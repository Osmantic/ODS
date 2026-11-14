"""Private durable records, without touching installed engines or services."""

import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TXID = "a" * 64
NAMES = {"env", "absent"}
BEFORE = {"env": {"data": b"KEY=synthetic\x00\xff", "mode": 0o640, "uid": 1000, "gid": 1001}, "absent": None}
TARGET = {"env": {"data": b"KEY=changed\n", "mode": 0o600, "uid": 1000, "gid": 1001},
          "absent": {"data": b"created", "mode": 0o600, "uid": 1000, "gid": 1001}}
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux journal protocol")


@pytest.fixture
def journal_case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "bin"))
    import model_lifecycle_lock
    import model_transition_journal

    monkeypatch.delenv("INSTALL_USER", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("ODS_MODEL_LIFECYCLE_LOCK_ROOT", str(tmp_path / "locks"))
    install = tmp_path / "install"
    install.mkdir(mode=0o755)
    (install / "data").mkdir(mode=0o700)
    with model_lifecycle_lock.acquire(install) as lease:
        yield install, lease, model_transition_journal


def make_journal(journal_case):
    install, lease, module = journal_case
    return module.Journal(install, TXID, lease.fd, NAMES)


def record_path(journal_case):
    return journal_case[0] / "data" / "model-transitions" / "current.json"


def test_private_roundtrip_survives_new_process(journal_case):
    journal = make_journal(journal_case)
    journal.create(BEFORE, TARGET)
    path = record_path(journal_case)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.stat().st_nlink == 1
    assert list(path.parent.iterdir()) == [path]
    # A separate interpreter can use the inherited lease and recover real bytes.
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from model_transition_journal import Journal
record = Journal(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), {'env','absent'}).load()
assert record['before'] == {'env': {'data': b'KEY=synthetic\\x00\\xff', 'mode': 0o640, 'uid': 1000, 'gid': 1001}, 'absent': None}
assert record['target'] == {'env': {'data': b'KEY=changed\\n', 'mode': 0o600, 'uid': 1000, 'gid': 1001},
                            'absent': {'data': b'created', 'mode': 0o600, 'uid': 1000, 'gid': 1001}}
assert record['phase'] == 'prepared'
"""
    install, lease, _ = journal_case
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "bin"), str(install), TXID, str(lease.fd)],
        pass_fds=(lease.fd,), capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("outcome", ["commit", "rollback"])
def test_phase_revision_and_completed_immutability(journal_case, outcome):
    journal = make_journal(journal_case)
    journal.create(BEFORE, TARGET)
    journal.checkpoint(0, "applying")
    with pytest.raises(RuntimeError, match="revision"):
        journal.checkpoint(0, "verifying")
    next_phase = "verifying" if outcome == "commit" else "rolling-back"
    journal.checkpoint(1, next_phase)
    done = journal.checkpoint(2, "completed", outcome)
    assert (done["revision"], done["outcome"]) == (3, outcome)
    saved = record_path(journal_case).read_bytes()
    with pytest.raises(RuntimeError, match="illegal phase"):
        journal.checkpoint(3, "applying")
    assert record_path(journal_case).read_bytes() == saved


def test_classifies_only_known_bytes(journal_case):
    journal = make_journal(journal_case)
    journal.create(BEFORE, {**TARGET, "absent": None})
    assert journal.classify(BEFORE) == {"env": "before", "absent": "both"}
    assert journal.classify(TARGET) == {"env": "target", "absent": "drift"}
    assert journal.classify({"env": {**BEFORE["env"], "data": b"owner edit"}, "absent": None})["env"] == "drift"


@pytest.mark.parametrize("field,value", [("mode", 0o600), ("uid", 1002), ("gid", 1002)])
def test_metadata_only_change_is_drift(journal_case, field, value):
    journal = make_journal(journal_case)
    journal.create(BEFORE, TARGET)
    current = {**BEFORE, "env": {**BEFORE["env"], field: value}}
    assert journal.classify(current)["env"] == "drift"


@pytest.mark.parametrize("field,value", [
    ("mode", True), ("mode", 0o1000), ("mode", -1), ("mode", 0.0),
    ("uid", True), ("uid", -1), ("uid", 4294967295), ("uid", "1000"),
    ("gid", True), ("gid", -1), ("gid", 4294967295), ("gid", 1.0),
    ("data", None), ("data", bytearray(b"mutable")),
])
def test_invalid_metadata_cannot_be_created_or_loaded(journal_case, field, value):
    journal = make_journal(journal_case)
    bad = {**BEFORE, "env": {**BEFORE["env"], field: value}}
    with pytest.raises(RuntimeError, match="snapshot invalid"):
        journal.create(bad, TARGET)
    assert not record_path(journal_case).exists()
    journal.create(BEFORE, TARGET)
    if field != "data":
        path = record_path(journal_case)
        encoded = json.loads(path.read_bytes())
        encoded["before"]["env"][field] = value
        path.write_text(json.dumps(encoded))
        with pytest.raises(RuntimeError, match="snapshot invalid"):
            journal.load()


def test_current_discovery_and_completed_replacement(journal_case):
    install, lease, module = journal_case
    assert module.Journal.current(install, lease.fd, NAMES) is None
    first = make_journal(journal_case)
    first.create(BEFORE, TARGET)
    discovered = module.Journal.current(install, lease.fd, NAMES)
    assert discovered.transaction_id == TXID
    assert discovered.load()["before"] == BEFORE
    second = module.Journal(install, "b" * 64, lease.fd, NAMES)
    saved = record_path(journal_case).read_bytes()
    with pytest.raises(FileExistsError, match="requires recovery"):
        second.create(TARGET, BEFORE)
    assert record_path(journal_case).read_bytes() == saved
    first.checkpoint(0, "rolling-back")
    first.checkpoint(1, "completed", "rollback")
    with pytest.raises(FileExistsError, match="reused"):
        first.create(BEFORE, TARGET)
    second.create(TARGET, BEFORE)
    assert module.Journal.current(install, lease.fd, NAMES).transaction_id == "b" * 64
    with pytest.raises(RuntimeError, match="transactionId mismatch"):
        first.checkpoint(0, "applying")
    assert second.load()["before"] == TARGET


def test_write_intent_survives_discovery_before_any_file_mutation(journal_case):
    install, lease, module = journal_case
    journal = make_journal(journal_case)
    journal.create(BEFORE, BEFORE)
    journal.checkpoint(0, "applying")
    planned = journal.plan(1, TARGET)
    assert planned["revision"] == 2
    discovered = module.Journal.current(install, lease.fd, NAMES)
    assert discovered.load()["target"] == TARGET
    assert discovered.load()["before"] == BEFORE
    assert not (install / ".env").exists()  # storage never mutates caller files
    assert journal.classify(BEFORE) == {"env": "before", "absent": "before"}
    with pytest.raises(RuntimeError, match="revision"):
        journal.plan(1, TARGET)
    with pytest.raises(RuntimeError, match="different write intent"):
        journal.plan(2, {"env": BEFORE["env"]})
    journal.checkpoint(2, "verifying")
    with pytest.raises(RuntimeError, match="cannot change"):
        journal.plan(3, TARGET)


def test_planned_deletion_cannot_be_replaced_by_a_second_intent(journal_case):
    journal = make_journal(journal_case)
    journal.create(BEFORE, BEFORE)
    journal.plan(0, {"env": None})
    with pytest.raises(RuntimeError, match="different write intent"):
        journal.plan(1, {"env": TARGET["env"]})
    assert journal.load()["target"]["env"] is None


def test_duplicate_create_preserves_first_record(journal_case):
    journal = make_journal(journal_case)
    journal.create(BEFORE, TARGET)
    path = record_path(journal_case)
    first = path.read_bytes()
    with pytest.raises(FileExistsError):
        journal.create(TARGET, BEFORE)
    assert path.read_bytes() == first
    loaded = journal.load()
    assert loaded["before"] == BEFORE and loaded["target"] == TARGET
    assert not list(path.parent.glob(".tmp-*"))


def test_unrelated_fd_refused_before_storage_creation(journal_case, tmp_path):
    install, _, module = journal_case
    path = tmp_path / "unrelated"
    path.touch(mode=0o600)
    with path.open("rb") as stream:
        journal = module.Journal(install, TXID, stream.fileno(), NAMES)
        with pytest.raises(RuntimeError, match="canonical lock"):
            journal.create(BEFORE, TARGET)
    assert not (install / "data" / "model-transitions").exists()


def test_private_directory_symlink_refused(journal_case, tmp_path):
    target = tmp_path / "outside"
    target.mkdir(mode=0o700)
    record_path(journal_case).parent.symlink_to(target, target_is_directory=True)
    with pytest.raises((RuntimeError, OSError)):
        make_journal(journal_case).create(BEFORE, TARGET)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("damage", ["symlink", "hardlink", "mode", "base64", "digest", "duplicate-key"])
def test_unsafe_record_refused_without_repair(journal_case, tmp_path, damage):
    journal = make_journal(journal_case)
    journal.create(BEFORE, TARGET)
    path = record_path(journal_case)
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    if damage == "symlink":
        path.unlink()
        path.symlink_to(outside)
    elif damage == "hardlink":
        os.link(path, tmp_path / "second-link")
    elif damage == "mode":
        path.chmod(0o644)
    elif damage == "duplicate-key":
        path.write_bytes(path.read_bytes().replace(b'{', b'{"schemaVersion":1,', 1))
    else:
        record = json.loads(path.read_bytes())
        record["before"]["env"]["data" if damage == "base64" else "sha256"] = "!"
        path.write_text(json.dumps(record))
    saved, outside_saved = path.read_bytes(), outside.read_bytes()
    with pytest.raises((RuntimeError, OSError)):
        journal.load()
    with pytest.raises((RuntimeError, OSError)):
        journal_case[2].Journal.current(journal_case[0], journal_case[1].fd, NAMES)
    with pytest.raises((RuntimeError, OSError)):
        journal.checkpoint(0, "applying")
    assert path.read_bytes() == saved
    assert outside.read_bytes() == outside_saved


@pytest.mark.parametrize("boundary", ["fsync-file", "rename", "fsync-directory"])
def test_checkpoint_failure_keeps_a_complete_record(journal_case, monkeypatch, boundary):
    journal = make_journal(journal_case)
    journal.create(BEFORE, TARGET)
    module = journal_case[2]
    original_fsync = os.fsync

    def fail_sync(fd):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if is_directory == (boundary == "fsync-directory"):
            raise OSError("injected fsync failure")
        return original_fsync(fd)

    def fail_rename(*args, **kwargs):
        raise OSError("injected rename failure")

    with monkeypatch.context() as patch:
        if boundary == "rename":
            patch.setattr(module.os, "replace", fail_rename)
        else:
            patch.setattr(module.os, "fsync", fail_sync)
        with pytest.raises(OSError, match="injected"):
            journal.checkpoint(0, "applying")
    recovered = make_journal(journal_case).load()
    assert recovered["phase"] == ("applying" if boundary == "fsync-directory" else "prepared")
    assert recovered["before"] == BEFORE
    assert recovered["target"] == TARGET
    assert list(record_path(journal_case).parent.iterdir()) == [record_path(journal_case)]


@pytest.mark.parametrize("boundary", ["before-replace", "after-replace"])
def test_killed_writer_releases_lease_and_leaves_readable_record(journal_case, boundary):
    install, lease, module = journal_case
    make_journal(journal_case).create(BEFORE, TARGET)
    lease.close()
    code = """
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import model_lifecycle_lock as locks
from model_transition_journal import Journal
replace = os.replace
def crash(*args, **kwargs):
    if sys.argv[4] == 'after-replace':
        replace(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGKILL)
with locks.acquire(Path(sys.argv[2])) as lease:
    journal = Journal(Path(sys.argv[2]), sys.argv[3], lease.fd, {'env','absent'})
    os.replace = crash
    journal.checkpoint(0, 'applying')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "bin"), str(install), TXID, boundary],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    import model_lifecycle_lock
    with model_lifecycle_lock.acquire(install) as recovered_lease:
        discovered = module.Journal.current(install, recovered_lease.fd, NAMES)
        assert discovered.transaction_id == TXID
        recovered = discovered.load()
        with pytest.raises(FileExistsError, match="requires recovery"):
            module.Journal(install, "b" * 64, recovered_lease.fd, NAMES).create(TARGET, BEFORE)
    assert recovered["before"] == BEFORE and recovered["target"] == TARGET
    assert recovered["phase"] == ("prepared" if boundary == "before-replace" else "applying")


def test_disappearing_open_record_is_not_reported_as_no_transaction(journal_case, monkeypatch):
    install, lease, module = journal_case
    make_journal(journal_case).create(BEFORE, TARGET)
    original_stat = os.stat

    def missing_after_open(path, *args, **kwargs):
        if path == "current.json" and kwargs.get("dir_fd") is not None:
            raise FileNotFoundError("injected loss after record open")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "stat", missing_after_open)
    with pytest.raises(FileNotFoundError, match="injected loss"):
        module.Journal.current(install, lease.fd, NAMES)


def test_directory_replaced_during_missing_record_check_refuses(journal_case, monkeypatch):
    install, lease, module = journal_case
    make_journal(journal_case).create(BEFORE, TARGET)
    directory = record_path(journal_case).parent
    parked = directory.with_name("parked")
    saved = record_path(journal_case).read_bytes()
    original_open = os.open

    def replace_directory(path, flags, *args, **kwargs):
        if path == "current.json" and kwargs.get("dir_fd") is not None:
            directory.rename(parked)
            directory.mkdir(mode=0o700)
            raise FileNotFoundError("injected missing leaf during parent replacement")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", replace_directory)
    with pytest.raises(RuntimeError, match="binding changed"):
        module.Journal.current(install, lease.fd, NAMES)
    assert (parked / "current.json").read_bytes() == saved
    assert list(directory.iterdir()) == []
