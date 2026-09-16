"""Pure declared-capacity selection and current-process readback comparison."""
from datetime import datetime, timezone
from pathlib import PurePosixPath
import math
import re
import shlex

from pixel_provider.config import ConfigError, normalize_config
from .contract import SettingsError, _capabilities, validate_preferences
from .projection import LEAVES, _lookup, _agents, _object, canonical


def settings_data_directory(install_dir, env_text):
    """Match host-agent load_env semantics without expansion or execution.

    Relative or empty custom values are unqualified for this new operation;
    returning None lets an existing access-only installation remain functional.
    """
    value = str(PurePosixPath(str(install_dir)) / "data")
    for line in env_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, _, raw = line.partition("=")
        if key.strip() != "ODS_DATA_DIR": continue
        raw = raw.strip()
        try: parsed = shlex.split(raw, comments=False, posix=True)
        except ValueError: parsed = []
        value = parsed[0] if len(parsed) == 1 else raw.strip("'\"")
    if not value or any(ord(char) < 32 for char in value): return None
    path = PurePosixPath(value)
    return str(path) if path.is_absolute() and ".." not in path.parts else None


def _timestamp(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise SettingsError("settings-runtime-readback-unavailable")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SettingsError("settings-runtime-readback-unavailable") from None
    age = (datetime.now(timezone.utc) - observed).total_seconds()
    if not -30 <= age <= 180:
        raise SettingsError("settings-runtime-readback-unavailable")
    return value


def saved_document(value):
    if (type(value) is not dict or set(value) != {"schemaVersion", "revision", "preferences"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= 2**53 - 1):
        raise SettingsError("invalid-saved-settings")
    return dict(value, preferences=validate_preferences(value["preferences"]))


def configured_fields(config):
    fields = {}
    for name, path in LEAVES.items():
        present, value = _lookup(config, path)
        if present:
            validate_preferences({"contextTokens" if name == "_pluginContext" else name: value})
            if value is None:
                raise SettingsError("invalid-runtime-settings")
        fields[name] = {"present": present, "value": value if present else None}
    return {"fields": {name: value for name, value in fields.items() if name != "_pluginContext"},
            "pluginContext": fields["_pluginContext"], "pixelOnlyRuntime": len(_agents(config)[1]) == 1}


def compare_readback(config, envelope, *, pid, revision):
    keys = {"schemaVersion", "source", "pid", "runtimeVersion", "revision", "observedAt", "pixelOnlyRuntime", "fields", "pluginContext"}
    if (type(envelope) is not dict or set(envelope) != keys or type(envelope["schemaVersion"]) is not int
            or envelope["schemaVersion"] != 1 or envelope["source"] != "current-runtime-config"
            or type(envelope["pid"]) is not int or envelope["pid"] != pid or pid <= 0
            or type(pid) is not int or type(revision) is not str or not re.fullmatch(r"[a-f0-9]{64}", revision)
            or envelope["revision"] != revision or envelope["runtimeVersion"] != "2026.6.33"):
        raise SettingsError("settings-runtime-readback-unavailable")
    _timestamp(envelope["observedAt"])
    expected = configured_fields(config)
    # Compare canonical representations: bool must not equal integer1, null is
    # not absence, and extra fields cannot become accidental proof.
    return all(canonical(envelope[name]) == canonical(value) for name, value in expected.items())


def _declared_output(defaults, pixel, primary, output):
    """Resolve configured output, not backend-observed generation capacity.

    The pinned SDK resolves defaults, a selected model's params, then the agent.
    Token aliases are resolved within each layer before later layers override it.
    Managed turns use a per-lease model id, not the static managed placeholder.
    """
    layers = [_object(defaults.get("params", {}))]
    if primary != "ods-policy/managed":
        selected = _object(defaults.get("models", {})).get(primary, {})
        layers.append(_object(_object(selected).get("params", {})))
    layers.append(_object(pixel.get("params", {})))
    declared = resolved = False
    for params in layers:
        for key in ("maxTokens", "max_completion_tokens", "max_tokens"):
            if key in params:
                declared = True
                value = params[key]
                # Match SDK alias selection and layer precedence. Validate the
                # final winner below, not a default shadowed by the agent.
                if (type(value) is int or type(value) is float and math.isfinite(value)) and value >= 0:
                    output = value
                    resolved = True
                    break
    # Invalid declarations are not evidence for a larger model default.
    if (declared and not resolved or type(output) is not int
            or not 1 <= output <= 10_000_000):
        raise SettingsError("settings-output-capacity-unavailable")
    return output


def declared_capabilities(config, provider_document=None, *, thinking_levels=None, sampling_supported=False):
    canonical(config)
    agents, entries, pixel = _agents(config)
    defaults = _object(agents.get("defaults", {}))
    model = pixel.get("model", defaults.get("model"))
    primary = model.get("primary") if type(model) is dict else model
    if type(primary) is not str or "/" not in primary:
        raise SettingsError("settings-model-capacity-unavailable")
    provider_id, model_id = primary.split("/", 1)
    plugin_entry = _object(_object(_object(config.get("plugins", {})).get("entries", {})).get("pixel-ods", {}))
    if plugin_entry.get("enabled") is not True:
        raise SettingsError("pixel-plugin-not-enabled")
    plugin = _object(plugin_entry.get("config", {}))
    if primary == "ods-policy/managed":
        try:
            provider_document = normalize_config(provider_document)
        except ConfigError:
            raise SettingsError("settings-provider-capacity-unavailable") from None
        binding = plugin.get("managedProvider")
        if (type(binding) is not dict or provider_document["enabled"] is not True
                or type(binding.get("revision")) is not int or binding["revision"] != provider_document["revision"]
                or type(binding.get("allowCloud")) is not bool or binding["allowCloud"] != provider_document["policy"]["allowCloud"]):
            raise SettingsError("settings-provider-binding-changed")
        leader = next(item for item in provider_document["providers"] if item["id"] == provider_document["roles"]["leader"])
        context, output, reasoning = leader["contextTokens"], leader["maxOutputTokens"], leader["reasoning"]
        source = "owner-declared"
    else:
        if provider_id == "ods-policy" or "managedProvider" in plugin:
            raise SettingsError("settings-provider-binding-changed")
        providers = _object(_object(config.get("models", {})).get("providers", {}))
        models = _object(providers.get(provider_id, {})).get("models", [])
        if type(models) is not list or any(type(item) is not dict for item in models):
            raise SettingsError("settings-model-capacity-unavailable")
        matches = [item for item in models if item.get("id") == model_id]
        if len(matches) != 1:
            raise SettingsError("settings-model-capacity-unavailable")
        row = matches[0]
        context, output, reasoning = row.get("contextWindow"), row.get("maxTokens"), row.get("reasoning")
        source = "provider-declared"
    if type(reasoning) is not bool:
        raise SettingsError("settings-model-capacity-unavailable")
    # A reasoning flag does not qualify arbitrary provider-specific efforts or
    # sampling parameters. Unknown support remains unavailable until supplied by
    # a qualified runtime profile; untouched preferences can still be applied.
    levels = thinking_levels if thinking_levels is not None else ([] if reasoning else ["off"])
    return _capabilities({"providerContextTokens": context, "providerMaxOutputTokens": output,
        "activeContextTokens": pixel.get("contextTokens", defaults.get("contextTokens", context)),
        "activeMaxOutputTokens": _declared_output(defaults, pixel, primary, output),
        "backendContextTokens": None, "capacitySource": source, "supportedThinkingLevels": levels,
        "samplingSupported": sampling_supported, "pixelOnlyRuntime": len(entries) == 1})
