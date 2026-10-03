"""Pure owned-leaf/reset/rollback tests, not runtime activation."""
import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "bin"))
from pixel_settings.contract import SettingsError, merge_preferences
from pixel_settings.projection import plan_preferences, restore_preferences, revision


def config():
    return {"agents": {"list": [{"id": "pixel", "model": {"primary": "ods-policy/managed", "fallbacks": []},
        "sandbox": {"mode": "all"}, "verboseDefault": "off"}]},
        "plugins": {"entries": {"pixel-ods": {"enabled": True, "config": {"managedProvider": {"revision": 3}}}}},
        "models": {"providers": {"ods-policy": {"baseUrl": "http://127.0.0.1:1/v1", "apiKey": "unchanged-placeholder"}}},
        "tools": {"deny": ["dangerous"]}, "env": {"ODS_MODE": "remote"}}


def caps(**changes):
    return {"providerContextTokens": 131072, "providerMaxOutputTokens": 16384,
        "activeContextTokens": 32768, "activeMaxOutputTokens": 4096,
        "backendContextTokens": 65536, "capacitySource": "backend-observed",
        "supportedThinkingLevels": ["off", "high"], "samplingSupported": True,
        "pixelOnlyRuntime": True, **changes}


def test_context_output_projection_preserves_provider_routing_auth_access_and_input():
    source = config()
    before = copy.deepcopy(source)
    plan = plan_preferences(source, {"contextTokens": 65536, "maxOutputTokens": 8192}, caps())
    result = plan["document"]
    assert source == before
    for field in ("models", "tools", "env"):
        assert result[field] == before[field]
    assert result["agents"]["list"][0]["model"] == before["agents"]["list"][0]["model"]
    assert result["agents"]["list"][0]["sandbox"] == {"mode": "all"}
    assert result["agents"]["list"][0]["params"]["maxTokens"] == 8192
    assert result["plugins"]["entries"]["pixel-ods"]["config"]["modelContextWindow"] == 65536
    assert result["agents"]["defaults"]["compaction"]["reserveTokens"] == 19661
    assert plan["expectedConfigRevision"] == revision(source)
    assert restore_preferences(result, plan["state"]) == before


def test_rollback_keeps_unrelated_concurrent_edits_and_created_siblings():
    source = config()
    plan = plan_preferences(source, {"maxOutputTokens": 8192, "verbosity": "full"}, caps())
    live = copy.deepcopy(plan["document"])
    live["logging"] = {"level": "debug"}
    live["agents"]["list"][0]["params"]["cacheRetention"] = "long"
    live["agents"]["defaults"]["compaction"]["customInstructions"] = "preserve owner note"
    restored = restore_preferences(live, plan["state"])
    assert restored["logging"] == {"level": "debug"}
    assert restored["agents"]["list"][0]["params"] == {"cacheRetention": "long"}
    assert restored["agents"]["defaults"]["compaction"] == {"customInstructions": "preserve owner note"}
    assert restored["agents"]["list"][0]["verboseDefault"] == "off"


def test_repeated_changes_and_reset_restore_original_not_last_override():
    original = config()
    original["agents"]["list"][0]["contextTokens"] = 32768
    preferences = {"contextTokens": 65536, "maxOutputTokens": 8192, "verbosity": "full"}
    first = plan_preferences(original, preferences, caps())
    changes = merge_preferences(preferences, {"contextTokens": 49152})
    second = plan_preferences(first["document"], changes, caps(activeContextTokens=65536, activeMaxOutputTokens=8192), previous=first["state"])
    reset = plan_preferences(second["document"], merge_preferences(changes, {"contextTokens": None, "maxOutputTokens": None}),
        caps(activeContextTokens=49152, activeMaxOutputTokens=8192), previous=second["state"])
    assert reset["document"]["agents"]["list"][0]["contextTokens"] == 32768
    assert "params" not in reset["document"]["agents"]["list"][0]
    assert "defaults" not in reset["document"]["agents"]
    assert reset["document"]["agents"]["list"][0]["verboseDefault"] == "full"
    assert reset["preview"]["proposed"]["contextTokens"] == 32768
    assert reset["preview"]["proposed"]["maxOutputTokens"] == 4096
    assert restore_preferences(reset["document"], reset["state"]) == original


def test_reset_of_missing_original_leaf_removes_it():
    source = config()
    first = plan_preferences(source, {"temperature": 0.5}, caps())
    reset = plan_preferences(first["document"], {"temperature": None}, caps(), previous=first["state"])
    assert reset["document"] == source and reset["state"]["fields"] == {}


