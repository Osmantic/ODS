"""Image policy must have evidence for the exact activated model route."""

import base64
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

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


# A remote route declares image input only from a vision probe of that route:
# two squares in two colours drawn per proof (model_profile
# probe_remote_vision) through ods/current, recorded in the route state with
# the route fingerprint (_record_remote_provider_vision).

_REMOTE_PROVIDER = {"model": "remote-vision", "transport": "direct", "baseUrl": "https://example.invalid/v1",
                    "contextLength": 32768, "maxTokens": 4096, "reasoning": False}


def _remote_state(**changes):
    """An enabled, proven direct route as the lifecycle writes it."""
    return {"schema": "ods.remote-routing-state.v1", "enabled": True, "mode": "cloud",
            "provider": {**_REMOTE_PROVIDER, **changes},
            "status": {"proven": True, "reason": "provider-handshake-ok"}}


def _vision_receipt(state, image_input):
    return {"schema": "ods.remote-provider-vision-receipt.v1", "imageInput": image_input,
            "routeFingerprint": agent._remote_provider_route_fingerprint(state),
            "verifiedAt": "2026-10-30T00:00:00+00:00"}


def _completion(content, finish="stop"):
    return 200, json.dumps({"choices": [{"message": {"role": "assistant", "content": content},
                                         "finish_reason": finish}]})


def _litellm_answers(monkeypatch, *answers):
    """Stand in for LiteLLM's ods/current; returns the requests it received."""
    requests, pending = [], list(answers)

    def exchange(_env, path, payload, timeout):
        requests.append((path, payload, timeout))
        answer = pending.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(agent, "_litellm_exchange", exchange)
    return requests


@pytest.mark.parametrize("image_input", ["supported", "unsupported"])
def test_a_remote_route_declares_what_its_own_vision_receipt_measured(image_input):
    state = _remote_state()
    assert agent._remote_provider_runtime_contract(state)["imageInput"] == "unknown"
    state["status"]["vision"] = _vision_receipt(state, image_input)
    assert agent._remote_provider_runtime_contract(state)["imageInput"] == image_input


@pytest.mark.parametrize("change", [
    {"baseUrl": "https://other.invalid/v1"}, {"model": "remote-other"}, {"transport": "ssh"},
    {"contextLength": 65536}, {"maxTokens": 8192}, {"reasoning": True},
])
def test_a_vision_receipt_belongs_to_the_route_it_measured(change):
    state = _remote_state()
    state["status"]["vision"] = _vision_receipt(state, "supported")
    moved = {**state, "provider": {**state["provider"], **change}}
    assert agent._remote_provider_runtime_contract(moved)["imageInput"] == "unknown"


@pytest.mark.parametrize("change", [
    {"schema": "ods.remote-provider-probe-receipt.v1"}, {"imageInput": "unknown"}, {"imageInput": True},
    {"routeFingerprint": None},
])
def test_a_malformed_vision_receipt_is_no_evidence(change):
    state = _remote_state()
    state["status"]["vision"] = {**_vision_receipt(state, "supported"), **change}
    assert agent._remote_provider_runtime_contract(state)["imageInput"] == "unknown"


def _png_pixels(url):
    """(width, height, left RGB, right RGB) of the probe's two-square PNG data URL."""
    data = base64.b64decode(url.split(",", 1)[1])
    width, height = struct.unpack(">II", data[16:24])
    raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
    row = raw[1:1 + 3 * width]
    return width, height, tuple(row[:3]), tuple(row[-3:])


