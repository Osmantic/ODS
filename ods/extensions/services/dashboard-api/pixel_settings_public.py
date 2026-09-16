"""Strict public validators for pixel settings responses. Standalone — no ods/bin imports."""
from __future__ import annotations

import math
import re
from datetime import datetime

CONTROLS = {
    "contextTokens": ("integer", 4096, 10_000_000),
    "maxOutputTokens": ("integer", 1, 10_000_000),
    "compactionMode": ("choice", "default", "safeguard"),
    "compactionReserveTokens": ("integer", 0, 10_000_000),
    "compactionReserveFloorTokens": ("integer", 0, 10_000_000),
    "compactionKeepRecentTokens": ("integer", 1, 10_000_000),
    "compactionHistoryShare": ("number", 0.1, 0.9),
    "compactionRecentTurns": ("integer", 0, 12),
    "compactionTimeoutSeconds": ("integer", 1, 3600),
    "compactionNotify": ("boolean",),
    "compactionMemoryFlush": ("boolean",),
    "thinking": ("choice", "off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"),
    "verbosity": ("choice", "off", "on", "full"),
    "reasoningVisibility": ("choice", "off", "on", "stream"),
    "toolProgress": ("choice", "explain", "raw"),
    "temperature": ("number", 0, 2),
    "topP": ("number", 0.000001, 1),
    "toolResultMaxChars": ("integer", 1, 2_000_000),
    "bootstrapMaxChars": ("integer", 1, 2_000_000),
    "bootstrapTotalMaxChars": ("integer", 1, 2_000_000),
}


def normalize_preferences(value):
    if type(value) is not dict:
        raise ValueError("invalid-settings-fields")
    for key in value:
        if type(key) is not str or key not in CONTROLS:
            raise ValueError("invalid-settings-fields")
    result = {}
    for name, item in value.items():
        if item is None:
            result[name] = None
            continue
        spec = CONTROLS[name]
        if spec[0] == "boolean":
            if type(item) is not bool:
                raise ValueError("invalid-setting-" + name)
        elif spec[0] == "choice":
            if type(item) is not str or item not in spec[1:]:
                raise ValueError("invalid-setting-" + name)
        else:
            if spec[0] == "integer":
                ok = type(item) is int and spec[1] <= item <= spec[2]
            else:
                ok = type(item) in (int, float) and spec[1] <= item <= spec[2] and math.isfinite(item)
            if not ok:
                raise ValueError("invalid-setting-" + name)
        result[name] = item
    return result


def normalize_edit(value):
    if type(value) is not dict:
        raise ValueError("invalid-edit")
    allowed = {"expectedRevision", "changes"}
    if set(value) != allowed:
        raise ValueError("invalid-edit")
    rev = value["expectedRevision"]
    if type(rev) is not int or not (0 <= rev < 2**53 - 1):
        raise ValueError("invalid-edit")
    changes = normalize_preferences(value["changes"])
    return {"expectedRevision": rev, "changes": changes}


def normalize_response(value):
    if type(value) is not dict:
        raise ValueError("invalid-settings-response")
    if set(value) != {"configuration", "runtime"}:
        raise ValueError("invalid-settings-response")
    cfg = value["configuration"]
    if type(cfg) is not dict:
        raise ValueError("invalid-settings-response")
    if set(cfg) != {"schemaVersion", "revision", "preferences"}:
        raise ValueError("invalid-settings-response")
    if type(cfg["schemaVersion"]) is not int or cfg["schemaVersion"] != 1:
        raise ValueError("invalid-settings-response")
    rev = cfg["revision"]
    if type(rev) is not int or not (0 <= rev <= 2**53 - 1):
        raise ValueError("invalid-settings-response")
    preferences = normalize_preferences(cfg["preferences"])
    rt = value["runtime"]
    if type(rt) is not dict:
        raise ValueError("invalid-settings-response")
    if set(rt) != {"status", "reason"}:
        raise ValueError("invalid-settings-response")
    if (rt["status"], rt["reason"]) not in (
            ("not-applied", "settings-runtime-not-integrated"),  # Older host compatibility.
            ("not-inspected", "runtime-status-separate")):
        raise ValueError("invalid-settings-response")
    return {"configuration": {"schemaVersion": 1, "revision": rev, "preferences": preferences},
            "runtime": dict(rt)}


