"""Linux source-only custody tests for future paired streaming restore."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_restore_journal as journals  # noqa: E402
import extension_data_stream_snapshot as snapshots  # noqa: E402
from extension_lifecycle_work import LifecycleWorkExecutionError  # noqa: E402
from test_extension_data_stream_snapshot import _command, _protocol_hash, _roots, _write  # noqa: E402


linux_effect = pytest.mark.skipif(os.name != "posix", reason="descriptor-relative Linux restore journal")


def _ready(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    journal_root = data / "assistant-first" / "restore-journals"
    journal_root.mkdir(mode=0o700)
    journal_root.chmod(0o700)
    _write(alpha / "note", b"private source")
    backup_command = _command()
    backup_command = replace(backup_command, request_hash=_protocol_hash(backup_command, "backup"))
    store = snapshots.StreamSnapshotStore(install, data, backup)
    store.backup(backup_command)
    restore_command = replace(
        backup_command, operation_key="restore",
        plan_material=replace(backup_command.plan_material, state="reconciling"),
        request_hash=_protocol_hash(backup_command, "restore"),
    )
    journal = journals.RestoreIntentJournal(install, journal_root)
    return install, backup, alpha, store, restore_command, journal_root, journal


@linux_effect
def test_verified_archive_intent_is_first_write_wins_and_secret_free(tmp_path: Path):
    _install, _backup, alpha, store, command, root, journal = _ready(tmp_path)
    with store.open_verified(command) as (_archive, document, receipt):
        alpha_path = document["services"][0]["paths"][0]
        first = journal.begin(command, receipt, "alpha", 0, alpha_path)
        again = journal.begin(command, receipt, "alpha", 0, alpha_path)
        assert again == first
        assert first["sourcePresent"] is True
        assert first["targetBefore"]["ino"] == str(alpha.stat().st_ino)
        beta_path = document["services"][1]["paths"][0]
        absent = journal.begin(command, receipt, "beta", 0, beta_path)
        assert absent["sourcePresent"] is False
        assert absent["targetBefore"] is None
        with pytest.raises(LifecycleWorkExecutionError) as caught:
            journal.begin(command, replace(receipt, archive_sha256="0" * 64), "alpha", 0, alpha_path)
        assert caught.value.code == "lifecycle-work-data-restore-intent-mismatch"
    records = list(root.iterdir())
    assert len(records) == 2
    assert all(item.stat().st_mode & 0o777 == 0o400 for item in records)
    assert all(b"private source" not in item.read_bytes() for item in records)


@linux_effect
def test_crash_after_journal_link_recovers_only_matching_temp(tmp_path: Path, monkeypatch):
    _install, _backup, _alpha, store, command, root, journal = _ready(tmp_path)
    original = journals.os.unlink
    interrupted = False

    def fail_first_temp(name, *, dir_fd=None):
        nonlocal interrupted
        if not interrupted and isinstance(name, str) and name.startswith(".tmp-r-"):
            interrupted = True
            raise OSError("simulated crash after journal link")
        return original(name, dir_fd=dir_fd)

    monkeypatch.setattr(journals.os, "unlink", fail_first_temp)
    with store.open_verified(command) as (_archive, document, receipt):
        path = document["services"][0]["paths"][0]
        with pytest.raises(LifecycleWorkExecutionError):
            journal.begin(command, receipt, "alpha", 0, path)
        finals = [item for item in root.iterdir() if item.name.startswith("r-")]
        assert interrupted and len(finals) == 1 and finals[0].stat().st_nlink == 2
        recovered = journal.begin(command, receipt, "alpha", 0, path)
        assert recovered["sourcePresent"] is True
        assert finals[0].stat().st_nlink == 1
        assert len(list(root.iterdir())) == 1


@linux_effect
def test_target_swap_after_intent_is_drift_not_implicit_approval(tmp_path: Path):
    install, _backup, alpha, store, command, _root, journal = _ready(tmp_path)
    with store.open_verified(command) as (_archive, document, receipt):
        path_state = document["services"][0]["paths"][0]
        journal.begin(command, receipt, "alpha", 0, path_state)
    alpha.rename(install / "data" / "held-alpha")
    alpha.mkdir(mode=0o700)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        journal.expect_original_target(command, receipt, "alpha", 0, path_state)
    assert caught.value.code == "lifecycle-work-data-restore-target-drift"


@linux_effect
@pytest.mark.parametrize("which", ["stage", "quarantine"])
def test_derived_transient_collision_refuses_new_intent(tmp_path: Path, which: str):
    install, _backup, _alpha, store, command, root, journal = _ready(tmp_path)
    with store.open_verified(command) as (_archive, document, receipt):
        path_state = document["services"][0]["paths"][0]
        _key, _record, stage, quarantine = journals._names(command, "alpha", 0, path_state["path"])
        collision = install / "data" / (stage if which == "stage" else quarantine)
        collision.mkdir(mode=0o700)
        with pytest.raises(LifecycleWorkExecutionError) as caught:
            journal.begin(command, receipt, "alpha", 0, path_state)
    assert caught.value.code == "lifecycle-work-data-restore-transient-collision"
    assert not list(root.iterdir())
    assert collision.is_dir()


@linux_effect
def test_symlinked_target_refuses_intent_before_publication(tmp_path: Path):
    install, _backup, alpha, store, command, root, journal = _ready(tmp_path)
    alpha.rename(install / "data" / "held-alpha")
    alpha.symlink_to(install / "data" / "held-alpha", target_is_directory=True)
    with store.open_verified(command) as (_archive, document, receipt):
        with pytest.raises(LifecycleWorkExecutionError) as caught:
            journal.begin(command, receipt, "alpha", 0, document["services"][0]["paths"][0])
    assert caught.value.code == "lifecycle-work-data-restore-target-unsafe"
    assert not list(root.iterdir())
