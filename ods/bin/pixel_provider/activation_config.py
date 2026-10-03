"""Reversible Pixel-only projection, not permission to activate a gateway.

The caller must protect the returned private plan, validate the staged document
with the pinned runtime and coordinate admission/startup before applying it.
Only managed leaves are restored; unrelated concurrent edits are retained.
"""
from __future__ import annotations

import copy
import json
import math
import uuid

from .store import StoreError

_MODEL = {"primary": "ods-policy/managed", "fallbacks": []}
_PROVIDER = {
    "baseUrl": "http://127.0.0.1:1/v1", "api": "openai-completions",
    "apiKey": "ods-policy-unavailable",
    "models": [{"id": "managed", "name": "ODS managed", "contextWindow": 32768,
                "maxTokens": 4096, "reasoning": False, "input": ["text"]}],
}
_FIELDS = {"model", "provider", "binding"}
_PARENTS = {"models", "providers", "pluginConfig"}


def _json(value):
    """Reject Python coercions (tuple, integer keys, bool-as-int) and NaN."""
    def walk(item, depth=0):
        if depth > 32:
            raise StoreError("invalid-activation-json")
        kind = type(item)
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise StoreError("invalid-activation-json")
                walk(child, depth + 1)
        elif kind is list:
            for child in item:
                walk(child, depth + 1)
        elif kind is float:
            if not math.isfinite(item):
                raise StoreError("invalid-activation-json")
        elif kind not in (str, int, bool, type(None)):
            raise StoreError("invalid-activation-json")
    try:
        walk(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(result) > 1024 * 1024:
            raise StoreError("invalid-activation-json")
        return result
    except (ValueError, TypeError, RecursionError):
        raise StoreError("invalid-activation-json") from None


def _object(value):
    if type(value) is not dict:
        raise StoreError("invalid-activation-config")
    return value


def _parts(config):
    root = _object(config)
    agents = _object(root.get("agents")).get("list")
    if type(agents) is not list or any(type(agent) is not dict for agent in agents):
        raise StoreError("invalid-activation-config")
    matches = [agent for agent in agents if agent.get("id") == "pixel"]
    if len(matches) != 1:
        raise StoreError("invalid-activation-config")
    entries = _object(_object(root.get("plugins")).get("entries"))
    plugin = _object(entries.get("pixel-ods"))
    models = _object(root.get("models", {}))
    return (matches[0], models, _object(models.get("providers", {})), plugin,
            _object(plugin.get("config", {})))


def _binding(revision, allow_cloud, activation_id):
    if (type(revision) is not int or not 0 <= revision < 2**53
            or type(allow_cloud) is not bool or type(activation_id) is not str):
        raise StoreError("invalid-activation-binding")
    try:
        if str(uuid.UUID(activation_id)) != activation_id:
            raise ValueError()
    except ValueError:
        raise StoreError("invalid-activation-binding") from None
    return {"schemaVersion": 1, "activationId": activation_id,
            "revision": revision, "allowCloud": allow_cloud}


def _leaves(config):
    agent, _, providers, _, plugin_config = _parts(config)
    return {"model": (agent, "model"), "provider": (providers, "ods-policy"),
            "binding": (plugin_config, "managedProvider")}


def plan_activation(config, *, revision, allow_cloud, activation_id):
    """Capture presence-aware baseline without granting hooks or tool access."""
    _json(config)
    binding = _binding(revision, allow_cloud, activation_id)
    agent, models, providers, plugin, plugin_config = _parts(config)
    if (plugin.get("enabled") is not True
            or _object(plugin.get("hooks")).get("allowConversationAccess") is not True):
        raise StoreError("activation-plugin-not-qualified")
    for scope in (config, agent):
        by_provider = _object(_object(scope.get("tools", {})).get("byProvider", {}))
        if by_provider:
            raise StoreError("unsupported-provider-tool-policy")
    if "ods-policy" in providers or "managedProvider" in plugin_config:
        raise StoreError("activation-reserved-field-collision")
    after = {"model": _MODEL, "provider": _PROVIDER, "binding": binding}
    fields = {name: {"present": key in parent, "before": parent.get(key), "after": after[name]}
              for name, (parent, key) in _leaves(config).items()}
    candidate = copy.deepcopy(config)
    candidate.setdefault("models", {}).setdefault("providers", {})
    candidate["plugins"]["entries"]["pixel-ods"].setdefault("config", {})
    for name, (parent, key) in _leaves(candidate).items():
        parent[key] = copy.deepcopy(after[name])
    return copy.deepcopy({"schemaVersion": 1, "document": candidate, "fields": fields,
                          "parents": {"models": "models" in config,
                                      "providers": "providers" in models,
                                      "pluginConfig": "config" in plugin}})


def _validate_plan(plan):
    _json(plan)
    if (type(plan) is not dict or set(plan) != {"schemaVersion", "document", "fields", "parents"}
            or type(plan["schemaVersion"]) is not int or plan["schemaVersion"] != 1):
        raise StoreError("invalid-activation-plan")
    fields, parents = plan["fields"], plan["parents"]
    if (type(fields) is not dict or set(fields) != _FIELDS
            or type(parents) is not dict or set(parents) != _PARENTS
            or any(type(flag) is not bool for flag in parents.values())
            or (parents["providers"] and not parents["models"])):
        raise StoreError("invalid-activation-plan")
    for name, record in fields.items():
        if (type(record) is not dict or set(record) != {"present", "before", "after"}
                or type(record["present"]) is not bool
                or (not record["present"] and record["before"] is not None)
                or (name != "model" and record["present"])):
            raise StoreError("invalid-activation-plan")
    binding = fields["binding"]["after"]
    if type(binding) is not dict or set(binding) != {"schemaVersion", "activationId", "revision", "allowCloud"}:
        raise StoreError("invalid-activation-plan")
    expected = {"model": _MODEL, "provider": _PROVIDER,
                "binding": _binding(binding["revision"], binding["allowCloud"], binding["activationId"])}
    for name, value in expected.items():
        if _json(fields[name]["after"]) != _json(value):
            raise StoreError("invalid-activation-plan")
    # The preview is not the restore source. Validate its managed projection,
    # but never overwrite current unrelated values with preview-era values.
    _check_installed(plan["document"], fields)
    return fields, parents


def _check_installed(config, fields):
    for name, (parent, key) in _leaves(config).items():
        if key not in parent or _json(parent[key]) != _json(fields[name]["after"]):
            raise StoreError("activation-config-drift")


def restore_activation(current, plan):
    """Restore exactly managed leaves, refusing drift before any mutation."""
    _json(current)
    fields, parents = _validate_plan(plan)
    _check_installed(current, fields)
    restored = copy.deepcopy(current)
    for name, (parent, key) in _leaves(restored).items():
        if fields[name]["present"]:
            parent[key] = copy.deepcopy(fields[name]["before"])
        else:
            del parent[key]
    _, models, providers, plugin, plugin_config = _parts(restored)
    if not parents["pluginConfig"] and not plugin_config:
        plugin.pop("config", None)
    if not parents["providers"] and not providers:
        models.pop("providers", None)
    if not parents["models"] and not models:
        restored.pop("models", None)
    return restored


def update_activation(current, plan, *, revision, allow_cloud, activation_id):
    """Plan a newer provider revision without adopting the managed route as baseline.

    This is pure projection, not permission to update the running gateway. The
    trusted coordinator must still validate the saved revision, acquire admission,
    replace deployment/config together, and prove the new runtime before success.
    A new binding identity invalidates old turn leases; retry/recovery belongs to
    the existing transaction, not a second projection with the same identity.
    """
    baseline = restore_activation(current, plan)  # Validates plan and live leaves.
    binding = _binding(revision, allow_cloud, activation_id)
    previous = plan["fields"]["binding"]["after"]
    if binding["revision"] <= previous["revision"]:
        raise StoreError("activation-revision-not-newer")
    if binding["activationId"] == previous["activationId"]:
        raise StoreError("activation-id-reused")
    return plan_activation(baseline, revision=revision, allow_cloud=allow_cloud,
                           activation_id=activation_id)
