#!/usr/bin/env python3
"""Bounded Linux lifecycle lease for ODS model installs.

Canonical lock path: <root>/ods-model-lifecycle-<cksum>.lock where cksum is
POSIX cksum over the canonical install path bytes + NUL. Root defaults to
/tmp/ods-model-lifecycle-<uid> and may be overridden by
ODS_MODEL_LIFECYCLE_LOCK_ROOT (absolute path).

Ownership: when running as root, the owner is INSTALL_USER, then SUDO_USER
(via pwd.getpwnam), else 0. Invalid configured usernames fail closed.

Legacy migration: the original shell helper created empty 0644 lock files
inside a private 0700 directory. We permit tightening ONLY an existing
single-link, owner-owned, regular, EMPTY 0644 file to 0600 via the held FD.
We never unlink, truncate, or replace an existing lock inode. Other wrong
modes are refused.

This is a cooperative private-owner boundary. It does NOT claim that a
malicious same-owner process cannot unlink the lock.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path

if sys.platform.startswith("linux"):
    import fcntl
    import pwd

_DEFAULT_ROOT = "/tmp/ods-model-lifecycle-{uid}"
_ENV_ROOT = "ODS_MODEL_LIFECYCLE_LOCK_ROOT"
_NAME_PREFIX = "ods-model-lifecycle-"
_NAME_SUFFIX = ".lock"


def _require_linux() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("model_lifecycle_lock requires Linux")


def _owner_uid() -> int:
    if os.geteuid() != 0:
        return os.geteuid()
    for var in ("INSTALL_USER", "SUDO_USER"):
        name = os.environ.get(var)
        if not name:
            continue
        try:
            return pwd.getpwnam(name).pw_uid
        except KeyError as exc:
            raise RuntimeError(
                f"invalid {var} user: {name!r}"
            ) from exc
    return 0


def _root_dir() -> Path:
    override = os.environ.get(_ENV_ROOT)
    if override:
        p = Path(override)
        if not p.is_absolute():
            raise RuntimeError(f"{_ENV_ROOT} must be absolute")
        return p
    return Path(_DEFAULT_ROOT.format(uid=_owner_uid()))


def _canonical_install(install_dir: Path) -> bytes:
    return os.fsencode(str(Path(install_dir).resolve(strict=False)))


def _cksum(data: bytes) -> str:
    # POSIX cksum: non-reflected CRC, then the little-endian byte length.
    # Keep the original shell lock namespace without spawning a subprocess.
    crc = 0
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    n = len(data)
    while n:
        crc ^= (n & 0xFF) << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
        n >>= 8
    return str(~crc & 0xFFFFFFFF)

def lock_path(install_dir: Path) -> Path:
    """Pure: compute canonical lock path. No filesystem creation."""
    _require_linux()
    canon = _canonical_install(install_dir)
    digest = _cksum(canon + b"\x00")
    return _root_dir() / f"{_NAME_PREFIX}{digest}{_NAME_SUFFIX}"


def _check_dir(fd: int, owner: int) -> None:
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError("lock root is not a directory")
    if st.st_uid != owner:
        raise RuntimeError("lock root wrong owner")
    if stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError("lock root must be 0700")


def _check_file(fd: int, owner: int, allow_legacy: bool) -> None:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError("lock is not a regular file")
    if st.st_uid != owner:
        raise RuntimeError("lock wrong owner")
    if st.st_nlink != 1:
        raise RuntimeError("lock must have single hardlink")
    mode = stat.S_IMODE(st.st_mode)
    if mode == 0o600:
        return
    if allow_legacy and mode == 0o644 and st.st_size == 0:
        os.fchmod(fd, 0o600)
        return
    raise RuntimeError(f"lock has invalid mode {mode:o}")


def _check_root_binding(root: Path, fd: int) -> None:
    named, held = root.lstat(), os.fstat(fd)
    if not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
        raise RuntimeError("lock root changed during acquisition")


def _open_root(owner: int, create: bool) -> int:
    root = _root_dir()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    created = False
    if create:
        try:
            os.mkdir(root, 0o700)
            created = True
        except FileExistsError:
            pass
    try:
        fd = os.open(root, flags)
    except OSError as exc:
        raise RuntimeError(f"cannot open lock root: {exc}") from exc
    try:
        _check_root_binding(root, fd)
        if created and os.geteuid() == 0:
            os.fchown(fd, owner, -1)
        _check_dir(fd, owner)
    except Exception:
        os.close(fd)
        raise
    return fd


def _open_lock(root_fd: int, name: str, owner: int, create: bool) -> int:
    # O_NONBLOCK prevents a substituted FIFO from hanging before fstat.
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    created = False
    try:
        if create:
            try:
                fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd)
                created = True
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=root_fd)
        else:
            fd = os.open(name, flags, dir_fd=root_fd)
    except OSError as exc:
        raise RuntimeError(f"cannot open lock file: {exc}") from exc
    try:
        if created and os.geteuid() == 0:
            os.fchown(fd, owner, -1)
        _check_file(fd, owner, allow_legacy=True)
    except Exception:
        os.close(fd)
        raise
    return fd


def prepare_lock_path(install_dir: Path) -> Path:
    """Create/check private root dir and empty lock file; return path."""
    _require_linux()
    owner = _owner_uid()
    path = lock_path(install_dir)
    root_fd = _open_root(owner, create=True)
    try:
        fd = _open_lock(root_fd, path.name, owner, create=True)
        try:
            _check_root_binding(path.parent, root_fd)
            validate_fd(install_dir, fd)
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    return path


def validate_fd(install_dir: Path, fd: int) -> None:
    """Read-only validation of an already-open lock FD."""
    _require_linux()
    if fd < 3:
        raise RuntimeError("fd must be >= 3")
    owner = _owner_uid()
    path = lock_path(install_dir)
    root_fd = _open_root(owner, create=False)
    try:
        try:
            named = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"cannot stat lock: {exc}") from exc
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError("fd is not a regular file")
        if st.st_uid != owner:
            raise RuntimeError("fd wrong owner")
        if st.st_nlink != 1:
            raise RuntimeError("fd must have single hardlink")
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise RuntimeError("fd has invalid mode")
        if (st.st_dev, st.st_ino) != (named.st_dev, named.st_ino):
            raise RuntimeError("fd does not match canonical lock")
        _check_root_binding(path.parent, root_fd)
    finally:
        os.close(root_fd)


class Lease:
    __slots__ = ("fd", "_closed")

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self.fd)

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def acquire(install_dir: Path, blocking: bool = False) -> Lease:
    _require_linux()
    owner = _owner_uid()
    path = lock_path(install_dir)
    root_fd = _open_root(owner, create=True)
    try:
        fd = _open_lock(root_fd, path.name, owner, create=True)
    finally:
        os.close(root_fd)
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise BlockingIOError(exc.errno, "lock held") from exc
            raise
        validate_fd(install_dir, fd)
    except Exception:
        os.close(fd)
        raise
    return Lease(fd)


def _cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: model_lifecycle_lock {name|path|validate} ...", file=sys.stderr)
        return 2
    cmd = argv[1]
    try:
        if cmd in {"name", "path"}:
            if len(argv) != 4 or argv[2] != "--install-dir":
                print("usage: path --install-dir PATH", file=sys.stderr)
                return 2
            print((prepare_lock_path if cmd == "path" else lock_path)(Path(argv[3])))
            return 0
        if cmd == "validate":
            if len(argv) != 6 or argv[2] != "--install-dir" or argv[4] != "--fd":
                print("usage: validate --install-dir PATH --fd N", file=sys.stderr)
                return 2
            validate_fd(Path(argv[3]), int(argv[5]))
            return 0
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
