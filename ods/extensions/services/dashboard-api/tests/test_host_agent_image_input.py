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


# What ODS's own llama-server reports in /props changes Pixel's declaration
# only when it says a vision projector is loaded (llama.cpp with an mmproj).

def _props_answer(monkeypatch, answer):
    def runtime_http(_env, path, **_kwargs):
        assert path == "/props"
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)

    monkeypatch.setattr(agent, "_runtime_http", runtime_http)


_SERVED = {"default_generation_settings": {"n_ctx": 65536}, "model_path": "/models/Model.gguf"}


@pytest.mark.parametrize("modalities,expected", [
    ({"vision": True, "audio": False}, True),
    ({"vision": False, "audio": False}, False),
    (None, None),  # a build that reports no modalities
    ({"vision": "true"}, None),
    ({"vision": 1}, None),
    ({"audio": True}, None),
    (True, None),
])
def test_props_report_vision_only_as_an_explicit_boolean(monkeypatch, modalities, expected):
    _props_answer(monkeypatch, _SERVED if modalities is None else {**_SERVED, "modalities": modalities})
    assert agent._llama_runtime_props({}) == (65536, "Model.gguf", expected)


@pytest.mark.parametrize("answer", [
    OSError("llama-server /props is unreachable (curl exit 7)"),
    subprocess.TimeoutExpired("curl", 5),
    "{", "[]", "",
])
def test_an_unreadable_props_answer_reports_no_vision(monkeypatch, answer):
    _props_answer(monkeypatch, answer)
    assert agent._llama_runtime_props({}) == (0, "", None)


def _llama_answers(modalities):
    props = dict(_SERVED) if modalities is None else {**_SERVED, "modalities": modalities}
    return {
        "/health": {"status": "ok"},
        "/v1/models": {"object": "list", "data": [{"id": "Model.gguf", "object": "model"}]},
        "/props": props,
        "/v1/chat/completions": {"model": "Model.gguf", "choices": [{"message": {"content": "READY"}}]},
    }


_KEY = "cd" * 32
# Every route whose model server is ODS's own llama-server, as its proof reaches it.
_ROUTES = {
    "linux-host": ({"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"}, "http://127.0.0.1:8080"),
    "container-agent": ({"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"}, "http://ods-llama-server:8080"),
    "windows-native": ({"GPU_BACKEND": "amd", "AMD_INFERENCE_LOCATION": "host", "AMD_INFERENCE_RUNTIME": "llama-server",
                        "AMD_INFERENCE_PORT": "18080", "LLAMA_SERVER_API_KEY": _KEY}, "http://127.0.0.1:18080"),
    "macos-native": ({"GPU_BACKEND": "apple", "ODS_NATIVE_LLAMA_PORT": "8080"}, "http://127.0.0.1:8080"),
    "wsl-router": ({"GPU_BACKEND": "cpu", "ODS_HOST_LLM_TRANSPORT": "model-router", "LLAMA_SERVER_API_KEY": _KEY},
                   "http://host.docker.internal:8080"),
}


def _serve_route(monkeypatch, route, answers):
    env, origin = _ROUTES[route]
    requests: list = []
    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    if route == "container-agent":
        monkeypatch.setenv("ODS_HOST_INSTALL_DIR", "/ods")
    monkeypatch.setattr(agent.platform, "system", lambda: "Windows" if route == "windows-native" else "Linux")
    monkeypatch.setattr(agent._wsl_runtime, "candidate", lambda _env: route == "wsl-router")
    if route == "wsl-router":
        def transport(_install, at, path, payload=None, api_key="", timeout=5):
            assert api_key == _KEY
            requests.append(at + path)
            return json.dumps(answers[path])

        monkeypatch.setattr(agent, "_router_transport_request", transport)
        monkeypatch.setattr(agent.subprocess, "run", lambda *_a, **_k: pytest.fail("WSL reaches Windows only through the router"))
    else:
        def run(command, **_kwargs):
            url = next(str(part) for part in command if str(part).startswith("http"))
            requests.append(url)
            return subprocess.CompletedProcess(command, 0, json.dumps(answers[url[len(origin):]]), "")

        monkeypatch.setattr(agent.subprocess, "run", run)
    return {**env, "CTX_SIZE": "65536"}, origin, requests


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("modalities,vision", [
    ({"vision": True, "audio": False}, True),
    ({"vision": False, "audio": False}, False),
    (None, None),
])
def test_the_readiness_proof_carries_vision_from_its_own_props_read(monkeypatch, route, modalities, vision):
    env, origin, requests = _serve_route(monkeypatch, route, _llama_answers(modalities))
    proof = agent._wait_for_model_readiness(
        env, model_id="model", gguf_file="Model.gguf", llm_model_name="model",
        attempts=1, initial_delay=0, interval=0, return_proof=True, require_exact_context=True)
    assert proof["identity"] == "Model.gguf" and proof["contextVerified"] is True
    assert proof.get("vision", "absent") == ("absent" if vision is None else vision)
    # No extra request: the vision report is the /props answer that proved the context.
    assert [url[len(origin):] for url in requests] == ["/health", "/v1/models", "/props", "/v1/chat/completions"]
    assert all(url.startswith(origin) for url in requests)


