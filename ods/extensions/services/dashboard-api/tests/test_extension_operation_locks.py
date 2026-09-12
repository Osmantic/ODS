"""Tests for the shared single/composite extension operation locks."""

from __future__ import annotations

import contextlib
import multiprocessing
import sys
import threading
import time
from pathlib import Path

import pytest


DASHBOARD_API_DIR = Path(__file__).resolve().parent.parent
if str(DASHBOARD_API_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_API_DIR))

import extension_operation_locks as locks  # noqa: E402


def _hold_lock_until_terminated(lock_path: str, ready_path: str) -> None:
    """Child-process target used to prove kernel-owned crash release."""
    with locks.exclusive_file_lock(Path(lock_path)):
        Path(ready_path).write_text("ready", encoding="utf-8")
        while True:
            time.sleep(1)


def test_composite_locks_are_deduplicated_and_acquired_in_canonical_order(
    tmp_path, monkeypatch
):
    entered = []
    exited = []

    @contextlib.contextmanager
    def recording_lock(lock_path, *, timeout=None):
        entered.append((lock_path.name, timeout))
        try:
            yield
        finally:
            exited.append(lock_path.name)

    monkeypatch.setattr(locks, "exclusive_file_lock", recording_lock)

    with locks.lock_services(tmp_path, ["voice", "documents", "voice"], timeout=5):
        assert [name for name, _ in entered] == [
            locks.operation_lock_path(tmp_path, service_id).name
            for service_id in ("documents", "voice")
        ]

    assert exited == [name for name, _ in reversed(entered)]


@pytest.mark.parametrize(
    "service_id",
    ["", "../voice", "voice/search", "Voice", ".voice", "voice search"],
)
def test_invalid_service_id_fails_before_lock_directory_creation(tmp_path, service_id):
    with pytest.raises(locks.ServiceLockError, match="invalid-service-id"):
        with locks.lock_services(tmp_path, [service_id]):
            pass

    assert not (tmp_path / ".extension-operation-locks").exists()


def test_factory_resolves_parent_once_per_acquisition(tmp_path):
    calls = []

    def parent_provider():
        calls.append(True)
        return tmp_path

    factory = locks.FileServiceLockFactory(parent_provider, timeout=1)
    with factory.lock_services(["documents"]):
        pass

    assert calls == [True]


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True, "1"])
def test_invalid_timeout_fails_before_lock_directory_creation(tmp_path, timeout):
    with pytest.raises(locks.ServiceLockError, match="invalid-lock-timeout"):
        with locks.lock_services(tmp_path, ["documents"], timeout=timeout):
            pass

    assert not (tmp_path / ".extension-operation-locks").exists()


def test_timeout_releases_already_acquired_composite_prefix(tmp_path):
    factory = locks.FileServiceLockFactory(tmp_path, timeout=0.1)
    blocked_path = locks.operation_lock_path(tmp_path, "voice")

    with locks.exclusive_file_lock(blocked_path):
        with pytest.raises(locks.ServiceLockTimeout):
            with factory.lock_services(["voice", "documents"]):
                pytest.fail("caller must not run without the complete lock set")

    # "documents" sorts first and was acquired before "voice" timed out.  It
    # must have been unwound, not leaked by the failed composite acquisition.
    documents_path = locks.operation_lock_path(tmp_path, "documents")
    with locks.exclusive_file_lock(documents_path, timeout=0.1):
        pass


def test_competing_lock_times_out_then_succeeds_after_release(tmp_path):
    lock_path = locks.operation_lock_path(tmp_path, "documents")
    outcome = []

    def compete():
        try:
            with locks.exclusive_file_lock(lock_path, timeout=0.05):
                outcome.append("acquired")
        except locks.ServiceLockTimeout:
            outcome.append("timed-out")

    with locks.exclusive_file_lock(lock_path):
        thread = threading.Thread(target=compete)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert outcome == ["timed-out"]
    with locks.exclusive_file_lock(lock_path, timeout=0.1):
        pass


def test_cross_process_timeout_and_crash_release(tmp_path):
    lock_path = locks.operation_lock_path(tmp_path, "documents")
    ready_path = tmp_path / "child-ready"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_hold_lock_until_terminated,
        args=(str(lock_path), str(ready_path)),
    )
    process.start()
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists(), "child did not acquire the operation lock"

        with pytest.raises(locks.ServiceLockTimeout):
            with locks.exclusive_file_lock(lock_path, timeout=0.05):
                pass
    finally:
        process.terminate()
        process.join(timeout=5)

    assert not process.is_alive()
    with locks.exclusive_file_lock(lock_path, timeout=1):
        pass


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_symlinked_lock_file_is_rejected(tmp_path):
    lock_path = locks.operation_lock_path(tmp_path, "documents")
    target = tmp_path / "unrelated"
    target.write_text("unchanged", encoding="utf-8")
    try:
        lock_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("current account cannot create symlinks")

    with pytest.raises(locks.ServiceLockError, match="operation-lock-file-is-symlink"):
        with locks.exclusive_file_lock(lock_path):
            pass

    assert target.read_text(encoding="utf-8") == "unchanged"


def test_symlinked_lock_directory_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    lock_dir = tmp_path / ".extension-operation-locks"
    try:
        lock_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("current account cannot create directory symlinks")

    with pytest.raises(
        locks.ServiceLockError, match="operation-lock-directory-is-symlink"
    ):
        with locks.lock_services(tmp_path, ["documents"]):
            pass
