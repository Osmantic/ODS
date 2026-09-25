#!/usr/bin/env python3
"""Copy one bounded proposal through no-follow file descriptors."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys


MAX_BYTES = 256 * 1024


class SnapshotError(RuntimeError):
    pass


def snapshot(source: Path, output: Path) -> int:
    source_info = source.lstat()
    if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1 or source_info.st_size > MAX_BYTES:
        raise SnapshotError("source proposal is not a bounded single-link regular file")
    if os.name != "nt" and source_info.st_mode & 0o022:
        raise SnapshotError("source proposal is writable by group or other")
    if os.name != "nt" and hasattr(os, "geteuid") and source_info.st_uid != os.geteuid():
        raise SnapshotError("source proposal is not owned by the gateway identity")
    output_info = output.lstat()
    if not stat.S_ISREG(output_info.st_mode) or output_info.st_nlink != 1 or output_info.st_size != 0:
        raise SnapshotError("snapshot output must be one new empty regular file")
    binary = getattr(os, "O_BINARY", 0)
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | binary)
    output_fd = os.open(output, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | binary)
    copied = 0
    try:
        opened_source = os.fstat(source_fd)
        opened_output = os.fstat(output_fd)
        if not stat.S_ISREG(opened_source.st_mode) or opened_source.st_nlink != 1 or opened_source.st_size > MAX_BYTES:
            raise SnapshotError("source proposal changed during secure open")
        if not stat.S_ISREG(opened_output.st_mode) or opened_output.st_nlink != 1 or opened_output.st_size != 0:
            raise SnapshotError("snapshot output must be one new empty regular file")
        while True:
            block = os.read(source_fd, min(64 * 1024, MAX_BYTES + 1 - copied))
            if not block:
                break
            copied += len(block)
            if copied > MAX_BYTES:
                raise SnapshotError("source proposal exceeded its size limit")
            view = memoryview(block)
            while view:
                written = os.write(output_fd, view)
                if written <= 0:
                    raise SnapshotError("snapshot output write failed")
                view = view[written:]
        os.fsync(output_fd)
    finally:
        os.close(source_fd)
        os.close(output_fd)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        copied = snapshot(args.source, args.output)
    except (SnapshotError, OSError) as exc:
        print(f"proposal snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(f"snapshotted {copied} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
