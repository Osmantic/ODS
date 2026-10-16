"""Image policy must have evidence for the exact activated model route."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_image_input", _path)
agent = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = agent
_spec.loader.exec_module(agent)


@pytest.mark.parametrize("record,expected", [
    ({"id": "selected", "vision": True}, "supported"),
    ({"id": "selected", "vision": False}, "unsupported"),
    ({"id": "selected"}, "unknown"),
    ({"id": "selected", "vision": "false"}, "unknown"),
    ({"id": "selected", "vision": 0}, "unknown"),
    ({"id": "other", "vision": True}, "unknown"),
    ({"id": "canonical", "llm_model_name": "selected", "vision": True}, "supported"),
])
def test_only_explicit_exact_curated_capability_is_evidence(tmp_path, monkeypatch, record, expected):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/model-library.json").write_text(json.dumps({"models": [record]}))
    monkeypatch.setattr(agent, "_load_model_library_records", lambda: pytest.fail("normalized/imported defaults are not evidence"))
    assert agent._pixel_model_image_input("selected") == expected


def test_ambiguous_missing_and_invalid_catalog_are_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._pixel_model_image_input("selected") == "unknown"
    (tmp_path / "config").mkdir()
    catalog = tmp_path / "config/model-library.json"
    catalog.write_text(json.dumps({"models": [
        {"id": "selected", "vision": True}, {"gguf_file": "selected", "vision": False},
    ]}))
    assert agent._pixel_model_image_input("selected") == "unknown"
    catalog.write_text("{")
    assert agent._pixel_model_image_input("selected") == "unknown"


def test_shell_transport_changes_and_clears_policy_without_reusing_previous_model(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "_ods_managed_pixel_identity", lambda: ("owner", tmp_path / "home"))
    monkeypatch.setattr(agent, "load_env", lambda _: {})
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(agent.subprocess, "run", run)
    common = {"contextLength": 32768, "maxTokens": 4096, "reasoning": False}
    for model, policy in [("vision", "supported"), ("text", "unsupported"), ("unidentified", None)]:
        contract = {**common, "model": model}
        if policy is not None:
            contract["imageInput"] = policy
        assert agent._reconcile_managed_pixel_contract(contract) == "reconciled"
        assert calls[-1][-2:] == [policy or "unknown", ""]
        assert '"$target_route_fingerprint" "" "$target_image_input"' in calls[-1][2]
    with pytest.raises(RuntimeError, match="image-input policy"):
        agent._reconcile_managed_pixel_contract({**common, "model": "vision", "imageInput": True})
    assert len(calls) == 3


def test_runtime_readback_retains_policy_and_rejects_boolean_default(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "_ods_managed_pixel_identity", lambda: ("owner", tmp_path / "home"))
    snapshot = agent._snapshot_text_file
    monkeypatch.setattr(agent, "_snapshot_text_file", lambda path: {**snapshot(path), "mode": 0o600})
    onboarding = tmp_path / "data/pixel/onboarding.json"
    onboarding.parent.mkdir(parents=True)
    value = {"modelProvider": "ods-gateway", "modelId": "ods/current", "modelName": "ODS Current (vision)",
             "modelContextWindow": 32768, "modelMaxTokens": 4096, "modelReasoning": False,
             "modelImageInput": "supported"}
    onboarding.write_text(json.dumps(value))
    onboarding.chmod(0o600)
    assert agent._managed_pixel_runtime_contract()["imageInput"] == "supported"
    value["modelImageInput"] = False
    onboarding.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="runtime contract"):
        agent._managed_pixel_runtime_contract()


def test_legacy_remote_proof_checks_route_without_claiming_visual_verification(monkeypatch):
    route = {"provider": {"model": "remote", "transport": "direct", "baseUrl": "https://example.invalid/v1",
                          "contextLength": 32768, "maxTokens": 4096, "reasoning": False}}
    monkeypatch.setattr(agent, "_read_remote_provider_route_state_for_update", lambda: route)
    verified = []
    monkeypatch.setattr(agent, "_verify_litellm_route", lambda *_args, **kwargs: verified.append(kwargs))
    contract = agent._remote_provider_runtime_contract(route)
    assert contract["imageInput"] == "unknown"
    legacy = {key: value for key, value in contract.items() if key != "imageInput"}
    assert agent._prove_pixel_model_contract({}, legacy)
    assert agent._prove_pixel_model_contract({}, {**contract, "imageInput": "supported"})
    assert not agent._prove_pixel_model_contract({}, {**contract, "model": "another"})
    assert not agent._prove_pixel_model_contract({}, {**contract, "routeFingerprint": "f" * 64})
    assert not agent._prove_pixel_model_contract({}, {**contract, "imageInput": False})
    assert verified == [{"model": "ods/current"}, {"model": "ods/current"}]
