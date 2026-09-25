"""Nonsecret runtime control envelopes for the authenticated host boundary."""
from datetime import datetime
import re

from .contract import SettingsError, _capabilities

STATES = {"not-applied", "applied", "saved-changes", "restored", "pending", "unavailable"}
KEYS = {"schemaVersion", "status", "revision", "settingsRevision", "appliedRevision",
        "capabilities", "pending", "lastVerifiedAt", "reason"}


def _revision(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def normalize_change(value):
    if (type(value) is not dict or set(value) != {"operation", "revision", "settingsRevision"}
            or value["operation"] not in ("apply", "recover") or not _revision(value["settingsRevision"])
            or type(value["revision"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["revision"])):
        raise SettingsError("invalid-settings-request")
    return dict(value)


def normalize_outcome(value):
    if (type(value) is not dict or set(value) != {"outcome", "appliedRevision"}
            or value["outcome"] not in ("applied", "rolled-back")
            or value["appliedRevision"] is not None and not _revision(value["appliedRevision"])
            or value["outcome"] == "applied" and value["appliedRevision"] is None):
        raise SettingsError("invalid-settings-response")
    return dict(value)


def unavailable(reason):
    return {"schemaVersion": 1, "status": "unavailable", "revision": None, "settingsRevision": None,
            "appliedRevision": None, "capabilities": None, "pending": None, "lastVerifiedAt": None,
            "reason": reason}


def normalize_runtime(value):
    if (type(value) is not dict or set(value) != KEYS or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1 or type(value["status"]) is not str or value["status"] not in STATES):
        raise SettingsError("invalid-settings-response")
    if value["status"] == "unavailable":
        reason = value["reason"]
        if (type(reason) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{0,95}", reason)
                or any(value[key] is not None for key in KEYS - {"schemaVersion", "status", "reason"})):
            raise SettingsError("invalid-settings-response")
        return dict(value)
    if (type(value["revision"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["revision"])
            or not _revision(value["settingsRevision"]) or type(value["pending"]) is not bool or value["reason"] is not None):
        raise SettingsError("invalid-settings-response")
    if value["status"] == "pending":
        if not value["pending"] or any(value[key] is not None for key in ("appliedRevision", "capabilities", "lastVerifiedAt")):
            raise SettingsError("invalid-settings-response")
    else:
        if value["pending"]:
            raise SettingsError("invalid-settings-response")
        capabilities = _capabilities(value["capabilities"])
        if value["status"] == "not-applied":
            if value["appliedRevision"] is not None or value["lastVerifiedAt"] is not None:
                raise SettingsError("invalid-settings-response")
        else:
            if (value["status"] == "restored" and value["appliedRevision"] is not None
                    or value["status"] != "restored" and (not _revision(value["appliedRevision"])
                    or (value["appliedRevision"] == value["settingsRevision"]) != (value["status"] == "applied"))):
                raise SettingsError("invalid-settings-response")
            timestamp = value["lastVerifiedAt"]
            if type(timestamp) is not str or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", timestamp):
                raise SettingsError("invalid-settings-response")
            try: datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError: raise SettingsError("invalid-settings-response") from None
        return dict(value, capabilities=capabilities)
    return dict(value)
