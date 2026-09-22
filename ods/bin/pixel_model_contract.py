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
    if (type(value) is not dict or not keys <= set(value) or set(value) - keys - {"routeFingerprint"}
            or type(value["model"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}", value["model"])
            or type(value["contextLength"]) is not int or not 4096 <= value["contextLength"] <= 10_000_000
            or type(value["maxTokens"]) is not int or not 1 <= value["maxTokens"] <= value["contextLength"]
            or type(value["reasoning"]) is not bool
            or "routeFingerprint" in value and not checksum(value["routeFingerprint"])):
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


def projection(config):
    agent, row, settings, provider, name = binding(config)
    contract = dict(model=name, contextLength=row.get("contextWindow"), maxTokens=row.get("maxTokens"), reasoning=row.get("reasoning"))
    if "modelRouteFingerprint" in settings:
        if provider != "ods-gateway": raise ModelError("model-route-not-managed")
        contract["routeFingerprint"] = settings["modelRouteFingerprint"]
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
    if limits["maxOutputTokens"] > limits["contextTokens"]:
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
    if "routeFingerprint" in proposed: settings["modelRouteFingerprint"] = proposed["routeFingerprint"]
    if projection(result)["contract"] != proposed: raise ModelError("model-projection-mismatch")
    return result
