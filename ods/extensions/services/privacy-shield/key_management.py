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
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
        try:
            os.chmod(path, 0o600)
        except Exception:
            # Best-effort only (may fail on some mounts/platforms)
            pass
    except Exception:
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
