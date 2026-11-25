#!/usr/bin/env python3
"""Publish one private backup directory without replacing a concurrent name."""

import ctypes
import errno
import os
from pathlib import Path
import stat
import sys


def rename_exclusive(parent_fd: int, source: str, destination: str) -> None:
    """Use the platform's atomic no-replace operation; never emulate it."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux":
        name, flag = "renameat2", 1  # RENAME_NOREPLACE
    elif sys.platform == "darwin":
        name, flag = "renameatx_np", 4  # RENAME_EXCL
    else:
        raise OSError(errno.ENOTSUP, "Atomic backup publication is unsupported")
    function = getattr(libc, name, None)
    if function is None:
        raise OSError(errno.ENOTSUP, "Atomic backup publication is unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), flag) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def publish(source: Path, destination: Path) -> None:
    if (source.parent != destination.parent or source.name in {"", ".", ".."}
            or not destination.name.startswith("backup-")
            or not source.name.startswith(f".{destination.name}.tmp.")):
        raise ValueError("Backup staging and destination must be siblings with matching names")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open(source.parent, flags)
    try:
        source_fd = os.open(source.name, flags, dir_fd=parent_fd)
        try:
            selected = os.fstat(source_fd)
            if selected.st_uid != os.geteuid() or stat.S_IMODE(selected.st_mode) & 0o077:
                raise ValueError("Backup staging directory must be owned and private")
            if identity(os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)) != identity(selected):
                raise ValueError("Backup staging directory changed")
            rename_exclusive(parent_fd, source.name, destination.name)
            published = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
            if identity(published) != identity(selected) or not stat.S_ISDIR(published.st_mode):
                raise ValueError("Published backup identity could not be confirmed")
        finally:
            os.close(source_fd)
    finally:
        os.close(parent_fd)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: publish-update-backup.py STAGING DESTINATION", file=sys.stderr)
        return 2
    try:
        publish(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError) as exc:
        # Neither a failed operation nor an uncertain response authorizes cleanup.
        print(f"Backup publication refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
