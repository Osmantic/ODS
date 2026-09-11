"""Pure, versioned customization contract. This module neither saves nor applies.

Capabilities must come from the authenticated owner/runtime adapter, never the
preference request. Unknown allocation stays unknown; an owner-declared remote
capacity is not a measured hardware limit. Runtime readback is a separate gate.
"""
from __future__ import annotations

import math


class SettingsError(ValueError):
    """Stable, nonsecret rejection suitable for the owner-facing preview."""


# Fixed scalar controls only. None explicitly requests automatic/default behavior;
# an omitted key is not an instruction to reset the corresponding owner setting.
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
COMPACTION_CONTROLS = frozenset(name for name in CONTROLS if name.startswith("compaction"))


def validate_preferences(value):
    """Validate a sparse document, retaining omitted-vs-reset distinction."""
    if type(value) is not dict or any(type(key) is not str or key not in CONTROLS for key in value):
        raise SettingsError("invalid-settings-fields")
    result = {}
    for name, item in value.items():
        if item is None:
            result[name] = None
            continue
        spec = CONTROLS[name]
        if spec[0] == "boolean":
            valid = type(item) is bool
        elif spec[0] == "choice":
            valid = type(item) is str and item in spec[1:]
        else:
            valid = type(item) is int or spec[0] == "number" and type(item) is float
            valid = valid and spec[1] <= item <= spec[2] and math.isfinite(item)
        if not valid:
            raise SettingsError("invalid-setting-" + name)
        result[name] = item
    return result


def merge_preferences(current, changes):
    """No mutation of either document; null resets only the named preference."""
    return {**validate_preferences(current), **validate_preferences(changes)}


def _integer(value, minimum=1, maximum=10_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise SettingsError("invalid-runtime-capabilities")
    return value


def _capabilities(value):
    required = {"providerContextTokens", "providerMaxOutputTokens", "activeContextTokens",
                "activeMaxOutputTokens", "backendContextTokens", "capacitySource",
                "supportedThinkingLevels", "samplingSupported", "pixelOnlyRuntime"}
    if type(value) is not dict or set(value) != required:
        raise SettingsError("invalid-runtime-capabilities")
    for name in ("providerContextTokens", "providerMaxOutputTokens", "activeContextTokens", "activeMaxOutputTokens"):
        _integer(value[name])
    if value["backendContextTokens"] is not None:
        _integer(value["backendContextTokens"])
    if (value["providerMaxOutputTokens"] > value["providerContextTokens"]
            or value["activeMaxOutputTokens"] > value["activeContextTokens"]
            or value["capacitySource"] not in ("provider-declared", "owner-declared", "backend-observed")
            or value["capacitySource"] == "backend-observed" and value["backendContextTokens"] is None
            or type(value["samplingSupported"]) is not bool
            or type(value["pixelOnlyRuntime"]) is not bool):
        raise SettingsError("invalid-runtime-capabilities")
    levels = value["supportedThinkingLevels"]
    if (type(levels) is not list or any(type(level) is not str or level not in CONTROLS["thinking"][1:] for level in levels)
            or len(set(levels)) != len(levels)):
        raise SettingsError("invalid-runtime-capabilities")
    return dict(value, supportedThinkingLevels=list(levels))


def preview_preferences(preferences, capabilities):
    """Resolve proposed budgets, not active/effective acceptance.

    A caller can show these values in a preview. It must still preserve unowned
    config leaves, validate the staged runtime document, drain/CAS/apply/reload,
    and independently read the active gateway before calling anything applied.
    """
    desired = validate_preferences(preferences)
    caps = _capabilities(capabilities)
    context = desired.get("contextTokens") or caps["activeContextTokens"]
    output = desired.get("maxOutputTokens") or caps["activeMaxOutputTokens"]
    declared_limit = caps["providerContextTokens"]
    backend_limit = caps["backendContextTokens"]
    limit = min(declared_limit, backend_limit) if backend_limit is not None else declared_limit
    if not 4096 <= context <= limit:
        raise SettingsError("context-exceeds-declared-or-observed-capacity")
    if output > min(caps["providerMaxOutputTokens"], context):
        raise SettingsError("output-exceeds-capacity")

    # Preserve the existing ODS OpenAI-compatible transport headroom (1.25 input
    # safety factor). Earlier compaction may reserve more, never less headroom.
    minimum_reserve = (context + 4 * output + 4) // 5
    default_floor = context // 2 if 8192 <= context < 32768 else 0
    proposed = {
        "contextTokens": context, "maxOutputTokens": output,
        "compactionReserveTokens": minimum_reserve,
        "compactionReserveFloorTokens": default_floor,
        "compactionKeepRecentTokens": max(512, min(20000, context // 16)),
    }
    proposed.update({key: item for key, item in desired.items() if item is not None})
    reserve = max(proposed["compactionReserveTokens"], proposed["compactionReserveFloorTokens"])
    if reserve < minimum_reserve:
        raise SettingsError("compaction-headroom-too-small")
    if reserve + proposed["compactionKeepRecentTokens"] >= context:
        raise SettingsError("compaction-leaves-no-compressible-context")

    budget_changed = context != caps["activeContextTokens"] or output != caps["activeMaxOutputTokens"]
    shared_change = bool(COMPACTION_CONTROLS.intersection(desired)) or budget_changed
    if shared_change and not caps["pixelOnlyRuntime"]:
        raise SettingsError("shared-compaction-requires-pixel-isolation")
    thinking = desired.get("thinking")
    if thinking is not None and thinking not in caps["supportedThinkingLevels"]:
        raise SettingsError("thinking-level-not-supported")
    if desired.get("reasoningVisibility") in ("on", "stream") and not any(
            level != "off" for level in caps["supportedThinkingLevels"]):
        raise SettingsError("reasoning-not-supported")
    if any(desired.get(key) is not None for key in ("temperature", "topP")) and not caps["samplingSupported"]:
        raise SettingsError("sampling-not-supported")
    if (desired.get("bootstrapMaxChars") is not None and desired.get("bootstrapTotalMaxChars") is not None
            and desired["bootstrapMaxChars"] > desired["bootstrapTotalMaxChars"]):
        raise SettingsError("bootstrap-file-exceeds-total")
    return {
        "schemaVersion": 1, "status": "preview", "applied": False,
        "configured": desired, "proposed": proposed, "capabilities": caps,
        "capacityVerified": backend_limit is not None and caps["capacitySource"] == "backend-observed",
        "sharedCompactionChange": shared_change,
        "warnings": [] if backend_limit is not None else ["backend-allocation-unknown"],
    }
