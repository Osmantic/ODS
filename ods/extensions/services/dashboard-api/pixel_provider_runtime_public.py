"""Strict nonsecret provider-runtime envelopes; standalone host/dashboard parity."""
import re
import uuid
from datetime import datetime

STATES = {"not-applied", "applied", "saved-changes", "inactive", "pending", "unavailable"}
KEYS = {"schemaVersion", "status", "revision", "providerRevision", "binding", "pending",
        "registrationVerified", "transportVerified", "lastVerifiedAt", "reason"}
REASONS = {
    "provider-controller-unavailable", "provider-transition-unavailable", "provider-transition-uncertain",
    "provider-inspection-changed", "provider-policy-disabled", "provider-not-managed",
    "provider-recovery-unavailable", "provider-recovery-conflict", "provider-recovery-journal-missing",
    "provider-owner-state-changed", "provider-runtime-custody-unqualified",
    "provider-runtime-descriptor-unqualified", "provider-runtime-custody-changed",
    "provider-source-changed", "provider-service-baseline-conflict",
    "provider-worker-runtime-not-ready",
    "settings-store-not-initialized", "settings-store-busy", "settings-data-directory-unqualified",
    "transition-recovery-required", "runtime-busy", "runtime-busy-or-unqualified",
    "model-lifecycle-busy", "macos-launchd-adapter-missing", "native-windows-adapter-missing",
}


def _revision(value):
    return type(value) is int and 0 <= value < 2**53


def _hex(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def safe_reason(value, fallback="provider-controller-unavailable"):
    return value if type(value) is str and value in REASONS else fallback


def normalize_binding(value):
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {"schemaVersion", "activationId", "revision", "allowCloud"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or not _revision(value["revision"]) or type(value["allowCloud"]) is not bool
            or type(value["activationId"]) is not str):
        raise ValueError("invalid-provider-runtime-response")
    try:
        if str(uuid.UUID(value["activationId"])) != value["activationId"]:
            raise ValueError()
    except ValueError:
        raise ValueError("invalid-provider-runtime-response") from None
    return dict(value)


def normalize_change(value):
    if (type(value) is not dict or set(value) != {"operation", "revision", "providerRevision"}
            or type(value["operation"]) is not str or value["operation"] not in ("apply", "deactivate", "recover")
            or not _hex(value["revision"]) or not _revision(value["providerRevision"])):
        raise ValueError("invalid-provider-runtime-request")
    return dict(value)


def unavailable(reason):
    if type(reason) is not str or reason not in REASONS:
        raise ValueError("invalid-provider-runtime-response")
    return {**dict.fromkeys(KEYS), "schemaVersion": 1, "status": "unavailable", "reason": reason}


def _timestamp(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise ValueError("invalid-provider-runtime-response")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid-provider-runtime-response") from None


def normalize_runtime(value):
    if (type(value) is not dict or set(value) != KEYS or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1 or type(value["status"]) is not str or value["status"] not in STATES):
        raise ValueError("invalid-provider-runtime-response")
    state = value["status"]
    if state == "unavailable":
        if (type(value["reason"]) is not str or value["reason"] not in REASONS
                or any(value[key] is not None for key in KEYS - {"schemaVersion", "status", "reason"})):
            raise ValueError("invalid-provider-runtime-response")
        return dict(value)
    if (not _hex(value["revision"]) or not _revision(value["providerRevision"])
            or type(value["pending"]) is not bool or type(value["registrationVerified"]) is not bool
            or value["transportVerified"] is not False or value["reason"] is not None):
        raise ValueError("invalid-provider-runtime-response")
    binding = normalize_binding(value["binding"])
    if state in ("pending", "not-applied"):
        if (value["pending"] != (state == "pending") or binding is not None
                or value["registrationVerified"] is not False or value["lastVerifiedAt"] is not None):
            raise ValueError("invalid-provider-runtime-response")
    else:
        if value["pending"] or value["registrationVerified"] is not True:
            raise ValueError("invalid-provider-runtime-response")
        _timestamp(value["lastVerifiedAt"])
        if state == "inactive":
            if binding is not None:
                raise ValueError("invalid-provider-runtime-response")
        elif (binding is None or (binding["revision"] == value["providerRevision"]) != (state == "applied")):
            raise ValueError("invalid-provider-runtime-response")
    return dict(value, binding=binding)


def from_controller(value):
    if type(value) is not dict or set(value) != KEYS - {"schemaVersion", "reason"}:
        raise ValueError("invalid-provider-runtime-response")
    return normalize_runtime(dict(value, schemaVersion=1, reason=None))


def normalize_outcome(value, request=None):
    if (type(value) is not dict or set(value) != {"outcome", "binding", "registrationVerified", "transportVerified"}
            or type(value["outcome"]) is not str or value["outcome"] not in ("applied", "rolled-back")
            or value["registrationVerified"] is not True or value["transportVerified"] is not False):
        raise ValueError("invalid-provider-runtime-response")
    binding = normalize_binding(value["binding"])
    if request is not None:
        checked = normalize_change(request)
        if checked["operation"] != "recover" and value["outcome"] != "applied":
            raise ValueError("provider-runtime-outcome-mismatch")
        if checked["operation"] == "apply" and (binding is None or binding["revision"] != checked["providerRevision"]):
            raise ValueError("provider-runtime-outcome-mismatch")
        if checked["operation"] == "deactivate" and binding is not None:
            raise ValueError("provider-runtime-outcome-mismatch")
    return dict(value, binding=binding)
