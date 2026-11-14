"""The owner's connected-apps choices, kept by ODS across installs and updates.

The record holds choices only, never credentials: which capabilities the owner
turned on, the account, the Calendar and Gmail window, and the owner's time zone.
The custodian in the access coordinator writes it. Installs read it so a
connection survives an update. No record means nothing is connected; a record
that fails validation also counts as nothing connected, and callers report why.
"""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PATH = Path("/etc/ods/portal-connections.json")
KIND = "ods-portal-connections"
SCHEMA_VERSION = 1
MAX_BYTES = 64 * 1024
GOOGLE_CAPABILITIES = ("calendar.read", "calendar.write", "calendar.direct", "gmail.read", "gmail.drafts")
TOP_KEYS = {"schemaVersion", "kind", "updatedAt", "ownerTimeZone", "connectors"}
GOOGLE_KEYS = {"status", "account", "calendarId", "gmailWindowDays", "capabilities", "cloudModels", "connectedAt"}
EMAIL = re.compile(r"^[^@\s<>\x00-\x1f\x7f]{1,64}@[^@\s<>\x00-\x1f\x7f]{1,253}$")
CALENDAR_ID = re.compile(r"^[A-Za-z0-9._@+-]{1,255}$")
TIME_ZONE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$")


class StateError(ValueError):
    """The connection record exists but is unreadable or fails its schema."""


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise StateError(f"{name} must be a UTC timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StateError(f"{name} is not a real date and time") from error
    return value


def valid_time_zone(value: Any) -> bool:
    if not isinstance(value, str) or not TIME_ZONE.fullmatch(value) or ".." in value:
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # Unknown names, and region directories such as "America", are not zones.
        return False
    return True


def _google(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != GOOGLE_KEYS:
        raise StateError("Google connection has missing or unknown fields")
    if value["status"] != "connected":
        raise StateError("Google connection status is unknown")
    if not isinstance(value["account"], str) or not EMAIL.fullmatch(value["account"]):
        raise StateError("Google account must be an email address")
    if not isinstance(value["calendarId"], str) or not CALENDAR_ID.fullmatch(value["calendarId"]):
        raise StateError("Calendar ID is invalid")
    window = value["gmailWindowDays"]
    if type(window) is not int or not 1 <= window <= 3650:
        raise StateError("Gmail window must be 1 to 3650 days")
    capabilities = value["capabilities"]
    if (not isinstance(capabilities, dict) or set(capabilities) != set(GOOGLE_CAPABILITIES)
            or any(type(item) is not bool for item in capabilities.values())):
        raise StateError("Google capabilities have missing, unknown or non-boolean fields")
    if type(value["cloudModels"]) is not bool:
        raise StateError("cloudModels must be true or false")
    _timestamp(value["connectedAt"], "connectedAt")
    return value


def validate(value: Any) -> dict[str, Any]:
    """Return the record unchanged when it matches the schema exactly."""
    if not isinstance(value, dict) or set(value) != TOP_KEYS:
        raise StateError("connection record has missing or unknown fields")
    if value["schemaVersion"] != SCHEMA_VERSION or value["kind"] != KIND:
        raise StateError("connection record has an unknown version or kind")
    _timestamp(value["updatedAt"], "updatedAt")
    if value["ownerTimeZone"] is not None and not valid_time_zone(value["ownerTimeZone"]):
        raise StateError("owner time zone is not an IANA time zone")
    connectors = value["connectors"]
    if not isinstance(connectors, dict) or set(connectors) - {"google"}:
        raise StateError("connection record names an unknown connector")
    if "google" in connectors:
        _google(connectors["google"])
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StateError(f"connection record repeats the key {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise StateError(f"connection record contains {value}")


def parse(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise StateError("connection record exceeds its size limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except StateError:
        raise
    except (UnicodeError, ValueError) as error:
        raise StateError("connection record is not strict UTF-8 JSON") from error
    return validate(value)


def read(path: Path = PATH, *, owners: tuple[int, ...] = (0,)) -> dict[str, Any] | None:
    """The validated record, None when there is none, or StateError when it is unusable.

    The file and its directory must belong to one of ``owners`` and must not be
    writable by group or others, so an unprivileged process cannot plant choices.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        if os.path.islink(path):
            raise StateError("connection record is a symbolic link") from None
        return None
    except OSError as error:
        raise StateError("connection record cannot be opened safely") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise StateError("connection record is not a bounded regular file")
        if os.name != "nt" and (info.st_uid not in owners or info.st_mode & 0o022):
            raise StateError("connection record is not protected from other accounts")
        raw = handle.read(MAX_BYTES + 1)
    parent = Path(path).parent.lstat()
    if stat.S_ISLNK(parent.st_mode) or (os.name != "nt" and (parent.st_uid not in owners or parent.st_mode & 0o022)):
        raise StateError("connection record directory is not protected from other accounts")
    return parse(raw)


def onboarding_overrides(record: dict[str, Any] | None, *, google_custody: bool) -> dict[str, Any]:
    """Pixel onboarding answers the record implies; empty when nothing is connected.

    A connected Google account counts only while its broker holds the sign-in
    (``google_custody``). After a restore without tokens the record still says
    connected, the sources stay off, and status reports that Google needs
    reconnecting.
    """
    if record is None:
        return {}
    overrides: dict[str, Any] = {}
    zone = record["ownerTimeZone"]
    if zone:
        overrides.update(timeZone=zone, userTimezone=zone)
    google = record["connectors"].get("google")
    if google and google_custody:
        capabilities = google["capabilities"]
        writes = capabilities["calendar.read"] and capabilities["calendar.write"]
        overrides.update(
            emailLimbEnabled=capabilities["gmail.read"],
            calendarLimbEnabled=capabilities["calendar.read"],
            calendarWriteEnabled=writes,
            calendarDirectEnabled=writes and capabilities["calendar.direct"],
            googleAccount=google["account"],
            calendarId=google["calendarId"],
            gmailWindowDays=google["gmailWindowDays"],
        )
    return overrides
