"""Content-free status of the owner's connected apps, for Settings and Portal.

It joins three things only root can see together: the owner's record
(state.py), whether the Source Broker still holds the Google sign-in, and the
broker's own status.json. Nothing here reads a token, a message, an event or any
other source content, so the result is safe to show in the dashboard.
"""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pixel_connection_agent_status import (
    AGENT_ERRORS, AGENT_PERMISSIONS, AGENT_READS, AGENT_REASONS,
    valid_agent_timestamp as _agent_timestamp, validate_agent_public,
)
from . import approvals, catalog, drafts, google_app, lifecycle, state
from . import plugins as portal_plugins

BROKER_STATE = Path("/var/lib/pixel-source-broker")
TOKEN = BROKER_STATE / "private/google-token.json"
BROKER_STATUS = BROKER_STATE / "projection/status.json"
SCHEMA_VERSION = 1
SUPPORTED_SURFACES = frozenset(catalog.GOOGLE["platforms"])
# The broker refreshes every minute; a status older than this means it stopped.
STALE_AFTER = timedelta(minutes=10)
MAX_STATUS_BYTES = 64 * 1024
TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$")
SCOPE = re.compile(r"^(?:https://www\.googleapis\.com/auth/[a-z0-9._-]{1,100}|https://mail\.google\.com/|openid|email|profile)$")
SOURCES = {"email": "gmail.read", "calendar": "calendar.read"}
# The owner-facing state an error class calls for, most actionable first.
FIXES = (
    ("needs-reconnect", {"invalid-grant", "not-connected"}),
    ("blocked-by-admin", {"blocked-by-admin"}),
    ("setup-broken", {"api-disabled", "client-invalid"}),
    ("missing-permission", {"missing-scope"}),
    ("unknown-error", {"unknown"}),
    ("behind", {"rate-limited", "network"}),
)
STATES = frozenset({"not-connected", "first-sync", "connected"} | {name for name, _classes in FIXES})


class StatusError(ValueError):
    """The broker's status file exists but cannot be used."""


def _timestamp(value: Any) -> str | None:
    return value if isinstance(value, str) and TIMESTAMP.fullmatch(value) else None


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def source_view(entry: Any) -> dict[str, Any]:
    """The fields of one broker source entry that Settings may show, type-checked."""
    if not isinstance(entry, dict) or entry.get("state") not in ("ok", "error", "disabled"):
        raise StatusError("broker source entry is invalid")
    error_class = entry.get("errorClass")
    http_status = entry.get("httpStatus")
    count = entry.get("recordCount")
    return {
        "state": entry["state"],
        "errorClass": error_class if isinstance(error_class, str) and TOKEN_PATTERN.fullmatch(error_class) else None,
        "httpStatus": http_status if type(http_status) is int and 100 <= http_status <= 599 else None,
        "lastSuccessAt": _timestamp(entry.get("lastSuccessAt")),
        "failingSince": _timestamp(entry.get("failingSince")),
        "recordCount": count if type(count) is int and count >= 0 else 0,
        "complete": entry.get("complete") if type(entry.get("complete")) is bool else None,
    }


