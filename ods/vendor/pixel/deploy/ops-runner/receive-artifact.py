#!/usr/bin/env python3
"""Receive one broker-staged artifact without following runner-controlled symlinks."""

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path


JOB_RE = re.compile(r"ops-[0-9]{13}-[a-f0-9]{12}")
FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")


def open_directory(parent: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError("artifact destination is not a real directory")
    return descriptor


def main() -> int:
    if len(sys.argv) != 4:
        raise RuntimeError("Usage: receive-artifact.py JOB_ID FILENAME SHA256")
    job_id, filename, expected = sys.argv[1:]
    if not JOB_RE.fullmatch(job_id) or not FILENAME_RE.fullmatch(filename) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise RuntimeError("unsafe artifact identity")
    maximum = min(max(int(os.environ.get("PIXEL_RUNNER_ARTIFACT_MAX", str(512 * 1024 * 1024))), 1), 2 * 1024 * 1024 * 1024)
    root = Path(os.environ.get("PIXEL_RUNNER_ARTIFACT_ROOT", "/var/lib/pixel-runner/jobs/artifacts"))
    if not root.is_absolute() or root == Path("/"):
        raise RuntimeError("artifact root must be an absolute non-root path")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    job_fd = open_directory(root_fd, job_id)
    descriptor = None
    created = False
    try:
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=job_fd)
        created = True
        checksum, total = hashlib.sha256(), 0
        while True:
            chunk = sys.stdin.buffer.read(min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise RuntimeError("artifact exceeds runner size limit")
            checksum.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise RuntimeError("artifact write made no progress")
                view = view[written:]
        os.fsync(descriptor)
        observed = checksum.hexdigest()
        if observed != expected:
            raise RuntimeError("artifact hash mismatch")
        print(json.dumps({"jobId": job_id, "filename": filename, "bytes": total, "sha256": observed, "path": str(root / job_id / filename), "executable": False}))
        return 0
    except Exception:
        if descriptor is not None:
            os.close(descriptor); descriptor = None
        if created:
            try:
                os.unlink(filename, dir_fd=job_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(job_fd); os.close(root_fd)


if __name__ == "__main__":
    raise SystemExit(main())
