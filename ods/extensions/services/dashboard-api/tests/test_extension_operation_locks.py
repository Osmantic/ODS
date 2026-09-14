"""Tests for the shared single/composite extension operation locks."""

from __future__ import annotations

import contextlib
import multiprocessing
import os
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
    with factory.lock_services(object(), ["documents"]):
        pass

    assert calls == [True]


def test_factory_creates_a_missing_private_lock_root(tmp_path):
    lock_root = tmp_path / "locks"
    factory = locks.FileServiceLockFactory(lock_root, timeout=1)

    with factory.lock_services(object(), ["documents"]):
        assert (lock_root / ".extension-operation-locks").is_dir()
    if os.name == "posix":
        assert lock_root.stat().st_mode & 0o777 == 0o700


def test_mutation_guard_precedes_sorted_service_locks(tmp_path, monkeypatch):
    entered = []
    exited = []

    @contextlib.contextmanager
    def recording_lock(lock_path, *, timeout=None):
        del timeout
        entered.append(lock_path.name)
        try:
            yield
        finally:
            exited.append(lock_path.name)

    monkeypatch.setattr(locks, "exclusive_file_lock", recording_lock)

    with locks.lock_mutation_and_services(
        tmp_path, ["voice", "documents", "voice"], timeout=5
    ) as service_ids:
        assert service_ids == ("documents", "voice")

    assert entered == [
        locks.operation_lock_path(
            tmp_path, locks.MUTATION_GUARD_SERVICE_ID
        ).name,
        locks.operation_lock_path(tmp_path, "documents").name,
        locks.operation_lock_path(tmp_path, "voice").name,
    ]
    assert exited == list(reversed(entered))


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner/mode contract")
def test_mutation_guard_directory_is_private_and_repairs_owner_controlled_mode(
    tmp_path,
):
    guard = locks.mutation_guard_path(tmp_path)
    assert guard.parent.stat().st_mode & 0o777 == 0o700

    guard.parent.chmod(0o755)
    with pytest.raises(
        locks.ServiceLockError, match="mutation-guard-directory-mode-unsafe"
    ):
        locks.mutation_guard_path(tmp_path)

    assert locks.mutation_guard_path(tmp_path, repair_mode=True) == guard
    assert guard.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner contract")
def test_mutation_guard_rejects_foreign_owned_directory(tmp_path, monkeypatch):
    guard_dir = locks.operation_lock_directory(tmp_path)
    monkeypatch.setattr(locks.os, "geteuid", lambda: guard_dir.stat().st_uid + 1)

    with pytest.raises(
        locks.ServiceLockError, match="mutation-guard-parent-owner-unsafe"
    ):
        locks.mutation_guard_path(tmp_path, repair_mode=True)


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent mode contract")
def test_mutation_guard_rejects_group_writable_parent(tmp_path):
    tmp_path.chmod(0o770)
    try:
        with pytest.raises(
            locks.ServiceLockError, match="mutation-guard-parent-mode-unsafe"
        ):
            locks.mutation_guard_path(tmp_path, repair_mode=True)
    finally:
        tmp_path.chmod(0o700)


def test_global_lock_validates_service_ids_before_creating_guard(tmp_path):
    with pytest.raises(locks.ServiceLockError, match="invalid-service-id"):
        with locks.lock_mutation_and_services(tmp_path, ["../voice"]):
            pass

    assert not (tmp_path / ".extension-operation-locks").exists()


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True, "1"])
def test_invalid_timeout_fails_before_lock_directory_creation(tmp_path, timeout):
    with pytest.raises(locks.ServiceLockError, match="invalid-lock-timeout"):
        with locks.lock_services(tmp_path, ["documents"], timeout=timeout):
            pass

    assert not (tmp_path / ".extension-operation-locks").exists()


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True, "1"])
def test_exclusive_lock_rejects_invalid_timeout_before_file_creation(
    tmp_path, timeout
):
    lock_path = tmp_path / "direct.lock"
    with pytest.raises(locks.ServiceLockError, match="invalid-lock-timeout"):
        with locks.exclusive_file_lock(lock_path, timeout=timeout):
            pass
    assert not lock_path.exists()


def test_timeout_releases_already_acquired_composite_prefix(tmp_path):
    factory = locks.FileServiceLockFactory(tmp_path, timeout=0.1)
    blocked_path = locks.operation_lock_path(tmp_path, "voice")

    with locks.exclusive_file_lock(blocked_path):
        with pytest.raises(locks.ServiceLockTimeout):
            with factory.lock_services(object(), ["voice", "documents"]):
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


def test_hardlinked_lock_file_is_rejected(tmp_path):
    lock_path = locks.operation_lock_path(tmp_path, "documents")
    target = tmp_path / "unrelated-hardlink-target"
    target.write_bytes(b"unchanged")
    try:
        os.link(target, lock_path)
    except (OSError, NotImplementedError):
        pytest.skip("current filesystem cannot create hard links")

    with pytest.raises(locks.ServiceLockError, match="operation-lock-file-unsafe"):
        with locks.exclusive_file_lock(lock_path):
            pass

    assert target.read_bytes() == b"unchanged"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode assertion")
def test_new_lock_file_is_owner_only(tmp_path):
    lock_path = locks.operation_lock_path(tmp_path, "documents")

    with locks.exclusive_file_lock(lock_path):
        assert lock_path.stat().st_mode & 0o777 == 0o600


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
