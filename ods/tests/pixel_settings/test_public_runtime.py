"""Public envelopes and host controller boundary, not installed proof."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
from pixel_settings import host_api, public
from pixel_provider.store import StoreError


def response(status="not-applied"):
    caps = {"providerContextTokens": 65536, "providerMaxOutputTokens": 8192,
            "activeContextTokens": 32768, "activeMaxOutputTokens": 4096, "backendContextTokens": None,
            "capacitySource": "owner-declared", "supportedThinkingLevels": [], "samplingSupported": False,
            "pixelOnlyRuntime": True}
    value = {"schemaVersion": 1, "status": status, "revision": "a" * 64, "settingsRevision": 3,
             "appliedRevision": None, "capabilities": caps, "pending": False, "lastVerifiedAt": None, "reason": None}
    if status in ("applied", "saved-changes"):
        value.update(appliedRevision=3 if status == "applied" else 2, lastVerifiedAt="2026-09-08T16:00:00Z")
    if status == "restored": value.update(lastVerifiedAt="2026-09-08T16:00:00Z")
    if status == "pending": value.update(pending=True, capabilities=None)
    if status == "unavailable": return public.unavailable("settings-controller-unavailable")
    return value


def dashboard_public():
    spec = importlib.util.spec_from_file_location("_settings_dashboard_public_parity", ROOT / "extensions/services/dashboard-api/pixel_settings_public.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("state", ["not-applied", "applied", "saved-changes", "restored", "pending", "unavailable"])
def test_host_and_dashboard_runtime_contract_agree(state):
    value = response(state)
    assert public.normalize_runtime(value) == dashboard_public().normalize_runtime(value) == value


@pytest.mark.parametrize("state,field,value", [
    ("applied", "schemaVersion", True), ("applied", "settingsRevision", True), ("applied", "appliedRevision", 2),
    ("applied", "lastVerifiedAt", "2026-99-08T00:00:00Z"), ("applied", "pending", True),
    ("saved-changes", "appliedRevision", 3), ("not-applied", "lastVerifiedAt", "2026-09-08T16:00:00Z"),
    ("pending", "pending", False), ("pending", "appliedRevision", 3),
    ("unavailable", "pending", False), ("unavailable", "revision", "a" * 64),
    ("unavailable", "reason", "secret/key/with/path"), ("not-applied", "revision", "bad"),
    ("not-applied", "private", "do-not-echo"),
    ("restored", "lastVerifiedAt", None), ("restored", "appliedRevision", 3),
])
def test_inconsistent_runtime_metadata_is_rejected_in_both_boundaries(state, field, value):
    document = response(state)
    document[field] = value
    for normalize in (public.normalize_runtime, dashboard_public().normalize_runtime):
        with pytest.raises(ValueError): normalize(document)


@pytest.mark.parametrize("field,value", [("activeContextTokens", True), ("providerContextTokens", 0),
    ("supportedThinkingLevels", ["high", "high"]), ("supportedThinkingLevels", [True]),
    ("backendContextTokens", -1), ("capacitySource", "backend-observed"), ("samplingSupported", 1),
    ("activeMaxOutputTokens", 999999), ("credential", "do-not-echo")])
def test_capability_checks_match_without_upgrading_declared_limits(field, value):
    document = response()
    document["capabilities"][field] = value
    for normalize in (public.normalize_runtime, dashboard_public().normalize_runtime):
        with pytest.raises(ValueError): normalize(document)


def test_public_projection_does_not_share_mutable_capability_list():
    value = response()
    for normalize in (public.normalize_runtime, dashboard_public().normalize_runtime):
        result = normalize(value)
        result["capabilities"]["supportedThinkingLevels"].append("high")
    assert value["capabilities"]["supportedThinkingLevels"] == []


def test_host_passes_actual_data_dir_not_http_selected_path(tmp_path):
    body = {"operation": "apply", "revision": "b" * 64, "settingsRevision": 3}
    calls = []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {"outcome": "applied", "appliedRevision": 3}
    assert host_api.runtime_change(tmp_path, body, request=request)["outcome"] == "applied"
    assert calls == [(("settings-change", body), {"settings_data_dir": tmp_path})]


@pytest.mark.parametrize("field,value", [("capabilities", {}), ("data_dir_id", "a" * 64), ("path", "/etc"),
                                       ("operation", "exec"), ("settingsRevision", True), ("revision", "bad")])
def test_invalid_change_does_not_call_controller(tmp_path, field, value):
    body = {"operation": "apply", "revision": "b" * 64, "settingsRevision": 3, field: value}
    with pytest.raises(StoreError, match="invalid-request"):
        host_api.runtime_change(tmp_path, body, request=lambda *_args, **_kwargs: pytest.fail("controller called"))


def test_lost_controller_reply_is_unknown_not_false_success_or_retry(tmp_path):
    calls = []
    def failed(*args, **_kwargs):
        calls.append(args)
        raise OSError("private failure detail")
    value = host_api.runtime_status(tmp_path, request=failed)
    assert value["status"] == "unavailable" and value["pending"] is None and value["revision"] is None
    body = {"operation": "apply", "revision": "b" * 64, "settingsRevision": 3}
    with pytest.raises(OSError): host_api.runtime_change(tmp_path, body, request=failed)
    assert len(calls) == 2


def test_fresh_store_code_survives_both_public_status_boundaries(tmp_path):
    value = host_api.runtime_status(tmp_path, request=lambda *_args, **_kwargs:
                                    (409, {"error": "settings-store-not-initialized"}))
    expected = public.unavailable("settings-store-not-initialized")
    assert value == dashboard_public().normalize_runtime(value) == expected
    assert not (tmp_path / "pixel-providers").exists()


def test_wrong_completed_revision_cannot_be_reported_as_success(tmp_path):
    body = {"operation": "apply", "revision": "b" * 64, "settingsRevision": 3}
    with pytest.raises(ValueError, match="revision-mismatch"):
        host_api.runtime_change(tmp_path, body, request=lambda *_args, **_kwargs: (200, {"outcome": "applied", "appliedRevision": 4}))


def test_saved_marker_is_honest_and_older_host_still_readable(tmp_path):
    value = host_api.get_settings(tmp_path)
    assert value["runtime"] == {"status": "not-inspected", "reason": "runtime-status-separate"}
    assert dashboard_public().normalize_response(value) == value
    old = copy.deepcopy(value)
    old["runtime"] = {"status": "not-applied", "reason": "settings-runtime-not-integrated"}
    assert dashboard_public().normalize_response(old) == old
