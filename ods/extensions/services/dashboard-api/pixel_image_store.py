"""Private owner/conversation-bound image blobs; no public preview URLs.

Capacity exhaustion never evicts an image referenced by existing conversation
history. Explicit conversation deletion is the lifecycle boundary.
"""

import hashlib
from collections import deque
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
MAX_DELETED_CONVERSATIONS = 8192
DRAFT_TTL_SECONDS = 7 * 24 * 60 * 60
_OWNER = re.compile(r"[a-f0-9]{64}")
_CHAT = re.compile(r"[A-Za-z0-9_-]{1,128}")
_IDENTITY = re.compile(r"img-[a-f0-9]{32}")
_FILE_ROLES = {"": "database", "-journal": "journal", "-wal": "wal", "-shm": "shm"}
_CUSTODY_REASONS = frozenset({
    "directory_path", "directory_owner", "directory_mode",
    *(f"{role}_{check}" for role in _FILE_ROLES.values()
      for check in ("type", "links", "owner", "mode")),
})
_CUSTODY_STAGES = frozenset({"directory", "existing_file", "created_file"})
_CUSTODY_NUMBERS = ("expected_uid", "actual_uid", "device", "inode", "mode", "nlink")
# Diagnostic observations only: never authorize an open or ownership repair.
# Bound memory even if callers use many temporary stores in one process.
# Device/inode pairs can be reused. This is historical evidence, not proof
# that the current file is the same generation as a recorded creation.
_created_stores: deque[dict] = deque(maxlen=64)


def _stat_metadata(info, expected_uid, stage):
    return {"stage": stage, "expected_uid": expected_uid, "actual_uid": info.st_uid,
            "device": info.st_dev, "inode": info.st_ino,
            "mode": stat.S_IMODE(info.st_mode), "nlink": info.st_nlink}


def _safe_metadata_description(metadata):
    """Fixed field names and bounded numbers only, even for malformed errors."""
    if type(metadata) is not dict:
        return ""
    fields = []
    stage = metadata.get("stage")
    if type(stage) is str and stage in _CUSTODY_STAGES:
        fields.append(f"stage={stage}")
    for name in _CUSTODY_NUMBERS:
        value = metadata.get(name)
        maximum = 0o7777 if name == "mode" else (1 << 64) - 1
        if type(value) is int and 0 <= value <= maximum:
            fields.append(f"{name}={value}")
    return "; ".join(fields)


class ImageStoreCustodyError(ValueError):
    """A refused storage identity; exception text is never safe to publish."""

    def __init__(self, reason: str, message: str, *, metadata=None, creation_metadata=None):
        super().__init__(message)
        self.reason = reason
        self.metadata = metadata
        self.creation_metadata = creation_metadata


def custody_failure_reason(error: BaseException) -> str | None:
    """Only fixed reason codes may enter diagnostics, never exception values."""
    if not isinstance(error, ImageStoreCustodyError):
        return None
    reason = error.reason
    return reason if type(reason) is str and reason in _CUSTODY_REASONS else "unknown"


def custody_failure_metadata(error: BaseException) -> str:
    if not isinstance(error, ImageStoreCustodyError):
        return ""
    observed = _safe_metadata_description(error.metadata)
    created = _safe_metadata_description(error.creation_metadata)
    return observed + (f"; historical_creation=({created})" if created else "")


def _historical_creation(info):
    for metadata in reversed(tuple(_created_stores)):
        if (metadata["device"], metadata["inode"]) == (info.st_dev, info.st_ino):
            return metadata.copy()
    return None


class ImageStoreCapacity(ValueError):
    pass


