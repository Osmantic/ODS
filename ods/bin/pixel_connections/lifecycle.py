"""The root journal of a connection in progress, from consent until Portal's gateway matches.

The coordinator writes it; status reads it so Settings can say "waiting for
Google" or offer the one button that finishes a stuck step. It holds no token,
code, PKCE verifier or state, only a hash of the state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

PATH = Path("/var/lib/ods-pixel-access/connection.json")
KEYS = {"kind", "connector", "phase", "flowId", "stateHash", "redirectUri", "expiresAt", "request",
        "error", "updatedAt"}
PHASES = ("consent", "activating", "deactivating")
CONSENT_KEYS = ("flowId", "stateHash", "redirectUri", "expiresAt", "request")
CODE = re.compile(r"^[a-z][a-z0-9-]{0,95}$")
INSTANT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
MAX_BYTES = 8192


class LifecycleError(ValueError):
    pass


def validate(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != KEYS or value["kind"] != "connection"
            or value["connector"] != "google" or value["phase"] not in PHASES
            or value["error"] is not None and (type(value["error"]) is not str or not CODE.fullmatch(value["error"]))
            or value["updatedAt"] is not None and (type(value["updatedAt"]) is not str
                                                   or not INSTANT.fullmatch(value["updatedAt"]))):
        raise LifecycleError("invalid connection journal")
    consent = value["phase"] == "consent"
    if consent != all(value[key] is not None for key in CONSENT_KEYS) or not consent and any(
            value[key] is not None for key in CONSENT_KEYS):
        raise LifecycleError("invalid connection journal")
    if consent and (type(value["flowId"]) is not str or not re.fullmatch(r"[a-f0-9]{32}", value["flowId"])
                    or type(value["stateHash"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["stateHash"])
                    or type(value["redirectUri"]) is not str
                    or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{4,5}", value["redirectUri"])
                    or type(value["expiresAt"]) is not str or not INSTANT.fullmatch(value["expiresAt"])
                    or not isinstance(value["request"], dict)
                    or set(value["request"]) != {"capabilities", "cloudModels", "ownerTimeZone"}):
        raise LifecycleError("invalid connection journal")
    return value


def read(path: Path = PATH, *, owners: tuple = (0,)) -> dict[str, Any] | None:
    """The journal, None when nothing is in progress; it must be root's private file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise LifecycleError("unsafe connection journal")
        if os.name != "nt" and (info.st_uid not in owners or info.st_mode & 0o077):
            raise LifecycleError("unsafe connection journal")
        raw = handle.read(MAX_BYTES + 1)
    try:
        return validate(json.loads(raw.decode("utf-8")))
    except (UnicodeError, ValueError) as error:
        raise LifecycleError("invalid connection journal") from error


def write(path: Path, value: dict[str, Any]) -> None:
    data = (json.dumps(validate(value), sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def public(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """What status may show about a step in progress; no flow ID, state hash or address."""
    if value is None:
        return None
    return {"action": "disconnect" if value["phase"] == "deactivating" else "connect",
            "phase": value["phase"], "expiresAt": value["expiresAt"], "error": value["error"],
            "since": value["updatedAt"]}


def valid_public(value: Any) -> bool:
    """The public pending shape that status, the Edge and dashboard-api all accept."""
    if value is None:
        return True
    if (not isinstance(value, dict) or set(value) != {"action", "phase", "expiresAt", "error", "since"}
            or type(value["since"]) is not str or not INSTANT.fullmatch(value["since"])
            or value["phase"] not in PHASES
            or value["action"] != ("disconnect" if value["phase"] == "deactivating" else "connect")
            or value["error"] is not None and (type(value["error"]) is not str or not CODE.fullmatch(value["error"]))):
        return False
    if value["phase"] == "consent":
        return type(value["expiresAt"]) is str and INSTANT.fullmatch(value["expiresAt"]) is not None
    return value["expiresAt"] is None
