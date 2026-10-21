#!/usr/bin/env python3
"""Fixed-store transfer worker, run with only one verified Docker volume.

This also supplies the host snapshot implementation. It does not relax either
API store's custody rules or interpret SQL supplied by a caller.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import time
import uuid

STORES = {"pixel-images": "images.sqlite3", "pixel-chat-results": "results.sqlite3"}
LIMIT = 512 * 1024 * 1024  # Includes SQLite indexes/free pages, not just blob quota.
MARKER = ".ods-private-store.json"


def private(path: Path, *, directory=False):
    info = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (path.resolve() != path.absolute() or not expected_type(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o077
            or not directory and (info.st_nlink != 1 or info.st_size > LIMIT)):
        raise ValueError("private-store-custody")
    return info


def check_files(directory: Path, kind: str):
    private(directory, directory=True)
    database = directory / STORES[kind]
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(database) + suffix)
        if os.path.lexists(candidate):
            private(candidate)
    return database


def sync_dir(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(directory: Path, kind: str, destination: Path):
    """Use SQLite backup, including committed WAL, without modifying the source.

    Migration callers require stopped writers. Backup callers may have a live
    writer; SQLite provides a consistent snapshot, bounded to 30 seconds.
    """
    database = check_files(directory, kind)
    if os.path.lexists(destination):
        raise ValueError("private-store-snapshot-exists")
    pending = destination.with_name(".snapshot-" + uuid.uuid4().hex)
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    start = time.monotonic()

    def progress(_status, _remaining, pages):
        if time.monotonic() - start > 30 or pages * 65536 > LIMIT * 16:
            raise ValueError("private-store-snapshot-limit")

    try:
        with sqlite3.connect(pending) as target:
            if database.exists() and database.stat().st_size:
                before = database.stat()
                with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
                    source.backup(target, pages=256, progress=progress)
                after = private(database)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise ValueError("private-store-source-replaced")
            else:
                target.execute("PRAGMA user_version=0")
            # Backup copies the source's WAL header. Only the completed output
            # travels in an archive, so make it independent of WAL sidecars.
            if target.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise ValueError("private-store-snapshot-journal")
            if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("private-store-snapshot-integrity")
        private(pending)
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        sha = digest(pending)
        # A killed copy must never leave a partial file at the rollback name.
        # Callers hold the private-store lock and reject other volume consumers.
        os.replace(pending, destination)
        sync_dir(destination.parent)
        return sha
    except BaseException:
        pending.unlink(missing_ok=True)
        raise


def receive(path: Path, expected: str):
    if not re.fullmatch("[a-f0-9]{64}", expected):
        raise ValueError("private-store-payload-hash")
    if os.path.lexists(path):
        private(path)
        if digest(path) != expected:
            raise ValueError("private-store-staging-conflict")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        total = 0
        with os.fdopen(fd, "wb") as stream:
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                total += len(chunk)
                if total > LIMIT:
                    raise ValueError("private-store-payload-limit")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(path) != expected:
            raise ValueError("private-store-payload-hash")
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("private-store-payload-integrity")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def remove_sidecars(database):
    for suffix in ("-wal", "-shm", "-journal"):
        path = Path(str(database) + suffix)
        if os.path.lexists(path):
            private(path)
            path.unlink()


def worker(action, kind, token, expected="", root=Path("/store")):
    if kind not in STORES or not re.fullmatch("[a-f0-9]{32}", token):
        raise ValueError("private-store-worker-arguments")
    if action == "init":
        # Only called for a newly-created, labelled, empty volume. Never
        # repair ownership of existing data.
        if (os.geteuid() != 0 or root.is_symlink() or not root.is_dir()
                or list(root.iterdir())):
            raise ValueError("private-store-init-not-empty")
        os.chown(root, 1000, 1000)
        os.chmod(root, 0o700)
        return
    database = check_files(root, kind)
    marker = root / MARKER
    staged = root / (".incoming-" + token)
    prior = root / (".prior-" + token)
    if action == "import":
        if marker.exists():
            private(marker)
            if json.loads(marker.read_text()) != {"kind": kind, "token": token}:
                raise ValueError("private-store-marker-conflict")
            return  # A prior completed import may already contain newer writes.
        if set(p.name for p in root.iterdir()) - {staged.name, database.name, ".marker-" + token}:
            raise ValueError("private-store-import-not-empty")
        receive(staged, expected)
        if database.exists() and digest(database) != expected:
            raise ValueError("private-store-incomplete-import-conflict")
        os.replace(staged, database)
        pending_marker = root / (".marker-" + token)
        if pending_marker.exists():
            private(pending_marker)
            pending_marker.unlink()
        fd = os.open(pending_marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"kind": kind, "token": token}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending_marker, marker)
        sync_dir(root)
        return
    private(marker)
    private(database)
    if json.loads(marker.read_text()) != {"kind": kind, "token": token} and action in {"check", "export"}:
        raise ValueError("private-store-marker-conflict")
    if action == "check":
        return
    if action == "export":
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / STORES[kind]
            snapshot(root, kind, output)
            with output.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sys.stdout.buffer.write(chunk)
        return
    # Restore callers verify no running volume consumers before every action.
    if action == "stage":
        receive(staged, expected)
    elif action == "publish":
        private(staged)
        if digest(staged) != expected:
            raise ValueError("private-store-staging-conflict")
        if not prior.exists():
            snapshot(root, kind, prior)
        else:
            private(prior)
        remove_sidecars(database)
        os.replace(staged, database)
    elif action == "rollback":
        if prior.exists():
            private(prior)
            remove_sidecars(database)
            os.replace(prior, database)
        if staged.exists():
            private(staged)
            staged.unlink()
    elif action == "commit":
        for path in (prior, staged):
            if path.exists():
                private(path)
                path.unlink()
    else:
        raise ValueError("private-store-worker-action")
    sync_dir(root)


if __name__ == "__main__":
    try:
        worker(*sys.argv[1:])
    except (ValueError, OSError, sqlite3.Error):
        # Database bytes, paths and SQLite exception messages stay private.
        print("Private dashboard store transfer refused; retain migration/restore staging.", file=sys.stderr)
        sys.exit(1)
