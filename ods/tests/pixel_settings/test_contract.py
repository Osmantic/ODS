"""Pure contract checks only: no claim of installed settings or runtime effects."""
import copy
import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location("pixel_settings_contract",
    pathlib.Path(__file__).resolve().parents[2] / "bin/pixel_settings/contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def caps(**changes):
    return {"providerContextTokens": 131072, "providerMaxOutputTokens": 16384,
            "activeContextTokens": 32768, "activeMaxOutputTokens": 4096,
            "backendContextTokens": 65536, "capacitySource": "backend-observed",
            "supportedThinkingLevels": ["off", "low", "high"], "samplingSupported": True,
            "pixelOnlyRuntime": True, **changes}


def test_preview_is_not_applied_and_never_mutates_inputs():
    desired = {"contextTokens": 65536, "maxOutputTokens": 8192, "verbosity": "full"}
    capabilities = caps()
    before = copy.deepcopy((desired, capabilities))
    result = contract.preview_preferences(desired, capabilities)
    assert (desired, capabilities) == before
    assert result["status"] == "preview" and result["applied"] is False
    assert result["proposed"]["compactionReserveTokens"] == 19661
    assert result["capacityVerified"] is True


def test_unknown_backend_does_not_invent_a_low_ceiling_or_claim_verified_capacity():
    result = contract.preview_preferences({"contextTokens": 131072},
        caps(backendContextTokens=None, capacitySource="owner-declared"))
    assert result["proposed"]["contextTokens"] == 131072
    assert result["capacityVerified"] is False
    assert result["capabilities"]["backendContextTokens"] is None
    assert result["warnings"] == ["backend-allocation-unknown"]


def test_sparse_updates_preserve_omitted_overrides_and_distinguish_reset():
    old = {"contextTokens": 32768, "verbosity": "on"}
    assert contract.merge_preferences(old, {"contextTokens": None}) == {"contextTokens": None, "verbosity": "on"}
    assert old["contextTokens"] == 32768


@pytest.mark.parametrize("change", [
    {"contextTokens": True}, {"contextTokens": 4096.0}, {"contextTokens": 4095},
    {"contextTokens": 10_000_001}, {"temperature": float("nan")},
    {"temperature": float("inf")}, {"temperature": True}, {"temperature": -0.1},
    {"topP": 0}, {"verbosity": "verbose"}, {"compactionMode": "off"},
    {"compactionNotify": 1}, {"compactionRecentTurns": 13}, {"command": "/bin/sh"},
    {"apiKey": "private"}, {"contextTokens": "8192"}, {"thinking": []},
    {"contextTokens": 10 ** 1000}, {"temperature": 10 ** 1000},
])
def test_invalid_or_unowned_controls_are_refused(change):
    with pytest.raises(contract.SettingsError):
        contract.validate_preferences(change)


@pytest.mark.parametrize("change,reason", [
    ({"contextTokens": 65537}, "context-exceeds"),
    ({"maxOutputTokens": 16385}, "output-exceeds"),
    ({"compactionReserveTokens": 0}, "headroom"),
    ({"compactionKeepRecentTokens": 30000}, "compressible"),
    ({"compactionReserveFloorTokens": 32768}, "compressible"),
    ({"thinking": "max"}, "thinking-level"),
    ({"bootstrapMaxChars": 5000, "bootstrapTotalMaxChars": 4000}, "bootstrap-file"),
])
def test_cross_field_and_capability_limits(change, reason):
    with pytest.raises(contract.SettingsError, match=reason):
        contract.preview_preferences(change, caps())


def test_compaction_cannot_silently_change_other_agents():
    for change in ({"compactionMode": "safeguard"}, {"contextTokens": 65536}, {"compactionNotify": None}):
        with pytest.raises(contract.SettingsError, match="pixel-isolation"):
            contract.preview_preferences(change, caps(pixelOnlyRuntime=False))
    assert contract.preview_preferences({"verbosity": "on"}, caps(pixelOnlyRuntime=False))["sharedCompactionChange"] is False


def test_sampling_and_reasoning_capabilities_are_not_invented():
    with pytest.raises(contract.SettingsError, match="sampling-not-supported"):
        contract.preview_preferences({"temperature": 0.5}, caps(samplingSupported=False))
    with pytest.raises(contract.SettingsError, match="reasoning-not-supported"):
        contract.preview_preferences({"reasoningVisibility": "stream"}, caps(supportedThinkingLevels=["off"]))


@pytest.mark.parametrize("changes", [
    {"backendContextTokens": True}, {"backendContextTokens": None},
    {"providerMaxOutputTokens": 200000}, {"supportedThinkingLevels": ["off", "off"]},
    {"supportedThinkingLevels": [None]}, {"samplingSupported": 1}, {"capacitySource": "guessed"},
])
def test_invalid_capability_evidence_is_not_used(changes):
    with pytest.raises(contract.SettingsError, match="invalid-runtime-capabilities"):
        contract.preview_preferences({}, caps(**changes))


@pytest.mark.parametrize("context", [4096, 8192, 16384, 32768, 65536, 131072])
def test_automatic_compaction_has_headroom_and_compressible_history(context):
    result = contract.preview_preferences({"contextTokens": context, "maxOutputTokens": min(8192, context // 4)},
        caps(backendContextTokens=131072))
    proposed = result["proposed"]
    assert max(proposed["compactionReserveTokens"], proposed["compactionReserveFloorTokens"]) + proposed["compactionKeepRecentTokens"] < context
