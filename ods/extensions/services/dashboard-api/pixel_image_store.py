"""Private owner/conversation-bound image blobs; no public preview URLs.

Capacity exhaustion never evicts an image referenced by existing conversation
history. Explicit conversation deletion is the lifecycle boundary.
"""

import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from pixel_image_input import validate_image


MAX_STORE_BYTES = 128 * 1024 * 1024
MAX_STORE_IMAGES = 512
DRAFT_TTL_SECONDS = 7 * 24 * 60 * 60
_OWNER = re.compile(r"[a-f0-9]{64}")
_CHAT = re.compile(r"[A-Za-z0-9_-]{1,128}")
_IDENTITY = re.compile(r"img-[a-f0-9]{32}")


class ImageStoreCapacity(ValueError):
    pass


def _scope(owner, chat):
    if not isinstance(owner, str) or not _OWNER.fullmatch(owner):
        raise ValueError("Invalid attachment owner namespace")
    if not isinstance(chat, str) or not _CHAT.fullmatch(chat):
        raise ValueError("Invalid attachment conversation")


class ImageStore:
    def __init__(self, directory: Path):
        directory = directory.absolute()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if directory.resolve() != directory or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Invalid image store directory")
        if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError("Image store directory must be private")
        path = directory / "images.sqlite3"
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists() or candidate.is_symlink():
                info = candidate.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
                    raise ValueError("Image store files must be private regular files")
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS images (
            owner TEXT NOT NULL, chat TEXT NOT NULL, id TEXT NOT NULL,
            sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
            width INTEGER NOT NULL, height INTEGER NOT NULL, data BLOB NOT NULL,
            PRIMARY KEY(owner,chat,id), UNIQUE(owner,chat,sha256))""")
        self.db.commit()
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(images)")}
        if "retained" not in columns:
            # Pre-existing blobs may already belong to conversation history.
            # Never infer that they are disposable during a schema upgrade.
            self.db.execute("ALTER TABLE images ADD COLUMN retained INTEGER NOT NULL DEFAULT 1")
            self.db.commit()
        if "draft_expires_at" not in columns:
            self.db.execute("ALTER TABLE images ADD COLUMN draft_expires_at REAL")
            self.db.commit()

    def close(self):
        self.db.close()

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ("id", "sha256", "media_type", "width", "height")}

    def put(self, owner, chat, data, media_type):
        _scope(owner, chat)
        image = validate_image(data, media_type)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            # Only uploads never retained by a send attempt can expire. Legacy
            # rows without a deadline are preserved. An active thumbnail read
            # or repeated upload renews the draft lease.
            now = time.time()
            self.db.execute("DELETE FROM images WHERE retained=0 AND draft_expires_at<=?", (now,))
            old = self.db.execute("SELECT * FROM images WHERE owner=? AND chat=? AND sha256=?",
                                  (owner, chat, image.sha256)).fetchone()
            if old is not None:
                if hashlib.sha256(old["data"]).hexdigest() != image.sha256:
                    raise ValueError("Stored image integrity check failed")
                self.db.execute("UPDATE images SET draft_expires_at=? WHERE owner=? AND chat=? AND id=? AND retained=0",
                                (now + DRAFT_TTL_SECONDS, owner, chat, old["id"]))
                self.db.commit()
                return self._public(old)
            usage = self.db.execute("SELECT COUNT(*), COALESCE(SUM(length(data)),0) FROM images").fetchone()
            if usage[0] >= MAX_STORE_IMAGES or usage[1] + len(data) > MAX_STORE_BYTES:
                raise ImageStoreCapacity("Image storage is full; existing conversation images were preserved.")
            identity = "img-" + uuid.uuid4().hex
            self.db.execute("INSERT INTO images (owner,chat,id,sha256,media_type,width,height,data,retained,draft_expires_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
                            (owner, chat, identity, image.sha256, image.media_type, image.width, image.height, data,
                             now + DRAFT_TTL_SECONDS))
            self.db.commit()
            return dict(id=identity, sha256=image.sha256, media_type=image.media_type,
                        width=image.width, height=image.height)
        except BaseException:
            self.db.rollback()
            raise

    def get(self, owner, chat, identity):
        _scope(owner, chat)
        if not isinstance(identity, str) or not _IDENTITY.fullmatch(identity):
            raise ValueError("Invalid image identity")
        row = self.db.execute("SELECT * FROM images WHERE owner=? AND chat=? AND id=?",
                              (owner, chat, identity)).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row["data"]).hexdigest() != row["sha256"]:
            raise ValueError("Stored image integrity check failed")
        if not row["retained"]:
            with self.db:
                self.db.execute("UPDATE images SET draft_expires_at=? WHERE owner=? AND chat=? AND id=?",
                                (time.time() + DRAFT_TTL_SECONDS, owner, chat, identity))
        return {**self._public(row), "data": row["data"]}

    def delete_conversation(self, owner, chat):
        _scope(owner, chat)
        with self.db:
            self.db.execute("DELETE FROM images WHERE owner=? AND chat=?", (owner, chat))

    def retain(self, owner, chat, references):
        """Protect resolved bytes before a chat can dispatch them.

        A failed dispatch may retain an unused image. This conservative choice
        prevents a concurrent draft removal from breaking a sent conversation.
        """
        _scope(owner, chat)
        with self.db:
            for reference in references:
                changed = self.db.execute(
                    "UPDATE images SET retained=1 WHERE owner=? AND chat=? AND id=? AND sha256=?",
                    (owner, chat, reference["id"], reference["sha256"]),
                )
                if changed.rowcount != 1:
                    raise ValueError("Conversation image disappeared before retention")

    def discard_draft(self, owner, chat, identity):
        """Remove only an unsubmitted upload, with an idempotent receipt."""
        _scope(owner, chat)
        if not isinstance(identity, str) or not _IDENTITY.fullmatch(identity):
            raise ValueError("Invalid image identity")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT retained FROM images WHERE owner=? AND chat=? AND id=?",
                                  (owner, chat, identity)).fetchone()
            retained = bool(row and row["retained"])
            if row and not retained:
                self.db.execute("DELETE FROM images WHERE owner=? AND chat=? AND id=?", (owner, chat, identity))
            self.db.commit()
            return {"retained": retained, "discarded": not retained}
        except BaseException:
            self.db.rollback()
            raise
