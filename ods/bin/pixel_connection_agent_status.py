"""Fixed read-only agent command and credential-free connection reply contract."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import stat
import sys

AGENT_MAX_BYTES = 8192
AGENT_READS = frozenset({"email", "calendar"})
AGENT_PERMISSIONS = frozenset({"gmailRead", "calendarRead"})
AGENT_ERRORS = frozenset({"invalid-grant", "not-connected", "blocked-by-admin", "api-disabled",
                          "client-invalid", "missing-scope", "unknown", "rate-limited", "network"})
AGENT_REASONS = AGENT_ERRORS | frozenset({
    "not-available-on-this-system", "connection-status-unavailable", "record-unusable",
    "journal-unusable", "connection-admission-held", "sign-in-missing", "status-unreadable", "sync-stopped",
})
AGENT_STATES = frozenset({"not-connected", "first-sync", "connected", "needs-reconnect", "blocked-by-admin",
    "setup-broken", "missing-permission", "unknown-error", "behind", "unknown", "transitioning"})
AGENT_KEYS = frozenset({"schemaVersion", "kind", "scope", "observedAt", "available", "reason",
    "recordConfigured", "localSavedSignIn", "connectionState", "ownerEnabled", "providerPermissionObserved",
    "sourceCheckedAt", "reads", "providerCheckedNow"})
AGENT_READ_KEYS = frozenset({"lastObservedState", "recent", "lastSuccessAt", "complete", "error"})
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$")
_PROGRAM_ROOT = Path("/usr/local/libexec/ods-pixel-access")


class AgentStatusError(ValueError):
    pass


def valid_agent_timestamp(value):
    if type(value) is not str or not _TIMESTAMP.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() == timedelta(0)
    except ValueError:
        return False


def _optional(value, check):
    return value is None or check(value)


def _tri(value):
    return value is None or type(value) is bool


def _member(value, choices):
    return type(value) is str and value in choices


def validate_agent_public(value):
    """Validate the whole closed reply before the CLI emits even one byte."""
    if (type(value) is not dict or set(value) != AGENT_KEYS
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or value["kind"] != "ods-connection-agent-status" or value["scope"] != "reviewed-google"
            or not valid_agent_timestamp(value["observedAt"]) or type(value["available"]) is not bool
            or not _optional(value["reason"], lambda item: _member(item, AGENT_REASONS))
            or not _tri(value["recordConfigured"]) or not _tri(value["localSavedSignIn"])
            or not _member(value["connectionState"], AGENT_STATES) or value["providerCheckedNow"] is not False
            or not _optional(value["sourceCheckedAt"], valid_agent_timestamp)):
        raise AgentStatusError("invalid agent connection status")
    for key in ("ownerEnabled", "providerPermissionObserved"):
        flags = value[key]
        if type(flags) is not dict or set(flags) != AGENT_PERMISSIONS or not all(_tri(flag) for flag in flags.values()):
            raise AgentStatusError("invalid agent connection permissions")
    if type(value["reads"]) is not dict or set(value["reads"]) != AGENT_READS:
        raise AgentStatusError("invalid agent source health")
    for item in value["reads"].values():
        if (type(item) is not dict or set(item) != AGENT_READ_KEYS
                or not _member(item["lastObservedState"], ("ok", "error", "disabled", "unknown"))
                or not _tri(item["recent"]) or not _tri(item["complete"])
                or not _optional(item["lastSuccessAt"], valid_agent_timestamp)
                or not _optional(item["error"], lambda error: _member(error, AGENT_ERRORS))):
            raise AgentStatusError("invalid agent source health")
    if (value["localSavedSignIn"] is True and value["recordConfigured"] is not True
            or value["connectionState"] == "not-connected" and (
                value["recordConfigured"] is not False or value["localSavedSignIn"] is not False)
            or not value["available"] and (value["connectionState"] != "unknown"
                or value["recordConfigured"] is not None or value["localSavedSignIn"] is not None
                or any(flag is not None for key in ("ownerEnabled", "providerPermissionObserved")
                       for flag in value[key].values()))):
        raise AgentStatusError("inconsistent agent connection status")
    try:
        size = len(json.dumps(value, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError):
        raise AgentStatusError("invalid agent connection status") from None
    if not 32 <= size <= AGENT_MAX_BYTES:
        raise AgentStatusError("invalid agent connection status size")
    return value


def _protected_program():
    program = Path(__file__).absolute().parent
    if program != _PROGRAM_ROOT:
        raise AgentStatusError("unavailable program custody")
    for path in (program, *program.parents):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise AgentStatusError("unavailable program custody")
    for name in ("pixel_connection_agent_status.py", "pixel_access_client.py"):
        info = (program / name).lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1):
            raise AgentStatusError("unavailable program custody")
    sys.path.insert(0, str(program))


def main():
    sys.dont_write_bytecode = True
    try:
        if len(sys.argv) != 1 or sys.platform != "linux":
            raise AgentStatusError("unavailable connection status")
        _protected_program()
        from pixel_access_client import request_access
        code, body = request_access("connection-agent-status")
        if code != 200:
            raise AgentStatusError("unavailable connection status")
        validate_agent_public(body)
        output = json.dumps(body, allow_nan=False, separators=(",", ":"))
        if not 32 <= len(output.encode("utf-8")) <= AGENT_MAX_BYTES:
            raise AgentStatusError("invalid connection status size")
    except Exception:
        print('{"error":"connection-status-unavailable"}')
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
