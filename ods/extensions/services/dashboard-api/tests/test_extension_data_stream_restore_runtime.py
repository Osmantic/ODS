"""Linux source tests for the generic receipted restore dispatcher."""

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
import extension_data_stream_restore_runtime as runtime  # noqa: E402
from extension_lifecycle_work import LifecycleWorkUncertainEffect  # noqa: E402
from test_extension_data_restore_journal import _ready  # noqa: E402


linux_effect = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only data restore")


def _read_noatime(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        return os.read(descriptor, 1024)
    finally:
        os.close(descriptor)


@linux_effect
def test_all_paths_restore_and_replay_keep_original_under_quarantine(tmp_path: Path):
    install, _backup, alpha, store, command, _root, _journal = _ready(tmp_path)
    (alpha / "note").write_bytes(b"new applied data")
    original_inode = alpha.stat().st_ino
    dispatcher = runtime.StreamRestoreDispatcher(store)

    evidence = dispatcher(command, witness=lambda: True)
    assert len(evidence) == 64
    assert _read_noatime(alpha / "note") == b"private source"
    assert alpha.stat().st_ino != original_inode
    quarantine = next((install / "data").glob(".ods-restore-quarantine-*"))
    assert quarantine.stat().st_ino == original_inode
    assert _read_noatime(quarantine / "note") == b"new applied data"
    assert not (install / "data" / "beta").exists()

    restored_inode = alpha.stat().st_ino
    assert dispatcher(command, witness=lambda: True) == evidence
    assert alpha.stat().st_ino == restored_inode
    assert quarantine.stat().st_ino == original_inode


@linux_effect
def test_staging_all_paths_precedes_any_live_transition(tmp_path: Path, monkeypatch):
    _install, _backup, alpha, store, command, _root, _journal = _ready(tmp_path)
    original_inode = alpha.stat().st_ino
    original = staging.StreamRestoreStager.stage

    def fail_second(self, loaded, service_id, index):
        if service_id == "beta":
            raise RuntimeError("synthetic stage failure")
        return original(self, loaded, service_id, index)

    monkeypatch.setattr(staging.StreamRestoreStager, "stage", fail_second)
    with pytest.raises(LifecycleWorkUncertainEffect):
        runtime.StreamRestoreDispatcher(store)(command, witness=lambda: True)
    assert alpha.stat().st_ino == original_inode
    assert _read_noatime(alpha / "note") == b"private source"
    assert not list(alpha.parent.glob(".ods-restore-quarantine-*"))


@linux_effect
def test_interrupted_first_rename_resumes_under_new_witness(tmp_path: Path, monkeypatch):
    install, _backup, alpha, store, command, _root, _journal = _ready(tmp_path)
    (alpha / "note").write_bytes(b"new applied data")
    original_inode = alpha.stat().st_ino
    dispatcher = runtime.StreamRestoreDispatcher(store)
    rename = paired._no_replace
    interrupted = False

    def interrupt(parent, source, destination):
        nonlocal interrupted
        rename(parent, source, destination)
        if not interrupted:
            interrupted = True
            raise OSError("synthetic crash after first rename")

    with monkeypatch.context() as patcher:
        patcher.setattr(paired, "_no_replace", interrupt)
        with pytest.raises(LifecycleWorkUncertainEffect):
            dispatcher(command, witness=lambda: True)
    assert interrupted
    assert not alpha.exists()
    quarantine = next((install / "data").glob(".ods-restore-quarantine-*"))
    assert quarantine.stat().st_ino == original_inode
    assert _read_noatime(quarantine / "note") == b"new applied data"

    assert len(dispatcher(command, witness=lambda: True)) == 64
    assert _read_noatime(alpha / "note") == b"private source"
    assert quarantine.stat().st_ino == original_inode


@linux_effect
def test_missing_witness_fails_before_effect(tmp_path: Path):
    _install, _backup, alpha, store, command, _root, _journal = _ready(tmp_path)
    original_inode = alpha.stat().st_ino
    with pytest.raises(Exception):
        runtime.StreamRestoreDispatcher(store)(command)
    assert alpha.stat().st_ino == original_inode
    assert not list(alpha.parent.glob(".ods-restore-stage-*"))
