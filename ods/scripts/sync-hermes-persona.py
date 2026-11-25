#!/usr/bin/env python3
"""Bounded Hermes runtime persona synchronizer.

Invoked inside the Docker Hermes container as:

    python -c '<module>'  # or via this script

with JSON on stdin: {"old_sha256": "<64hex>", "content": "<utf8 new persona>"}.

Destination is fixed at /opt/data/SOUL.md.  The component only ever
updates the runtime file when its current bytes still match the
previously auto-generated digest (old_sha256).  Any other content is
considered user-customized and is preserved untouched.

Statuses:
  updated   - runtime file was overwritten in place with new content
  current   - runtime file already equals the new content (no write)
  preserved - runtime file exists but is neither old nor new (no write)

Safety properties:
  * old_sha256 must be 64 lowercase/uppercase hex characters.
  * content must be a str (UTF-8 encodable).
  * Input is bounded to 1 MiB of UTF-8 content.
  * Symlinks, directories, FIFOs, sockets, devices and hardlinked
    regular files are refused.
  * Nothing is ever unlinked.
  * Creation uses O_CREAT|O_EXCL with mode 0600 as the current user.
  * Existing files are opened with O_NOFOLLOW|O_NONBLOCK|O_RDWR and
    verified via fstat to be a regular file with nlink == 1.
  * Overwrites are in-place (seek 0, write, fsync, truncate, fsync).
"""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import stat
import sys

DEFAULT_PATH = "/opt/data/SOUL.md"
MAX_CONTENT_BYTES = 1024 * 1024  # 1 MiB

STATUS_UPDATED = "updated"
STATUS_CURRENT = "current"
STATUS_PRESERVED = "preserved"


class SyncError(Exception):
    """Base class for sync failures."""


class InputError(SyncError):
    """Malformed input (sha, content type, size, encoding)."""


class PathError(SyncError):
    """Unsafe or unsupported filesystem object at destination."""


class RaceError(SyncError):
    """Creation race or unexpected state change."""


def _validate_sha(old_sha256: object) -> str:
    if not isinstance(old_sha256, str):
        raise InputError("old_sha256 must be a string")
    if len(old_sha256) != 64:
        raise InputError("old_sha256 must be 64 hex characters")
    for ch in old_sha256:
        if ch not in "0123456789abcdefABCDEF":
            raise InputError("old_sha256 must be 64 hex characters")
    return old_sha256.lower()


def _validate_content(content: object) -> bytes:
    if not isinstance(content, str):
        raise InputError("content must be a string")
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InputError("content is not valid UTF-8") from exc
    if len(data) > MAX_CONTENT_BYTES:
        raise InputError("content exceeds 1 MiB limit")
    return data


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
        if sum(len(c) for c in chunks) > MAX_CONTENT_BYTES:
            raise PathError("existing file exceeds 1 MiB limit")
    return b"".join(chunks)


def _open_existing(path: str) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise PathError(f"cannot open destination: {exc.__class__.__name__}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathError("destination is not a regular file")
        if st.st_nlink != 1:
            raise PathError("destination has multiple hard links")
    except Exception:
        os.close(fd)
        raise
    return fd


def _create_exclusive(path: str, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise RaceError("destination appeared during creation")
    except OSError as exc:
        raise PathError(f"cannot create destination: {exc.__class__.__name__}") from exc
    try:
        try:
            _overwrite_in_place(fd, data)
        except OSError as exc:
            raise PathError(f"write failed: {exc.__class__.__name__}") from exc
    finally:
        os.close(fd)


def _overwrite_in_place(fd: int, data: bytes) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n <= 0:
                raise PathError("short write")
            written += n
        os.fsync(fd)
        os.ftruncate(fd, len(data))
        os.fsync(fd)
    except OSError as exc:
        raise PathError(f"overwrite failed: {exc.__class__.__name__}") from exc


def sync_persona(path: str, old_sha256: str, content: str) -> str:
    """Synchronize the runtime persona file at *path*.

    Returns one of "updated", "current", "preserved".
    Raises SyncError subclasses on malformed input or unsafe paths.
    """
    old_hex = _validate_sha(old_sha256)
    new_bytes = _validate_content(content)
    new_hex = _sha256_hex(new_bytes)

    try:
        fd = _open_existing(path)
    except FileNotFoundError:
        _create_exclusive(path, new_bytes)
        return STATUS_UPDATED

    try:
        # Serialize our own refreshes and detect an edit made during readback.
        # The descriptor keeps symlink replacement from redirecting writes.
        fcntl.flock(fd, fcntl.LOCK_EX)
        before = os.fstat(fd)
        existing = _read_all(fd)
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_size, before.st_nlink) != (after.st_mtime_ns, after.st_size, after.st_nlink):
            raise RaceError("destination changed during readback")
        existing_hex = _sha256_hex(existing)
        if existing_hex == new_hex:
            return STATUS_CURRENT
        if existing_hex == old_hex:
            _overwrite_in_place(fd, new_bytes)
            return STATUS_UPDATED
        return STATUS_PRESERVED
    finally:
        os.close(fd)


def _read_stdin_payload() -> tuple[str, str]:
    max_payload = MAX_CONTENT_BYTES * 6 + 4096
    raw = sys.stdin.buffer.read(max_payload + 1)
    if len(raw) > max_payload:
        raise InputError("stdin payload too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError("stdin is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise InputError("stdin JSON must be an object")
    if set(payload.keys()) != {"old_sha256", "content"}:
        raise InputError("stdin JSON must contain exactly old_sha256 and content")
    return payload["old_sha256"], payload["content"]


def main(argv: list[str] | None = None) -> int:
    try:
        old_sha256, content = _read_stdin_payload()
        status = sync_persona(DEFAULT_PATH, old_sha256, content)
    except SyncError as exc:
        sys.stderr.write(f"sync-hermes-persona: {exc.__class__.__name__}\n")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"sync-hermes-persona: {exc.__class__.__name__}\n")
        return 1
    sys.stdout.write(status + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
