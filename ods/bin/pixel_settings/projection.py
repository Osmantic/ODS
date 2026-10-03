"""Presence-aware Pixel-only config projection and rollback, without I/O.

Plans/state contain private owner config. Keep them in qualified owner storage,
not in public API responses. A generated document is not authority to activate:
the lifecycle adapter must CAS the full revision, validate, drain and read back.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

from .contract import SettingsError, preview_preferences, validate_preferences


LEAVES = {
    "contextTokens": ("pixel", "contextTokens"),
    "maxOutputTokens": ("pixel", "params", "maxTokens"),
    "thinking": ("pixel", "thinkingDefault"),
    "verbosity": ("pixel", "verboseDefault"),
    "reasoningVisibility": ("pixel", "reasoningDefault"),
    "toolProgress": ("pixel", "toolProgressDetail"),
    "temperature": ("pixel", "params", "temperature"),
    "topP": ("pixel", "params", "topP"),
    "toolResultMaxChars": ("pixel", "contextLimits", "toolResultMaxChars"),
    "bootstrapMaxChars": ("pixel", "bootstrapMaxChars"),
    "bootstrapTotalMaxChars": ("pixel", "bootstrapTotalMaxChars"),
    "compactionMode": ("defaults", "compaction", "mode"),
    "compactionReserveTokens": ("defaults", "compaction", "reserveTokens"),
    "compactionReserveFloorTokens": ("defaults", "compaction", "reserveTokensFloor"),
    "compactionKeepRecentTokens": ("defaults", "compaction", "keepRecentTokens"),
    "compactionHistoryShare": ("defaults", "compaction", "maxHistoryShare"),
    "compactionRecentTurns": ("defaults", "compaction", "recentTurnsPreserve"),
    "compactionTimeoutSeconds": ("defaults", "compaction", "timeoutSeconds"),
    "compactionNotify": ("defaults", "compaction", "notifyUser"),
    "compactionMemoryFlush": ("defaults", "compaction", "memoryFlush", "enabled"),
    "_pluginContext": ("plugin", "modelContextWindow"),
}
PARENTS = {path[:index] for path in LEAVES.values() for index in range(1, len(path))}


def canonical(value):
    def walk(item, depth=0):
        if depth > 32:
            raise SettingsError("invalid-settings-json")
        kind = type(item)
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise SettingsError("invalid-settings-json")
                walk(child, depth + 1)
        elif kind is list:
            for child in item:
                walk(child, depth + 1)
        elif kind is float:
            if not math.isfinite(item):
                raise SettingsError("invalid-settings-json")
        elif kind not in (str, int, bool, type(None)):
            raise SettingsError("invalid-settings-json")
    try:
        walk(value)
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > 1024 * 1024:
            raise SettingsError("invalid-settings-json")
        return encoded
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise SettingsError("invalid-settings-json") from None


def revision(config):
    return hashlib.sha256(canonical(config).encode("ascii")).hexdigest()


def _object(value):
    if type(value) is not dict:
        raise SettingsError("invalid-settings-config")
    return value


def _agents(config):
    agents = _object(_object(config).get("agents"))
    items = agents.get("list")
    if type(items) is not list or any(type(item) is not dict for item in items):
        raise SettingsError("invalid-settings-config")
    pixels = [item for item in items if item.get("id") == "pixel"]
    if len(pixels) != 1:
        raise SettingsError("ambiguous-pixel-agent")
    return agents, items, pixels[0]


def _root(config, scope, create=False):
    agents, _, pixel = _agents(config)
    if scope == "pixel":
        return pixel
    if scope == "defaults":
        if create:
            agents.setdefault("defaults", {})
        return _object(agents["defaults"]) if "defaults" in agents else None
    entries = _object(_object(config.get("plugins")).get("entries"))
    plugin = _object(entries.get("pixel-ods"))
    if plugin.get("enabled") is not True:
        raise SettingsError("pixel-plugin-not-enabled")
    if create:
        plugin.setdefault("config", {})
    return _object(plugin["config"]) if "config" in plugin else None


def _lookup(config, path):
    node = _root(config, path[0])
    if node is None:
        return False, None
    for key in path[1:]:
        node = _object(node)
        if key not in node:
            return False, None
        node = node[key]
    return True, node


def _write(config, path, value, present=True):
    node = _root(config, path[0], create=present)
    if node is None:
        return
    for key in path[1:-1]:
        if present:
            node.setdefault(key, {})
        elif key not in node:
            return
        node = _object(node[key])
    if present:
        node[path[-1]] = copy.deepcopy(value)
    else:
        node.pop(path[-1], None)


def _identity(config):
    _, _, pixel = _agents(config)
    entries = _object(_object(config.get("plugins")).get("entries"))
    plugin = _object(entries.get("pixel-ods"))
    return revision({"pixelModel": pixel.get("model"), "providers": config.get("models"),
                     "pluginEnabled": plugin.get("enabled"),
                     "providerBinding": _object(plugin.get("config", {})).get("managedProvider")})


def _validate_state(state):
    canonical(state)
    if (type(state) is not dict or set(state) != {"schemaVersion", "identity", "fields", "absentParents", "baseBudgets"}
            or type(state["schemaVersion"]) is not int or state["schemaVersion"] != 1
            or type(state["identity"]) is not str or len(state["identity"]) != 64
            or any(char not in "0123456789abcdef" for char in state["identity"])
            or type(state["fields"]) is not dict or any(name not in LEAVES for name in state["fields"])
            or type(state["absentParents"]) is not list):
        raise SettingsError("invalid-settings-state")
    parents = []
    for path in state["absentParents"]:
        if type(path) is not list or any(type(part) is not str for part in path) or tuple(path) not in PARENTS:
            raise SettingsError("invalid-settings-state")
        parents.append(tuple(path))
    if len(set(parents)) != len(parents):
        raise SettingsError("invalid-settings-state")
    owned_parents = {LEAVES[name][:index] for name in state["fields"] for index in range(1, len(LEAVES[name]))}
    if not set(parents) <= owned_parents:
        raise SettingsError("invalid-settings-state")
    for name, record in state["fields"].items():
        if (type(record) is not dict or set(record) != {"present", "before", "after"}
                or type(record["present"]) is not bool or not record["present"] and record["before"] is not None):
            raise SettingsError("invalid-settings-state")
        validate_preferences({"contextTokens" if name == "_pluginContext" else name: record["after"]})
        if record["after"] is None:
            raise SettingsError("invalid-settings-state")
    budgets = state["baseBudgets"]
    if (type(budgets) is not dict or set(budgets) != {"contextTokens", "maxOutputTokens"}
            or any(type(value) is not int for value in budgets.values())):
        raise SettingsError("invalid-settings-state")
    validate_preferences(budgets)


def restore_preferences(current, state):
    """Refuse changed owned leaves/identity; preserve unrelated concurrent edits."""
    canonical(current)
    _validate_state(state)
    if _identity(current) != state["identity"]:
        raise SettingsError("settings-runtime-identity-drift")
    if any(LEAVES[name][0] == "defaults" for name in state["fields"]) and len(_agents(current)[1]) != 1:
        raise SettingsError("shared-compaction-requires-pixel-isolation")
    for name, record in state["fields"].items():
        present, value = _lookup(current, LEAVES[name])
        if not present or canonical(value) != canonical(record["after"]):
            raise SettingsError("settings-owned-leaf-drift")
    restored = copy.deepcopy(current)
    for name, record in state["fields"].items():
        _write(restored, LEAVES[name], record["before"], record["present"])
    # Remove only empty containers this projection created. Preserve new siblings.
    for path in sorted(state["absentParents"], key=len, reverse=True):
        present, value = _lookup(restored, tuple(path))
        if not present or value != {}:
            continue
        if len(path) > 1:
            _write(restored, tuple(path), None, False)
        elif path[0] == "defaults":
            restored["agents"].pop("defaults", None)
        elif path[0] == "plugin":
            restored["plugins"]["entries"]["pixel-ods"].pop("config", None)
    return restored


def plan_preferences(current, preferences, capabilities, *, previous=None):
    """Project a complete managed-preference document; omitted leaves are unowned.

    Merge sparse UI changes with the saved preferences before calling. Null resets
    to the original owner value, not the already-overridden active runtime value.
    The original model/routing identity and every unrelated leaf remain unchanged.
    """
    expected_revision = revision(current)
    desired = validate_preferences(preferences)
    baseline = restore_preferences(current, previous) if previous is not None else copy.deepcopy(current)
    caps = copy.deepcopy(capabilities)
    if type(caps) is not dict:
        raise SettingsError("invalid-runtime-capabilities")
    if previous is not None:
        if "contextTokens" in previous["fields"]:
            caps["activeContextTokens"] = previous["baseBudgets"]["contextTokens"]
        if "maxOutputTokens" in previous["fields"]:
            caps["activeMaxOutputTokens"] = previous["baseBudgets"]["maxOutputTokens"]
    # Actual config topology constrains the adapter's claimed isolation.
    if len(_agents(baseline)[1]) != 1:
        caps["pixelOnlyRuntime"] = False
    preview = preview_preferences(desired, caps)
    values = {name: value for name, value in desired.items() if value is not None}
    budget_changed = (preview["proposed"]["contextTokens"] != caps["activeContextTokens"]
                      or preview["proposed"]["maxOutputTokens"] != caps["activeMaxOutputTokens"])
    if budget_changed:
        for name in ("compactionReserveTokens", "compactionReserveFloorTokens", "compactionKeepRecentTokens"):
            values[name] = preview["proposed"][name]
    # Check the actual write set as well as the preview. Naming an unchanged
    # Pixel budget must not silently claim global settings used by other agents.
    if not caps["pixelOnlyRuntime"] and any(LEAVES[name][0] == "defaults" for name in values):
        raise SettingsError("shared-compaction-requires-pixel-isolation")
    if "contextTokens" in values:
        values["_pluginContext"] = values["contextTokens"]
    parents = {LEAVES[name][:index] for name in values for index in range(1, len(LEAVES[name]))}
    absent = [list(path) for path in sorted(parents) if not _lookup(baseline, path)[0]]
    fields = {}
    candidate = copy.deepcopy(baseline)
    for name, value in values.items():
        present, before = _lookup(baseline, LEAVES[name])
        fields[name] = {"present": present, "before": copy.deepcopy(before), "after": copy.deepcopy(value)}
        _write(candidate, LEAVES[name], value)
    state = {"schemaVersion": 1, "identity": _identity(baseline), "fields": fields,
             "absentParents": absent, "baseBudgets": {"contextTokens": caps["activeContextTokens"],
                                                       "maxOutputTokens": caps["activeMaxOutputTokens"]}}
    _validate_state(state)
    return {"expectedConfigRevision": expected_revision, "document": candidate, "state": state, "preview": preview}
