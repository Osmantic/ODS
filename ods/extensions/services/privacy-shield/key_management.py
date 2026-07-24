"""Small utilities for managing Privacy Shield credentials.

Kept separate from proxy.py so it can be unit-tested without importing FastAPI/httpx.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional


def load_persisted_key(path: str) -> Optional[str]:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
        return key or None
    except Exception:
        logging.exception("Failed to read persisted SHIELD_API_KEY")
        return None


def persist_key(path: str, key: str) -> None:
    """Write the generated key, created owner-readable only.

    The mode is set when the file is created rather than by a chmod after the
    write. Writing first leaves the credential at the process umask (0644 on
    a default Linux host) for the window between the two calls, and the
    directory is a bind mount shared with the host — and a chmod that fails
    used to be swallowed, leaving the key world-readable permanently.
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
        try:
            # O_CREAT leaves an existing file's mode alone, so tighten it too.
            os.chmod(path, 0o600)
        except OSError:
            # Some mounts (Docker volumes on non-POSIX hosts) don't honour
            # chmod. Say so instead of hiding it — the file may be readable.
            logging.warning(
                "Could not set 0600 on %s; the shield API key may be readable "
                "by other users on this host", path,
            )
    except OSError:
        logging.exception("Failed to persist generated SHIELD_API_KEY")


def resolve_shield_api_key(env_key: Optional[str], key_path: str) -> str:
    """Resolve the API key used by Privacy Shield.

    Precedence:
    1) Explicit env var (preferred)
    2) Persisted key file (to survive restarts)
    3) Generated key (persisted for future reuse)
    """

    if env_key:
        return env_key

    persisted = load_persisted_key(key_path)
    if persisted:
        logging.info("Loaded persisted SHIELD_API_KEY from disk")
        return persisted

    key = secrets.token_urlsafe(32)
    persist_key(key_path, key)
    logging.warning(
        "SHIELD_API_KEY not set. Generated a key and persisted it for reuse. "
        "Set SHIELD_API_KEY in .env to manage it explicitly."
    )
    return key