def test_empty_preferences_do_not_create_containers_or_change_owner_values():
    source = config()
    assert plan_preferences(source, {}, caps())["document"] == source


def test_agent_order_changes_do_not_retarget_preferences():
    source = config()
    source["agents"]["list"].append({"id": "other", "verboseDefault": "on"})
    plan = plan_preferences(source, {"verbosity": "full"}, caps(pixelOnlyRuntime=False))
    plan["document"]["agents"]["list"].reverse()
    restored = restore_preferences(plan["document"], plan["state"])
    assert restored["agents"]["list"][0] == {"id": "other", "verboseDefault": "on"}
    assert restored["agents"]["list"][1]["verboseDefault"] == "off"


def test_actual_multiagent_config_overrules_false_isolation_claim():
    source = config()
    source["agents"]["list"].append({"id": "other"})
    with pytest.raises(SettingsError, match="pixel-isolation"):
        plan_preferences(source, {"contextTokens": 65536}, caps())


@pytest.mark.parametrize("preferences", [{"contextTokens": 32768}, {"maxOutputTokens": 4096}])
def test_same_budget_preference_does_not_rewrite_other_agents_compaction(preferences):
    source = config()
    source["agents"]["list"].append({"id": "other"})
    source["agents"]["defaults"] = {"compaction": {"reserveTokens": 14000, "keepRecentTokens": 1200}}
    before = copy.deepcopy(source)
    plan = plan_preferences(source, preferences, caps())
    assert plan["document"]["agents"]["defaults"] == source["agents"]["defaults"]
    assert plan["preview"]["sharedCompactionChange"] is False
    assert not any(name.startswith("compaction") for name in plan["state"]["fields"])
    assert restore_preferences(plan["document"], plan["state"]) == before


def test_new_agent_refuses_rollback_of_shared_compaction():
    plan = plan_preferences(config(), {"compactionMode": "safeguard"}, caps())
    plan["document"]["agents"]["list"].append({"id": "other"})
    with pytest.raises(SettingsError, match="pixel-isolation"):
        restore_preferences(plan["document"], plan["state"])


def test_unowned_context_change_is_not_replaced_by_stale_baseline_budget():
    plan = plan_preferences(config(), {"maxOutputTokens": 8192}, caps())
    live = copy.deepcopy(plan["document"])
    live["agents"]["list"][0]["contextTokens"] = 65536
    next_plan = plan_preferences(live, {"maxOutputTokens": 8192}, caps(activeContextTokens=65536, activeMaxOutputTokens=8192), previous=plan["state"])
    assert next_plan["document"]["agents"]["list"][0]["contextTokens"] == 65536
    assert next_plan["preview"]["proposed"]["compactionReserveTokens"] == 19661


@pytest.mark.parametrize("change", [
    lambda c: c["agents"]["list"][0].update(verboseDefault="on"),
    lambda c: c["agents"]["list"][0].pop("verboseDefault"),
    lambda c: c["models"]["providers"]["ods-policy"].update(baseUrl="http://other/v1"),
    lambda c: c["agents"]["list"][0].update(model="other/model"),
    lambda c: c["plugins"]["entries"]["pixel-ods"]["config"]["managedProvider"].update(revision=4),
])
def test_drift_refuses_without_partial_mutation(change):
    plan = plan_preferences(config(), {"verbosity": "full"}, caps())
    change(plan["document"])
    before = copy.deepcopy(plan["document"])
    with pytest.raises(SettingsError, match="drift"):
        restore_preferences(plan["document"], plan["state"])
    assert plan["document"] == before


@pytest.mark.parametrize("corrupt", [
    lambda s: s.update(schemaVersion=True),
    lambda s: s["fields"].update(sandbox={"present": True, "before": "off", "after": "all"}),
    lambda s: s.update(absentParents=[["pixel", "sandbox"]]),
    lambda s: s.update(absentParents=[["pixel", "params"]]),
    lambda s: s["fields"]["verbosity"].update(present=0),
    lambda s: s["fields"]["verbosity"].update(after=None),
    lambda s: s["fields"]["verbosity"].update(after={"unexpected": True}),
    lambda s: s["baseBudgets"].update(contextTokens=True),
])
def test_malformed_state_cannot_target_unowned_policy(corrupt):
    plan = plan_preferences(config(), {"verbosity": "full"}, caps())
    corrupt(plan["state"])
    with pytest.raises(SettingsError):
        restore_preferences(plan["document"], plan["state"])
