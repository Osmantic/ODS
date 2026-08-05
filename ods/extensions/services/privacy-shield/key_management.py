"""Small utilities for managing Privacy Shield credentials.

Kept separate from proxy.py so it can be unit-tested without importing FastAPI/httpx.
"""

from __future__ import annotations

import logging
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional


def load_persisted_key(path: str) -> Optional[str]:
    try:
        key = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return key or None


def persist_key(path: str, key: str) -> None:
    key_path = Path(path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{key_path.name}.",
        suffix=".tmp",
        dir=key_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, key_path)
    finally:
        temporary.unlink(missing_ok=True)


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
