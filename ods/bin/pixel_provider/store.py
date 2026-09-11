"""Private POSIX configuration storage; cooperating writers use one stable lock.

This does not isolate state from a malicious process with the same OS identity.
Windows needs a native ACL/locking adapter; importing this module is harmless.
"""

from __future__ import annotations

import copy
import errno
import json
import math
import os
from pathlib import Path
import secrets
import stat
import time
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # Native Windows host-agent must remain importable.
    fcntl = None

MAX_BYTES = 256 * 1024
LOCK_TIMEOUT = 5.0
CONFIG_NAME = "provider-config.json"
LOCK_NAME = ".provider-config.lock"


class StoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite-number")
    return result


def _constant(_value):
    raise ValueError("nonfinite-number")


def _check_depth(text):
    # Limit parser nesting before json.loads, including on builds with a high
    # recursion limit. Brackets inside escaped JSON strings do not count.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("document-too-deep")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("invalid-nesting")


def decode_document(raw):
    """Bounded strict JSON; errors never include document contents."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise StoreError("malformed-json")
    try:
        text = raw.decode("utf-8")
        _check_depth(text)
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_float=_float, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise StoreError("malformed-json") from None


def _private(metadata, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return (kind(metadata.st_mode) and metadata.st_uid == os.geteuid()
            and stat.S_IMODE(metadata.st_mode) == (0o700 if directory else 0o600)
            and (directory or metadata.st_nlink == 1))


class ProviderStore:
    config_name = CONFIG_NAME
    def __init__(self, directory, *, validator=None, default_factory=None):
        from .config import default_config, normalize_config
        # Do not resolve symlinks away before inspecting custody.
        self.directory = Path(directory).absolute()
        self.validator = validator or normalize_config
        self.default_factory = default_factory or default_config

    def _validate(self, value):
        try:
            result = self.validator(copy.deepcopy(value))
            if not isinstance(result, dict) or type(result.get("revision")) is not int:
                raise ValueError("invalid-revision")
            if not 0 <= result["revision"] <= 2**53 - 1:
                raise ValueError("invalid-revision")
            return copy.deepcopy(result)
        except (ValueError, TypeError, RecursionError):
            raise StoreError("invalid-config") from None

    def _open_file(self, directory_fd, name, *, create=False):
        # lstat first avoids opening a known device. O_NONBLOCK/O_NOFOLLOW plus
        # fstat protect against ordinary substitutions between inspection/open.
        try:
            prior = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                raise
        else:
            if not _private(prior):
                raise StoreError("unsafe-file")
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY)
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        if not _private(os.fstat(fd)):
            os.close(fd)
            raise StoreError("unsafe-file")
        return fd

    @contextmanager
    def _locked(self, exclusive):
        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise StoreError("unsupported-platform")
        directory_fd = lock_fd = None
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if not _private(os.fstat(directory_fd), directory=True):
                raise StoreError("unsafe-directory")
            lock_fd = self._open_file(directory_fd, LOCK_NAME, create=True)
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(lock_fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise StoreError("lock-timeout")
                    time.sleep(0.025)
            yield directory_fd
        except OSError:
            raise StoreError("storage-unavailable") from None
        finally:
            # Closing the descriptor releases flock without unlinking its inode.
            if lock_fd is not None:
                os.close(lock_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def _load(self, directory_fd):
        try:
            fd = self._open_file(directory_fd, self.config_name)
        except FileNotFoundError:
            value = self._validate(self.default_factory())
            if value["revision"] != 0:
                raise StoreError("invalid-config")
            return value
        try:
            if os.fstat(fd).st_size > MAX_BYTES:
                raise StoreError("malformed-json")
            raw = bytearray()
            while len(raw) <= MAX_BYTES:
                chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            return self._validate(decode_document(bytes(raw)))
        finally:
            os.close(fd)

    def load(self):
        with self._locked(False) as directory_fd:
            return self._load(directory_fd)

    def read_snapshot(self):
        """Read an atomically replaced document from a read-only service mount.

        No lock file is created or opened for writing. Writers still use flock
        and atomic replace; readers get one complete revision, not a read/modify
        transaction. Intended for revocation polling by the owner-UID service.
        """
        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise StoreError("unsupported-platform")
        fd = None
        try:
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if not _private(os.fstat(fd), directory=True):
                raise StoreError("unsafe-directory")
            return self._load(fd)
        except OSError:
            raise StoreError("storage-unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)

    def save(self, document, *, expected_revision):
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**53 - 1:
            raise StoreError("invalid-request")
        candidate = self._validate(document)
        if candidate["revision"] != expected_revision:
            raise StoreError("stale-revision")
        with self._locked(True) as directory_fd:
            if self._load(directory_fd)["revision"] != expected_revision:
                raise StoreError("stale-revision")
            return self._commit(directory_fd, candidate, expected_revision)

    def _commit(self, directory_fd, candidate, expected_revision):
        """Caller holds exclusive lock and has checked the committed revision."""
        candidate = self._validate(candidate)
        if candidate["revision"] != expected_revision:
            raise StoreError("stale-revision")
        candidate["revision"] += 1
        candidate = self._validate(candidate)
        if candidate["revision"] != expected_revision + 1:
            raise StoreError("invalid-config")
        try:
            raw = json.dumps(candidate, ensure_ascii=True, allow_nan=False,
                             separators=(",", ":")).encode("utf-8") + b"\n"
        except (ValueError, TypeError, RecursionError):
            raise StoreError("invalid-config") from None
        if len(raw) > MAX_BYTES:
            raise StoreError("invalid-config")
        temp_name = ".provider-" + secrets.token_hex(16) + ".tmp"
        temp_fd = None
        created = replaced = False
        try:
            temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=directory_fd)
            created = True
            if not _private(os.fstat(temp_fd)):
                raise StoreError("unsafe-file")
            remaining = memoryview(raw)
            while remaining:
                written = os.write(temp_fd, remaining)
                if written <= 0:
                    raise OSError(errno.EIO, "short-write")
                remaining = remaining[written:]
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = None
            bound = self.directory.lstat()
            original = os.fstat(directory_fd)
            if not _private(bound, directory=True) or (bound.st_dev, bound.st_ino) != (original.st_dev, original.st_ino):
                raise StoreError("directory-replaced")
            os.replace(temp_name, self.config_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            replaced = True
            os.fsync(directory_fd)
        except OSError:
            raise StoreError("write-durability-unknown" if replaced else "write-failed") from None
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
            if created and not replaced:
                try:
                    os.unlink(temp_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
        return copy.deepcopy(candidate)
