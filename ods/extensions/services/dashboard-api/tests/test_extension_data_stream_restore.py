"""Linux source-only stage custody tests; no live restore effect."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_stream_restore as restores  # noqa: E402
import extension_data_restore_journal as journals  # noqa: E402
import extension_data_stream_snapshot as snapshots  # noqa: E402
from extension_lifecycle_work import LifecycleWorkExecutionError  # noqa: E402
from test_extension_data_restore_journal import _ready  # noqa: E402
from test_extension_data_stream_snapshot import _command, _protocol_hash, _roots, _write  # noqa: E402


linux_effect = pytest.mark.skipif(os.name != "posix", reason="descriptor-relative Linux restore stage")


def _read_noatime(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


@linux_effect
def test_staged_verified_bytes_leave_live_target_untouched_and_complete_replay(tmp_path: Path):
    install, _backup, alpha, store, command, _root, journal = _ready(tmp_path)
    source = alpha / "note"
    before = (alpha.stat().st_ino, alpha.stat().st_mtime_ns,
              source.stat().st_ino, source.stat().st_atime_ns,
              source.stat().st_mtime_ns, _read_noatime(source))
    stager = restores.StreamRestoreStager(install, store, journal)
    stage_name = stager.stage(command, "alpha", 0)
    assert stage_name is not None
    staged = install / "data" / stage_name
    assert staged.is_dir() and _read_noatime(staged / "note") == before[-1]
    assert (staged / "note").stat().st_mtime_ns == before[4]
    stage_before = (staged.stat().st_ino, staged.stat().st_mtime_ns)
    assert stager.stage(command, "alpha", 0) == stage_name
    assert (staged.stat().st_ino, staged.stat().st_mtime_ns) == stage_before
    assert (alpha.stat().st_ino, alpha.stat().st_mtime_ns,
            source.stat().st_ino, source.stat().st_atime_ns,
            source.stat().st_mtime_ns, _read_noatime(source)) == before


@linux_effect
def test_absent_source_publishes_intent_but_does_not_create_stage(tmp_path: Path):
    install, _backup, _alpha, store, command, root, journal = _ready(tmp_path)
    stager = restores.StreamRestoreStager(install, store, journal)
    assert stager.stage(command, "beta", 0) is None
    assert len(list(root.iterdir())) == 1
    assert not list((install / "data").glob(".ods-restore-stage-*"))


@linux_effect
def test_partial_stage_is_retained_and_refused_on_retry(tmp_path: Path, monkeypatch):
    install, _backup, alpha, store, command, _root, journal = _ready(tmp_path)
    with store.open_verified(command) as (_archive, document, receipt):
        path = document["services"][0]["paths"][0]
        intent = journal.begin(command, receipt, "alpha", 0, path)
    original = os.write
    writes = 0

    def interrupt(fd, content):
        nonlocal writes
        writes += 1
        if writes == 1:
            return original(fd, content[:max(1, len(content) // 2)])
        raise OSError("simulated interrupted stage copy")

    stager = restores.StreamRestoreStager(install, store, journal)
    with monkeypatch.context() as patcher:
        patcher.setattr(restores.os, "write", interrupt)
        with pytest.raises(LifecycleWorkExecutionError):
            stager.stage(command, "alpha", 0)
    stage = install / "data" / intent["stageName"]
    assert stage.is_dir() and (stage / "note").exists()
    assert (stage / "note").read_bytes() != (alpha / "note").read_bytes()
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        stager.stage(command, "alpha", 0)
    assert caught.value.code == "lifecycle-work-data-restore-stage-readback-invalid"
    assert (alpha / "note").read_bytes() == b"private source"
    assert stage.is_dir()


@linux_effect
def test_symlink_stage_collision_after_intent_refuses_without_following(tmp_path: Path):
    install, _backup, alpha, store, command, _root, journal = _ready(tmp_path)
    with store.open_verified(command) as (_archive, document, receipt):
        path = document["services"][0]["paths"][0]
        intent = journal.begin(command, receipt, "alpha", 0, path)
    stage = install / "data" / intent["stageName"]
    stage.symlink_to(alpha, target_is_directory=True)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        restores.StreamRestoreStager(install, store, journal).stage(command, "alpha", 0)
    assert caught.value.code == "lifecycle-work-data-restore-stage-collision"
    assert stage.is_symlink() and (alpha / "note").read_bytes() == b"private source"


@linux_effect
def test_nested_tree_modes_and_nanosecond_times_are_staged_exactly(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    journal_root = data / "assistant-first" / "restore-journals"
    journal_root.mkdir(mode=0o700)
    journal_root.chmod(0o700)
    nested = alpha / "deep"
    nested.mkdir(mode=0o750)
    nested.chmod(0o750)
    source = nested / "payload"
    _write(source, b"nested private bytes")
    source.chmod(0o640)
    file_ns = (1_600_000_000_123_456_789, 1_600_000_001_987_654_321)
    dir_ns = (1_600_000_002_222_222_222, 1_600_000_003_333_333_333)
    root_ns = (1_600_000_004_444_444_444, 1_600_000_005_555_555_555)
    os.utime(source, ns=file_ns)
    os.utime(nested, ns=dir_ns)
    alpha.chmod(0o750)
    os.utime(alpha, ns=root_ns)
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
    stager = restores.StreamRestoreStager(install, store, journal)
    stage_name = stager.stage(restore_command, "alpha", 0)
    assert stage_name is not None
    stage = install / "data" / stage_name
    staged_dir, staged_file = stage / "deep", stage / "deep" / "payload"
    assert _read_noatime(staged_file) == b"nested private bytes"
    for staged, original, mode, times in (
        (stage, alpha, 0o750, root_ns),
        (staged_dir, nested, 0o750, dir_ns),
        (staged_file, source, 0o640, file_ns),
    ):
        observed, prior = staged.stat(), original.stat()
        assert (observed.st_mode & 0o7777, observed.st_atime_ns, observed.st_mtime_ns) == (mode, *times)
        assert (prior.st_mode & 0o7777, prior.st_atime_ns, prior.st_mtime_ns) == (mode, *times)
    assert stager.stage(restore_command, "alpha", 0) == stage_name


@linux_effect
@pytest.mark.parametrize("tamper", ["content", "extra"])
def test_complete_stage_replay_refuses_tampered_or_foreign_tree(tmp_path: Path, tamper: str):
    install, _backup, alpha, store, command, _root, journal = _ready(tmp_path)
    stager = restores.StreamRestoreStager(install, store, journal)
    stage_name = stager.stage(command, "alpha", 0)
    assert stage_name is not None
    stage = install / "data" / stage_name
    if tamper == "content":
        (stage / "note").write_bytes(b"unapproved bytes")
    else:
        (stage / "unexpected").write_bytes(b"foreign")
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        stager.stage(command, "alpha", 0)
    assert caught.value.code == "lifecycle-work-data-restore-stage-readback-invalid"
    assert stage.is_dir() and (alpha / "note").read_bytes() == b"private source"


@linux_effect
def test_stage_path_swap_after_descriptor_pin_cannot_write_into_live_tree(tmp_path: Path, monkeypatch):
    install, _backup, alpha, store, command, _root, journal = _ready(tmp_path)
    with store.open_verified(command) as (_archive, document, receipt):
        path = document["services"][0]["paths"][0]
        intent = journal.begin(command, receipt, "alpha", 0, path)
    visible = install / "data" / intent["stageName"]
    held = install / "data" / "held-stage"
    original = restores._copy_file
    swapped = False

    def swap_before_copy(parent, name, archive, member, entry):
        nonlocal swapped
        if not swapped:
            swapped = True
            visible.rename(held)
            visible.symlink_to(alpha, target_is_directory=True)
        return original(parent, name, archive, member, entry)

    monkeypatch.setattr(restores, "_copy_file", swap_before_copy)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        restores.StreamRestoreStager(install, store, journal).stage(command, "alpha", 0)
    assert caught.value.code == "lifecycle-work-data-restore-stage-drift"
    assert swapped and visible.is_symlink()
    assert (held / "note").read_bytes() == b"private source"
    assert (alpha / "note").read_bytes() == b"private source"
