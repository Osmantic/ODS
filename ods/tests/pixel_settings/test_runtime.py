"""Declared metadata and process-config comparison, not inference acceptance."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
from pixel_settings import runtime
from pixel_settings.contract import SettingsError, preview_preferences
from pixel_provider.config import default_config, normalize_config


def config():
    return {"agents": {"list": [{"id": "pixel", "model": "local/pixel", "contextTokens": 32768,
                                 "params": {"maxTokens": 4096}, "verboseDefault": "off"}]},
            "plugins": {"entries": {"pixel-ods": {"enabled": True, "config": {}}}},
            "models": {"providers": {"local": {"models": [
                {"id": "pixel", "contextWindow": 131072, "maxTokens": 16384, "reasoning": True}]}}}}


def envelope(source):
    return {"schemaVersion": 1, "source": "current-runtime-config", "pid": 123,
            "runtimeVersion": "2026.6.33", "revision": "a" * 64,
            "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **runtime.configured_fields(source)}


def test_declared_capacity_not_backend_measurement_or_effort_support():
    caps = runtime.declared_capabilities(config())
    assert caps["providerContextTokens"] == 131072 and caps["activeContextTokens"] == 32768
    assert caps["activeMaxOutputTokens"] == 4096
    assert caps["backendContextTokens"] is None and caps["capacitySource"] == "provider-declared"
    assert caps["supportedThinkingLevels"] == [] and caps["samplingSupported"] is False
    preview = preview_preferences({"verbosity": "full"}, caps)
    assert preview["capacityVerified"] is False and preview["warnings"] == ["backend-allocation-unknown"]


def managed():
    source = config()
    source["agents"]["list"][0]["model"] = "ods-policy/managed"
    source["plugins"]["entries"]["pixel-ods"]["config"]["managedProvider"] = {"revision": 4, "allowCloud": False}
    # The placeholder is deliberately too small; it must never be selected.
    source["models"]["providers"]["ods-policy"] = {"models": [
        {"id": "managed", "contextWindow": 32768, "maxTokens": 4096, "reasoning": False}]}
    document = default_config()
    document.update(revision=4, enabled=True, providers=[{
        "id": "tower", "label": "Tower", "kind": "ods-peer", "baseUrl": "https://tower.example.test/v1",
        "model": "model", "contextTokens": 131072, "maxOutputTokens": 16384, "reasoning": True,
        "enabled": True, "supportsTools": True, "supportsVision": False, "credentialRef": None}])
    document["roles"]["leader"] = "tower"
    return source, document


def test_managed_capacity_uses_real_bound_leader_not_placeholder():
    source, document = managed()
    normalize_config(document)
    caps = runtime.declared_capabilities(source, document)
    assert caps["providerContextTokens"] == 131072 and caps["providerMaxOutputTokens"] == 16384
    assert caps["capacitySource"] == "owner-declared"


@pytest.mark.parametrize("alias", ["maxTokens", "max_completion_tokens", "max_tokens"])
@pytest.mark.parametrize("use_managed", [False, True])
def test_output_capacity_inherits_runtime_default_params(alias, use_managed):
    source, document = managed() if use_managed else (config(), None)
    source["agents"]["list"][0].pop("params")
    source["agents"]["defaults"] = {"params": {alias: 384}}
    before = copy.deepcopy(source)
    caps = runtime.declared_capabilities(source, document)
    assert caps["activeMaxOutputTokens"] == 384
    assert caps["samplingSupported"] is False
    assert source == before


@pytest.mark.parametrize("default_alias", ["maxTokens", "max_completion_tokens", "max_tokens"])
@pytest.mark.parametrize("agent_alias", ["maxTokens", "max_completion_tokens", "max_tokens"])
def test_agent_output_overrides_inherited_aliases(default_alias, agent_alias):
    source = config()
    source["agents"]["defaults"] = {"params": {default_alias: 384}}
    source["agents"]["list"][0]["params"] = {agent_alias: 768}
    assert runtime.declared_capabilities(source)["activeMaxOutputTokens"] == 768


@pytest.mark.parametrize("value", [True, False, None, "384", 0, -1, 384.5, 10**400])
def test_unqualified_inherited_output_is_not_replaced_with_model_capacity(value):
    source = config()
    source["agents"]["list"][0].pop("params")
    source["agents"]["defaults"] = {"params": {"maxTokens": value}}
    with pytest.raises(SettingsError):
        runtime.declared_capabilities(source)


def test_output_reset_preview_and_projection_restore_inherited_budget():
    from pixel_settings.projection import plan_preferences
    source, document = managed()
    source["agents"]["list"][0].pop("params")
    source["agents"]["defaults"] = {"params": {"maxTokens": 384}}
    caps = runtime.declared_capabilities(source, document)
    first = plan_preferences(source, {"maxOutputTokens": 768}, caps)
    reset = plan_preferences(first["document"], {"maxOutputTokens": None},
        runtime.declared_capabilities(first["document"], document), previous=first["state"])
    assert reset["document"] == source
    assert reset["preview"]["proposed"]["maxOutputTokens"] == 384


@pytest.mark.parametrize("alias", ["maxTokens", "max_completion_tokens", "max_tokens"])
def test_selected_model_params_override_global_but_not_agent(alias):
    source = config()
    source["agents"]["list"][0].pop("params")
    source["agents"]["defaults"] = {"params": {"maxTokens": 384}, "models": {
        "local/pixel": {"params": {alias: 512}},
        "local/unrelated": {"params": {"maxTokens": 8192}}}}
    assert runtime.declared_capabilities(source)["activeMaxOutputTokens"] == 512
    source["agents"]["list"][0]["params"] = {"max_tokens": 768}
    assert runtime.declared_capabilities(source)["activeMaxOutputTokens"] == 768


def test_managed_placeholder_model_params_are_not_dynamic_lease_params():
    source, document = managed()
    source["agents"]["list"][0].pop("params")
    source["agents"]["defaults"] = {"params": {"maxTokens": 384}, "models": {
        "ods-policy/managed": {"params": {"maxTokens": 8192}}}}
    assert runtime.declared_capabilities(source, document)["activeMaxOutputTokens"] == 384


def test_first_token_alias_in_layer_wins_without_mutating_owner_params():
    source = config()
    source["agents"]["list"][0]["params"] = {
        "maxTokens": 256, "max_completion_tokens": 512, "max_tokens": 768}
    before = copy.deepcopy(source)
    assert runtime.declared_capabilities(source)["activeMaxOutputTokens"] == 256
    assert source == before


@pytest.mark.parametrize("value", [True, None, "384", 0, -1, 384.5, 10**400])
def test_shadowed_default_does_not_override_valid_agent_output(value):
    source = config()
    source["agents"]["defaults"] = {"params": {"maxTokens": value}}
    assert runtime.declared_capabilities(source)["activeMaxOutputTokens"] == 4096


def test_invalid_alias_is_skipped_like_sdk_without_losing_valid_inheritance():
    source = config()
    source["agents"]["defaults"] = {"params": {"max_tokens": 384}}
    source["agents"]["list"][0]["params"] = {"maxTokens": "ignored", "max_tokens": 768}
    assert runtime.declared_capabilities(source)["activeMaxOutputTokens"] == 768
    source["agents"]["list"][0]["params"].pop("max_tokens")
    assert runtime.declared_capabilities(source)["activeMaxOutputTokens"] == 384


@pytest.mark.parametrize("mutation", ["revision", "policy", "disabled", "missing"])
def test_managed_binding_drift_refused(mutation):
    source, document = managed()
    if mutation == "revision": document["revision"] = 5
    if mutation == "policy": document["policy"]["allowCloud"] = True
    if mutation == "disabled": document["enabled"] = False
    if mutation == "missing": document = None
    with pytest.raises(SettingsError): runtime.declared_capabilities(source, document)


@pytest.mark.parametrize("path", [("plugins",), ("plugins", "entries"), ("models",),
                                  ("models", "providers"), ("agents", "defaults")])
def test_malformed_container_is_stable_error(path):
    source = config()
    target = source
    for key in path[:-1]: target = target[key]
    target[path[-1]] = []
    with pytest.raises(SettingsError): runtime.declared_capabilities(source)


def test_presence_exact_types_and_no_extra_fields():
    source = config()
    response = envelope(source)
    assert runtime.compare_readback(source, response, pid=123, revision="a" * 64)
    response["fields"]["temperature"] = {"present": True, "value": None}
    assert not runtime.compare_readback(source, response, pid=123, revision="a" * 64)
    response = envelope(source)
    response["pixelOnlyRuntime"] = 1
    assert not runtime.compare_readback(source, response, pid=123, revision="a" * 64)
    response = envelope(source)
    response["fields"]["unowned"] = {"present": False, "value": None}
    assert not runtime.compare_readback(source, response, pid=123, revision="a" * 64)


@pytest.mark.parametrize("change", [{"pid": True}, {"pid": 124}, {"revision": "b" * 64},
    {"runtimeVersion": "other"}, {"schemaVersion": True}, {"source": "disk"}, {"observedAt": "now"},
    {"observedAt": "2026-99-08T00:00:00Z"}, {"observedAt": "2020-01-01T00:00:00Z"},
    {"observedAt": "2999-01-01T00:00:00Z"}, {"observedAt": 123}, {"extra": True}])
def test_stale_or_unqualified_envelope_refused(change):
    source = config()
    response = copy.deepcopy(envelope(source))
    response.update(change)
    with pytest.raises(SettingsError): runtime.compare_readback(source, response, pid=123, revision="a" * 64)


@pytest.mark.parametrize("text,expected", [
    ("", "/opt/ods/data"), ("# ODS_DATA_DIR=/bad\nOTHER=1", "/opt/ods/data"),
    (" ODS_DATA_DIR = '/mnt/my data' ", "/mnt/my data"),
    ("ODS_DATA_DIR=/one\nODS_DATA_DIR=/two", "/two"),
    ("ODS_DATA_DIR=/mnt/my\\ data", "/mnt/my data"),
    ("ODS_DATA_DIR=relative", None), ("ODS_DATA_DIR=", None),
    ("ODS_DATA_DIR=$HOME/data", None), ("ODS_DATA_DIR=${INSTALL_DIR}/data", None),
    ("ODS_DATA_DIR=/safe/../other", None),
])
def test_data_directory_matches_plain_host_env_without_expansion(text, expected):
    assert runtime.settings_data_directory("/opt/ods", text) == expected
