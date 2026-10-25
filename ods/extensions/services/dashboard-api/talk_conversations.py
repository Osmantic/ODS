"""Owner/tab-bound durable identity. No cookies, credentials or transcript stored.

This first delivery supports one dashboard-api process per state directory.
An OS-held lock refuses a second worker rather than inventing an expiring lease
which could permit two writers after a slow request. Process death releases the
lock; durable delivery state still records an uncertain submission.
"""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3

_STORES = {}
_TOKEN = re.compile(r'^[A-Za-z0-9_-]{32,128}$')


class ConversationError(RuntimeError):
    pass


def enabled():
    return os.environ.get('ODS_TALK_DURABLE', '').lower() == 'true'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def store():
    from config import DATA_DIR
    path = Path(DATA_DIR) / 'talk' / 'conversations.sqlite3'
    key = str(path.resolve())
    if key not in _STORES:
        _STORES[key] = Store(path)
    return _STORES[key]


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.guard = self.path.with_suffix('.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.guard.seek(0)
                if self.guard.read(1) == b'':
                    self.guard.write(b'0')
                    self.guard.flush()
                self.guard.seek(0)
                msvcrt.locking(self.guard.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.guard.close()
            raise ConversationError('Durable Talk currently requires one dashboard API worker.') from exc
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS conversations ('
                       'key TEXT PRIMARY KEY, owner TEXT NOT NULL, tab TEXT NOT NULL, '
                       'root TEXT, tip TEXT, runtime TEXT, delivery TEXT NOT NULL DEFAULT \'provisioning\')')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def resolve(self, owner, tab, handle, *, create=False):
        if not all(isinstance(x, str) and _TOKEN.fullmatch(x) for x in (tab, handle)):
            raise ConversationError('Conversation is unavailable for this tab.')
        key = digest(handle)
        with self.connect() as db:
            if create:
                db.execute('INSERT OR IGNORE INTO conversations(key,owner,tab) VALUES (?,?,?)',
                           (key, owner, digest(tab)))
            row = db.execute('SELECT * FROM conversations WHERE key=? AND owner=? AND tab=?',
                             (key, owner, digest(tab))).fetchone()
        if row is None:
            raise ConversationError('Conversation is unavailable for this tab.')
        return 'durable:' + key

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM conversations WHERE key=?', (key.removeprefix('durable:'),)).fetchone()
        if row is None:
            raise ConversationError('Conversation is unavailable for this tab.')
        return dict(row)

    def created(self, key, root, runtime):
        with self.connect() as db:
            db.execute('UPDATE conversations SET root=?,tip=?,runtime=?,delivery=\'idle\' WHERE key=? AND root IS NULL',
                       (root, root, runtime, key.removeprefix('durable:')))

    def resumed(self, key, tip, runtime, *, running):
        with self.connect() as db:
            db.execute('UPDATE conversations SET tip=?,runtime=?,delivery=? WHERE key=?',
                       (tip, runtime, 'uncertain' if running else 'idle', key.removeprefix('durable:')))

    def delivery(self, key, state):
        with self.connect() as db:
            db.execute('UPDATE conversations SET delivery=? WHERE key=?', (state, key.removeprefix('durable:')))

    def close(self):
        self.guard.close()