@pytest.mark.parametrize("answers,expected", [
    ([_completion("Green, blue.")], "supported"),
    ([_completion("<think>Two squares: green, then blue.</think>\n\n**Green** and **Blue**")], "supported"),
    # Unambiguous misses: a palette colour that is not there, or no sight.
    ([_completion("Red, blue")], "unsupported"),
    ([_completion("I can't see the image you've shared.")], "unsupported"),
    ([(400, '{"error": {"message": "image input is not supported"}}')], "unsupported"),
    # Neither: a swap, one colour, a hedge, a long or cut-off answer, an error.
    ([_completion("Blue, green")], "unknown"),
    ([_completion("Green")], "unknown"),
    ([_completion("Maybe green, blue")], "unknown"),
    ([_completion("The left square is green and the right one is blue.")], "unknown"),
    ([_completion("Green, bl", "length")], "unknown"),
    ([(400, '{"error": {"message": "max_tokens is too large"}}')], "unknown"),
    ([(429, "image requests are rate limited")], "unknown"),
    ([(200, "not json")], "unknown"),
    ([OSError("LiteLLM did not answer in time")], "unknown"),
    # A busy route is asked once more inside the same budget, never again.
    ([(503, "busy"), _completion("green blue")], "supported"),
    ([(503, "busy"), (503, "busy")], "unknown"),
])
def test_one_vision_probe_through_the_gateway_decides_only_on_a_clear_answer(monkeypatch, answers, expected):
    monkeypatch.setattr(agent, "_remote_provider_vision_colors", lambda: ("green", "blue"))
    requests = _litellm_answers(monkeypatch, *answers)
    assert agent._remote_provider_vision_probe({}, 4096) == expected
    assert len(requests) == len(answers)
    for path, payload, timeout in requests:
        assert path == "/v1/chat/completions" and 0 < timeout <= 30
        text, image = payload["messages"][0]["content"]
        assert text["text"] == agent._model_profile_probes.REMOTE_VISION_PROMPT
        assert _png_pixels(image["image_url"]["url"]) == (224, 112, (0, 160, 0), (0, 0, 255))
        # No thinking switch a provider could refuse; room for a reasoning model.
        assert "chat_template_kwargs" not in payload and payload["max_tokens"] == 1024


@pytest.mark.parametrize("answer", ["I can't see the image you've shared.", "No image appeared.", "Shared",
                                    "Which colored square do you mean?", "Rendered.", "Red", "Shared, blue.",
                                    "Rendered blue"])
def test_a_word_that_merely_contains_a_colour_is_never_sight(monkeypatch, answer):
    # The P6 substring test read "red" in all of these (H1): none may pass.
    monkeypatch.setattr(agent, "_remote_provider_vision_colors", lambda: ("red", "blue"))
    _litellm_answers(monkeypatch, _completion(answer))
    assert agent._remote_provider_vision_probe({}, 4096) != "supported"


def test_the_probe_answers_within_the_route_output_limit(monkeypatch):
    requests = _litellm_answers(monkeypatch, _completion("Green, blue"))
    monkeypatch.setattr(agent, "_remote_provider_vision_colors", lambda: ("green", "blue"))
    assert agent._remote_provider_vision_probe({}, 256) == "supported"
    assert requests[0][1]["max_tokens"] == 256


def test_each_proof_draws_two_different_palette_colours():
    palette = set(agent._model_profile_probes.REMOTE_VISION_COLORS)
    pairs = {agent._remote_provider_vision_colors() for _ in range(300)}
    assert all(left != right and {left, right} <= palette for left, right in pairs)
    assert len(pairs) > 20, "a fixed or narrow choice would make the answer guessable"


def test_the_gateway_exchange_names_the_alias_and_keeps_the_key_off_argv(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"choices": []}\n200', "")

    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    monkeypatch.setattr(agent.subprocess, "run", run)
    env = {"LITELLM_KEY": "gateway-secret", "LITELLM_PORT": "4100"}
    assert agent._litellm_exchange(env, "/v1/chat/completions", {"messages": []}, 30.0) == (200, '{"choices": []}')
    command, kwargs = calls[0]
    assert command[command.index("-X") + 2] == "http://127.0.0.1:4100/v1/chat/completions"
    assert json.loads(command[command.index("-d") + 1]) == {"messages": [], "model": "ods/current"}
    assert command[command.index("--max-time") + 1] == "30" and kwargs["timeout"] == 35
    assert "gateway-secret" not in " ".join(command)
    assert kwargs["input"] == "Authorization: Bearer gateway-secret\n"

    for result in (subprocess.CompletedProcess([], 7, "", ""), subprocess.CompletedProcess([], 0, "\n000", "")):
        monkeypatch.setattr(agent.subprocess, "run", lambda *_a, _result=result, **_k: _result)
        with pytest.raises(OSError):
            agent._litellm_exchange(env, "/v1/chat/completions", {"messages": []}, 30.0)

    def slow(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 35)

    monkeypatch.setattr(agent.subprocess, "run", slow)
    with pytest.raises(OSError, match="in time"):
        agent._litellm_exchange(env, "/v1/chat/completions", {"messages": []}, 30.0)


