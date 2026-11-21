"""Cookie-scoped conversation bindings and durable, non-replaying turn claims.

Use a dedicated private directory. Claims survive worker death: a new worker
cannot assume an interrupted remote operation did not happen. Only the caller
holding the matching token may finish a known outcome. No prompt text is stored.
"""

import sqlite3
import secrets
import time
from contextlib import contextmanager
from pathlib import Path


class StoreError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


_MAX_ID = 128
_MAX_OWNER = 256
_MAX_TOKEN = 128
_MAX_STORED = 256


def _check_ident(value, name, maxlen):
    if not isinstance(value, str) or not value.strip() or len(value) > maxlen:
        raise StoreError("invalid", "invalid " + name)
    return value


def _check_expiry(expires_at, now):
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        raise StoreError("invalid", "invalid expiry")
    if expires_at <= now or expires_at > 2**63 - 1:
        raise StoreError("invalid", "expiry must be in the future")
    return expires_at


class TalkStore:
    def __init__(self, path, max_global=10000, max_per_owner=64, clock=None):
        self.path = Path(path)
        self.max_global = int(max_global)
        self.max_per_owner = int(max_per_owner)
        if min(self.max_global, self.max_per_owner) < 1:
            raise ValueError("Talk store capacity must be positive")
        self.clock = clock or (lambda: int(time.time()))
        self._prepare_path()

    def _prepare_path(self):
        p = self.path
        if p.is_symlink():
            raise StoreError("unavailable", "store path is a symlink")
        parent = p.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise StoreError("unavailable", "cannot create store directory")
        if parent.is_symlink():
            raise StoreError("unavailable", "store directory is a symlink")
        try:
            import os

            os.chmod(parent, 0o700)
        except OSError as exc:
            raise StoreError("unavailable", "cannot protect store directory") from exc
        if p.exists() and p.is_symlink():
            raise StoreError("unavailable", "store file is a symlink")
        try:
            conn = sqlite3.connect(str(p), timeout=5.0, isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS conversations ("
                    "id TEXT PRIMARY KEY,"
                    "owner_hash TEXT NOT NULL,"
                    "expires_at INTEGER NOT NULL,"
                    "stored_id TEXT UNIQUE,"
                    "state TEXT NOT NULL CHECK(state IN ('new','creating','ready','preparing','submitted','uncertain')),"
                    "token TEXT"
                    ")"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_owner ON conversations(owner_hash)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_expires ON conversations(expires_at)"
                )
            finally:
                conn.close()
        except sqlite3.Error:
            raise StoreError("unavailable", "cannot initialize store")
        try:
            import os

            os.chmod(p, 0o600)
        except OSError as exc:
            raise StoreError("unavailable", "cannot protect store file") from exc

    @contextmanager
    def _connect(self):
        try:
            conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        except sqlite3.Error:
            raise StoreError("unavailable", "cannot open store")
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            yield conn
        except sqlite3.Error:
            raise StoreError("unavailable", "store operation failed")
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    @contextmanager
    def _txn(self, conn):
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            raise StoreError("unavailable", "cannot begin transaction")
        try:
            yield
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _now(self):
        try:
            n = self.clock()
        except Exception:
            raise StoreError("unavailable", "clock failure")
        if isinstance(n, bool) or not isinstance(n, int):
            raise StoreError("unavailable", "clock failure")
        return n

    def _cleanup(self, conn, now):
        try:
            rows = conn.execute(
                "SELECT id FROM conversations WHERE expires_at <= ? LIMIT 100",
                (now,),
            ).fetchall()
            if rows:
                ids = [r[0] for r in rows]
                conn.executemany(
                    "DELETE FROM conversations WHERE id = ? AND expires_at <= ?",
                    [(i, now) for i in ids],
                )
        except sqlite3.Error:
            raise StoreError("unavailable", "cleanup failed")

    def _row(self, conn, owner, cid):
        try:
            return conn.execute(
                "SELECT id, owner_hash, expires_at, stored_id, state, token "
                "FROM conversations WHERE id = ? AND owner_hash = ?",
                (cid, owner),
            ).fetchone()
        except sqlite3.Error:
            raise StoreError("unavailable", "lookup failed")

    def _snapshot(self, row):
        return {
            "id": row[0],
            "expires_at": row[2],
            "stored_id": row[3],
            "state": row[4],
        }

    def allocate(self, owner, expires_at):
        owner = _check_ident(owner, "owner", _MAX_OWNER)
        now = self._now()
        expires_at = _check_expiry(expires_at, now)
        with self._connect() as conn:
            with self._txn(conn):
                now = self._now()
                expires_at = _check_expiry(expires_at, now)
                self._cleanup(conn, now)
                try:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM conversations WHERE expires_at > ?",
                        (now,),
                    ).fetchone()[0]
                    mine = conn.execute(
                        "SELECT COUNT(*) FROM conversations WHERE owner_hash = ? AND expires_at > ?",
                        (owner, now),
                    ).fetchone()[0]
                except sqlite3.Error:
                    raise StoreError("unavailable", "capacity check failed")
                if total >= self.max_global or mine >= self.max_per_owner:
                    raise StoreError("capacity", "store capacity reached")
                for _ in range(8):
                    cid = secrets.token_urlsafe(24)
                    if len(cid) > _MAX_ID:
                        continue
                    try:
                        conn.execute(
                            "INSERT INTO conversations "
                            "(id, owner_hash, expires_at, stored_id, state, token) "
                            "VALUES (?, ?, ?, NULL, 'new', NULL)",
                            (cid, owner, expires_at),
                        )
                        return cid
                    except sqlite3.IntegrityError:
                        continue
                    except sqlite3.Error:
                        raise StoreError("unavailable", "insert failed")
                raise StoreError("unavailable", "could not allocate id")

    def get(self, owner, cid):
        owner = _check_ident(owner, "owner", _MAX_OWNER)
        cid = _check_ident(cid, "id", _MAX_ID)
        now = self._now()
        with self._connect() as conn:
            with self._txn(conn):
                now = self._now()
                self._cleanup(conn, now)
                row = self._row(conn, owner, cid)
                if row is None:
                    raise StoreError("not_found", "conversation not found")
                if row[2] <= now:
                    raise StoreError("expired", "conversation expired")
                return self._snapshot(row)

    def _claim(self, owner, cid, from_state, to_state):
        owner = _check_ident(owner, "owner", _MAX_OWNER)
        cid = _check_ident(cid, "id", _MAX_ID)
        now = self._now()
        with self._connect() as conn:
            with self._txn(conn):
                now = self._now()
                self._cleanup(conn, now)
                row = self._row(conn, owner, cid)
                if row is None:
                    raise StoreError("not_found", "conversation not found")
                if row[2] <= now:
                    raise StoreError("expired", "conversation expired")
                if row[4] != from_state:
                    raise StoreError("conflict", "invalid state")
                token = secrets.token_urlsafe(24)
                if len(token) > _MAX_TOKEN:
                    raise StoreError("unavailable", "token generation failed")
                try:
                    conn.execute(
                        "UPDATE conversations SET state = ?, token = ? "
                        "WHERE id = ? AND owner_hash = ? AND state = ?",
                        (to_state, token, cid, owner, from_state),
                    )
                except sqlite3.Error:
                    raise StoreError("unavailable", "claim failed")
                return token

    def claim_create(self, owner, cid):
        return self._claim(owner, cid, "new", "creating")

    def claim_turn(self, owner, cid):
        return self._claim(owner, cid, "ready", "preparing")

    def _finish(self, owner, cid, token, from_state, to_state, stored_id):
        owner = _check_ident(owner, "owner", _MAX_OWNER)
        cid = _check_ident(cid, "id", _MAX_ID)
        token = _check_ident(token, "token", _MAX_TOKEN)
        if stored_id is not None:
            stored_id = _check_ident(stored_id, "stored_id", _MAX_STORED)
        now = self._now()
        with self._connect() as conn:
            with self._txn(conn):
                now = self._now()
                self._cleanup(conn, now)
                row = self._row(conn, owner, cid)
                if row is None:
                    raise StoreError("not_found", "conversation not found")
                if row[2] <= now:
                    raise StoreError("expired", "conversation expired")
                if row[4] != from_state:
                    raise StoreError("conflict", "invalid state")
                if row[5] != token:
                    raise StoreError("conflict", "invalid token")
                try:
                    conn.execute(
                        "UPDATE conversations SET state = ?, token = ?, "
                        "stored_id = COALESCE(?, stored_id) "
                        "WHERE id = ? AND owner_hash = ? AND state = ? AND token = ?",
                        (
                            to_state,
                            token if to_state == "submitted" else None,
                            stored_id,
                            cid,
                            owner,
                            from_state,
                            token,
                        ),
                    )
                except sqlite3.Error:
                    raise StoreError("unavailable", "finish failed")

    def finish_create(self, owner, cid, token, stored_id):
        _check_ident(stored_id, "stored_id", _MAX_STORED)
        self._finish(owner, cid, token, "creating", "ready", stored_id)

    def submit_turn(self, owner, cid, token):
        """Commit immediately before attempting prompt.submit, never after."""
        self._finish(owner, cid, token, "preparing", "submitted", None)

    def release_prepared_turn(self, owner, cid, token):
        """Release only when no prompt submission was attempted."""
        self._finish(owner, cid, token, "preparing", "ready", None)

    def finish_turn(self, owner, cid, token):
        self._finish(owner, cid, token, "submitted", "ready", None)

    def mark_uncertain(self, owner, cid, token):
        owner = _check_ident(owner, "owner", _MAX_OWNER)
        cid = _check_ident(cid, "id", _MAX_ID)
        token = _check_ident(token, "token", _MAX_TOKEN)
        now = self._now()
        with self._connect() as conn:
            with self._txn(conn):
                now = self._now()
                self._cleanup(conn, now)
                row = self._row(conn, owner, cid)
                if row is None:
                    raise StoreError("not_found", "conversation not found")
                if row[2] <= now:
                    raise StoreError("expired", "conversation expired")
                if row[4] not in ("creating", "preparing", "submitted"):
                    raise StoreError("conflict", "invalid state")
                if row[5] != token:
                    raise StoreError("conflict", "invalid token")
                try:
                    conn.execute(
                        "UPDATE conversations SET state = 'uncertain', token = NULL "
                        "WHERE id = ? AND owner_hash = ? AND state IN "
                        "('creating','preparing','submitted') AND token = ?",
                        (cid, owner, token),
                    )
                except sqlite3.Error:
                    raise StoreError("unavailable", "mark failed")
