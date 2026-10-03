#!/usr/bin/env python3
"""Copy an exact regular-file tree into or out of a bounded Codex comparison volume."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys


MAX_ENTRIES = 100000
BUFFER_BYTES = 1024 * 1024
OPERATIONS = {
    "init": "codex-comparison-workspace-init",
    "export": "codex-comparison-workspace-export",
}


class WorkspaceError(RuntimeError):
    pass


def inventory_and_copy(source: Path, destination: Path, maximum_bytes: int) -> dict:
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=True)
    if source == destination or source in destination.parents or destination in source.parents:
        raise WorkspaceError("comparison workspace roots overlap")
    if not source.is_dir() or source.is_symlink() or not destination.is_dir() or destination.is_symlink():
        raise WorkspaceError("comparison workspace roots must be real directories")
    if any(destination.iterdir()):
        raise WorkspaceError("comparison workspace destination is not empty")
    entries = 0
    total = 0
    digest = hashlib.sha256()

    def visit(relative: Path) -> None:
        nonlocal entries, total
        children = sorted((source / relative).iterdir(), key=lambda item: item.name)
        for child in children:
            entries += 1
            if entries > MAX_ENTRIES or child.name in {".", ".."} or "/" in child.name or "\\" in child.name:
                raise WorkspaceError("comparison workspace entry ceiling or path contract was exceeded")
            child_relative = relative / child.name
            target = destination / child_relative
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise WorkspaceError("comparison workspace contains a symbolic link")
            if stat.S_ISDIR(info.st_mode):
                target.mkdir(mode=0o700)
                visit(child_relative)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkspaceError("comparison workspace contains a special or hard-linked file")
            total += info.st_size
            if total > maximum_bytes:
                raise WorkspaceError("comparison workspace exceeds its exact disk ceiling")
            source_fd = os.open(child, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            target_fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                opened = os.fstat(source_fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != info.st_size:
                    raise WorkspaceError("comparison workspace file changed before copy")
                file_digest = hashlib.sha256()
                while True:
                    chunk = os.read(source_fd, BUFFER_BYTES)
                    if not chunk:
                        break
                    file_digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(target_fd, view)
                        view = view[written:]
                after = os.fstat(source_fd)
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                    raise WorkspaceError("comparison workspace file changed during copy")
                digest.update(child_relative.as_posix().encode("utf-8") + b"\0")
                digest.update(str(info.st_size).encode("ascii") + b"\0" + file_digest.digest())
            finally:
                os.close(source_fd)
                os.close(target_fd)

    try:
        visit(Path())
    except Exception:
        for path in sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
        raise
    return {"entries": entries, "bytes": total, "sha256": digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["init", "export"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("maximum_bytes", type=int)
    args = parser.parse_args()
    try:
        if args.maximum_bytes < 1 or args.maximum_bytes > 1099511627776:
            raise WorkspaceError("comparison workspace byte ceiling is invalid")
        result = inventory_and_copy(args.source, args.destination, args.maximum_bytes)
        print(f"{{\"operation\":\"{OPERATIONS[args.operation]}\",\"entries\":{result['entries']},\"bytes\":{result['bytes']},\"sha256\":\"{result['sha256']}\"}}")
        return 0
    except (WorkspaceError, OSError, UnicodeError) as exc:
        print(f"pixel-codex-workspace: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
