"""Persistent, salted dashboard password; never store the plaintext credential."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path

from config import DATA_DIR
from fastapi import HTTPException

PASSWORD_FILE = Path(DATA_DIR) / "dashboard-password.json"
_ITERATIONS = 600_000


def record() -> dict | None:
    try:
        value = json.loads(PASSWORD_FILE.read_text(encoding="utf-8"))
        if (value.get("version") != 1 or value.get("iterations") != _ITERATIONS
                or len(bytes.fromhex(value["salt"])) != 32
                or len(bytes.fromhex(value["digest"])) != 32
                or len(bytes.fromhex(value["revision"])) != 24):
            raise ValueError("Invalid password record")
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Never turn a damaged or unreadable credential into an unclaimed setup.
        raise HTTPException(503, "Dashboard password storage is unavailable. Check ODS on this machine.") from None


def revision() -> str:
    saved = record()
    return saved["revision"] if saved else "unconfigured"


def verify(password: str) -> bool:
    saved = record()
    # Same expensive derivation even before setup; no cheap password probe.
    salt = bytes.fromhex(saved["salt"]) if saved else bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS).hex()
    return bool(saved and hmac.compare_digest(digest, saved["digest"]))


def save(password: str) -> None:
    if not isinstance(password, str) or not 6 <= len(password) <= 128:
        raise HTTPException(422, "Use a password or passphrase with 6 to 128 characters.")
    salt = secrets.token_bytes(32)
    value = {"version": 1, "iterations": _ITERATIONS, "salt": salt.hex(),
             "digest": hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS).hex(),
             "revision": secrets.token_hex(24)}
    temporary = None
    try:
        PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".dashboard-password-", dir=PASSWORD_FILE.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, PASSWORD_FILE)
    except OSError:
        raise HTTPException(503, "Could not save the password. Check available storage on the ODS machine and try again.") from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