@pytest.mark.parametrize("proof,configured,expected", [
    ({"identity": "m", "vision": True}, "unknown", "supported"),
    ({"identity": "m", "vision": True}, "unsupported", "supported"),
    ({"identity": "m", "vision": False}, "unknown", "unknown"),
    ({"identity": "m", "vision": False}, "unsupported", "unsupported"),
    ({"identity": "m", "vision": False}, "supported", "supported"),
    ({"identity": "m"}, "unknown", "unknown"),
    ({"identity": "m", "vision": "true"}, "unknown", "unknown"),
    ({"identity": "m", "vision": 1}, "unknown", "unknown"),
    ({}, "unknown", "unknown"),
    (None, "unsupported", "unsupported"),
])
def test_only_a_reported_vision_projector_declares_support(proof, configured, expected):
    assert agent._pixel_image_input_readback(configured, proof) == expected


def test_a_remote_route_never_reads_the_local_vision_report(tmp_path, monkeypatch):
    # Whatever the local llama-server would report, the remote provider's
    # declaration stays unknown and the previous local one is kept for restore.
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir()
    (install / ".env").write_text("ODS_MODE=local\nLLM_API_URL=http://llama-server:8080\nLITELLM_KEY=k\n"
                                  "PIXEL_OPENWEBUI_KEY=configured\n", encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", install)
    monkeypatch.setattr(agent, "DATA_DIR", data)
    local = {"model": "vision.gguf", "contextLength": 65536, "maxTokens": 8192, "reasoning": False,
             "imageInput": "supported"}
    events: list = []

    class Transaction:
        previous = dict(local)
        completed = False

        def apply(self, target):
            events.append(("apply", dict(target)))
            return "reconciled"

        def finish(self, outcome):
            events.append(("finish", outcome))
            self.completed = True

    monkeypatch.setattr(agent, "_managed_pixel_runtime_contract", lambda: dict(local))
    monkeypatch.setattr(agent, "_begin_pixel_model_transaction", lambda _env: Transaction())
    monkeypatch.setattr(agent, "_capture_container_state", lambda name:
                        {"exists": name != "ods-hermes", "running": name != "ods-hermes"})
    monkeypatch.setattr(agent, "_restart_existing_container", lambda *_a, **_k: True)
    monkeypatch.setattr(agent, "_wait_for_container_health", lambda _name: None)
    monkeypatch.setattr(agent, "_verify_litellm_route", lambda *_a, **_k: None)
    monkeypatch.setattr(agent, "_render_remote_provider_cloud_config", lambda *_a: None)
    for name in ("_runtime_http", "_llama_runtime_props", "_wait_for_model_readiness"):
        monkeypatch.setattr(agent, name, lambda *_a, _name=name, **_k: pytest.fail(f"remote activation called {_name}"))
    route = {"provider": {"model": "remote-vision", "transport": "direct", "baseUrl": "https://example.invalid/v1",
                          "contextLength": 32768, "maxTokens": 4096, "reasoning": False}}

    activation = agent._activate_remote_provider_route(route)

    assert activation["pixel"] == "reconciled"
    assert events == [("apply", agent._remote_provider_runtime_contract(route)), ("finish", "commit")]
    assert events[0][1]["imageInput"] == "unknown"
    assert agent._read_remote_provider_activation_state()["previous"]["pixel"] == local