def parse_broker_status(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_STATUS_BYTES:
        raise StatusError("broker status exceeds its size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise StatusError("broker status is not JSON") from error
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1
            or value.get("kind") != "pixel-source-broker-status" or not isinstance(value.get("sources"), dict)):
        raise StatusError("broker status has an unknown shape")
    generated = _timestamp(value.get("generatedAt"))
    if generated is None:
        raise StatusError("broker status has no time")
    granted = value.get("grantedScopes")
    if granted is not None and (not isinstance(granted, list) or any(not isinstance(item, str) or not SCOPE.fullmatch(item) for item in granted)):
        raise StatusError("broker status scopes are invalid")
    sources = {name: source_view(value["sources"][name]) for name in SOURCES if name in value["sources"]}
    return {"generatedAt": generated, "grantedScopes": granted, "sources": sources}


def read_broker_status(path: Path = BROKER_STATUS) -> dict[str, Any] | None:
    """The broker's latest status, None before its first refresh, StatusError if unusable."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StatusError("broker status cannot be opened safely") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATUS_BYTES:
            raise StatusError("broker status is not a bounded regular file")
        raw = handle.read(MAX_STATUS_BYTES + 1)
    return parse_broker_status(raw)


def google_state(google: dict[str, Any], *, custody: bool, broker: dict[str, Any] | None,
                 broker_error: bool, now: datetime) -> tuple[str, str | None]:
    """The single owner-facing state of a connected Google account, and why."""
    if not custody:
        return "needs-reconnect", "sign-in-missing"
    if broker_error:
        return "unknown-error", "status-unreadable"
    if broker is None:
        return "first-sync", None
    wanted = [name for name, capability in SOURCES.items() if google["capabilities"][capability]]
    views = [broker["sources"].get(name) for name in wanted]
    # A source the owner turned on that the broker has not started yet is still activating.
    if any(view is None or view["state"] == "disabled" for view in views):
        return "first-sync", None
    classes = {view["errorClass"] or "unknown" for view in views if view["state"] == "error"}
    for name, fix_classes in FIXES:
        if classes & fix_classes:
            return name, sorted(classes & fix_classes)[0]
    if now - _instant(broker["generatedAt"]) > STALE_AFTER:
        return "behind", "sync-stopped"
    if any(view['complete'] is False and view['recordCount'] == 0 for view in views):
        # An empty partial batch has no usable history yet. Nonempty partial
        # snapshots remain usable; complete=False still tells each source row
        # its coverage is limited, without asserting an active background fetch.
        return 'first-sync', None
    return "connected", None


def not_connected(pending: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": "not-connected", "reason": None, "account": None, "capabilities": None,
            "cloudModels": None, "connectedAt": None, "grantedScopes": None, "checkedAt": None,
            "sources": {"email": None, "calendar": None}, "pending": pending, "waitingChanges": None,
            "waitingDrafts": None}


def google_view(record: dict[str, Any] | None, *, custody: bool, broker: dict[str, Any] | None,
                broker_error: bool, now: datetime, pending: dict[str, Any] | None = None,
                waiting_changes: int = 0, waiting_drafts: int = 0) -> dict[str, Any]:
    google = (record or {}).get("connectors", {}).get("google")
    if google is None:
        return not_connected(pending)
    name, reason = google_state(google, custody=custody, broker=broker, broker_error=broker_error, now=now)
    sources = (broker or {}).get("sources", {})
    return {
        "state": name,
        'authMode': google.get('authMode', 'advanced'),
        "reason": reason,
        "account": google["account"],
        "capabilities": dict(google["capabilities"]),
        "cloudModels": google["cloudModels"],
        "connectedAt": google["connectedAt"],
        "grantedScopes": (broker or {}).get("grantedScopes"),
        "checkedAt": (broker or {}).get("generatedAt"),
        "sources": {name: sources.get(name) for name in SOURCES} if custody and not broker_error else
                   {"email": None, "calendar": None},
        "pending": pending,
        "waitingChanges": waiting_changes,
        "waitingDrafts": waiting_drafts,
    }


SURFACES = frozenset({"linux-systemd", "wsl-systemd", "linux", "darwin", "windows"})
CAPABILITY_IDS = frozenset(capability["id"] for capability in catalog.GOOGLE["capabilities"])
GOOGLE_KEYS = {"state", "reason", "account", "capabilities", "cloudModels", "connectedAt", "grantedScopes",
               "checkedAt", "sources", "pending", "waitingChanges", "waitingDrafts"}
SOURCE_KEYS = {"state", "errorClass", "httpStatus", "lastSuccessAt", "failingSince", "recordCount", "complete"}


def _optional(value: Any, check) -> bool:
    return value is None or check(value)


def _valid_source(value: Any) -> bool:
    return (value is None or isinstance(value, dict) and set(value) == SOURCE_KEYS
            and value["state"] in ("ok", "error", "disabled")
            and _optional(value["errorClass"], lambda item: isinstance(item, str) and bool(TOKEN_PATTERN.fullmatch(item)))
            and _optional(value["httpStatus"], lambda item: type(item) is int and 100 <= item <= 599)
            and _optional(value["lastSuccessAt"], _timestamp) and _optional(value["failingSince"], _timestamp)
            and type(value["recordCount"]) is int and value["recordCount"] >= 0
            and _optional(value["complete"], lambda item: type(item) is bool))


def _valid_google(value: Any) -> bool:
    if (not isinstance(value, dict) or not GOOGLE_KEYS <= set(value) <= GOOGLE_KEYS | {'authMode'}
            or value["state"] not in STATES):
        return False
    connected = value["state"] != "not-connected"
    if 'authMode' in value and (not connected or value['authMode'] not in ('managed', 'advanced')):
        return False
    capabilities = value["capabilities"]
    return (_optional(value["reason"], lambda item: isinstance(item, str) and bool(TOKEN_PATTERN.fullmatch(item)))
            and (value["account"] is None) != connected
            and _optional(value["account"], lambda item: isinstance(item, str) and bool(state.EMAIL.fullmatch(item)))
            and (capabilities is None) != connected
            and _optional(capabilities, lambda item: isinstance(item, dict) and set(item) == CAPABILITY_IDS
                          and all(type(flag) is bool for flag in item.values()))
            and _optional(value["cloudModels"], lambda item: type(item) is bool)
            and _optional(value["connectedAt"], _timestamp) and _optional(value["checkedAt"], _timestamp)
            and _optional(value["grantedScopes"], lambda item: isinstance(item, list)
                          and all(isinstance(scope, str) and SCOPE.fullmatch(scope) for scope in item))
            and isinstance(value["sources"], dict) and set(value["sources"]) == set(SOURCES)
            and all(_valid_source(item) for item in value["sources"].values())
            and lifecycle.valid_public(value["pending"])
            and all((value[key] is None) != connected
                    and _optional(value[key], lambda item: type(item) is int and 0 <= item <= approvals.CONSIDERED)
                    for key in ("waitingChanges", "waitingDrafts")))


def plugin_flags(record: dict[str, Any] | None) -> dict[str, bool]:
    """Which reviewed plugins Portal may use; every one of them is off without a usable record."""
    chosen = state.enabled_plugins(record)
    return {plugin_id: plugin_id in chosen for plugin_id in portal_plugins.IDS}


def validate_public(value: Any) -> dict[str, Any]:
    """Accept exactly the public status shape, so nothing else can reach the browser."""
    keys = {"schemaVersion", "available", "surface", "reason", "ownerTimeZone", "connectors", "plugins"}
    if (not isinstance(value, dict)
            or not keys <= set(value) <= keys | {'googleAuth'}
            or not isinstance(value["plugins"], dict) or set(value["plugins"]) != set(portal_plugins.IDS)
            or any(type(flag) is not bool for flag in value["plugins"].values())
            or value["schemaVersion"] != SCHEMA_VERSION or type(value["available"]) is not bool
            or value["surface"] not in SURFACES
            or not _optional(value["reason"], lambda item: isinstance(item, str) and bool(TOKEN_PATTERN.fullmatch(item)))
            # Shape only: the coordinator resolved the zone, and a Windows host may lack tz data.
            or not _optional(value["ownerTimeZone"], lambda item: isinstance(item, str) and bool(state.TIME_ZONE.fullmatch(item)))
            or not isinstance(value["connectors"], dict) or set(value["connectors"]) != {"google"}
            or not _valid_google(value["connectors"]["google"])):
        raise StatusError("connection status has an unexpected shape")
    if 'googleAuth' in value:
        try:
            google_app.validate_public(value['googleAuth'])
        except google_app.ManagedAppError:
            raise StatusError('connection status has an unexpected shape') from None
    return value


def unavailable(surface: str) -> dict[str, Any]:
    """Connected apps need the Linux or WSL Source Broker; other systems say so plainly."""
    return {"schemaVersion": SCHEMA_VERSION, "available": False, "surface": surface,
            "reason": "not-available-on-this-system", "ownerTimeZone": None,
            "connectors": {"google": not_connected()}, "plugins": plugin_flags(None)}


def _pending(path: Path, owners: tuple) -> tuple[dict[str, Any] | None, bool]:
    try:
        return lifecycle.public(lifecycle.read(path, owners=owners)), False
    except lifecycle.LifecycleError:
        return None, True


def _admission_pending(path: Path, owners: tuple, pending: dict[str, Any] | None, now: datetime):
    """Project durable custody, never whether a callback process is alive.

    The same valid hold can be observed during a running callback or after its
    death. Both expose Finish. Observation neither acquires a lease nor writes
    recovery metadata, and an old account during consent is not a new grant.
    """
    try:
        operation = lifecycle.read_operation(path.parent / "transition.json", owners=owners)
        if not operation or operation.get("kind") != "connection" or "google" not in operation:
            return pending, False, False
        from .coordinator import _journal
        from pixel_access_bridge import AccessError
        try:
            hold = _journal(operation)["google"]
        except (AccessError, ValueError, KeyError, TypeError):
            raise lifecycle.LifecycleError("invalid Google admission hold") from None
        if hold["failure"] is not None:
            pending = {"action": "disconnect" if pending and pending["phase"] == "deactivating" else "connect",
                       "phase": "deactivating" if pending and pending["phase"] == "deactivating" else "activating",
                       "expiresAt": None, "error": hold["failure"],
                       "since": (pending or {}).get("since") or hold["createdAt"]}
        elif pending is None or pending["phase"] == "failed" or (
                pending["phase"] == "consent" and hold["stage"] not in ("draining", "exchanging")):
            pending = {"action": "connect", "phase": "activating", "expiresAt": None,
                       "error": hold["failure"] or (pending or {}).get("error"), "since": hold["createdAt"]}
        return pending, True, False
    except (lifecycle.LifecycleError, OSError, ImportError):
        # Never interpret a malformed private hold as permission for a new
        # sign-in or expose any of its token/flow/config fields to the owner.
        pending = {"action": "connect", "phase": "activating", "expiresAt": None,
                   "error": "connection-transition-unavailable",
                   "since": (pending or {}).get("since") or now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        return pending, False, True


def report(*, surface: str, record_path: Path = state.PATH, token_path: Path = TOKEN,
           status_path: Path = BROKER_STATUS, lifecycle_path: Path = lifecycle.PATH, now: datetime | None = None,
           owners: tuple[int, ...] = (0,), broker_state: Path = BROKER_STATE) -> dict[str, Any]:
    """The complete public status. Runs as root, which alone can see the sign-in."""
    if surface not in SUPPORTED_SURFACES:
        return unavailable(surface)
    now = now or datetime.now(timezone.utc)
    pending, journal_unusable = _pending(lifecycle_path, owners)
    pending, admission_held, admission_unusable = _admission_pending(lifecycle_path, owners, pending, now)
    journal_unusable = journal_unusable or admission_unusable
    try:
        record = state.read(record_path, owners=owners)
    except state.StateError:
        # Settings offers to reconnect; the install already kept everything off.
        return {"schemaVersion": SCHEMA_VERSION, "available": True, "surface": surface,
                "reason": "journal-unusable" if journal_unusable else "connection-admission-held" if admission_held else "record-unusable", "ownerTimeZone": None,
                "connectors": {"google": not_connected(pending)}, "plugins": plugin_flags(None)}
    custody = token_path.is_file()
    broker, broker_error, waiting, waiting_drafts = None, False, 0, 0
    if record is not None and "google" in record["connectors"]:
        # Only how many calendar changes and drafts wait for the owner; what they say never reaches status.
        waiting = approvals.pending_count(approvals.Dirs.under(broker_state))
        waiting_drafts = drafts.pending_count(approvals.Dirs.under(broker_state))
    if record is not None and "google" in record["connectors"] and custody:
        try:
            broker = read_broker_status(status_path)
        except StatusError:
            broker_error = True
    return {
        "schemaVersion": SCHEMA_VERSION,
        "available": True,
        "surface": surface,
        "reason": "journal-unusable" if journal_unusable else "connection-admission-held" if admission_held else None,
        "ownerTimeZone": record["ownerTimeZone"] if record else None,
        "connectors": {"google": google_view(record, custody=custody, broker=broker, broker_error=broker_error,
                                             now=now, pending=pending, waiting_changes=waiting,
                                             waiting_drafts=waiting_drafts)},
        "plugins": plugin_flags(record),
    }


def _agent_now(now):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise StatusError("agent status needs an aware clock")
    return now.astimezone(timezone.utc)


def _agent_empty(now, *, available=False, reason="connection-status-unavailable"):
    return {"schemaVersion": 1, "kind": "ods-connection-agent-status", "scope": "reviewed-google",
            "observedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "available": available, "reason": reason,
            "recordConfigured": None, "localSavedSignIn": None, "connectionState": "unknown",
            "ownerEnabled": {name: None for name in AGENT_PERMISSIONS},
            "providerPermissionObserved": {name: None for name in AGENT_PERMISSIONS},
            "sourceCheckedAt": None,
            "reads": {name: {"lastObservedState": "unknown", "recent": None, "lastSuccessAt": None,
                             "complete": None, "error": None} for name in AGENT_READS},
            "providerCheckedNow": False}


def agent_projection(value, *, now=None):
    """Redact a validated owner view in Root, before crossing into the agent."""
    now = _agent_now(now)
    validate_public(value)
    if type(value["schemaVersion"]) is not int:
        raise StatusError("invalid owner status version")
    answer = _agent_empty(now)
    if not value["available"] or value["surface"] not in SUPPORTED_SURFACES:
        answer["reason"] = "not-available-on-this-system"
        return validate_agent_public(answer)
    answer["available"] = True
    if value["reason"] is not None:
        answer["reason"] = value["reason"] if value["reason"] in AGENT_REASONS else "unknown"
        if value["reason"] == "connection-admission-held":
            answer["connectionState"] = "transitioning"
        return validate_agent_public(answer)
    google = value["connectors"]["google"]
    answer["reason"] = google["reason"] if google["reason"] in AGENT_REASONS else "unknown" if google["reason"] else None
    answer["connectionState"] = "transitioning" if google["pending"] else google["state"]
    configured = google["capabilities"] is not None
    answer["recordConfigured"] = configured
    answer["localSavedSignIn"] = configured and google["reason"] != "sign-in-missing"
    if not configured:
        return validate_agent_public(answer)
    for name, capability, scope in (("gmailRead", "gmail.read", catalog.GMAIL_READ_SCOPE),
                                     ("calendarRead", "calendar.read", catalog.CALENDAR_READ_SCOPE)):
        answer["ownerEnabled"][name] = google["capabilities"][capability]
        scopes = google["grantedScopes"]
        answer["providerPermissionObserved"][name] = scope in scopes if scopes is not None else None
    checked = google["checkedAt"]
    if checked is not None and (not _agent_timestamp(checked) or _instant(checked) > now):
        raise StatusError("invalid source observation clock")
    connected_at = google["connectedAt"]
    if not _agent_timestamp(connected_at) or _instant(connected_at) > now:
        raise StatusError("invalid saved connection clock")
    if checked is not None and _instant(checked) < _instant(connected_at):
        # A previous account's status is not evidence for this saved sign-in.
        answer["providerPermissionObserved"] = {name: None for name in AGENT_PERMISSIONS}
        answer["connectionState"] = "transitioning" if google["pending"] else "first-sync"
        answer["reason"] = None
        return validate_agent_public(answer)
    answer["sourceCheckedAt"] = checked
    for name, source in google["sources"].items():
        if source is None:
            continue
        last = source["lastSuccessAt"]
        if last is not None and (not _agent_timestamp(last) or _instant(last) > now):
            raise StatusError("invalid source success clock")
        answer["reads"][name] = {
            "lastObservedState": source["state"],
            "recent": (now - _instant(checked) <= STALE_AFTER) if checked is not None else None,
            "lastSuccessAt": last, "complete": source["complete"],
            "error": source["errorClass"] if source["errorClass"] in AGENT_ERRORS else "unknown" if source["errorClass"] else None,
        }
    return validate_agent_public(answer)


def agent_report(*, surface, now=None, **paths):
    """No provider call, mutation, credential read or owner identifiers in output."""
    now = _agent_now(now)
    try:
        return agent_projection(report(surface=surface, now=now, **paths), now=now)
    except (StatusError, ValueError, OSError):
        return validate_agent_public(_agent_empty(now))