def _runtime_revision(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def normalize_runtime_change(value):
    if (type(value) is not dict or set(value) != {"operation", "revision", "settingsRevision"}
            or value["operation"] not in ("apply", "recover") or not _runtime_revision(value["settingsRevision"])
            or type(value["revision"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["revision"])):
        raise ValueError("invalid-settings-request")
    return dict(value)


def normalize_runtime_outcome(value):
    if (type(value) is not dict or set(value) != {"outcome", "appliedRevision"}
            or value["outcome"] not in ("applied", "rolled-back")
            or value["appliedRevision"] is not None and not _runtime_revision(value["appliedRevision"])
            or value["outcome"] == "applied" and value["appliedRevision"] is None):
        raise ValueError("invalid-settings-response")
    return dict(value)


def _runtime_capabilities(value):
    keys = {"providerContextTokens", "providerMaxOutputTokens", "activeContextTokens", "activeMaxOutputTokens",
            "backendContextTokens", "capacitySource", "supportedThinkingLevels", "samplingSupported", "pixelOnlyRuntime"}
    if type(value) is not dict or set(value) != keys: raise ValueError("invalid-runtime-capabilities")
    for key in ("providerContextTokens", "providerMaxOutputTokens", "activeContextTokens", "activeMaxOutputTokens", "backendContextTokens"):
        item = value[key]
        if key == "backendContextTokens" and item is None: continue
        if type(item) is not int or not 1 <= item <= 10_000_000: raise ValueError("invalid-runtime-capabilities")
    if (value["providerMaxOutputTokens"] > value["providerContextTokens"]
            or value["activeMaxOutputTokens"] > value["activeContextTokens"]
            or value["capacitySource"] not in ("provider-declared", "owner-declared", "backend-observed")
            or value["capacitySource"] == "backend-observed" and value["backendContextTokens"] is None
            or type(value["samplingSupported"]) is not bool or type(value["pixelOnlyRuntime"]) is not bool):
        raise ValueError("invalid-runtime-capabilities")
    levels = value["supportedThinkingLevels"]
    if (type(levels) is not list or any(type(item) is not str or item not in CONTROLS["thinking"][1:] for item in levels)
            or len(set(levels)) != len(levels)):
        raise ValueError("invalid-runtime-capabilities")
    return dict(value, supportedThinkingLevels=list(levels))


def normalize_runtime(value):
    keys = {"schemaVersion", "status", "revision", "settingsRevision", "appliedRevision",
            "capabilities", "pending", "lastVerifiedAt", "reason"}
    if (type(value) is not dict or set(value) != keys or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1 or type(value["status"]) is not str
            or value["status"] not in ("not-applied", "applied", "saved-changes", "restored", "pending", "unavailable")):
        raise ValueError("invalid-settings-response")
    if value["status"] == "unavailable":
        reason = value["reason"]
        if (type(reason) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{0,95}", reason)
                or any(value[key] is not None for key in keys - {"schemaVersion", "status", "reason"})):
            raise ValueError("invalid-settings-response")
        return dict(value)
    if (type(value["revision"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["revision"])
            or not _runtime_revision(value["settingsRevision"]) or type(value["pending"]) is not bool or value["reason"] is not None):
        raise ValueError("invalid-settings-response")
    if value["status"] == "pending":
        if not value["pending"] or any(value[key] is not None for key in ("appliedRevision", "capabilities", "lastVerifiedAt")):
            raise ValueError("invalid-settings-response")
    else:
        if value["pending"]: raise ValueError("invalid-settings-response")
        capabilities = _runtime_capabilities(value["capabilities"])
        if value["status"] == "not-applied":
            if value["appliedRevision"] is not None or value["lastVerifiedAt"] is not None:
                raise ValueError("invalid-settings-response")
        else:
            if (value["status"] == "restored" and value["appliedRevision"] is not None
                    or value["status"] != "restored" and (not _runtime_revision(value["appliedRevision"])
                    or (value["appliedRevision"] == value["settingsRevision"]) != (value["status"] == "applied"))):
                raise ValueError("invalid-settings-response")
            timestamp = value["lastVerifiedAt"]
            if type(timestamp) is not str or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", timestamp):
                raise ValueError("invalid-settings-response")
            try: datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError: raise ValueError("invalid-settings-response") from None
        return dict(value, capabilities=capabilities)
    return dict(value)
