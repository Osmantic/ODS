"""Fixed model metadata projection. No endpoints, files or execution authority."""
import copy
import math
import re


class ModelError(ValueError):
    pass


def checksum(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def target(value):
    keys = {"model", "contextLength", "maxTokens", "reasoning"}
    if (type(value) is not dict or not keys <= set(value) or set(value) - keys - {"routeFingerprint", "imageInput", "thinkingControl"}
            or type(value["model"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}", value["model"])
            or type(value["contextLength"]) is not int or not 4096 <= value["contextLength"] <= 10_000_000
            or type(value["maxTokens"]) is not int or not 1 <= value["maxTokens"] <= value["contextLength"]
            or type(value["reasoning"]) is not bool
            or "routeFingerprint" in value and not checksum(value["routeFingerprint"])
            or "imageInput" in value and value["imageInput"] not in ("supported", "unsupported", "unknown")
            or "thinkingControl" in value and value["thinkingControl"] not in ("enable_thinking", "always", "none")):
        raise ModelError("invalid-model-contract")
    return dict(value)


def binding(config):
    try:
        agents = config["agents"]["list"]
        if type(agents) is not list or len(agents) != 1 or agents[0]["id"] != "pixel": raise ValueError()
        agent = agents[0]
        selected = agent["model"]
        if type(selected) is dict: selected = selected["primary"]
        provider, model_id = selected.split("/", 1)
        if provider not in ("ods-local", "ods-gateway"): raise ValueError()
        rows = config["models"]["providers"][provider]["models"]
        matches = [row for row in rows if row["id"] == model_id]
        if len(matches) != 1: raise ValueError()
        row = matches[0]
        if provider == "ods-gateway":
            label = {"ods/current": "Current", "default": "Default"}.get(model_id)
            match = re.fullmatch(r"ODS " + str(label) + r" \((.+)\)", row["name"])
            if not label or not match: raise ValueError()
            name = match.group(1)
        else:
            name = model_id
            if row["name"] != "ODS Local " + name: raise ValueError()
        plugin = config["plugins"]["entries"]["pixel-ods"]
        if plugin.get("enabled", True) is not True: raise ValueError()
        settings = plugin.get("config", {})
        if type(settings) is not dict or "managedProvider" in settings: raise ValueError()
        return agent, row, settings, provider, name
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ModelError("model-route-not-managed") from None



def _thinking_projection(config, agent, row, settings, contract):
    if "modelThinkingControl" not in settings:
        return
    control = settings["modelThinkingControl"]
    if control not in ("enable_thinking", "always", "none"):
        raise ModelError("model-thinking-control-mismatch")
    selected = agent["model"]
    selected = selected["primary"] if type(selected) is dict else selected
    defaults = config["agents"].get("defaults", {})
    layers = [defaults.get("params", {}), defaults.get("models", {}).get(selected, {}).get("params", {}), agent.get("params", {})]
    params = {}
    for layer in layers:
        if type(layer) is not dict:
            raise ModelError("model-thinking-control-mismatch")
        params.update(layer)
    compat = row.get("compat", {})
    if type(compat) is not dict:
        raise ModelError("model-thinking-control-mismatch")
    kwargs = params.get("chat_template_kwargs", {})
    if type(kwargs) is not dict:
        raise ModelError("model-thinking-control-mismatch")
    switched = control == "enable_thinking"
    enabled = switched and contract["reasoning"]
    if ((switched and kwargs.get("enable_thinking") is not contract["reasoning"])
            or (not switched and "enable_thinking" in kwargs)
            or compat.get("thinkingFormat") != ("qwen-chat-template" if enabled else None)
            or agent.get("thinkingDefault", defaults.get("thinkingDefault")) != ("low" if enabled else None)):
        raise ModelError("model-thinking-control-mismatch")
    contract["thinkingControl"] = control


def _apply_thinking(config, agent, row, proposed):
    control = proposed.get("thinkingControl")
    switched = "qwen" in proposed["model"].lower() if control is None else control == "enable_thinking"
    enabled = switched and proposed["reasoning"]
    compat = row.get("compat", {})
    params = agent.setdefault("params", {})
    if type(compat) is not dict or type(params) is not dict:
        raise ModelError("invalid-model-thinking-settings")
    selected = agent["model"]
    selected = selected["primary"] if type(selected) is dict else selected
    defaults = config["agents"].get("defaults", {})
    inherited = [defaults.get("params", {}), defaults.get("models", {}).get(selected, {}).get("params", {})]
    effective = {}
    for layer in [*inherited, params]:
        if type(layer) is not dict:
            raise ModelError("invalid-model-thinking-settings")
        effective.update(layer)
    kwargs = copy.deepcopy(effective.get("chat_template_kwargs", {}))
    if type(kwargs) is not dict:
        raise ModelError("invalid-model-thinking-settings")
    defaults.pop("thinkingDefault", None)
    # There is exactly one managed agent. Clear the old model's switch from
    # inherited parameter layers too, so removing an agent override cannot
    # reactivate it. Leave all unrelated default/model parameters intact.
    for layer in inherited:
        if type(layer) is not dict:
            raise ModelError("invalid-model-thinking-settings")
        value = layer.get("chat_template_kwargs", {})
        if type(value) is not dict:
            raise ModelError("invalid-model-thinking-settings")
        value.pop("enable_thinking", None)
        if not value:
            layer.pop("chat_template_kwargs", None)
    # Only the model-owned switch changes. Preserve provider compatibility
    # options, sampling parameters and unrelated template arguments.
    compat.pop("thinkingFormat", None)
    if enabled:
        compat["thinkingFormat"] = "qwen-chat-template"
        agent["thinkingDefault"] = "low"
    else:
        agent.pop("thinkingDefault", None)
    if compat:
        row["compat"] = compat
    else:
        row.pop("compat", None)
    kwargs.pop("enable_thinking", None)
    if switched:
        kwargs["enable_thinking"] = proposed["reasoning"]
    if kwargs:
        params["chat_template_kwargs"] = kwargs
    else:
        params.pop("chat_template_kwargs", None)


def projection(config):
    agent, row, settings, provider, name = binding(config)
    contract = dict(model=name, contextLength=row.get("contextWindow"), maxTokens=row.get("maxTokens"), reasoning=row.get("reasoning"))
    if "modelRouteFingerprint" in settings:
        if provider != "ods-gateway": raise ModelError("model-route-not-managed")
        contract["routeFingerprint"] = settings["modelRouteFingerprint"]
    if "modelImageInput" in settings:
        capability = settings["modelImageInput"]
        expected_input = ["text"] if capability == "unsupported" else ["text", "image"]
        if row.get("input") != expected_input:
            raise ModelError("model-image-input-mismatch")
        contract["imageInput"] = capability
    _thinking_projection(config, agent, row, settings, contract)
    contract = target(contract)
    defaults = config["agents"].get("defaults", {})
    selected = agent["model"]
    selected = selected["primary"] if type(selected) is dict else selected
    # Match the pinned SDK: defaults, selected-model params, then agent params.
    # Resolve aliases inside each layer before applying the next override.
    layers = [defaults.get("params", {}), defaults.get("models", {}).get(selected, {}).get("params", {}), agent.get("params", {})]
    output, declared, resolved = contract["maxTokens"], False, False
    for params in layers:
        if type(params) is not dict: raise ModelError("invalid-model-limits")
        for key in ("maxTokens", "max_completion_tokens", "max_tokens"):
            if key not in params: continue
            declared = True
            value = params[key]
            if (type(value) is int or type(value) is float and math.isfinite(value)) and value >= 0:
                output, resolved = value, True
                break
    if declared and not resolved: raise ModelError("invalid-model-limits")
    compaction = defaults.get("compaction", {})
    limits = {"contextTokens": agent.get("contextTokens", defaults.get("contextTokens", contract["contextLength"])),
              "maxOutputTokens": output,
              "pluginContext": settings.get("modelContextWindow", contract["contextLength"]),
              **{key: compaction.get(key) for key in ("reserveTokens", "reserveTokensFloor", "keepRecentTokens")}}
    if any(value is not None and (type(value) is not int or not 0 <= value <= 10_000_000) for value in limits.values()):
        raise ModelError("invalid-model-limits")
    if any(type(limits[key]) is not int or limits[key] < 1 for key in ("contextTokens", "maxOutputTokens", "pluginContext")):
        raise ModelError("invalid-model-limits")
    return {"contract": contract, "limits": limits}


def plan(config, proposed):
    proposed = target(proposed)
    result = copy.deepcopy(config)
    agent, row, settings, provider, _name = binding(result)
    context, output = proposed["contextLength"], proposed["maxTokens"]
    if provider == "ods-local":
        if "routeFingerprint" in proposed: raise ModelError("model-route-not-managed")
        row["id"] = proposed["model"]
        selected = agent["model"]
        if type(selected) is dict:
            if selected.get("fallbacks"): raise ModelError("model-route-not-managed")
            selected["primary"] = "ods-local/" + row["id"]
        else: agent["model"] = "ods-local/" + row["id"]
        row["name"] = "ODS Local " + row["id"]
    else:
        label = "Current" if row["id"] == "ods/current" else "Default"
        row["name"] = "ODS " + label + " (" + proposed["model"] + ")"
    row.update(contextWindow=context, maxTokens=output, reasoning=proposed["reasoning"])
    # Unknown permits an explicitly informed transport attempt; it is not a
    # claim that the provider supports vision. Admission is checked separately.
    if "imageInput" in proposed:
        row["input"] = ["text"] if proposed["imageInput"] == "unsupported" else ["text", "image"]
    elif "modelImageInput" in settings:
        # A legacy caller switching routes cannot inherit the previous route's
        # image permission. Legacy configurations without this field stay intact.
        row["input"] = ["text"]
    agent["contextTokens"] = context
    params = agent.setdefault("params", {})
    params["maxTokens"] = output
    # These SDK output aliases express the same owned budget, not independent
    # sampling preferences. Avoid a conflicting provider serialization order.
    params.pop("max_completion_tokens", None)
    params.pop("max_tokens", None)
    agent.setdefault("contextLimits", {})["toolResultMaxChars"] = max(4000, min(16000, context // 4))
    lean = context < 32768 or any(float(n) <= 4 for n in re.findall(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", proposed["model"].lower()))
    agent.update(bootstrapMaxChars=2000 if lean else 14000, bootstrapTotalMaxChars=6000 if lean else 36000,
                 contextInjection="never" if lean else "continuation-skip")
    compaction = result["agents"].setdefault("defaults", {}).setdefault("compaction", {})
    # The computed reserve already includes output and transport headroom.
    # A half-window floor would unnecessarily reject compacted tool history.
    compaction.update(reserveTokens=(context + 4 * output + 4) // 5,
                      reserveTokensFloor=0,
                      keepRecentTokens=max(512, min(20000, context // 16)))
    # binding() returns the existing config dictionary; create it if absent.
    settings = result["plugins"]["entries"]["pixel-ods"].setdefault("config", {})
    settings.update(modelContextWindow=context, leanPrompt=lean)
    settings.pop("modelRouteFingerprint", None)
    settings.pop("modelImageInput", None)
    if "imageInput" in proposed: settings["modelImageInput"] = proposed["imageInput"]
    if "routeFingerprint" in proposed: settings["modelRouteFingerprint"] = proposed["routeFingerprint"]
    settings.pop("modelThinkingControl", None)
    if "thinkingControl" in proposed: settings["modelThinkingControl"] = proposed["thinkingControl"]
    _apply_thinking(result, agent, row, proposed)
    if projection(result)["contract"] != proposed: raise ModelError("model-projection-mismatch")
    return result