def _saved_route(tmp_path, monkeypatch, state):
    monkeypatch.setattr(agent, "DATA_DIR", tmp_path)
    path = tmp_path / "remote-provider" / "routing-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def test_a_route_proof_records_its_vision_and_an_inconclusive_probe_keeps_it(tmp_path, monkeypatch):
    state = _remote_state()
    path = _saved_route(tmp_path, monkeypatch, state)
    monkeypatch.setattr(agent, "_iso_now", lambda: "2026-10-30T12:00:00+00:00")
    monkeypatch.setattr(agent, "_remote_provider_vision_colors", lambda: ("green", "blue"))
    _litellm_answers(monkeypatch, _completion("Green, blue"), OSError("LiteLLM did not answer in time"),
                     (503, "busy"), (503, "busy"), _completion("", "length"), _completion("Red, white"))

    recorded = agent._record_remote_provider_vision({"provider": dict(_REMOTE_PROVIDER)}, {})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == recorded
    receipt = {"schema": "ods.remote-provider-vision-receipt.v1", "imageInput": "supported",
               "routeFingerprint": agent._remote_provider_route_fingerprint(state),
               "verifiedAt": "2026-10-30T12:00:00+00:00"}
    assert saved["status"] == {"proven": True, "reason": "provider-handshake-ok", "vision": receipt}
    assert agent._remote_provider_runtime_contract(saved)["imageInput"] == "supported"

    # A timeout, a busy route or a cut-off answer is no evidence: the receipt stands.
    for _ in range(3):
        before = path.read_bytes()
        assert agent._record_remote_provider_vision(saved, {})["status"]["vision"] == receipt
        assert path.read_bytes() == before
    # A clear miss replaces it.
    assert agent._record_remote_provider_vision(saved, {})["status"]["vision"]["imageInput"] == "unsupported"
    assert agent._remote_provider_runtime_contract(json.loads(path.read_text(encoding="utf-8")))["imageInput"] == "unsupported"


def test_a_vision_probe_never_records_against_another_saved_route(tmp_path, monkeypatch):
    path = _saved_route(tmp_path, monkeypatch, _remote_state(baseUrl="https://other.invalid/v1"))
    before = path.read_bytes()
    monkeypatch.setattr(agent, "_litellm_exchange", lambda *_a: pytest.fail("no probe for another route"))
    with pytest.raises(RuntimeError, match="does not describe the route"):
        agent._record_remote_provider_vision({"provider": dict(_REMOTE_PROVIDER)}, {})
    assert path.read_bytes() == before


@pytest.mark.parametrize("answer,image_input", [(_completion("Green, blue"), "supported"),
                                                (OSError("LiteLLM did not answer"), "unknown")])
