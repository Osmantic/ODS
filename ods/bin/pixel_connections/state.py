"""The owner's connected-apps choices, kept by ODS across installs and updates.

The record holds choices only, never credentials: which capabilities the owner
turned on, the account, the Calendar and Gmail window, the owner's time zone,
and which reviewed plugins Portal may use (plugins.py).
One fact rides along with them: ``directPaused`` says Portal had Full Access when
the custodian last looked, so the calendar changes Portal may make by itself wait
(Decision 4). Every renderer reads the effective capabilities, never the raw ones.
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

from . import plugins as portal_plugins
from . import native_apps

PATH = Path("/etc/ods/portal-connections.json")
KIND = "ods-portal-connections"
SCHEMA_VERSION = 1
MAX_BYTES = 64 * 1024
GOOGLE_CAPABILITIES = ("calendar.read", "calendar.write", "calendar.direct", "gmail.read", "gmail.drafts")
TOP_KEYS = {"schemaVersion", "kind", "updatedAt", "ownerTimeZone", "connectors"}
# Written since the plugin catalog; a record without it turns no plugin on. An ID
# this release does not know (say, after going back to an older ODS) is kept and ignored.
OPTIONAL_TOP_KEYS = {"plugins", "nativeApps"}
MAX_PLUGINS = 32
GOOGLE_KEYS = {"status", "account", "calendarId", "gmailWindowDays", "capabilities", "cloudModels", "connectedAt"}
# Written since automatic calendar changes became available; a record without it is not paused.
OPTIONAL_GOOGLE_KEYS = {"directPaused"}
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
    if not isinstance(value, dict) or not GOOGLE_KEYS <= set(value) <= GOOGLE_KEYS | OPTIONAL_GOOGLE_KEYS:
        raise StateError("Google connection has missing or unknown fields")
    if "directPaused" in value and type(value["directPaused"]) is not bool:
        raise StateError("directPaused must be true or false")
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
    if not isinstance(value, dict) or not TOP_KEYS <= set(value) <= TOP_KEYS | OPTIONAL_TOP_KEYS:
        raise StateError("connection record has missing or unknown fields")
    chosen = value.get("plugins", {})
    if (not isinstance(chosen, dict) or len(chosen) > MAX_PLUGINS
            or any(not isinstance(key, str) or not portal_plugins.PLUGIN_ID.fullmatch(key) or type(item) is not bool
                   for key, item in chosen.items())):
        raise StateError("connection record has an invalid plugin choice")
    if value["schemaVersion"] != SCHEMA_VERSION or value["kind"] != KIND:
        raise StateError("connection record has an unknown version or kind")
    native = value.get('nativeApps', {})
    if (type(native) is not dict or len(native) > MAX_PLUGINS
            or any(type(key) is not str or not portal_plugins.PLUGIN_ID.fullmatch(key)
                   or type(flag) is not bool for key, flag in native.items())):
        raise StateError('connection record has an invalid native app choice')
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


# The connection keys of pixel-onboarding.py's contract with nothing connected;
# the optional ones are left out entirely.
DISCONNECTED_ANSWERS = {"timeZone": "UTC", "googleAccount": "ods@localhost.local", "calendarId": "primary",
                        "emailLimbEnabled": False, "calendarLimbEnabled": False, "calendarDirectEnabled": False}
OPTIONAL_ANSWERS = ("userTimezone", "calendarWriteEnabled", "gmailWindowDays", "emailDraftsEnabled", "portalPlugins", "nativeMcpApps")
ANSWER_KEYS = (*DISCONNECTED_ANSWERS, *OPTIONAL_ANSWERS)


def connection_answers(record: dict[str, Any] | None, *, google_custody: bool) -> dict[str, Any]:
    """Every connection key of the onboarding contract for the record; None removes a key."""
    answers: dict[str, Any] = dict(DISCONNECTED_ANSWERS, **dict.fromkeys(OPTIONAL_ANSWERS))
    answers.update(onboarding_overrides(record, google_custody=google_custody))
    return answers


def valid_connection_answers(value: Any) -> bool:
    """The exact shape connection_answers produces, as it crosses the owner-worker pipe."""
    if not isinstance(value, dict) or set(value) not in (set(ANSWER_KEYS), set(ANSWER_KEYS) - {'nativeMcpApps'}):
        return False
    window, write, zone = value["gmailWindowDays"], value["calendarWriteEnabled"], value["userTimezone"]
    drafts = value["emailDraftsEnabled"]
    return (isinstance(value["timeZone"], str) and bool(TIME_ZONE.fullmatch(value["timeZone"]))
            and (zone is None or isinstance(zone, str) and bool(TIME_ZONE.fullmatch(zone)))
            and isinstance(value["googleAccount"], str) and bool(EMAIL.fullmatch(value["googleAccount"]))
            and isinstance(value["calendarId"], str) and bool(CALENDAR_ID.fullmatch(value["calendarId"]))
            and all(type(value[key]) is bool for key in ("emailLimbEnabled", "calendarLimbEnabled",
                                                         "calendarDirectEnabled"))
            and (write is None or type(write) is bool) and (drafts is None or type(drafts) is bool)
            and (window is None or type(window) is int and 1 <= window <= 3650)
            and portal_plugins.valid_answers(value["portalPlugins"])
            and native_apps.valid_answers(value.get('nativeMcpApps')))


def native_app_choices(record: dict[str, Any] | None) -> dict[str, bool]:
    chosen = (record or {}).get('nativeApps', {})
    return {app_id: chosen[app_id] for app_id in native_apps.IDS if app_id in chosen}


def enabled_plugins(record: dict[str, Any] | None) -> list[str]:
    """The catalog plugins the owner turned on, in catalog order."""
    chosen = (record or {}).get("plugins", {})
    return [plugin_id for plugin_id in portal_plugins.IDS if chosen.get(plugin_id) is True]


def effective_capabilities(google: dict[str, Any]) -> dict[str, bool]:
    """What Portal may do now: the owner's choices, with automatic changes off while they are paused."""
    capabilities = dict(google["capabilities"])
    capabilities["calendar.direct"] = capabilities["calendar.direct"] and not google.get("directPaused", False)
    return capabilities


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
    native = native_apps.answers(native_app_choices(record))
    if native:
        overrides['nativeMcpApps'] = native
    zone = record["ownerTimeZone"]
    if zone:
        overrides.update(timeZone=zone, userTimezone=zone)
    # Plugins need no Google sign-in; ODS's runtime overlay loads them from this answer.
    chosen = portal_plugins.answers(enabled_plugins(record))
    if chosen:
        overrides["portalPlugins"] = chosen
    google = record["connectors"].get("google")
    if google and google_custody:
        capabilities = effective_capabilities(google)
        writes = capabilities["calendar.read"] and capabilities["calendar.write"]
        overrides.update(
            emailLimbEnabled=capabilities["gmail.read"],
            emailDraftsEnabled=capabilities["gmail.read"] and capabilities["gmail.drafts"],
            calendarLimbEnabled=capabilities["calendar.read"],
            calendarWriteEnabled=writes,
            calendarDirectEnabled=writes and capabilities["calendar.direct"],
            googleAccount=google["account"],
            calendarId=google["calendarId"],
            gmailWindowDays=google["gmailWindowDays"],
        )
    return overrides