class ConversationDeleted(ValueError):
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
        expected_uid = os.geteuid() if os.name == "posix" else None
        directory_metadata = _stat_metadata(info, expected_uid, "directory")
        if directory.resolve() != directory or not stat.S_ISDIR(info.st_mode):
            raise ImageStoreCustodyError("directory_path", "Invalid image store directory", metadata=directory_metadata)
        if os.name == "posix" and info.st_uid != expected_uid:
            raise ImageStoreCustodyError("directory_owner", "Image store directory must be private", metadata=directory_metadata)
        if os.name == "posix" and info.st_mode & 0o077:
            raise ImageStoreCustodyError("directory_mode", "Image store directory must be private", metadata=directory_metadata)
        path = directory / "images.sqlite3"
        for suffix, role in _FILE_ROLES.items():
            candidate = Path(str(path) + suffix)
            if candidate.exists() or candidate.is_symlink():
                info = candidate.lstat()
                reason = None
                if not stat.S_ISREG(info.st_mode):
                    reason = "type"
                elif info.st_nlink != 1:
                    reason = "links"
                elif os.name == "posix" and info.st_uid != expected_uid:
                    reason = "owner"
                elif os.name == "posix" and info.st_mode & 0o077:
                    reason = "mode"
                if reason is not None:
                    raise ImageStoreCustodyError(f"{role}_{reason}", "Image store files must be private regular files",
                                                metadata=_stat_metadata(info, expected_uid, "existing_file"),
                                                creation_metadata=_historical_creation(info))
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                metadata = _stat_metadata(os.fstat(fd), expected_uid, "created_file")
                _created_stores.append(metadata)
            finally:
                os.close(fd)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.execute("PRAGMA secure_delete=ON")
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
        self.db.execute("CREATE TABLE IF NOT EXISTS deleted_conversations (owner TEXT NOT NULL, chat TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,chat))")
        self.db.commit()

    def close(self):
        self.db.close()

    def deletion(self, owner, chat):
        _scope(owner, chat)
        row = self.db.execute("SELECT completed FROM deleted_conversations WHERE owner=? AND chat=?", (owner, chat)).fetchone()
        return None if row is None else {"completed": bool(row["completed"])}

    def assert_available(self, owner, chat):
        if self.deletion(owner, chat) is not None:
            raise ConversationDeleted("This conversation is being deleted or was deleted. Start a new conversation.")

    def begin_delete(self, owner, chat):
        _scope(owner, chat)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.deletion(owner, chat) is None and self.db.execute("SELECT COUNT(*) FROM deleted_conversations").fetchone()[0] >= MAX_DELETED_CONVERSATIONS:
                raise ImageStoreCapacity("Conversation deletion records are full. Existing deletion fences were preserved.")
            self.db.execute("INSERT OR IGNORE INTO deleted_conversations(owner,chat) VALUES (?,?)", (owner, chat))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self.deletion(owner, chat)

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ("id", "sha256", "media_type", "width", "height")}

    def put(self, owner, chat, data, media_type):
        _scope(owner, chat)
        image = validate_image(data, media_type)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.assert_available(owner, chat)
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
        self.assert_available(owner, chat)
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
        self.begin_delete(owner, chat)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO deleted_conversations(owner,chat) VALUES (?,?)", (owner, chat))
            self.db.execute("DELETE FROM images WHERE owner=? AND chat=?", (owner, chat))
            self.db.execute("UPDATE deleted_conversations SET completed=1 WHERE owner=? AND chat=?", (owner, chat))
        return {"deleted": True}

    def retain(self, owner, chat, references):
        """Protect resolved bytes before a chat can dispatch them.

        A failed dispatch may retain an unused image. This conservative choice
        prevents a concurrent draft removal from breaking a sent conversation.
        """
        _scope(owner, chat)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.assert_available(owner, chat)
            for reference in references:
                changed = self.db.execute(
                    "UPDATE images SET retained=1 WHERE owner=? AND chat=? AND id=? AND sha256=?",
                    (owner, chat, reference["id"], reference["sha256"]),
                )
                if changed.rowcount != 1:
                    raise ValueError("Conversation image disappeared before retention")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

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