def test_a_remote_route_never_reads_the_local_vision_report(tmp_path, monkeypatch, answer, image_input):
    # Whatever the local llama-server would report, the remote route's image
    # input is its own probe's, and the previous local one is kept for restore.
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir()
    (install / ".env").write_text("ODS_MODE=local\nLLM_API_URL=http://llama-server:8080\nLITELLM_KEY=k\n"
                                  "PIXEL_OPENWEBUI_KEY=configured\n", encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", install)
    state_path = _saved_route(data, monkeypatch, _remote_state())
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
    monkeypatch.setattr(agent, "_capture_container_state", lambda _name: {"exists": True, "running": True})
    monkeypatch.setattr(agent, "_restart_existing_container", lambda *_a, **_k: True)
    monkeypatch.setattr(agent, "_wait_for_container_health", lambda _name: None)
    verified: list = []
    monkeypatch.setattr(agent, "_verify_litellm_route", lambda *_a, **_k: verified.append("ods/current"))
    monkeypatch.setattr(agent, "_render_remote_provider_cloud_config", lambda *_a: None)
    for name in ("_runtime_http", "_llama_runtime_props", "_wait_for_model_readiness"):
        monkeypatch.setattr(agent, name, lambda *_a, _name=name, **_k: pytest.fail(f"remote activation called {_name}"))

    def probe(_env, path, payload, timeout):
        assert verified, "the vision probe runs only once LiteLLM serves the route"
        assert not events, "the probe decides before Pixel is bound"
        requests.append(path)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    requests: list = []
    monkeypatch.setattr(agent, "_litellm_exchange", probe)
    monkeypatch.setattr(agent, "_remote_provider_vision_colors", lambda: ("green", "blue"))
    route = {"provider": dict(_REMOTE_PROVIDER)}

    activation = agent._activate_remote_provider_route(route)

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    expected = agent._remote_provider_runtime_contract(saved)
    assert activation["pixel"] == "reconciled" and requests == ["/v1/chat/completions"]
    assert events == [("apply", expected), ("finish", "commit")]
    assert expected["imageInput"] == image_input
    assert ("vision" in saved["status"]) is (image_input != "unknown")
    assert agent._read_remote_provider_activation_state()["remote"] == expected
    assert agent._read_remote_provider_activation_state()["previous"]["pixel"] == local


def test_without_portal_a_changed_verdict_rewrites_only_the_activation_record(tmp_path, monkeypatch):
    # No managed Pixel and no Portal admission lane: nothing to rebind, and
    # LiteLLM and the relay already serve the route, so nothing restarts.
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir()
    (install / ".env").write_text("ODS_MODE=cloud\nLLM_API_URL=http://litellm:4000\nLITELLM_KEY=k\n", encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", install)
    state = _remote_state()
    state_path = _saved_route(data, monkeypatch, state)
    runtime = agent._remote_provider_runtime_contract(state)
    agent._write_remote_provider_activation_state({
        "schema": "ods.remote-provider-activation-state.v1", "phase": "active", "remote": runtime,
        "routeFingerprint": runtime["routeFingerprint"], "updatedAt": "2026-10-30T00:00:00+00:00",
        "previous": {"odsMode": "local", "llmApiUrl": "http://llama-server:8080", "pixel": None,
                     "cloudConfig": {"exists": False, "text": None, "mode": None, "uid": None, "gid": None}}})
    monkeypatch.setattr(agent, "_managed_pixel_runtime_contract", lambda: None)
    monkeypatch.setattr(agent, "_ods_managed_pixel_identity", lambda: None)
    monkeypatch.setattr(agent, "_litellm_route_failure", lambda _env, *, model="ods/current": None)
    for name in ("_restart_existing_container", "_capture_container_state", "_render_remote_provider_cloud_config",
                 "_verify_litellm_route"):
        monkeypatch.setattr(agent, name, lambda *_a, _name=name, **_k: pytest.fail(f"the rebind called {_name}"))
    monkeypatch.setattr(agent, "_remote_provider_vision_colors", lambda: ("green", "blue"))
    _litellm_answers(monkeypatch, _completion("Green, blue"))
    proof = {"schema": "ods.remote-provider-egress-probe.v1", "ok": True, "transport": "direct",
             "probe": {"schema": "ods.remote-provider-probe-receipt.v1", "ok": True,
                       "verifiedAt": "2026-10-30T12:00:00+00:00", "endpoint": "/v1/models", "httpStatus": 200}}

    result = agent._record_remote_provider_egress_probe(proof)

    record = agent._read_remote_provider_activation_state()
    assert record["remote"]["imageInput"] == "supported" and record["previous"]["odsMode"] == "local"
    assert result["activation"]["pixel"] == "not_installed" and "imageCheck" not in result
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert result["status"] == saved["status"] and saved["status"]["vision"]["imageInput"] == "supported"
