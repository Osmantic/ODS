"""Cross-process locks for extension lifecycle operations.

Single-extension routes and composite transactions must use the same lock
namespace.  The lock files live beside the durable ODS data root rather than
inside an extension directory, so reinstalling an extension cannot replace a
held lock inode.  Composite acquisitions are sorted and completed before the
caller is allowed to mutate anything.

These locks are advisory.  Every process that mutates extension lifecycle
state must participate in this protocol and must see the same underlying data
directory.  Kernel locks are released automatically when a process exits.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import stat
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable, Iterable

if os.name == "nt":  # pragma: no cover - branch covered on Windows CI
    fcntl = None
    import msvcrt
else:  # pragma: no cover - branch covered on POSIX CI
    import fcntl

    msvcrt = None


_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_LOCK_DIRECTORY = ".extension-operation-locks"
_POLL_SECONDS = 0.05


class ServiceLockError(RuntimeError):
    """The requested extension lock could not be acquired safely."""


class ServiceLockTimeout(ServiceLockError):
    """A bounded extension lock acquisition exceeded its deadline."""


def validate_service_id(service_id: str) -> str:
    """Return a canonical service ID or fail before creating any path."""
    if not isinstance(service_id, str) or _SERVICE_ID_RE.fullmatch(service_id) is None:
        raise ServiceLockError("invalid-service-id")
    return service_id


def operation_lock_directory(lock_parent: Path) -> Path:
    """Create and validate the stable operation-lock directory."""
    parent = Path(lock_parent).resolve()
    lock_dir = parent / _LOCK_DIRECTORY
    if lock_dir.is_symlink():
        raise ServiceLockError("operation-lock-directory-is-symlink")
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ServiceLockError("operation-lock-directory-unavailable") from exc
    if lock_dir.is_symlink():
        raise ServiceLockError("operation-lock-directory-is-symlink")
    try:
        resolved = lock_dir.resolve(strict=True)
    except OSError as exc:
        raise ServiceLockError("operation-lock-directory-unavailable") from exc
    if not resolved.is_relative_to(parent):
        raise ServiceLockError("operation-lock-directory-escaped-parent")
    if not resolved.is_dir():
        raise ServiceLockError("operation-lock-directory-not-directory")
    return resolved


def operation_lock_path(lock_parent: Path, service_id: str) -> Path:
    """Return the non-user-controlled filename for one service lock."""
    import hashlib

    service_id = validate_service_id(service_id)
    lock_dir = operation_lock_directory(lock_parent)
    lock_name = hashlib.sha256(service_id.encode("utf-8")).hexdigest() + ".lock"
    return lock_dir / lock_name


def _open_lock_file(lock_path: Path):
    if lock_path.is_symlink():
        raise ServiceLockError("operation-lock-file-is-symlink")

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOINHERIT"):
        flags |= os.O_NOINHERIT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ServiceLockError("operation-lock-file-unavailable") from exc

    try:
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode) or descriptor_stat.st_nlink != 1:
            raise ServiceLockError("operation-lock-file-unsafe")
        if lock_path.is_symlink():
            raise ServiceLockError("operation-lock-file-is-symlink")
        path_stat = lock_path.stat()
        if (
            os.name == "posix"
            and (descriptor_stat.st_dev, descriptor_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise ServiceLockError("operation-lock-file-replaced")
        return os.fdopen(descriptor, "r+b", buffering=0)
    except Exception:
        os.close(descriptor)
        raise


def _acquire_lock(lockfile, lock_path: Path, timeout: float | None) -> None:
    if timeout is not None and timeout < 0:
        raise ServiceLockError("invalid-lock-timeout")

    if timeout is None:
        if fcntl is not None:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
            return
        lockfile.seek(0, os.SEEK_END)
        if lockfile.tell() == 0:
            lockfile.write(b"\0")
        lockfile.seek(0)
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_LOCK, 1)
        return

    deadline = time.monotonic() + timeout
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                lockfile.seek(0, os.SEEK_END)
                if lockfile.tell() == 0:
                    lockfile.write(b"\0")
                lockfile.seek(0)
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except (BlockingIOError, OSError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceLockTimeout(f"service-lock-timeout:{lock_path.name}")
            time.sleep(min(_POLL_SECONDS, remaining))


def _release_lock(lockfile) -> None:
    if fcntl is not None:
        fcntl.flock(lockfile, fcntl.LOCK_UN)
    else:
        lockfile.seek(0)
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def exclusive_file_lock(lock_path: Path, *, timeout: float | None = None):
    """Acquire one symlink-safe cross-process lock file."""
    lockfile = _open_lock_file(Path(lock_path))
    acquired = False
    try:
        _acquire_lock(lockfile, Path(lock_path), timeout)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                _release_lock(lockfile)
        finally:
            lockfile.close()


@contextlib.contextmanager
def lock_services(
    lock_parent: Path,
    service_ids: Iterable[str],
    *,
    timeout: float | None = None,
):
    """Acquire every requested service lock in canonical order.

    No caller code runs until all locks are held.  If any acquisition fails,
    ``ExitStack`` releases the already-held prefix in reverse order.
    """
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise ServiceLockError("invalid-lock-timeout")
    canonical_ids = tuple(sorted({validate_service_id(item) for item in service_ids}))
    lock_dir = operation_lock_directory(lock_parent)
    deadline = None if timeout is None else time.monotonic() + timeout
    with ExitStack() as stack:
        for service_id in canonical_ids:
            remaining = None
            if deadline is not None:
                remaining = max(0.0, deadline - time.monotonic())
            lock_path = operation_lock_path(lock_dir.parent, service_id)
            stack.enter_context(exclusive_file_lock(lock_path, timeout=remaining))
        yield canonical_ids


class FileServiceLockFactory:
    """TransactionExecutor lock factory backed by the shared file namespace."""

    def __init__(
        self,
        lock_parent: Path | Callable[[], Path],
        *,
        timeout: float | None = None,
    ) -> None:
        self._lock_parent = lock_parent
        self._timeout = timeout

    def lock_services(self, service_ids: list[str]):
        parent = self._lock_parent() if callable(self._lock_parent) else self._lock_parent
        return lock_services(parent, service_ids, timeout=self._timeout)
