"""Private, bounded SSE receipts for one dashboard API process.

The receipt identifies an execution attempt, not just its conversation. A new
API process can read completed receipts but never adopts unfinished execution.
No prompt, credential, or provider configuration is stored here.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_STORE_BYTES = 128 * 1024 * 1024
MAX_RECORDS = 4096
MAX_ACTIVE = 8
RETENTION_SECONDS = 7 * 24 * 60 * 60


class ResultConflict(Exception):
    """Attempt reuse or unresolved execution prevents another submission."""


class ResultCapacity(Exception):
    """Retention capacity is exhausted; existing work must not be evicted."""


def owner_namespace(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


class ChatResultStore:
    def __init__(self, directory: Path):
        directory = directory.absolute()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.resolve() != directory:
            raise ValueError("Chat receipt directory must not traverse symlinks")
        info = directory.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Chat receipt directory is invalid")
        if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError("Chat receipt directory is not private")
        path = directory / "results.sqlite3"
        # The directory is private. Validate existing SQLite files before SQLite
        # follows any path, and create the main file exclusively without links.
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists() or candidate.is_symlink():
                info = candidate.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
                    raise ValueError("Chat receipt file is not private")
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.instance = uuid.uuid4().hex
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS attempts (
                owner TEXT NOT NULL, chat TEXT NOT NULL, attempt TEXT NOT NULL,
                fingerprint TEXT NOT NULL, instance TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, size INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(owner, chat, attempt));
            CREATE TABLE IF NOT EXISTS chunks (
                owner TEXT NOT NULL, chat TEXT NOT NULL, attempt TEXT NOT NULL,
                sequence INTEGER NOT NULL, data BLOB NOT NULL,
                PRIMARY KEY(owner, chat, attempt, sequence),
                FOREIGN KEY(owner, chat, attempt) REFERENCES attempts(owner, chat, attempt) ON DELETE CASCADE);
        """)

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute("SELECT * FROM attempts WHERE owner=? AND chat=? AND attempt=?", key).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result["state"] == "active" and result["instance"] != self.instance:
            result["state"] = "unresolved"
        return result

    def reserve(self, key, fingerprint):
        """Commit identity before upstream submission; duplicate POSTs never run twice."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("DELETE FROM attempts WHERE state NOT IN ('active','unresolved') AND created < ?", (time.time() - RETENTION_SECONDS,))
            previous = self.get(key)
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise ResultConflict("Attempt identity was already used for different input")
                return False
            if self.has_pending(key[:2]):
                raise ResultConflict("This conversation has an unresolved attempt; recover or stop it first")
            count, active, allocation = self.db.execute(
                "SELECT COUNT(*), COALESCE(SUM(state='active' AND instance=?),0), "
                "COALESCE(SUM(CASE WHEN state='active' AND instance=? THEN ? ELSE size END),0) FROM attempts",
                (self.instance, self.instance, MAX_RESULT_BYTES)
            ).fetchone()
            if count >= MAX_RECORDS or active >= MAX_ACTIVE or allocation + MAX_RESULT_BYTES > MAX_STORE_BYTES:
                raise ResultCapacity("Chat result storage is full; existing results were preserved")
            self.db.execute("INSERT INTO attempts(owner,chat,attempt,fingerprint,instance,state,created) VALUES(?,?,?,?,?,'active',?)",
                            (*key, fingerprint, self.instance, time.time()))
        return True

    def append(self, key, data, *, terminal=False):
        with self.db:
            row = self.get(key)
            if row is None or row["state"] != "active":
                raise ResultConflict("Attempt is no longer active")
            limit = MAX_RESULT_BYTES if terminal else MAX_RESULT_BYTES - 4096
            if row["size"] + len(data) > limit:
                raise ResultCapacity("Chat response exceeded retained-result capacity")
            sequence = self.db.execute("SELECT COALESCE(MAX(sequence),-1)+1 FROM chunks WHERE owner=? AND chat=? AND attempt=?", key).fetchone()[0]
            self.db.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", (*key, sequence, data))
            self.db.execute("UPDATE attempts SET size=size+? WHERE owner=? AND chat=? AND attempt=?", (len(data), *key))

    def finish(self, key, state):
        if state not in {"complete", "interrupted", "cancelled", "unresolved"}:
            raise ValueError("Invalid receipt state")
        with self.db:
            self.db.execute("UPDATE attempts SET state=? WHERE owner=? AND chat=? AND attempt=? AND state IN ('active','unresolved')", (state, *key))

    def has_pending(self, conversation):
        return self.db.execute("SELECT 1 FROM attempts WHERE owner=? AND chat=? AND state IN ('active','unresolved')", conversation).fetchone() is not None

    def chunks(self, key, after=-1):
        return self.db.execute("SELECT sequence,data FROM chunks WHERE owner=? AND chat=? AND attempt=? AND sequence>? ORDER BY sequence", (*key, after)).fetchall()
