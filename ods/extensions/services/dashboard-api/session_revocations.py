"""Persistent per-cookie revocation for signed ODS sessions."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class RevocationStore:
    """Thread-safe, process-local cache backed by an atomic JSON record."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._revoked: dict[str, int] | None = None

    def _load_locked(self) -> dict[str, int]:
        if self._revoked is not None:
            return self._revoked
        if not self.path.exists():
            self._revoked = {}
            return self._revoked

        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("session revocation store has an unsupported schema")
        entries = data.get("revoked")
        if not isinstance(entries, dict):
            raise ValueError("session revocation store must contain a revoked object")

        revoked: dict[str, int] = {}
        for session_id, expiry in entries.items():
            if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
                raise ValueError("session revocation store contains an invalid session id")
            if isinstance(expiry, bool) or not isinstance(expiry, int):
                raise ValueError("session revocation store contains an invalid expiry")
            revoked[session_id] = expiry
        self._revoked = revoked
        return revoked

    def _write_locked(self, revoked: dict[str, int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
        )
        try:
            os.chmod(temporary, 0o600)
            payload = {"schema_version": 1, "revoked": revoked}
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            Path(temporary).unlink(missing_ok=True)

    def is_revoked(self, session_id: str, *, now: int | None = None) -> bool:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("invalid session id")
        current_time = int(time.time()) if now is None else now
        with self._lock:
            expiry = self._load_locked().get(session_id)
            return expiry is not None and expiry > current_time

    def revoke(
        self,
        session_id: str,
        expires_at: int,
        *,
        now: int | None = None,
    ) -> None:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("invalid session id")
        if isinstance(expires_at, bool) or not isinstance(expires_at, int):
            raise ValueError("expires_at must be an integer")
        current_time = int(time.time()) if now is None else now
        if expires_at <= current_time:
            raise ValueError("cannot revoke an expired session")

        with self._lock:
            current = self._load_locked()
            active = {
                key: expiry for key, expiry in current.items()
                if expiry > current_time
            }
            active[session_id] = max(expires_at, active.get(session_id, 0))
            self._write_locked(active)
            self._revoked = active
