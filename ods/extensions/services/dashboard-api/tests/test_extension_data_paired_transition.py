"""Linux source-only paired-data transition tests; no host dispatcher is wired."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_paired_transition as paired  # noqa: E402
import extension_data_stream_restore as staging  # noqa: E402
from extension_lifecycle_work import LifecycleWorkExecutionError  # noqa: E402
from test_extension_data_restore_journal import _ready  # noqa: E402


linux_effect = pytest.mark.skipif(os.name != "posix", reason="Linux descriptor-relative renameat2")


def _read(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW)
    try:
        return os.read(descriptor, 1024 * 1024)
    finally:
        os.close(descriptor)


def _fixture(tmp_path: Path, *, current: bytes = b"current live data"):
    install, _backup, alpha, store, command, journal_root, journal = _ready(tmp_path)
    (alpha / "note").write_bytes(current)
    stager = staging.StreamRestoreStager(install, store, journal)
    stage_name = stager.stage(command, "alpha", 0)
    assert stage_name is not None
    with store.open_verified(command) as (_archive, document, receipt):
        intent = journal.begin(command, receipt, "alpha", 0, document["services"][0]["paths"][0])
    transition = paired.PairedDataTransition(install, store, journal)
    return install, alpha, store, command, journal_root, journal, intent, transition


@linux_effect
def test_missing_quiescence_gate_refuses_before_any_live_effect(tmp_path: Path):
    install, alpha, _store, command, root, _journal, intent, transition = _fixture(tmp_path)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        transition.apply(command, "alpha", 0, quiesced=None)
    assert caught.value.code == "lifecycle-work-data-transition-quiescence-required"
    assert _read(alpha / "note") == b"current live data"
    assert not (install / "data" / intent["quarantineName"]).exists()
    assert not list(root.glob("p-*"))


@linux_effect
def test_live_swap_retains_exact_original_and_complete_replay_is_read_only(tmp_path: Path):
    install, alpha, _store, command, root, _journal, intent, transition = _fixture(tmp_path)
    original_inode = alpha.stat().st_ino
    staged_inode = (install / "data" / intent["stageName"]).stat().st_ino
    first = transition.apply(command, "alpha", 0, quiesced=lambda: True)
    quarantine = install / "data" / intent["quarantineName"]
    assert _read(alpha / "note") == b"private source"
    assert _read(quarantine / "note") == b"current live data"
    assert alpha.stat().st_ino == staged_inode and quarantine.stat().st_ino == original_inode
    assert not (install / "data" / intent["stageName"]).exists()
    before = (alpha.stat().st_ino, quarantine.stat().st_ino, sorted(item.name for item in root.iterdir()))
    assert transition.apply(command, "alpha", 0, quiesced=lambda: True) == first
    assert (alpha.stat().st_ino, quarantine.stat().st_ino,
            sorted(item.name for item in root.iterdir())) == before


@linux_effect
def test_crash_after_target_to_quarantine_recovers_only_proven_mid_state(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, root, _journal, intent, transition = _fixture(tmp_path)
    publish = paired._publish_exact
    interrupted = False

    def interrupt(descriptor, name, record):
        nonlocal interrupted
        if name.endswith("-quarantined.json") and not interrupted:
            interrupted = True
            raise OSError("fault injection after first directory rename")
        publish(descriptor, name, record)

    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_publish_exact", interrupt)
        with pytest.raises(LifecycleWorkExecutionError):
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert interrupted and not alpha.exists()
    assert _read(install / "data" / intent["quarantineName"] / "note") == b"current live data"
    assert _read(install / "data" / intent["stageName"] / "note") == b"private source"
    assert not list(root.glob("p-*-quarantined.json"))
    transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert _read(alpha / "note") == b"private source"
    assert _read(install / "data" / intent["quarantineName"] / "note") == b"current live data"


@linux_effect
def test_crash_after_stage_to_target_replays_without_swapping_again(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, root, _journal, intent, transition = _fixture(tmp_path)
    publish = paired._publish_exact
    interrupted = False

    def interrupt(descriptor, name, record):
        nonlocal interrupted
        if name.endswith("-installed.json") and not interrupted:
            interrupted = True
            raise OSError("fault injection after second directory rename")
        publish(descriptor, name, record)

    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_publish_exact", interrupt)
        with pytest.raises(LifecycleWorkExecutionError):
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert interrupted and _read(alpha / "note") == b"private source"
    assert _read(install / "data" / intent["quarantineName"] / "note") == b"current live data"
    inode = alpha.stat().st_ino
    assert not list(root.glob("p-*-installed.json"))
    transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert alpha.stat().st_ino == inode and _read(alpha / "note") == b"private source"


@linux_effect
def test_deep_target_drift_after_prepared_marker_refuses_before_rename(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, root, _journal, intent, transition = _fixture(tmp_path)
    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_no_replace", lambda *_args: paired._fail("injected-stop"))
        with pytest.raises(LifecycleWorkExecutionError):
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert list(root.glob("p-*-prepared.json"))
    (alpha / "note").write_bytes(b"foreign live data")
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert caught.value.code == "lifecycle-work-data-transition-original-drift"
    assert _read(alpha / "note") == b"foreign live data"
    assert not (install / "data" / intent["quarantineName"]).exists()


@linux_effect
def test_stage_drift_after_prepared_marker_refuses_before_rename(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, _root, _journal, intent, transition = _fixture(tmp_path)
    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_no_replace", lambda *_args: paired._fail("injected-stop"))
        with pytest.raises(LifecycleWorkExecutionError):
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    stage_note = install / "data" / intent["stageName"] / "note"
    stage_note.write_bytes(b"foreign stage")
    with pytest.raises(LifecycleWorkExecutionError):
        transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert _read(alpha / "note") == b"current live data"
    assert not (install / "data" / intent["quarantineName"]).exists()


@linux_effect
def test_foreign_quarantine_collision_refuses_without_effect(tmp_path: Path):
    install, alpha, _store, command, root, _journal, intent, transition = _fixture(tmp_path)
    foreign = install / "data" / intent["quarantineName"]
    foreign.mkdir(mode=0o700)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert caught.value.code == "lifecycle-work-data-transition-fresh-state-invalid"
    assert alpha.exists() and foreign.exists() and not list(root.glob("p-*"))


@linux_effect
def test_original_absent_direct_stage_to_target_keeps_foreign_held_path(tmp_path: Path):
    install, _backup, alpha, store, command, _root, journal = _ready(tmp_path)
    held = install / "data" / "held-original"
    alpha.rename(held)
    assert staging.StreamRestoreStager(install, store, journal).stage(command, "alpha", 0)
    paired.PairedDataTransition(install, store, journal).apply(command, "alpha", 0, quiesced=lambda: True)
    assert _read(alpha / "note") == b"private source"
    assert _read(held / "note") == b"private source"
    assert not list((install / "data").glob(".ods-restore-quarantine-*"))


@linux_effect
def test_source_absent_quarantines_present_target_without_creating_stage(tmp_path: Path):
    install, _backup, _alpha, store, command, _root, journal = _ready(tmp_path)
    beta = install / "data" / "beta"
    beta.mkdir(mode=0o700)
    (beta / "note").write_bytes(b"new beta data")
    (beta / "note").chmod(0o600)
    assert staging.StreamRestoreStager(install, store, journal).stage(command, "beta", 0) is None
    with store.open_verified(command) as (_archive, document, receipt):
        intent = journal.begin(command, receipt, "beta", 0, document["services"][1]["paths"][0])
    transition = paired.PairedDataTransition(install, store, journal)
    first = transition.apply(command, "beta", 0, quiesced=lambda: True)
    assert not beta.exists()
    quarantine = install / "data" / intent["quarantineName"]
    assert _read(quarantine / "note") == b"new beta data"
    assert transition.apply(command, "beta", 0, quiesced=lambda: True) == first
    assert _read(quarantine / "note") == b"new beta data"


@linux_effect
def test_source_and_target_both_absent_publish_only_noop_marker(tmp_path: Path):
    install, _backup, _alpha, store, command, root, journal = _ready(tmp_path)
    assert staging.StreamRestoreStager(install, store, journal).stage(command, "beta", 0) is None
    transition = paired.PairedDataTransition(install, store, journal)
    first = transition.apply(command, "beta", 0, quiesced=lambda: True)
    assert transition.apply(command, "beta", 0, quiesced=lambda: True) == first
    assert not (install / "data" / "beta").exists()
    assert not list((install / "data").glob(".ods-restore-*"))
    assert len(list(root.glob("p-*"))) == 2


@linux_effect
def test_lost_quiescence_after_first_rename_retains_both_proven_trees(tmp_path: Path):
    install, alpha, _store, command, _root, _journal, intent, transition = _fixture(tmp_path)
    quarantine = install / "data" / intent["quarantineName"]

    def gate():
        return not quarantine.exists()

    with pytest.raises(LifecycleWorkExecutionError) as caught:
        transition.apply(command, "alpha", 0, quiesced=gate)
    assert caught.value.code == "lifecycle-work-data-transition-not-quiesced"
    assert not alpha.exists() and _read(quarantine / "note") == b"current live data"
    assert _read(install / "data" / intent["stageName"] / "note") == b"private source"
    transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert _read(alpha / "note") == b"private source"


@linux_effect
def test_foreign_target_after_quarantine_blocks_second_no_replace(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, _root, _journal, intent, transition = _fixture(tmp_path)
    publish = paired._publish_exact
    injected = False

    def inject(descriptor, name, record):
        nonlocal injected
        publish(descriptor, name, record)
        if name.endswith("-quarantined.json") and not injected:
            injected = True
            alpha.mkdir(mode=0o700)

    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_publish_exact", inject)
        with pytest.raises(LifecycleWorkExecutionError) as caught:
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert injected and caught.value.code == "lifecycle-work-data-transition-ambiguous-state"
    assert alpha.is_dir() and not list(alpha.iterdir())
    assert _read(install / "data" / intent["quarantineName"] / "note") == b"current live data"
    assert _read(install / "data" / intent["stageName"] / "note") == b"private source"


@linux_effect
def test_no_replace_unavailable_refuses_with_prepared_receipt_and_no_live_move(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, root, _journal, intent, transition = _fixture(tmp_path)
    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_no_replace", lambda *_args: paired._fail(
            "lifecycle-work-data-transition-no-replace-unsupported"))
        with pytest.raises(LifecycleWorkExecutionError) as caught:
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert caught.value.code == "lifecycle-work-data-transition-no-replace-unsupported"
    assert alpha.exists() and not (install / "data" / intent["quarantineName"]).exists()
    assert list(root.glob("p-*-prepared.json"))


@linux_effect
def test_missing_renameat2_symbol_never_falls_back_to_plain_rename(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, _root, _journal, intent, transition = _fixture(tmp_path)
    with monkeypatch.context() as patcher:
        patcher.setattr(paired.ctypes, "CDLL", lambda *_args, **_kwargs: object())
        with pytest.raises(LifecycleWorkExecutionError) as caught:
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert caught.value.code == "lifecycle-work-data-transition-no-replace-unsupported"
    assert _read(alpha / "note") == b"current live data"
    assert _read(install / "data" / intent["stageName"] / "note") == b"private source"


@linux_effect
def test_quarantine_appearing_at_rename_boundary_is_never_overwritten(tmp_path: Path, monkeypatch):
    install, alpha, _store, command, _root, _journal, intent, transition = _fixture(tmp_path)
    rename = paired._no_replace
    collided = False

    def race(parent, source, destination):
        nonlocal collided
        if destination == intent["quarantineName"] and not collided:
            collided = True
            os.mkdir(destination, 0o700, dir_fd=parent)
        rename(parent, source, destination)

    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_no_replace", race)
        with pytest.raises(LifecycleWorkExecutionError) as caught:
            transition.apply(command, "alpha", 0, quiesced=lambda: True)
    assert collided and caught.value.code == "lifecycle-work-data-transition-collision"
    assert _read(alpha / "note") == b"current live data"
    assert _read(install / "data" / intent["stageName"] / "note") == b"private source"
    assert (install / "data" / intent["quarantineName"]).is_dir()
