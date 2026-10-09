"""Tests for model activation helpers in ods-host-agent.py (one llama-server path)."""

import base64
import hashlib
import importlib.util
import http.client
import io
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

_real_subprocess_run = subprocess.run

# Import the host agent module from bin/ using importlib.
# The module has an ``if __name__ == "__main__":`` guard so no server starts.
_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent_activate", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent_activate"] = _mod
_spec.loader.exec_module(_mod)

_meaningful_completion = _mod._meaningful_completion
_patch_hermes_model_config = _mod._patch_hermes_model_config
_compose_restart_llama_server = _mod._compose_restart_llama_server
_launch_native_llama_server = _mod._launch_native_llama_server
_is_windows_host_llama_server = _mod._is_windows_host_llama_server
_restart_windows_native_llama_server = _mod._restart_windows_native_llama_server
_write_host_native_litellm_config = _mod._write_host_native_litellm_config
_wait_for_container_health = _mod._wait_for_container_health
_capture_container_state = _mod._capture_container_state
_container_exists = _mod._container_exists


def _llama_runtime_run(
    identity="Model.gguf",
    *,
    n_ctx=65536,
    model_path=None,
    health=None,
    completion=None,
    calls=None,
):
    """Fake subprocess.run for one llama-server: /health, /v1/models, /props, chat."""

    def fake_run(cmd, **kwargs):
        if calls is not None:
            calls.append((cmd, kwargs))
        url = next((str(part) for part in cmd if str(part).startswith("http")), "")
        if url.endswith("/health"):
            body = json.dumps(health or {"status": "ok"})
        elif url.endswith("/v1/models"):
            body = _llama_identity_response(identity)
        elif url.endswith("/props"):
            props = {"default_generation_settings": {"n_ctx": n_ctx}}
            if model_path is not None:
                props["model_path"] = model_path
            body = json.dumps(props)
        else:
            body = json.dumps(completion or {
                "model": identity,
                "choices": [{"message": {"content": "READY"}}],
            })
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    return fake_run


@pytest.fixture(autouse=True)
def _isolate_opencode_config(monkeypatch, tmp_path):
    """Never let model-activation tests mutate the developer's real config."""
    config_dir = tmp_path / "isolated-home" / ".config" / "opencode"
    monkeypatch.setattr(
        _mod,
        "_opencode_config_paths",
        lambda: (config_dir / "opencode.json", config_dir / "config.json"),
    )
    monkeypatch.setattr(
        _mod,
        "_capture_container_state",
        lambda container: {
            "exists": _mod._container_exists(container),
            "running": (
                container != "ods-perplexica" and _mod._container_exists(container)
            ),
        },
    )
    monkeypatch.setattr(_mod, "_wait_for_container_health", lambda _container: None)
    monkeypatch.setattr(
        _mod,
        "_capture_managed_opencode_state",
        lambda: {"system": _mod.platform.system(), "active": False},
    )
    monkeypatch.setattr(_mod, "_opencode_installed", lambda: False)
    # These fixtures describe synthetic containers. Never fingerprint a real
    # developer's running gateway and accidentally converge it during a test.
    # Live-input reuse is exercised separately in test_model_switch_speed.py.
    monkeypatch.setattr(_mod, "_dependent_bind_inputs", lambda _container: None)


@pytest.fixture(autouse=True)
def _install_runtime_renderer(tmp_path):
    """Exercise host-agent rendering through the shipped canonical script."""
    source = _agent_path.parents[1] / "scripts" / "render-runtime-configs.py"
    target = tmp_path / "scripts" / "render-runtime-configs.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def test_host_agent_backlog_handles_dashboard_poll_bursts():
    assert _mod.ThreadedHTTPServer.request_queue_size >= 64


def test_hermes_health_wait_covers_delayed_docker_health_transition(monkeypatch):
    statuses = iter(
        ["starting"] * (_mod.HERMES_MODEL_ACTIVATION_HEALTH_ATTEMPTS - 1)
        + ["healthy"]
    )
    inspections = []
    sleeps = []

    def inspect(*args, **_kwargs):
        inspections.append(args)
        return subprocess.CompletedProcess(args, 0, next(statuses) + "\n", "")

    monkeypatch.setattr(_mod.subprocess, "run", inspect)
    monkeypatch.setattr(_mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        _mod,
        "_capture_container_state",
        lambda _container: {"exists": True, "running": True},
    )

    _wait_for_container_health("ods-hermes")

    assert len(inspections) == _mod.HERMES_MODEL_ACTIVATION_HEALTH_ATTEMPTS
    assert sleeps == [2] * (_mod.HERMES_MODEL_ACTIVATION_HEALTH_ATTEMPTS - 1)


def test_hermes_health_wait_remains_bounded_and_fail_closed(monkeypatch):
    inspections = []
    sleeps = []

    def inspect(*args, **_kwargs):
        inspections.append(args)
        return subprocess.CompletedProcess(args, 0, "starting\n", "")

    monkeypatch.setattr(_mod.subprocess, "run", inspect)
    monkeypatch.setattr(_mod.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(
        RuntimeError,
        match="ods-hermes did not become healthy after model activation",
    ):
        _wait_for_container_health("ods-hermes")

    assert len(inspections) == _mod.HERMES_MODEL_ACTIVATION_HEALTH_ATTEMPTS
    assert sleeps == [2] * (_mod.HERMES_MODEL_ACTIVATION_HEALTH_ATTEMPTS - 1)


@pytest.mark.parametrize(
    ("container", "attempts", "expected_attempts"),
    [
        ("ods-litellm", None, _mod.MODEL_ACTIVATION_HEALTH_ATTEMPTS),
        ("ods-hermes", 3, 3),
    ],
)
def test_container_health_wait_preserves_other_defaults_and_explicit_overrides(
    monkeypatch, container, attempts, expected_attempts,
):
    inspections = []

    def inspect(*args, **_kwargs):
        inspections.append(args)
        return subprocess.CompletedProcess(args, 0, "starting\n", "")

    monkeypatch.setattr(_mod.subprocess, "run", inspect)
    monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="did not become healthy after model activation"):
        _wait_for_container_health(container, attempts=attempts)

    assert len(inspections) == expected_attempts


@pytest.mark.parametrize("mode", ["lemonade", " LEMONADE "])
def test_legacy_lemonade_ods_mode_reads_as_local_for_one_release(mode):
    # Managed AMD installs persisted ODS_MODE=lemonade before round F. Until
    # the .env migration rewrites it, activation must not lock them out.
    assert _mod._normalize_ods_mode(mode) == "local"
    assert _mod._model_activation_mode_denial(mode, "local") is None


def test_amd_install_routes_through_llama_server_not_lemonade(monkeypatch):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    env = {
        "ODS_MODE": "lemonade",
        "GPU_BACKEND": "amd",
        "LLM_BACKEND": "lemonade",
        "AMD_INFERENCE_RUNTIME": "lemonade",
        "LEMONADE_MODEL": "extra.Qwen3.6-35B-A3B-Q4_K_M.gguf",
        "LLM_MODEL": "qwen3.6-35b-a3b",
        "GGUF_FILE": "Qwen3.6-35B-A3B-Q4_K_M.gguf",
    }
    assert _mod._initial_switchboard_backend(env) == (
        "llama-server", "llama-server-default", None,
    )
    # A leftover LEMONADE_MODEL never relabels the route identity.
    assert _mod._current_runtime_model_inputs(
        env, {"runtimeModelId": "stale.gguf", "catalogId": "stale"},
    ) == ("Qwen3.6-35B-A3B-Q4_K_M.gguf", "qwen3.6-35b-a3b")


@pytest.mark.parametrize(("system", "release", "env", "agent_in_container", "expected"), [
    ("Linux", "6.8.0", {"GPU_BACKEND": "amd", "OLLAMA_PORT": "8091"}, False,
     ("http://127.0.0.1:8091", "direct")),
    ("Linux", "6.8.0", {"GPU_BACKEND": "amd"}, True,
     ("http://ods-llama-server:8080", "direct")),
    ("Darwin", "24.0", {"GPU_BACKEND": "apple", "ODS_NATIVE_LLAMA_PORT": "8181"}, False,
     ("http://127.0.0.1:8181", "direct")),
    ("Windows", "11", {"GPU_BACKEND": "amd", "AMD_INFERENCE_LOCATION": "host",
                       "AMD_INFERENCE_RUNTIME": "llama-server", "AMD_INFERENCE_PORT": "18080"}, False,
     ("http://127.0.0.1:18080", "direct")),
    ("Linux", "5.15.167.4-microsoft-standard-WSL2",
     {"GPU_BACKEND": "cpu", "ODS_HOST_LLM_TRANSPORT": "model-router",
      "NATIVE_LLM_CONTAINER_BASE_URL": "http://host.docker.internal:18080/v1",
      "AMD_INFERENCE_PORT": "18080"}, False,
     ("http://host.docker.internal:18080", "router")),
])
def test_runtime_endpoint_covers_every_llama_server_family(
    monkeypatch, system, release, env, agent_in_container, expected,
):
    monkeypatch.setattr(_mod.platform, "system", lambda: system)
    monkeypatch.setattr(_mod._wsl_runtime.platform, "system", lambda: system)
    monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: release)
    if agent_in_container:
        monkeypatch.setenv("ODS_HOST_INSTALL_DIR", "/opt/ods")
    else:
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    assert _mod._runtime_endpoint(env) == expected


@pytest.mark.parametrize(("env", "expected"), [
    ({"ODS_HOST_LLM_TRANSPORT": "model-router",
      "NATIVE_LLM_CONTAINER_BASE_URL": "http://host.docker.internal:18080"},
     "http://host.docker.internal:18080"),
    # One-release compatibility reads of the pre-round-F names.
    ({"LEMONADE_HOST_TRANSPORT": "model-router",
      "LEMONADE_CONTAINER_BASE_URL": "http://host.docker.internal:13305/api/v1"},
     "http://host.docker.internal:13305"),
    # The current name wins over a leftover legacy one.
    ({"ODS_HOST_LLM_TRANSPORT": "model-router",
      "NATIVE_LLM_CONTAINER_BASE_URL": "http://host.docker.internal:8080",
      "LEMONADE_CONTAINER_BASE_URL": "http://host.docker.internal:13305"},
     "http://host.docker.internal:8080"),
    ({"ODS_HOST_LLM_TRANSPORT": "model-router", "AMD_INFERENCE_PORT": "28080",
      "NATIVE_LLM_CONTAINER_BASE_URL": "http://user:secret@host.docker.internal:8080"},
     "http://host.docker.internal:28080"),
])
def test_wsl_runtime_origin_reads_neutral_then_legacy_keys(monkeypatch, env, expected):
    monkeypatch.setattr(_mod._wsl_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    assert _mod._runtime_endpoint(env) == (expected, "router")


def test_runtime_api_key_is_sent_only_to_windows_owned_servers(monkeypatch):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod._wsl_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: "6.8.0")
    assert _mod._runtime_api_key({"GPU_BACKEND": "amd", "LLAMA_SERVER_API_KEY": "ab" * 32}) == ""
    monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    assert _mod._runtime_api_key({
        "ODS_HOST_LLM_TRANSPORT": "model-router", "LLAMA_SERVER_API_KEY": "ab" * 32,
    }) == "ab" * 32


@pytest.mark.parametrize("original", [
    (
        "ODS_MODE=local\nLLM_BACKEND=external\n"
        "EXTERNAL_LLM_URL=http://192.168.1.20:13305\n"
        "EXTERNAL_LLM_MODEL=Qwen3.6-35B-A3B-GGUF\n"
    ),
    # An unmigrated .env for the owner's own Lemonade (no WSL bridge): one
    # release until the installer rewrites it to the generic external keys.
    (
        "ODS_MODE=lemonade\nGPU_BACKEND=amd\nLLM_BACKEND=lemonade\nLEMONADE_EXTERNAL=true\n"
        "LEMONADE_BASE_URL=http://192.168.1.20:13305\nLEMONADE_MODEL=Qwen3.6-35B-A3B-GGUF\n"
    ),
])
def test_external_llm_local_activation_rejects_before_mutation(
    monkeypatch, tmp_path, original,
):
    install = tmp_path / "ods"
    install.mkdir()
    env_path = install / ".env"
    env_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install)
    monkeypatch.setattr(_mod, "STARTUP_ODS_MODE", "local")
    monkeypatch.setattr(
        _mod, "_load_model_library_records",
        lambda: pytest.fail("external runtime must be rejected before model lookup"),
    )
    monkeypatch.setattr(
        _mod, "_recreate_llama_server",
        lambda *_args, **_kwargs: pytest.fail("external runtime must not be recreated"),
    )
    handler = _ResponseHandler()
    _mod.AgentHandler._do_model_activate(handler, "Qwen3.5-2B-Q4_K_M")
    assert handler.response_code == 409
    assert handler.parse_response()["code"] == "external_runtime_unmanaged"
    assert env_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("recovery", [
    {"pending": True},
    RuntimeError("unsafe journal"),
])
def test_host_model_status_marks_uncertain_native_transaction_pending(
    monkeypatch, recovery,
):
    monkeypatch.setattr(_mod, "_active_remote_provider_pixel_runtime", lambda **_: None)
    monkeypatch.setattr(_mod, "_switchboard_state", None)

    def status():
        if isinstance(recovery, Exception):
            raise recovery
        return recovery

    monkeypatch.setattr(_mod, "_pixel_model_recovery_status", status)
    payload = {}
    _mod._project_switchboard_agent_viability(payload)
    assert payload["modelTransactionPending"] is True


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/v1/model/external-observation"),
    ("GET", "/v1/model/external-observation?stats=1"),
    ("POST", "/v1/model/external-adopt"),
    ("POST", "/v1/runtime/lemonade/ensure"),
])
def test_retired_lemonade_endpoints_answer_gone_after_auth(monkeypatch, method, path):
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "retired-endpoint-key")
    monkeypatch.setattr(_mod, "_begin_model_activation", lambda *_args: pytest.fail(
        "a retired endpoint must not take the model lifecycle"
    ))
    server = _mod.ThreadedHTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps({"model_id": "loaded-B"}) if method == "POST" else None
        headers = {"Content-Type": "application/json"} if body else {}
        connection.request(method, path, body=body, headers=headers)
        denied = connection.getresponse()
        assert denied.status == 401
        denied.read()
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(method, path, body=body, headers={
            **headers, "Authorization": "Bearer retired-endpoint-key",
        })
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 410
        assert payload["code"] == "external_lemonade_removed"
        assert "external-llm" in payload["hint"]
        assert "no-store" in response.getheader("Cache-Control")
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(("plan_model", "plan_context", "running", "live_context", "proven"), [
    ("Qwen3.5-2B-Q4_K_M.gguf", 65536, True, 65536, True),
    ("Qwen3.6-35B-A3B-Q4_K_M.gguf", 65536, True, 65536, False),
    ("Qwen3.5-2B-Q4_K_M.gguf", 32768, True, 65536, False),
    ("Qwen3.5-2B-Q4_K_M.gguf", 65536, False, 65536, False),
    ("Qwen3.5-2B-Q4_K_M.gguf", 65536, True, 32768, False),
])
def test_wsl_runtime_pixel_proof_requires_owned_plan_and_live_model(
    monkeypatch, plan_model, plan_context, running, live_context, proven,
):
    config = {
        "ODS_HOST_LLM_TRANSPORT": "model-router",
        "GGUF_FILE": "Qwen3.5-2B-Q4_K_M.gguf",
        "LLM_MODEL": "qwen3.5-2b",
        "CTX_SIZE": "65536",
        "MAX_CONTEXT": "65536",
    }
    monkeypatch.setattr(_mod, "_managed_wsl_runtime", lambda _env: {
        "managed": True, "running": running,
        "plan": {"GgufFile": plan_model, "ContextSize": plan_context},
    })
    monkeypatch.setattr(_mod, "_runtime_endpoint", lambda _env: ("http://host.docker.internal:18080", "router"))

    def transport(_install, origin, path, payload=None, api_key="", timeout=5):
        assert origin == "http://host.docker.internal:18080"
        if path == "/health":
            return json.dumps({"status": "ok"})
        if path == "/v1/models":
            return _llama_identity_response("Qwen3.5-2B-Q4_K_M.gguf")
        if path == "/props":
            return json.dumps({"default_generation_settings": {"n_ctx": live_context}})
        assert path == "/v1/chat/completions"
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        return json.dumps({"model": "Qwen3.5-2B-Q4_K_M.gguf",
                           "choices": [{"message": {"content": "READY"}}]})

    monkeypatch.setattr(_mod, "_router_transport_request", transport)
    assert _mod._prove_pixel_model_contract(config, {
        "model": "Qwen3.5-2B-Q4_K_M.gguf", "contextLength": 65536,
    }) is proven


def test_host_agent_keeps_gets_alive_and_closes_posts(monkeypatch):
    class _CountingServer(_mod.ThreadedHTTPServer):
        accepted_connections = 0

        def get_request(self):
            request = super().get_request()
            self.accepted_connections += 1
            return request

    server = _CountingServer(("127.0.0.1", 0), _mod.AgentHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "keepalive-test-key")
        for _ in range(20):
            connection.request("GET", "/health")
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok", "version": _mod.VERSION}
        assert server.accepted_connections == 1

        connection.request(
            "POST",
            "/v1/model/download/cancel",
            body="{}",
            headers={
                "Authorization": "Bearer keepalive-test-key",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Connection") == "close"
        assert json.loads(response.read()) == {"status": "no_download"}

        # http.client transparently opens a fresh socket after the explicit
        # close; the unread cancel body cannot corrupt this request.
        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok", "version": _mod.VERSION}
        assert server.accepted_connections == 2
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


class TestCompletionProof:

    def test_completion_accepts_reasoning_content_when_message_content_empty(self):
        body = {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": "Okay",
                    "role": "assistant",
                }
            }]
        }
        assert _meaningful_completion(body) is True
        # The readiness proof requires visible content.
        assert _meaningful_completion(body, include_reasoning=False) is False

    def test_success_when_completion_has_choices(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"choices":[{"message":{"content":"ok"}}]}',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _mod._chat_completion_ready(
            "", "", "Model.gguf", "/v1",
            base_url="http://127.0.0.1:8080",
            disable_thinking=True, require_visible_content=True,
        ) is True
        cmd = calls[0][0]
        assert "http://127.0.0.1:8080/v1/chat/completions" in cmd
        payload = json.loads(cmd[cmd.index("-d") + 1])
        assert payload["model"] == "Model.gguf"
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    def test_false_on_nonzero_exit(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _mod._chat_completion_ready("127.0.0.1", "8080", "model.gguf") is False

    def test_false_on_invalid_json(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not-json", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _mod._chat_completion_ready("127.0.0.1", "8080", "model.gguf") is False

    @pytest.mark.parametrize("content", ["", "???", " ? ? ? ", "!!!"])
    def test_rejects_empty_or_pathological_output(self, monkeypatch, content):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"choices": [{"message": {"content": content}}]}),
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _mod._chat_completion_ready("127.0.0.1", "8080", "model.gguf") is False

    def test_runtime_proof_rejects_reasoning_only_output_on_every_runtime(self, monkeypatch):
        # Contract section 1.4: a reasoning-only answer does not prove a
        # runtime can serve consumers. NVIDIA's container proof included.
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run(
            "Model.gguf",
            completion={
                "model": "Model.gguf",
                "choices": [{"message": {"content": "", "reasoning_content": "thinking..."}}],
            },
        ))
        assert _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080", "CTX_SIZE": "65536"},
            model_id="model", gguf_file="Model.gguf", llm_model_name="model",
            attempts=1, initial_delay=0, interval=0,
        ) is False


class TestRuntimeReadiness:

    def test_readiness_reports_a_loading_runtime_without_probing_identity(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run(
            "Model.gguf",
            health={"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}},
            calls=calls,
        ))
        diagnosis: dict = {}
        assert _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "amd", "OLLAMA_PORT": "8080"},
            model_id="model", gguf_file="Model.gguf", llm_model_name="model",
            attempts=1, initial_delay=0, interval=0, diagnosis=diagnosis,
        ) is False
        urls = [next(str(part) for part in cmd if str(part).startswith("http")) for cmd, _ in calls]
        assert urls == ["http://127.0.0.1:8080/health"]
        assert diagnosis["reason"] == "llama-server is still loading the model"

    def test_amd_container_proves_health_identity_props_and_visible_completion(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run(
            "Model.gguf", n_ctx=65536, model_path="/models/Model.gguf", calls=calls,
        ))
        proof = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "amd", "OLLAMA_PORT": "8080", "CTX_SIZE": "65536"},
            model_id="model", gguf_file="Model.gguf", llm_model_name="model",
            attempts=1, initial_delay=0, interval=0, return_proof=True,
            require_exact_context=True,
        )
        assert proof["identity"] == "Model.gguf"
        assert proof["contextLength"] == 65536 and proof["contextVerified"] is True
        urls = [next(str(part) for part in cmd if str(part).startswith("http")) for cmd, _ in calls]
        assert urls == [
            "http://127.0.0.1:8080/health",
            "http://127.0.0.1:8080/v1/models",
            "http://127.0.0.1:8080/props",
            "http://127.0.0.1:8080/v1/chat/completions",
        ]
        completion = calls[-1][0]
        payload = json.loads(completion[completion.index("-d") + 1])
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        # No Lemonade route, id prefix or warm-up request remains.
        assert not any("/api/v1" in url for url in urls)
        assert all(kwargs.get("input") is None for _cmd, kwargs in calls)

    def test_readiness_rejects_a_server_launched_with_another_model_file(self, monkeypatch):
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run(
            "Model.gguf", n_ctx=65536, model_path=r"C:\models\Other.gguf",
        ))
        diagnosis: dict = {}
        assert _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080", "CTX_SIZE": "65536"},
            model_id="model", gguf_file="Model.gguf", llm_model_name="model",
            attempts=1, initial_delay=0, interval=0, diagnosis=diagnosis,
        ) is False
        assert diagnosis["reason"] == "Model.gguf is served from Other.gguf, not Model.gguf"

    def test_windows_native_readiness_sends_the_key_on_stdin_only(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run(
            "Model.gguf", n_ctx=65536, model_path=r"C:\ods\data\models\Model.gguf", calls=calls,
        ))
        key = "cd" * 32
        proof = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "amd", "AMD_INFERENCE_LOCATION": "host",
             "AMD_INFERENCE_RUNTIME": "llama-server", "AMD_INFERENCE_PORT": "18080",
             "CTX_SIZE": "65536", "LLAMA_SERVER_API_KEY": key},
            model_id="model", gguf_file="Model.gguf", llm_model_name="model",
            attempts=1, initial_delay=0, interval=0, return_proof=True,
        )
        assert proof["identity"] == "Model.gguf"
        for cmd, kwargs in calls:
            assert key not in " ".join(str(part) for part in cmd)
            assert "@-" in cmd
            assert kwargs["input"] == f"Authorization: Bearer {key}\n"
            assert next(str(p) for p in cmd if str(p).startswith("http")).startswith("http://127.0.0.1:18080/")

    def test_wsl_router_readiness_uses_owned_origin_bearer_key_and_visible_output(self, monkeypatch):
        monkeypatch.setattr(_mod._wsl_runtime.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
        monkeypatch.setattr(_mod.subprocess, "run", lambda *_a, **_k: pytest.fail(
            "WSL localhost is not Windows localhost; never probe it directly"
        ))
        requests: list = []
        key = "ef" * 32

        def transport(_install, origin, path, payload=None, api_key="", timeout=5):
            requests.append((origin, path, api_key))
            if path == "/health":
                return json.dumps({"status": "ok"})
            if path == "/v1/models":
                return _llama_identity_response("Qwen3.6-35B-A3B-Q4_K_M.gguf")
            if path == "/props":
                return json.dumps({"default_generation_settings": {"n_ctx": 65536},
                                   "model_path": r"C:\Users\me\AppData\Local\ODS\lemonade\models\Qwen3.6-35B-A3B-Q4_K_M.gguf"})
            assert payload["chat_template_kwargs"] == {"enable_thinking": False}
            return json.dumps({"model": "Qwen3.6-35B-A3B-Q4_K_M.gguf",
                               "choices": [{"message": {"content": "READY"}}]})

        monkeypatch.setattr(_mod, "_router_transport_request", transport)
        proof = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "cpu", "ODS_HOST_LLM_TRANSPORT": "model-router",
             "NATIVE_LLM_CONTAINER_BASE_URL": "http://host.docker.internal:18080",
             "CTX_SIZE": "65536", "LLAMA_SERVER_API_KEY": key},
            model_id="qwen3.6-35b-a3b", gguf_file="Qwen3.6-35B-A3B-Q4_K_M.gguf",
            llm_model_name="qwen3.6-35b-a3b", attempts=1, initial_delay=0, interval=0,
            return_proof=True, require_exact_context=True,
        )
        assert proof["identity"] == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
        assert proof["contextVerified"] is True
        assert [path for _origin, path, _key in requests] == [
            "/health", "/v1/models", "/props", "/v1/chat/completions",
        ]
        assert {origin for origin, _path, _key in requests} == {"http://host.docker.internal:18080"}
        assert {api_key for _origin, _path, api_key in requests} == {key}

    def test_catalog_non_agent_viability_overrides_context_floor(self):
        model = {
            "app_compatibility": {
                "agent_viability": {"status": "not_agent_viable"}
            }
        }
        assert _mod._model_agent_viable(model, 131072) is False
        assert _mod._model_agent_viable({}, 131072) is True
        assert _mod._model_agent_viable({}, 32768) is False

    @pytest.mark.parametrize(
        ("completion_model", "expected_identity"),
        [
            ("runtime/new-model.gguf", "runtime/new-model.gguf"),
            ("old-model.gguf", ""),
        ],
    )
    def test_readiness_returns_only_completion_verified_runtime_identity(
        self, monkeypatch, completion_model, expected_identity
    ):
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run(
            "runtime/new-model.gguf",
            completion={"model": completion_model, "choices": [{"message": {"content": "READY"}}]},
        ))

        identity = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"},
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_identity=True,
        )
        assert identity == expected_identity

    def test_readiness_proof_carries_actual_runtime_context(self, monkeypatch):
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run("runtime/new-model.gguf", n_ctx=65536))
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_a, **_k: True)
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "nvidia",
                "OLLAMA_PORT": "8080",
                "CTX_SIZE": "65536",
            },
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
        )
        assert proof["identity"] == "runtime/new-model.gguf"
        assert proof["contextLength"] == 65536
        assert proof["contextVerified"] is True
        assert proof["verifiedAt"].endswith("+00:00")

    def test_readiness_accepts_llama_context_alignment_padding(self, monkeypatch):
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run("runtime/new-model.gguf", n_ctx=20224))
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_a, **_k: True)
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "nvidia",
                "OLLAMA_PORT": "8080",
                "CTX_SIZE": "20000",
            },
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
            require_exact_context=True,
        )
        assert proof["contextLength"] == 20224
        assert proof["contextVerified"] is True

    def test_readiness_rejects_material_llama_context_drift(self, monkeypatch):
        completion_calls = []
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run("runtime/new-model.gguf", n_ctx=20256))
        monkeypatch.setattr(
            _mod,
            "_chat_completion_ready",
            lambda *_a, **_k: completion_calls.append(True) or True,
        )
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "nvidia",
                "OLLAMA_PORT": "8080",
                "CTX_SIZE": "20000",
            },
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
            require_exact_context=True,
        )
        assert proof == {}
        assert completion_calls == []

    def test_readiness_rejects_runtime_context_below_requested(self, monkeypatch):
        completion_calls = []
        monkeypatch.setattr(_mod.subprocess, "run", _llama_runtime_run("runtime/new-model.gguf", n_ctx=32768))
        monkeypatch.setattr(
            _mod,
            "_chat_completion_ready",
            lambda *_a, **_k: completion_calls.append(True) or True,
        )
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "nvidia",
                "OLLAMA_PORT": "8080",
                "CTX_SIZE": "65536",
            },
            model_id="new-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
        )
        assert proof == {}
        assert completion_calls == []

    @staticmethod
    def _capped_runtime(n_ctx_train, n_ctx, probes):
        """llama.cpp b9014 caps a slot at the GGUF training context."""

        def fake_run(cmd, **_kwargs):
            url = next((str(part) for part in cmd if str(part).startswith("http")), "")
            if url.endswith("/health"):
                body = json.dumps({"status": "ok"})
            elif url.endswith("/v1/models"):
                probes.append(url)
                body = json.dumps({
                    "object": "list",
                    "data": [{
                        "id": "Qwen3-30B-A3B-Q4_K_M.gguf",
                        "object": "model",
                        "meta": {"n_ctx_train": n_ctx_train},
                    }],
                })
            elif url.endswith("/props"):
                body = json.dumps({"default_generation_settings": {"n_ctx": n_ctx}})
            else:
                body = ""
            return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

        return fake_run

    @pytest.mark.parametrize("fast_poll_seconds", [0.0, 30.0])
    def test_readiness_fails_fast_when_request_exceeds_training_context(
        self, monkeypatch, fast_poll_seconds
    ):
        # Live tower2 2026-09-25: catalog asked for 131072 on a 40960-token
        # GGUF; the model loaded in 4 s but the host agent reported
        # identity=False for ~5.5 minutes, then rolled back.
        probes = []
        monkeypatch.setattr(_mod.subprocess, "run", self._capped_runtime(40960, 40960, probes))
        monkeypatch.setattr(_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_a, **_k: True)
        diagnosis = {}
        proof = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "11434", "CTX_SIZE": "131072"},
            model_id="qwen3-30b-a3b-q4",
            gguf_file="Qwen3-30B-A3B-Q4_K_M.gguf",
            llm_model_name="qwen3-30b-a3b",
            attempts=55,
            initial_delay=0,
            interval=0,
            return_proof=True,
            fast_poll_seconds=fast_poll_seconds,
            fast_poll_interval=0.05,
            diagnosis=diagnosis,
        )
        assert proof == {}
        assert len(probes) == 1
        assert diagnosis["final"] is True
        assert diagnosis["reason"] == (
            "Qwen3-30B-A3B-Q4_K_M.gguf is loaded but serves a 40960-token context; "
            "131072 was requested, above the model's 40960-token training context "
            "(llama.cpp caps the slot there)"
        )

    def test_readiness_keeps_polling_a_short_context_below_training_context(
        self, monkeypatch
    ):
        # A runtime short of the request for another reason (for example a
        # memory fit) is not proven final by the training context.
        probes = []
        monkeypatch.setattr(_mod.subprocess, "run", self._capped_runtime(262144, 32768, probes))
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_a, **_k: True)
        diagnosis = {}
        proof = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "11434", "CTX_SIZE": "65536"},
            model_id="qwen3-30b-a3b-q4",
            gguf_file="Qwen3-30B-A3B-Q4_K_M.gguf",
            llm_model_name="qwen3-30b-a3b",
            attempts=3,
            initial_delay=0,
            interval=0,
            return_identity=True,
            diagnosis=diagnosis,
        )
        assert proof == ""
        assert len(probes) == 3
        assert "final" not in diagnosis
        assert diagnosis["reason"] == (
            "Qwen3-30B-A3B-Q4_K_M.gguf is loaded but serves a 32768-token context; "
            "65536 was requested"
        )

    def test_readiness_succeeds_at_the_native_training_context(self, monkeypatch):
        probes = []
        monkeypatch.setattr(_mod.subprocess, "run", self._capped_runtime(40960, 40960, probes))
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_a, **_k: True)
        diagnosis = {}
        proof = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "11434", "CTX_SIZE": "40960"},
            model_id="qwen3-30b-a3b-q4",
            gguf_file="Qwen3-30B-A3B-Q4_K_M.gguf",
            llm_model_name="qwen3-30b-a3b",
            attempts=3,
            initial_delay=0,
            interval=0,
            return_proof=True,
            require_exact_context=True,
            diagnosis=diagnosis,
        )
        assert proof["identity"] == "Qwen3-30B-A3B-Q4_K_M.gguf"
        assert proof["contextLength"] == 40960
        assert diagnosis == {}


class TestRuntimeLogExcerpt:
    def test_excerpt_keeps_bounded_redacted_signal_lines(self):
        noise = [f"print_info: tensor {index} loaded" for index in range(400)]
        log = "\n".join(
            noise
            + [
                "\x1b[33mllama_context: n_ctx_seq (131072) > n_ctx_train (40960) -- possible training context overflow\x1b[0m",
                "srv    load_model: the slot context (131072) exceeds the training context of the model (40960) - capping",
                "main: invalid argument --api-key sk-live-secret-value",
                "error: Authorization: Bearer abc.def.ghi",
                "x" * 600 + " error",
                "main: server is listening on http://0.0.0.0:8080",
            ]
        )
        excerpt = _mod._runtime_log_excerpt(log)
        lines = excerpt.splitlines()
        assert len(lines) <= _mod._RUNTIME_LOG_EXCERPT_MAX_LINES
        assert len(excerpt) <= _mod._RUNTIME_LOG_EXCERPT_MAX_CHARS
        assert all(len(line) <= 240 for line in lines)
        assert "exceeds the training context of the model (40960) - capping" in excerpt
        assert "\x1b[" not in excerpt
        assert "sk-live-secret-value" not in excerpt
        assert "abc.def.ghi" not in excerpt
        assert excerpt.count("[REDACTED]") == 2
        assert "tensor 12 loaded" not in excerpt

    def test_excerpt_falls_back_to_the_log_tail_without_signal_lines(self):
        log = "\n".join(f"line {index}" for index in range(30))
        assert _mod._runtime_log_excerpt(log).splitlines() == [
            f"line {index}" for index in range(18, 30)
        ]
        assert _mod._runtime_log_excerpt(None) == ""

    def test_failed_container_log_read_never_raises(self, monkeypatch):
        def broken_run(*_args, **_kwargs):
            raise RuntimeError("docker unavailable")

        monkeypatch.setattr(_mod.subprocess, "run", broken_run)
        assert _mod._failed_llama_server_log_excerpt() == ""

        seen = []

        def logs_run(cmd, **kwargs):
            seen.append((cmd, kwargs.get("stderr")))
            return subprocess.CompletedProcess(cmd, 0, stdout="main: error loading model\n")

        monkeypatch.setattr(_mod.subprocess, "run", logs_run)
        assert _mod._failed_llama_server_log_excerpt() == "main: error loading model"
        assert seen == [(["docker", "logs", "--tail", "400", "ods-llama-server"], subprocess.STDOUT)]


# --- host-native LiteLLM route ---


class TestHostNativeLiteLLMConfig:

    def _env(self, **extra):
        env = {
            "GPU_BACKEND": "amd",
            "AMD_INFERENCE_LOCATION": "host",
            "AMD_INFERENCE_RUNTIME": "llama-server",
            "AMD_INFERENCE_PORT": "18080",
            "ODS_MODE": "local",
            "LITELLM_KEY": "sk-gateway",
        }
        env.update(extra)
        return env

    def test_windows_native_route_reads_the_key_by_name(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        key = "ab" * 32
        _write_host_native_litellm_config(self._env(LLAMA_SERVER_API_KEY=key), "Model.gguf", "model")

        content = (tmp_path / "config" / "litellm" / "local.yaml").read_text(encoding="utf-8")
        assert "model: openai/Model.gguf" in content
        assert "api_base: http://host.docker.internal:18080/v1" in content
        assert "api_key: os.environ/LLAMA_SERVER_API_KEY" in content
        assert key not in content
        assert "enable_thinking: false" in content
        assert "/api/v1" not in content and "extra." not in content

    def test_unkeyed_native_route_keeps_the_placeholder_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        _write_host_native_litellm_config(self._env(), "Model.gguf", "model")

        content = (tmp_path / "config" / "litellm" / "local.yaml").read_text(encoding="utf-8")
        assert "api_key: not-needed" in content
        assert "LLAMA_SERVER_API_KEY" not in content

    def test_wsl_bridge_route_uses_the_configured_container_origin(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod._wsl_runtime.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
        _write_host_native_litellm_config({
            "GPU_BACKEND": "cpu",
            "ODS_HOST_LLM_TRANSPORT": "model-router",
            "NATIVE_LLM_CONTAINER_BASE_URL": "http://host.docker.internal:28080",
            "LLAMA_SERVER_API_KEY": "ab" * 32,
        }, "Model.gguf", "model")

        content = (tmp_path / "config" / "litellm" / "local.yaml").read_text(encoding="utf-8")
        assert "api_base: http://host.docker.internal:28080/v1" in content
        assert "api_key: os.environ/LLAMA_SERVER_API_KEY" in content

    def test_renderer_failure_preserves_existing_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        litellm_dir = tmp_path / "config" / "litellm"
        litellm_dir.mkdir(parents=True)
        config = litellm_dir / "local.yaml"
        config.write_text("known-good\n", encoding="utf-8")
        monkeypatch.setattr(_mod, "_render_runtime_config", lambda *args, **kwargs: False)

        with pytest.raises(RuntimeError, match="host-native LiteLLM route"):
            _write_host_native_litellm_config(self._env(), "Model.gguf", "model")

        assert config.read_text(encoding="utf-8") == "known-good\n"


class TestSwitchboardRuntimeConfig:
    def test_render_runtime_config_passes_switchboard_and_runtime_args(
        self, monkeypatch, tmp_path,
    ):
        renderer = tmp_path / "scripts" / "render-runtime-configs.py"
        renderer.parent.mkdir(parents=True, exist_ok=True)
        renderer.write_text("# renderer placeholder\n", encoding="utf-8")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._render_runtime_config(
            tmp_path,
            "litellm-switchboard",
            model="qwen",
            gguf_file="Qwen.gguf",
            litellm_key="sk-runtime",
            llm_base_url="http://runtime:8080/v1",
            llm_api_key_env="LLAMA_SERVER_API_KEY",
            ods_mode="hybrid",
            gpu_backend="nvidia",
            context_length=65536,
            switchboard_mode="enabled",
        ) is True

        cmd, options = calls[0]
        assert cmd[cmd.index("--switchboard-mode") + 1] == "enabled"
        assert cmd[cmd.index("--model") + 1] == "qwen"
        assert cmd[cmd.index("--llm-base-url") + 1] == "http://runtime:8080/v1"
        assert cmd[cmd.index("--llm-api-key-env") + 1] == "LLAMA_SERVER_API_KEY"
        assert cmd[cmd.index("--context-length") + 1] == "65536"
        assert options["env"]["ODS_RENDER_LITELLM_KEY"] == "sk-runtime"
        assert "sk-runtime" not in cmd
        # Renderer and caller flags changed together: no retired Lemonade flag.
        assert not any(str(part).startswith("--lemonade") for part in cmd)

    def test_legacy_lemonade_mode_renders_as_local(self, monkeypatch, tmp_path):
        renderer = tmp_path / "scripts" / "render-runtime-configs.py"
        renderer.parent.mkdir(parents=True, exist_ok=True)
        renderer.write_text("# renderer placeholder\n", encoding="utf-8")
        calls: list = []
        monkeypatch.setattr(_mod.subprocess, "run", lambda cmd, **kwargs: (
            calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        ))
        assert _mod._render_runtime_config(
            tmp_path, "model-router-endpoints", gguf_file="Qwen.gguf", litellm_key="",
            ods_mode="lemonade", gpu_backend="amd",
        ) is True
        assert calls[0][calls[0].index("--ods-mode") + 1] == "local"

    def test_enabled_mode_renderer_failure_aborts(self, monkeypatch, tmp_path):
        calls = []

        def fake_render(_install_dir, surface, **_kwargs):
            calls.append(surface)
            return False

        monkeypatch.setattr(_mod, "_render_runtime_config", fake_render)

        with pytest.raises(RuntimeError, match="model-router-endpoints"):
            _mod._render_model_router_runtime_configs(
                tmp_path,
                {"ODS_MODEL_SWITCHBOARD": "enabled"},
                model="qwen",
                gguf_file="Qwen.gguf",
                context_length=65536,
            )

        assert calls == ["model-router-endpoints"]

    def test_router_endpoint_renders_host_native_origin_and_key_name(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        _mod._render_model_router_runtime_configs(
            tmp_path,
            {"ODS_MODEL_SWITCHBOARD": "enabled", "GPU_BACKEND": "amd",
             "AMD_INFERENCE_LOCATION": "host", "AMD_INFERENCE_RUNTIME": "llama-server",
             "AMD_INFERENCE_PORT": "18080", "LLAMA_SERVER_API_KEY": "ab" * 32},
            model="qwen",
            gguf_file="Qwen.gguf",
            context_length=65536,
        )
        endpoints = json.loads(
            (tmp_path / "config" / "model-router" / "endpoints.json").read_text(encoding="utf-8")
        )
        assert endpoints == {"endpoints": [{
            "id": "llama-server-default",
            "baseUrl": "http://host.docker.internal:18080",
            "apiKeyEnv": "LLAMA_SERVER_API_KEY",
        }]}

    def test_router_runtime_base_never_points_to_litellm(self):
        assert _mod._runtime_llama_api_base({
            "LLM_API_URL": "http://litellm:4000/v1",
        }) == "http://llama-server:8080/v1"

    def test_windows_native_runtime_base_uses_host_gateway(self, monkeypatch):
        monkeypatch.setattr(_mod, "_is_windows_host_llama_server", lambda _env: True)

        assert _mod._runtime_llama_api_base({
            "AMD_INFERENCE_PORT": "9234",
            "LLM_API_URL": "http://litellm:4000/v1",
        }) == "http://host.docker.internal:9234/v1"

    @pytest.mark.parametrize(
        ("container_base", "expected"),
        [
            ("http://host.docker.internal:18080", "http://host.docker.internal:18080/v1"),
            ("http://host.docker.internal:18080/v1", "http://host.docker.internal:18080/v1"),
            ("http://host.docker.internal:18080/arbitrary", "http://host.docker.internal:9234/v1"),
            ("http://host.docker.internal:18080/v1?debug=1", "http://host.docker.internal:9234/v1"),
        ],
    )
    def test_wsl_bridge_runtime_base_uses_a_safe_container_origin(
        self, monkeypatch, container_base, expected,
    ):
        monkeypatch.setattr(_mod._wsl_runtime.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
        assert _mod._runtime_llama_api_base({
            "ODS_HOST_LLM_TRANSPORT": "model-router",
            "NATIVE_LLM_CONTAINER_BASE_URL": container_base,
            "AMD_INFERENCE_PORT": "9234",
        }) == expected


class TestOpenCodeModelRoute:
    @pytest.mark.parametrize("available,expected", [
        ({"pwsh.exe": "C:/PowerShell 7/pwsh.exe", "pwsh": "other"}, "C:/PowerShell 7/pwsh.exe"),
        ({"pwsh": "C:/PowerShell/pwsh"}, "C:/PowerShell/pwsh"),
        ({}, "powershell.exe"),
    ])
    @pytest.mark.parametrize("action,output", [("inspect", "false"), ("restart", "true")])
    def test_windows_control_selects_available_shell_before_one_hidden_execution(self, monkeypatch, available, expected, action, output):
        calls = []
        monkeypatch.setattr(_mod.shutil, "which", lambda name: available.get(name))
        monkeypatch.setattr(_mod, "_opencode_port", lambda: 3456)
        monkeypatch.setattr(_mod.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout=output + "\n", stderr="")
        monkeypatch.setattr(_mod.subprocess, "run", run)
        assert _mod._run_windows_opencode_control(action) is (output == "true")
        assert len(calls) == 1
        command, options = calls[0]
        assert command[0] == expected
        assert "-NonInteractive" in command
        assert options["creationflags"] == 0x08000000
        assert options["env"]["ODS_OPENCODE_ACTION"] == action
        assert options["env"]["ODS_OPENCODE_PORT"] == "3456"
        assert "-WindowStyle Hidden" in command[-1]

    @pytest.mark.parametrize("failure", ["exit", "timeout", "missing", "invalid"])
    def test_windows_control_does_not_replay_failed_or_ambiguous_restart(self, monkeypatch, failure):
        calls = []
        monkeypatch.setattr(_mod, "_windows_management_shell", lambda: "selected-pwsh.exe")
        monkeypatch.setattr(_mod, "_opencode_port", lambda: 3003)
        def run(command, **kwargs):
            calls.append(command)
            if failure == "timeout": raise subprocess.TimeoutExpired(command, 90)
            if failure == "missing": raise FileNotFoundError("selected shell disappeared")
            return subprocess.CompletedProcess(command, 1 if failure == "exit" else 0,
                stdout="unknown", stderr="S\x00y\x00s\x00t\x00e\x00m\x00 access denied" if failure == "exit" else "")
        monkeypatch.setattr(_mod.subprocess, "run", run)
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired, FileNotFoundError)) as caught:
            _mod._run_windows_opencode_control("restart")
        assert len(calls) == 1
        if failure == "exit":
            assert "System access denied" in str(caught.value)
            assert "\x00" not in str(caught.value)

    def test_amd_container_uses_the_direct_llama_route(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        assert _mod._opencode_route({
            "GPU_BACKEND": "amd",
            "OLLAMA_PORT": "8091",
            "LITELLM_PORT": "4400",
            "LITELLM_KEY": "secret-key",
        }) == ("http://127.0.0.1:8091/v1", "no-key")

    def test_wsl_bridge_uses_the_authenticated_host_litellm_route(self, monkeypatch):
        monkeypatch.setattr(_mod._wsl_runtime.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod._wsl_runtime.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
        # WSL localhost is not Windows localhost: OpenCode goes through LiteLLM.
        assert _mod._opencode_route({
            "GPU_BACKEND": "cpu",
            "ODS_HOST_LLM_TRANSPORT": "model-router",
            "LITELLM_PORT": "4400",
            "LITELLM_KEY": "secret-key",
        }) == ("http://127.0.0.1:4400/v1", "secret-key")

    def test_keyed_windows_native_server_is_reached_through_litellm(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        env = {
            "GPU_BACKEND": "amd",
            "AMD_INFERENCE_RUNTIME": "llama-server",
            "AMD_INFERENCE_LOCATION": "host",
            "AMD_INFERENCE_PORT": "8090",
            "LITELLM_PORT": "4400",
            "LITELLM_KEY": "secret-key",
        }
        assert _mod._opencode_route(env) == ("http://127.0.0.1:8090/v1", "no-key")
        env["LLAMA_SERVER_API_KEY"] = "ab" * 32
        assert _mod._opencode_route(env) == ("http://127.0.0.1:4400/v1", "secret-key")

    def test_linux_managed_service_is_restarted_with_user_bus(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod.os, "getuid", lambda: 1001, raising=False)
        # _opencode_user_service_env() honors an ambient session bus via
        # setdefault; clear the host's values so the derived uid path is tested.
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda: None)

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        state = {
            "system": "Linux",
            "active": True,
            "env": _mod._opencode_user_service_env(),
        }
        assert _mod._restart_managed_opencode(state) is True
        assert [call[0] for call in calls] == [
            ["systemctl", "--user", "restart", "opencode-web.service"],
        ]
        assert calls[0][1]["env"]["XDG_RUNTIME_DIR"] == "/run/user/1001"
        assert calls[0][1]["env"]["DBUS_SESSION_BUS_ADDRESS"] == (
            "unix:path=/run/user/1001/bus"
        )

    def test_windows_inactive_runtime_is_not_force_restarted(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("Windows OpenCode is not ODS-managed"),
        )

        assert _mod._restart_managed_opencode({
            "system": "Windows",
            "active": False,
        }) is False

    def test_windows_active_runtime_is_restarted_and_proved(self, monkeypatch):
        actions = []
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            _mod,
            "_run_windows_opencode_control",
            lambda action: actions.append(action) or True,
        )
        monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda: actions.append("health"))

        assert _mod._restart_managed_opencode({
            "system": "Windows",
            "active": True,
        }) is True
        assert actions == ["restart", "health"]


class TestPerplexicaModelRoute:
    @staticmethod
    def _apply_config_post(current, payload):
        target = current
        parts = payload["key"].split(".")
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[parts[-1]] = json.loads(json.dumps(payload["value"]))


    @staticmethod
    def _snapshot():
        return {
            "url": "http://127.0.0.1:3004/api/config",
            "values": {
                "modelProviders": [{
                    "id": "openai-provider",
                    "type": "openai",
                    "chatModels": [{"key": "old-model", "name": "old-model"}],
                    "config": {"baseURL": "http://old/v1", "apiKey": "old-key"},
                }, {
                    "id": "transformers-provider", "type": "transformers",
                    "embeddingModels": [{"key": "built-in"}] * 27,
                }],
                "preferences": {
                    "defaultChatModel": "old-model",
                    "defaultChatProvider": "openai-provider",
                },
            },
        }

    def test_keyed_host_native_server_uses_the_gguf_through_litellm(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        model, base_url, api_key = _mod._perplexica_model_route(
            {
                "GPU_BACKEND": "amd",
                "AMD_INFERENCE_RUNTIME": "llama-server",
                "AMD_INFERENCE_LOCATION": "host",
                "LLAMA_SERVER_API_KEY": "ab" * 32,
                "HERMES_LLM_BASE_URL": "http://litellm:4000/v1",
                "LITELLM_KEY": "secret-key",
            },
            "Modern-Model.gguf",
        )

        assert model == "Modern-Model.gguf"
        assert base_url == "http://litellm:4000/v1"
        assert api_key == "secret-key"

    def test_amd_container_uses_the_gguf_directly(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        model, base_url, api_key = _mod._perplexica_model_route(
            {
                "ODS_MODE": "lemonade",
                "GPU_BACKEND": "amd",
                "LLM_BACKEND": "lemonade",
                "LLM_API_URL": "http://llama-server:8080",
                "LEMONADE_MODEL": "extra.Modern-Model.gguf",
                "LITELLM_KEY": "secret-key",
            },
            "Modern-Model.gguf",
        )

        assert model == "Modern-Model.gguf"
        assert base_url == "http://llama-server:8080/v1"

    def test_switchboard_mode_uses_stable_alias_through_litellm(self):
        model, base_url, api_key = _mod._perplexica_model_route(
            {
                "ODS_MODEL_SWITCHBOARD": "enabled",
                "GPU_BACKEND": "amd",
                "LITELLM_KEY": "secret-key",
            },
            "Modern-Model.gguf",
        )

        assert model == "ods/current"
        assert base_url == "http://litellm:4000/v1"
        assert api_key == "secret-key"

    def test_update_persists_and_verifies_model_route(self, monkeypatch):
        snapshot = self._snapshot()
        current = json.loads(json.dumps(snapshot["values"]))
        posts = []

        def fake_http(_url, payload=None):
            if payload is None:
                return {"values": json.loads(json.dumps(current))}
            posts.append(payload)
            self._apply_config_post(current, payload)
            return {}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)

        _mod._update_perplexica_model(
            {
                "LLM_API_URL": "http://llama-server:8080",
                "LITELLM_KEY": "no-key",
            },
            snapshot,
            gguf_file="new-model.gguf",
        )

        assert [post["key"] for post in posts] == [
            "modelProviders.0.chatModels", "modelProviders.0.config", "preferences",
        ]
        assert current["preferences"]["defaultChatModel"] == "new-model.gguf"
        provider = current["modelProviders"][0]
        assert provider["chatModels"] == [{"key": "new-model.gguf", "name": "new-model.gguf"}]
        assert provider["config"]["baseURL"] == "http://llama-server:8080/v1"
        assert current["modelProviders"][1] == snapshot["values"]["modelProviders"][1]

    def test_restore_reinstates_and_verifies_snapshot(self, monkeypatch):
        snapshot = self._snapshot()
        current = json.loads(json.dumps(snapshot["values"]))
        current["modelProviders"][0]["chatModels"] = [{"key": "wrong", "name": "wrong"}]
        current["preferences"]["defaultChatModel"] = "wrong"

        def fake_http(_url, payload=None):
            if payload is None:
                return {"values": json.loads(json.dumps(current))}
            self._apply_config_post(current, payload)
            return {}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)

        _mod._restore_perplexica_config(snapshot)

        assert current == snapshot["values"]

    def test_restore_accepts_perplexica_normalized_snapshot(self, monkeypatch):
        snapshot = self._snapshot()
        current = json.loads(json.dumps(snapshot["values"]))
        current["modelProviders"][0]["chatModels"] = [{"key": "wrong", "name": "wrong"}]
        current["preferences"]["defaultChatModel"] = "wrong"

        def fake_http(_url, payload=None):
            if payload is None:
                restored = json.loads(json.dumps(current))
                restored["preferences"]["theme"] = "system"
                restored["modelProviders"][0]["config"]["label"] = "OpenAI"
                restored["modelProviders"][0]["chatModels"].append({
                    "key": "extra-model",
                    "name": "extra-model",
                })
                return {"values": restored}
            self._apply_config_post(current, payload)
            return {}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)

        _mod._restore_perplexica_config(snapshot)

        assert current == snapshot["values"]

    def test_restore_changes_openai_when_owner_default_was_custom(self, monkeypatch):
        snapshot = self._snapshot()
        snapshot["values"]["modelProviders"].append({
            "id": "owner-chat", "type": "custom", "chatModels": [{"key": "owner-model"}],
            "config": {"owner": True},
        })
        snapshot["values"]["preferences"].update({
            "defaultChatProvider": "owner-chat", "defaultChatModel": "owner-model",
        })
        current = json.loads(json.dumps(snapshot["values"]))
        current["modelProviders"][0]["chatModels"] = [{"key": "new-model", "name": "new-model"}]
        current["modelProviders"][0]["config"] = {
            "baseURL": "http://new/v1", "apiKey": "new-key",
        }

        current["preferences"].update({
            "defaultChatProvider": "openai-provider", "defaultChatModel": "new-model",
        })
        posts = []

        def fake_http(_url, payload=None):
            if payload is None:
                return {"values": json.loads(json.dumps(current))}
            posts.append(payload)
            self._apply_config_post(current, payload)
            return {}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)

        _mod._restore_perplexica_config(snapshot)

        assert current == snapshot["values"]
        assert [post["key"] for post in posts] == [
            "modelProviders.0.chatModels", "modelProviders.0.config", "preferences",
        ]

    def test_official_openai_catalog_is_not_taken_as_restorable_route(self, monkeypatch):
        snapshot = self._snapshot()
        snapshot["values"]["modelProviders"][0]["config"]["baseURL"] = "https://api.openai.com/v1"
        posts = []

        def fake_http(_url, payload=None):
            if payload is not None:
                posts.append(payload)
            return {"values": json.loads(json.dumps(snapshot["values"]))}

        monkeypatch.setattr(_mod, "_perplexica_http_json", fake_http)
        assert _mod._capture_perplexica_config(
            {}, {"exists": True, "running": True},
        ) is None
        with pytest.raises(RuntimeError, match="hydrated OpenAI catalog"):
            _mod._restore_perplexica_config(snapshot)
        assert posts == []


class TestDownstreamRouteVerification:

    def test_llama_readiness_probes_the_v1_contract_with_thinking_disabled(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            url = next((str(part) for part in cmd if str(part).startswith("http")), "")
            body: dict
            if url.endswith("/health"):
                body = {"status": "ok"}
            elif url.endswith("/v1/models"):
                body = {"data": [{"id": "portable.gguf", "status": "loaded"}]}
            elif url.endswith("/props"):
                body = {"default_generation_settings": {"n_ctx": 8192}}
            else:
                body = {
                    "model": "portable.gguf",
                    "choices": [{"message": {"content": "READY"}}],
                }
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(body), stderr=""
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        proof = _mod._wait_for_model_readiness(
            {
                "GPU_BACKEND": "nvidia",
                "GGUF_FILE": "portable.gguf",
                "LLM_MODEL": "portable",
                "CTX_SIZE": "8192",
                "OLLAMA_PORT": "8080",
            },
            model_id="portable",
            gguf_file="portable.gguf",
            llm_model_name="portable",
            attempts=1,
            initial_delay=0,
            interval=0,
            return_proof=True,
        )

        assert proof["identity"] == "portable.gguf"
        urls = [
            next((str(part) for part in cmd if str(part).startswith("http")), "")
            for cmd, _kwargs in calls
        ]
        assert urls == [
            "http://127.0.0.1:8080/health",
            "http://127.0.0.1:8080/v1/models",
            "http://127.0.0.1:8080/props",
            "http://127.0.0.1:8080/v1/chat/completions",
        ]
        payload = json.loads(calls[-1][0][calls[-1][0].index("-d") + 1])
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["max_tokens"] == 64

    def test_completion_probe_sends_bearer_key_when_requested(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"choices": [{"message": {"content": "READY"}}]}),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._chat_completion_ready(
            "127.0.0.1",
            "4000",
            "default",
            "/v1",
            "secret",
        )
        command, kwargs = calls[0]
        assert "Authorization: Bearer secret" not in command
        assert "secret" not in " ".join(command)
        assert ["-H", "@-"] == command[command.index("@-") - 1:command.index("@-") + 1]
        assert kwargs["input"] == "Authorization: Bearer secret\n"

    def test_completion_probe_requires_response_model_when_identity_expected(
        self, monkeypatch
    ):
        def fake_run(_cmd, **_kwargs):
            return subprocess.CompletedProcess(
                _cmd,
                0,
                stdout=json.dumps({
                    "model": "old-model.gguf",
                    "choices": [{"message": {"content": "READY"}}],
                }),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert not _mod._chat_completion_ready(
            "127.0.0.1",
            "8080",
            "new-model.gguf",
            expected_gguf_file="new-model.gguf",
        )

    def test_completion_probe_accepts_matching_runtime_identity(self, monkeypatch):
        def fake_run(_cmd, **_kwargs):
            return subprocess.CompletedProcess(
                _cmd,
                0,
                stdout=json.dumps({
                    "model": "runtime/new-model.gguf",
                    "choices": [{"message": {"content": "READY"}}],
                }),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._chat_completion_ready(
            "127.0.0.1",
            "8080",
            "new-model.gguf",
            expected_gguf_file="new-model.gguf",
        )

    def test_litellm_probe_uses_default_route_and_master_key(self, monkeypatch):
        calls = []
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_chat_completion_ready",
            lambda *args: calls.append(args) or True,
        )

        _mod._verify_litellm_route({"LITELLM_PORT": "4100", "LITELLM_KEY": "secret"})

        assert calls == [("127.0.0.1", "4100", "default", "/v1", "secret")]


class TestPatchHermesModelConfig:

    def test_updates_model_default_only(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(
            "# example\n"
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://llama-server:8080/v1\"\n"
            "other:\n"
            "  default: \"leave-me\"\n",
            encoding="utf-8",
        )

        assert _patch_hermes_model_config(config, "new-model.gguf") is True

        text = config.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in text
        assert '  provider: "custom"' in text
        assert '  default: "leave-me"' in text

    def test_updates_context_and_base_url(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://host.docker.internal:8080/v1\"\n"
            "  context_length: 32768\n"
            "auxiliary:\n"
            "  compression:\n"
            "    context_length: 32768\n",
            encoding="utf-8",
        )

        assert _patch_hermes_model_config(
            config,
            "new-model.gguf",
            base_url="http://llama-server:8080/v1",
            context_length=131072,
        ) is True

        text = config.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in text
        assert '  base_url: "http://llama-server:8080/v1"' in text
        assert "  context_length: 131072" in text
        assert "    context_length: 131072" in text

    def test_inserts_missing_required_route_fields(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(
            "model:\n"
            '  provider: "custom"\n'
            "providers:\n"
            "  custom: {}\n",
            encoding="utf-8",
        )

        assert _patch_hermes_model_config(
            config,
            "new-model.gguf",
            base_url="http://litellm:4000/v1",
            context_length=65536,
        ) is True

        text = config.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in text
        assert '  base_url: "http://litellm:4000/v1"' in text
        assert "  context_length: 65536" in text
        assert _mod._hermes_config_matches(
            text,
            "new-model.gguf",
            "http://litellm:4000/v1",
            65536,
        )

    @pytest.mark.parametrize(
        "text",
        [
            'model:\n  base_url: "http://litellm:4000/v1"\n  context_length: 4096\n',
            'model:\n  default: "model"\n  context_length: 4096\n',
            'model:\n  default: "model"\n  base_url: "http://litellm:4000/v1"\n',
        ],
    )
    def test_route_verification_rejects_missing_required_fields(self, text):
        assert not _mod._hermes_config_matches(
            text,
            "model",
            "http://litellm:4000/v1",
            4096,
        )

    def test_missing_file_is_noop(self, tmp_path):
        assert _patch_hermes_model_config(tmp_path / "missing.yaml", "model.gguf") is False

    def test_permission_denied_stat_is_noop(self, tmp_path, monkeypatch):
        config = tmp_path / "config.yaml"
        original_exists = Path.exists

        def fake_exists(path):
            if path == config:
                raise PermissionError("container-owned")
            return original_exists(path)

        monkeypatch.setattr(Path, "exists", fake_exists)
        assert _patch_hermes_model_config(config, "model.gguf") is False


class TestComposeRestartLlamaServer:

    @pytest.mark.parametrize("backend", ["amd", "nvidia"])
    def test_force_recreates_without_strict_stop(self, backend, monkeypatch, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(
            _mod,
            "resolve_compose_flags",
            lambda: ["--env-file", ".env", "-f", "docker-compose.base.yml"],
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        _compose_restart_llama_server({"GPU_BACKEND": backend})

        assert calls == [
            [
                "docker", "compose", "--env-file", ".env", "-f",
                "docker-compose.base.yml", "up", "-d", "--force-recreate",
                "--no-deps", "llama-server",
            ],
        ]

    def test_amd_without_compose_flags_recreates_to_apply_rocm_visibility(
        self,
        monkeypatch,
        tmp_path,
    ):
        recreated = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod, "resolve_compose_flags", lambda: [])
        monkeypatch.setattr(
            _mod,
            "_recreate_llama_server",
            lambda env: recreated.append(dict(env)),
        )

        _compose_restart_llama_server(
            {
                "GPU_BACKEND": "amd",
                "ROCR_VISIBLE_DEVICES": "0,1,2",
            }
        )

        assert recreated == [
            {
                "GPU_BACKEND": "amd",
                "ROCR_VISIBLE_DEVICES": "0,1,2",
            }
        ]


class TestRecreateLlamaServerFromInspect:

    def _capture_recreate(self, monkeypatch, inspect_config, env, override_image=""):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd == ["docker", "inspect", "ods-llama-server"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps([inspect_config]),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        _mod._recreate_llama_server(env, override_image=override_image)
        run_argv = next(cmd for cmd in calls if cmd[:3] == ["docker", "run", "-d"])
        return run_argv, calls

    def test_amd_recreate_preserves_devices_groups_entrypoint_and_runtime(
        self, monkeypatch,
    ):
        image = (
            "ghcr.io/ggml-org/llama.cpp:server-vulkan-b9014@sha256:"
            "15c30b560d61ead1e08bee837503203a776fd968736118e313240c32157fd973"
        )
        inspect_config = {
            "Config": {
                "Image": image,
                "Entrypoint": ["/app/llama-server"],
                "Cmd": [
                    "--model", "/models/old.gguf", "--alias", "old.gguf",
                    "--host", "0.0.0.0", "--port", "8080",
                    "--ctx-size", "4096", "--parallel", "1", "--metrics",
                ],
                "Env": [
                    "PATH=/usr/bin",
                    "GGUF_FILE=old.gguf",
                    "CTX_SIZE=4096",
                    "MAX_CONTEXT=4096",
                    "ROCR_VISIBLE_DEVICES=0,1",
                    "LLAMA_SERVER_GPU_INDICES=0,1",
                    "HSA_OVERRIDE_GFX_VERSION=11.5.1",
                ],
                "Labels": {"com.docker.compose.service": "llama-server"},
                "Hostname": "llama-amd",
                "ExposedPorts": {"8080/tcp": {}},
                "Healthcheck": {
                    "Test": ["CMD", "curl", "-sf", "http://127.0.0.1:8080/health"],
                    "Interval": 15000000000,
                    "Timeout": 10000000000,
                    "Retries": 10,
                },
            },
            "HostConfig": {
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "NetworkMode": "ods-network",
                "PortBindings": {
                    "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
                },
                "Binds": ["/srv/ods/models:/models:ro"],
                "Devices": [
                    {
                        "PathOnHost": "/dev/dri",
                        "PathInContainer": "/dev/dri",
                        "CgroupPermissions": "rwm",
                    },
                    {
                        "PathOnHost": "/dev/kfd",
                        "PathInContainer": "/dev/kfd",
                        "CgroupPermissions": "rwm",
                    },
                ],
                "GroupAdd": ["44", "109"],
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "Runtime": "runc",
                "ReadonlyRootfs": True,
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m"}},
            },
            "NetworkSettings": {
                "Networks": {
                    "ods-network": {"Aliases": ["ods-llama-server", "llama-server"]},
                },
            },
            "Mounts": [],
        }
        env = {
            "GGUF_FILE": "new-amd.gguf",
            "CTX_SIZE": "65536",
            "MAX_CONTEXT": "65536",
            "LLM_MODEL": "new-amd",
            "GPU_BACKEND": "amd",
            "ROCR_VISIBLE_DEVICES": "0,1,2",
            "LLAMA_SERVER_GPU_INDICES": "0,1,2",
        }

        argv, calls = self._capture_recreate(monkeypatch, inspect_config, env)

        assert ["docker", "stop", "ods-llama-server"] in calls
        assert ["--device", "/dev/dri:/dev/dri:rwm"] == argv[
            argv.index("--device"):argv.index("--device") + 2
        ]
        second_device = argv.index("--device", argv.index("--device") + 1)
        assert argv[second_device:second_device + 2] == [
            "--device", "/dev/kfd:/dev/kfd:rwm",
        ]
        assert argv.count("--group-add") == 2
        assert "44" in argv and "109" in argv
        assert ["--runtime", "runc"] == argv[argv.index("--runtime"):argv.index("--runtime") + 2]
        assert "--read-only" in argv
        assert "--security-opt" in argv and "no-new-privileges:true" in argv
        assert argv[argv.index("--health-cmd"):argv.index("--health-cmd") + 2] == [
            "--health-cmd", "curl -sf http://127.0.0.1:8080/health",
        ]
        assert "/srv/ods/models:/models:ro" in argv
        assert "GGUF_FILE=new-amd.gguf" in argv
        assert "CTX_SIZE=65536" in argv
        assert "ROCR_VISIBLE_DEVICES=0,1,2" in argv
        assert "LLAMA_SERVER_GPU_INDICES=0,1,2" in argv
        # Vulkan (the default) reads no HSA variable; .env no longer names it.
        assert not any(arg.startswith("HSA_OVERRIDE_GFX_VERSION=") for arg in argv)
        # AMD keeps the overlay's pinned image; no AMD default spec type.
        image_index = argv.index(image)
        assert not any(arg.startswith("LLAMA_ARG_SPEC_TYPE=") for arg in argv)
        assert argv[argv.index("--entrypoint"):argv.index("--entrypoint") + 2] == [
            "--entrypoint", "/app/llama-server",
        ]
        # The model, its served alias and the context follow the new .env.
        assert argv[image_index + 1:] == [
            "--model", "/models/new-amd.gguf", "--alias", "new-amd.gguf",
            "--host", "0.0.0.0", "--port", "8080",
            "--ctx-size", "65536", "--parallel", "1", "--metrics",
        ]

    def test_amd_recreate_carries_layer_split_through_llama_env(
        self, monkeypatch,
    ):
        inspect_config = {
            "Config": {
                "Image": "ghcr.io/ggml-org/llama.cpp:server-vulkan-b9014",
                "Entrypoint": ["/app/llama-server"],
                "Cmd": ["--model", "/models/old.gguf", "--alias", "old.gguf", "--metrics"],
                "Env": [
                    "GPU_BACKEND=amd",
                    "LLAMA_ARG_SPLIT_MODE=row",
                    "LLAMA_ARG_TENSOR_SPLIT=3,1",
                    "ROCR_VISIBLE_DEVICES=0,1",
                ],
            },
            "HostConfig": {},
            "NetworkSettings": {"Networks": {}},
            "Mounts": [],
        }
        env = {
            "GPU_BACKEND": "amd",
            "GGUF_FILE": "new.gguf",
            "LLAMA_ARG_SPLIT_MODE": "layer",
            "LLAMA_ARG_TENSOR_SPLIT": "",
            "ROCR_VISIBLE_DEVICES": "0,1,2",
            "LLAMA_SERVER_GPU_INDICES": "0,1,2",
        }

        argv, _calls = self._capture_recreate(monkeypatch, inspect_config, env)

        # llama-server reads LLAMA_ARG_* itself; there is no passthrough arg.
        assert "--llamacpp-args" not in argv
        assert "LLAMA_ARG_SPLIT_MODE=layer" in argv
        assert "LLAMA_ARG_SPLIT_MODE=row" not in argv
        assert "LLAMA_ARG_TENSOR_SPLIT=" in argv
        assert "ROCR_VISIBLE_DEVICES=0,1,2" in argv
        image_index = argv.index("ghcr.io/ggml-org/llama.cpp:server-vulkan-b9014")
        assert argv[image_index + 1:] == [
            "--model", "/models/new.gguf", "--alias", "new.gguf", "--metrics",
        ]

    def test_nvidia_recreate_preserves_device_request_full_command_and_networks(
        self, monkeypatch,
    ):
        inspect_config = {
            "Config": {
                "Image": "host.example/llama:cuda",
                "Entrypoint": ["/app/llama-server", "--factory-mode"],
                "Cmd": [
                    "--model", "/models/old.gguf", "--ctx-size=4096",
                    "--parallel", "2", "--metrics",
                ],
                "Env": [
                    "GGUF_FILE=old.gguf",
                    "CTX_SIZE=4096",
                    "LLAMA_PARALLEL=2",
                    "NVIDIA_VISIBLE_DEVICES=GPU-ti-0,GPU-1080",
                    "LLAMA_ARG_TENSOR_SPLIT=0.5789,0.4211",
                ],
                "Labels": {"com.docker.compose.project": "ods"},
                "Hostname": "llama-nvidia",
            },
            "HostConfig": {
                "RestartPolicy": {"Name": "on-failure", "MaximumRetryCount": 3},
                "Binds": ["/srv/models:/models:ro"],
                "DeviceRequests": [{
                    "Driver": "nvidia",
                    "Count": 0,
                    "DeviceIDs": ["GPU-ti-0", "GPU-1080"],
                    "Capabilities": [["gpu"]],
                    "Options": {},
                }],
                "Runtime": "nvidia",
                "SecurityOpt": ["seccomp=/srv/seccomp.json"],
                "ShmSize": 1073741824,
            },
            "NetworkSettings": {
                "Networks": {
                    "ods-network": {"Aliases": ["llama-server"]},
                    "metrics-network": {"Aliases": ["llama-metrics"]},
                },
            },
            "Mounts": [],
        }
        env = {
            "GGUF_FILE": "new-nvidia.gguf",
            "CTX_SIZE": "32768",
            "MAX_CONTEXT": "32768",
            "LLAMA_PARALLEL": "1",
            "GPU_BACKEND": "nvidia",
            "LLAMA_SERVER_GPU_UUIDS": "GPU-ti-0,GPU-1080,GPU-ti-2",
            "LLAMA_ARG_SPLIT_MODE": "layer",
            "LLAMA_ARG_TENSOR_SPLIT": "",
        }

        argv, calls = self._capture_recreate(
            monkeypatch,
            inspect_config,
            env,
            override_image="catalog.example/llama:target",
        )

        assert argv[argv.index("--restart"):argv.index("--restart") + 2] == [
            "--restart", "on-failure:3",
        ]
        assert argv[argv.index("--gpus"):argv.index("--gpus") + 2] == ["--gpus", "all"]
        assert argv[argv.index("--runtime"):argv.index("--runtime") + 2] == [
            "--runtime", "nvidia",
        ]
        assert "NVIDIA_VISIBLE_DEVICES=GPU-ti-0,GPU-1080,GPU-ti-2" in argv
        assert "LLAMA_ARG_SPLIT_MODE=layer" in argv
        assert "LLAMA_ARG_TENSOR_SPLIT=" in argv
        assert "LLAMA_ARG_TENSOR_SPLIT=0.5789,0.4211" not in argv
        image_index = argv.index("catalog.example/llama:target")
        assert argv[image_index + 1:] == [
            "--factory-mode",
            "--model", "/models/new-nvidia.gguf",
            "--ctx-size=32768",
            "--parallel", "1",
            "--metrics",
        ]
        assert [
            "docker", "network", "connect", "--alias", "llama-metrics",
            "--alias", "llama-server", "metrics-network", "ods-llama-server",
        ] in calls

    @staticmethod
    def _spec_env_values(argv):
        return [
            argv[index + 1]
            for index, token in enumerate(argv[:-1])
            if token == "-e" and argv[index + 1].startswith("LLAMA_ARG_SPEC_TYPE=")
        ]

    @pytest.mark.parametrize(
        ("env_extra", "expected"),
        [
            ({"GPU_BACKEND": "nvidia"}, ["LLAMA_ARG_SPEC_TYPE=ngram-mod"]),
            ({"GPU_BACKEND": "jetson"}, ["LLAMA_ARG_SPEC_TYPE=ngram-mod"]),
            ({"GPU_BACKEND": "cpu"}, ["LLAMA_ARG_SPEC_TYPE=ngram-mod"]),
            ({"GPU_BACKEND": "nvidia", "LLAMA_SPEC_TYPE": ""}, ["LLAMA_ARG_SPEC_TYPE=ngram-mod"]),
            ({"GPU_BACKEND": "nvidia", "LLAMA_SPEC_TYPE": "none"}, ["LLAMA_ARG_SPEC_TYPE=none"]),
            ({"GPU_BACKEND": "cpu", "LLAMA_SPEC_TYPE": "ngram-simple"}, ["LLAMA_ARG_SPEC_TYPE=ngram-simple"]),
            (
                {"GPU_BACKEND": "nvidia", "LLAMA_SPEC_TYPE": "none", "LLAMA_ARG_SPEC_TYPE": "draft-mtp"},
                ["LLAMA_ARG_SPEC_TYPE=draft-mtp"],
            ),
            ({"GPU_BACKEND": "amd"}, []),
            ({"GPU_BACKEND": "sycl"}, []),
            ({"GPU_BACKEND": "apple"}, []),
        ],
    )
    def test_recreate_keeps_the_compose_overlay_speculative_default(
        self, monkeypatch, env_extra, expected,
    ):
        """A recreate drops inspected LLAMA_ARG_* values that .env does not
        name, so it must re-derive docker-compose.{nvidia,cpu}.yml's default
        instead of silently serving without speculation after a model switch."""
        inspect_config = {
            "Config": {
                "Image": "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014",
                "Cmd": ["--model", "/models/old.gguf", "--metrics"],
                "Env": [
                    "PATH=/usr/bin",
                    "GGUF_FILE=old.gguf",
                    # Resolved by Compose from the overlay default.
                    "LLAMA_ARG_SPEC_TYPE=ngram-mod",
                ],
            },
            "HostConfig": {"Binds": ["/srv/models:/models:ro"]},
            "NetworkSettings": {"Networks": {}},
            "Mounts": [],
        }
        env = {"GGUF_FILE": "new.gguf", "CTX_SIZE": "8192", "MAX_CONTEXT": "8192", **env_extra}

        argv, _calls = self._capture_recreate(monkeypatch, inspect_config, env)

        assert self._spec_env_values(argv) == expected


class TestLaunchNativeLlamaServer:

    def test_reads_env_and_writes_pid(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "GGUF_FILE=test-model.gguf\n"
            "CTX_SIZE=8192\n"
            "LLAMA_REASONING=on\n"
            "AMD_INFERENCE_PORT=9090\n",
            encoding="utf-8",
        )
        (tmp_path / "data" / "models").mkdir(parents=True)
        (tmp_path / "data").mkdir(exist_ok=True)
        llama_bin = tmp_path / "bin" / "llama-server"
        llama_bin.parent.mkdir(parents=True)
        llama_bin.write_text("", encoding="utf-8")
        llama_log = tmp_path / "data" / "llama-server.log"
        pid_file = tmp_path / "data" / ".llama-server.pid"

        calls = []

        class _FakeProc:
            pid = 4321

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return _FakeProc()

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        _launch_native_llama_server(env_path, llama_bin, llama_log, pid_file)

        assert pid_file.read_text(encoding="utf-8") == "4321"
        cmd, _kwargs = calls[0]
        assert cmd[0] == str(llama_bin)
        assert "--model" in cmd
        assert str(tmp_path / "data" / "models" / "test-model.gguf") in cmd
        assert cmd[cmd.index("--port") + 1] == "9090"
        assert "--ctx-size" in cmd
        assert "8192" in cmd
        assert "--reasoning-format" in cmd
        assert "deepseek" in cmd
        assert _kwargs["cwd"] == str(tmp_path)

    # Windows launches through ods.ps1 native-llm-restart, which binds loopback.
    @pytest.mark.parametrize('system', ['Darwin', 'Linux'])
    def test_ui_lan_preference_does_not_publish_native_inference(self, monkeypatch, tmp_path, system):
        env = {
            "GGUF_FILE": "test-model.gguf",
            "BIND_ADDRESS": "0.0.0.0",
            "ODS_MACOS_HOST_GATEWAY": "192.168.106.1",
        }
        events = []

        class _FakeProc:
            pid = 4321

        def fake_disable(actual_env, bind_addr, label):
            events.append(("bootout", actual_env, bind_addr, label))
            return True

        def fake_popen(cmd, **kwargs):
            events.append(("bind", cmd, kwargs))
            return _FakeProc()

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod, "load_env", lambda _path: env)
        monkeypatch.setattr(_mod.platform, "system", lambda: system)
        monkeypatch.setattr(_mod, "_disable_conflicting_macos_bridge", fake_disable)
        monkeypatch.setattr(_mod.subprocess, "Popen", fake_popen)

        _launch_native_llama_server(
            tmp_path / ".env",
            tmp_path / "bin" / "llama-server",
            tmp_path / "data" / "llama-server.log",
            tmp_path / "data" / ".llama-server.pid",
        )

        assert events[0] == (
            "bootout",
            env,
            "127.0.0.1",
            "com.ods.llm-bridge",
        )
        assert events[1][0] == "bind"
        assert events[1][1][events[1][1].index("--host") + 1] == "127.0.0.1"


class TestWindowsNativeLlamaServer:

    @pytest.mark.parametrize("mode", [
        "windows-native-llama-server",
        # Pre-round-F name of the same topology.
        "windows-llama-server-fallback",
    ])
    def test_detects_managed_windows_amd_llama_server(self, monkeypatch, mode):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")

        assert _is_windows_host_llama_server({
            "GPU_BACKEND": "amd",
            "AMD_INFERENCE_RUNTIME_MODE": mode,
            "AMD_INFERENCE_LOCATION": "host",
            "AMD_INFERENCE_MANAGED": "true",
        }) is True
        assert _is_windows_host_llama_server({
            "GPU_BACKEND": "amd",
            "LLM_BACKEND": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "AMD_INFERENCE_LOCATION": "host",
        }) is False

    @staticmethod
    def _native_install(monkeypatch, tmp_path):
        install_dir = tmp_path / "ODS Install"
        (install_dir / "data" / "models").mkdir(parents=True)
        (install_dir / "data" / "models" / "model.gguf").write_text("model", encoding="utf-8")
        (install_dir / "ods.ps1").write_text("# ods.ps1\n", encoding="utf-8")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        return install_dir

    def test_model_switch_relaunches_through_ods_native_llm_restart(self, monkeypatch, tmp_path):
        install_dir = self._native_install(monkeypatch, tmp_path)
        key = "9a" * 32
        calls: list = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="Native llama-server ready", stderr="")

        monkeypatch.setattr(_mod, "_windows_management_shell", lambda: "selected-pwsh.exe")
        monkeypatch.setattr(_mod.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod.subprocess, "Popen", lambda *_a, **_k: pytest.fail("the agent launched llama-server.exe"))

        _restart_windows_native_llama_server(install_dir / ".env", {
            "GGUF_FILE": "model.gguf",
            "AMD_INFERENCE_PORT": "9090",
            "LLAMA_SERVER_API_KEY": key,
        })

        # The Windows installer's entry point stops only the llama-server it
        # proves, relaunches from .env and proves the model; nothing else runs.
        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert cmd == [
            "selected-pwsh.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(install_dir / "ods.ps1"), "native-llm-restart", str(install_dir),
        ]
        assert kwargs["creationflags"] == 0x08000000
        assert kwargs["timeout"] > 900
        # The key stays in .env and ods.ps1's private key file.
        assert key not in " ".join(cmd)
        assert key not in json.dumps(kwargs.get("env") or {})
        assert not (install_dir / "data" / "llama-server.api-key").exists()

    def test_failed_restart_reports_its_reason_without_the_key(self, monkeypatch, tmp_path):
        install_dir = self._native_install(monkeypatch, tmp_path)
        key = "5c" * 32
        output = (
            "Starting native llama-server with model.gguf\n"
            "Native llama-server did not start: the model reported 4096 tokens of context, not 8192\n"
            f"sent {key}\n"
        )
        monkeypatch.setattr(_mod.subprocess, "run", lambda cmd, **_k: subprocess.CompletedProcess(
            cmd, 1, stdout=output, stderr="",
        ))

        with pytest.raises(RuntimeError, match="did not restart") as raised:
            _restart_windows_native_llama_server(install_dir / ".env", {
                "GGUF_FILE": "model.gguf", "LLAMA_SERVER_API_KEY": key,
            })

        assert "4096 tokens of context, not 8192" in str(raised.value)
        assert key not in str(raised.value)

    @pytest.mark.parametrize(("key", "missing", "reason"), [
        ("not a key\nX-Injected: 1", "", "64 hex characters"),
        ("", "", "64 hex characters"),
        # ods.ps1 refuses these too, but only after it stopped the server.
        ("9A" * 32, "", "64 hex characters"),
        ("9a" * 16, "", "64 hex characters"),
        ("9a" * 32, "ods.ps1", "ods.ps1 not found"),
        ("9a" * 32, "model", "Model file not ready"),
    ])
    def test_preconditions_are_refused_before_any_process_change(
        self, monkeypatch, tmp_path, key, missing, reason,
    ):
        install_dir = self._native_install(monkeypatch, tmp_path)
        if missing == "ods.ps1":
            (install_dir / "ods.ps1").unlink()
        elif missing == "model":
            (install_dir / "data" / "models" / "model.gguf").unlink()
        monkeypatch.setattr(_mod.subprocess, "run", lambda *_a, **_k: pytest.fail("no process may be stopped"))

        with pytest.raises(RuntimeError, match=reason):
            _restart_windows_native_llama_server(install_dir / ".env", {
                "GGUF_FILE": "model.gguf", "LLAMA_SERVER_API_KEY": key,
            })

    def test_windows_agent_launcher_detaches_from_host_agent(self):
        ods_root = Path(__file__).resolve().parents[4]
        source = (ods_root / "installers" / "windows" / "ods.ps1").read_text(
            encoding="utf-8",
        )
        marker = "Start-Process -FilePath $_pythonLiteral"
        start = source.index(marker)
        launcher_line = source[start:source.index("\n", start)]

        assert "-RedirectStandardError $_logFileLiteral" in launcher_line
        assert " -Wait" not in launcher_line


# --- Rollback integration ---


class _ResponseHandler:
    def __init__(self, wfile=None, request_body=None, api_key="test-agent-key"):
        self.wfile = wfile or io.BytesIO()
        self.response_code = None
        self.response_headers = []
        if request_body is not None:
            payload = json.dumps(request_body).encode("utf-8")
            self.rfile = io.BytesIO(payload)
            self.headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Length": str(len(payload)),
            }

    def send_response(self, code):
        self.response_code = code

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def parse_response(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class TestModelActivateRequest:
    def test_validates_and_forwards_cli_metadata(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-agent-key")
        monkeypatch.setattr(_mod, "_begin_model_activation", lambda _model: (True, None))
        monkeypatch.setattr(_mod, "_end_model_activation", lambda: None)
        handler = _ResponseHandler(request_body={
            "model_id": "qwen3.5-9b-q4",
            "context_length": 16384,
            "tier": "sh_compact",
        })
        handler._do_model_activate = (
            lambda model_id, **kwargs: calls.append((model_id, kwargs))
        )

        _mod.AgentHandler._handle_model_activate(handler)

        assert calls == [(
            "qwen3.5-9b-q4",
            {"requested_context_length": 16384, "requested_tier": "SH_COMPACT"},
        )]

    def test_normalizes_model_id_whitespace(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-agent-key")
        monkeypatch.setattr(_mod, "_begin_model_activation", lambda _model: (True, None))
        monkeypatch.setattr(_mod, "_end_model_activation", lambda: None)
        handler = _ResponseHandler(request_body={"model_id": "  target-model  "})
        handler._do_model_activate = lambda model_id, **_kwargs: calls.append(model_id)

        _mod.AgentHandler._handle_model_activate(handler)

        assert calls == ["target-model"]

    @pytest.mark.parametrize(
        "request_body",
        [
            {"model_id": "target", "context_length": True},
            {"model_id": "target", "context_length": 512},
            {"model_id": "target", "context_length": 9007199254740992},
            {"model_id": "target", "tier": "UNKNOWN"},
            {"model_id": "target", "tier": "../1"},
            {"model_id": "target\nINJECTED=value"},
        ],
    )
    def test_rejects_invalid_cli_metadata(self, monkeypatch, request_body):
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-agent-key")
        monkeypatch.setattr(
            _mod.AgentHandler,
            "_do_model_activate",
            lambda *_args, **_kwargs: pytest.fail("invalid metadata must not activate"),
        )
        handler = _ResponseHandler(request_body=request_body)

        _mod.AgentHandler._handle_model_activate(handler)

        assert handler.response_code == 400


class _BrokenPipeWriter:
    def write(self, _payload):
        raise BrokenPipeError("client disconnected")


def _llama_identity_response(model_id):
    return json.dumps({
        "object": "list",
        "data": [{
            "id": model_id,
            "object": "model",
            "status": {"value": "loaded"},
        }],
    })


def _mock_verified_readiness(*_args, **kwargs):
    """Mirror the production bool/identity/proof readiness return contract."""
    identity = str(kwargs.get("gguf_file") or "mock-runtime-model.gguf")
    if kwargs.get("return_proof"):
        return {
            "identity": identity,
            "contextLength": 65536,
            "contextVerified": True,
            "verifiedAt": "2026-07-20T00:00:00+00:00",
        }
    if kwargs.get("return_identity"):
        return identity
    return True


def _write_model_activation_fixture(tmp_path, gpu_backend="nvidia", litellm_local_text=None):
    """Install fixture; returns (..., litellm local.yaml path, its seeded text)."""
    install_dir = tmp_path / "install"
    config_dir = install_dir / "config"
    models_dir = install_dir / "data" / "models"
    llama_dir = config_dir / "llama-server"
    litellm_dir = config_dir / "litellm"
    models_dir.mkdir(parents=True)
    llama_dir.mkdir(parents=True)
    litellm_dir.mkdir(parents=True)
    renderer_source = _agent_path.parents[1] / "scripts" / "render-runtime-configs.py"
    renderer_target = install_dir / "scripts" / "render-runtime-configs.py"
    renderer_target.parent.mkdir(parents=True)
    shutil.copyfile(renderer_source, renderer_target)

    (models_dir / "new-model.gguf").write_text("model", encoding="utf-8")
    (config_dir / "model-library.json").write_text(
        json.dumps({
            "models": [{
                "id": "target-model",
                "gguf_file": "new-model.gguf",
                "gguf_url": "https://example.test/new-model.gguf",
                "gguf_sha256": hashlib.sha256(b"model").hexdigest(),
                "llm_model_name": "new-model",
                "context_length": 4096,
            }]
        }),
        encoding="utf-8",
    )

    env_text = (
        f"GPU_BACKEND={gpu_backend}\n"
        "GGUF_FILE=old-model.gguf\n"
        "LLM_MODEL=old-model\n"
        "CTX_SIZE=2048\n"
        "OLLAMA_PORT=8080\n"
        f"OPENCODE_CONFIG_DIR={install_dir / 'config' / 'opencode'}\n"
    )
    env_path = install_dir / ".env"
    env_path.write_text(env_text, encoding="utf-8")

    ini_text = "[old-model]\nfilename = old-model.gguf\n"
    models_ini = llama_dir / "models.ini"
    models_ini.write_text(ini_text, encoding="utf-8")

    local_yaml = litellm_dir / "local.yaml"
    if litellm_local_text is not None:
        local_yaml.write_text(litellm_local_text, encoding="utf-8")

    return install_dir, env_path, env_text, models_ini, ini_text, local_yaml, litellm_local_text


def test_text_snapshot_restores_exact_line_endings(tmp_path):
    path = tmp_path / "config.env"
    original = b"MODEL=old\r\nEMPTY=\r\n"
    path.write_bytes(original)
    original_owner = (path.stat().st_uid, path.stat().st_gid)
    snapshot = _mod._snapshot_text_file(path)

    _mod._atomic_write_text(path, "MODEL=new\n")
    _mod._restore_text_file(path, snapshot)

    assert path.read_bytes() == original
    assert (path.stat().st_uid, path.stat().st_gid) == original_owner


def test_atomic_write_text_retries_windows_replace_race(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("MODEL=old\n", encoding="utf-8")
    monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
    real_replace = _mod.os.replace
    calls = []

    def flaky_replace(src, dst):
        calls.append((src, dst))
        if len(calls) < 3:
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(_mod.os, "replace", flaky_replace)

    _mod._atomic_write_text(path, "MODEL=new\n")

    assert len(calls) == 3
    assert path.read_text(encoding="utf-8") == "MODEL=new\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_text_cleans_temp_after_replace_race_exhausted(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / ".env"
    path.write_text("MODEL=old\n", encoding="utf-8")
    monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
    calls = []

    def locked_replace(src, dst):
        calls.append((src, dst))
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(_mod.os, "replace", locked_replace)

    with pytest.raises(PermissionError):
        _mod._atomic_write_text(path, "MODEL=new\n")

    assert len(calls) == 10
    assert path.read_text(encoding="utf-8") == "MODEL=old\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def _davep_gpu_contract():
    topology = {
        "vendor": "nvidia",
        "gpu_count": 3,
        "gpus": [
            {"index": 0, "uuid": "GPU-ti-0", "name": "GTX 1080 Ti", "memory_gb": 11},
            {"index": 1, "uuid": "GPU-1080", "name": "GTX 1080", "memory_gb": 8},
            {"index": 2, "uuid": "GPU-ti-2", "name": "GTX 1080 Ti", "memory_gb": 11},
        ],
        "links": [
            {"gpu_a": 0, "gpu_b": 1, "link_type": "PHB", "link_label": "PHB", "rank": 30},
            {"gpu_a": 0, "gpu_b": 2, "link_type": "PHB", "link_label": "PHB", "rank": 30},
            {"gpu_a": 1, "gpu_b": 2, "link_type": "PHB", "link_label": "PHB", "rank": 30},
        ],
    }
    assignment = {
        "gpu_assignment": {
            "version": "1.0",
            "strategy": "colocated",
            "services": {
                "llama_server": {
                    "gpus": ["GPU-ti-0", "GPU-1080"],
                    "gpu_indices": [0, 1],
                    "parallelism": {
                        "mode": "pipeline",
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 2,
                        "gpu_memory_utilization": 0.95,
                        "tensor_split": [0.5789, 0.4211],
                    },
                },
                "whisper": {"gpus": ["GPU-ti-2"], "gpu_indices": [2]},
                "comfyui": {"gpus": ["GPU-ti-2"], "gpu_indices": [2]},
                "embeddings": {"gpus": ["GPU-ti-2"], "gpu_indices": [2]},
            },
        }
    }
    return topology, assignment


def _install_davep_gpu_contract(install_dir, env_path):
    config_dir = install_dir / "config"
    scripts_dir = install_dir / "scripts"
    config_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    planner_source = _agent_path.parents[1] / "scripts" / "assign_gpus.py"
    (scripts_dir / "assign_gpus.py").write_text(
        planner_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    topology, assignment = _davep_gpu_contract()
    (config_dir / "gpu-topology.json").write_text(
        json.dumps(topology),
        encoding="utf-8",
    )
    encoded = base64.b64encode(
        json.dumps(assignment, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    with env_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "GPU_COUNT=3\n"
            f"GPU_ASSIGNMENT_JSON_B64={encoded}\n"
            "LLAMA_SERVER_GPU_UUIDS=GPU-ti-0,GPU-1080\n"
            "LLAMA_SERVER_GPU_INDICES=0,1\n"
            "LLAMA_ARG_SPLIT_MODE=layer\n"
            "LLAMA_ARG_TENSOR_SPLIT=0.5789,0.4211\n"
        )
    return encoded


def _native_nvidia_host(monkeypatch):
    # These fixtures describe a native Linux GPU fleet, not the machine running
    # pytest. Keep the real WSL guard active and test its inputs separately.
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod.platform, "release", lambda: "6.8.0-generic")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)


def _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch):
    _native_nvidia_host(monkeypatch)
    install_dir = tmp_path / "install"
    models_dir = install_dir / "data" / "models"
    models_dir.mkdir(parents=True)
    env_path = install_dir / ".env"
    env_path.write_text("GPU_BACKEND=nvidia\n", encoding="utf-8")
    encoded = _install_davep_gpu_contract(install_dir, env_path)
    target = models_dir / "target.gguf"
    target.write_bytes(b"model")
    env = {
        "GPU_BACKEND": "nvidia",
        "GPU_COUNT": "3",
        "GPU_ASSIGNMENT_JSON_B64": encoded,
    }
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    return install_dir, target, env


def _amd_gpu_contract():
    topology = {
        "vendor": "amd",
        "gpu_count": 3,
        "gpus": [
            {
                "index": 0,
                "uuid": "AMD-card-0",
                "name": "Radeon PRO W7900",
                "memory_gb": 16,
                "gfx_version": "gfx1100",
                "memory_type": "discrete",
            },
            {
                "index": 1,
                "uuid": "AMD-card-1",
                "name": "Radeon PRO W7900",
                "memory_gb": 16,
                "gfx_version": "gfx1100",
                "memory_type": "discrete",
            },
            {
                "index": 2,
                "uuid": "AMD-card-2",
                "name": "Radeon PRO W7900",
                "memory_gb": 16,
                "gfx_version": "gfx1100",
                "memory_type": "discrete",
            },
        ],
        "links": [
            {"gpu_a": 0, "gpu_b": 1, "link_type": "PCIE", "link_label": "PHB", "rank": 30},
            {"gpu_a": 0, "gpu_b": 2, "link_type": "PCIE", "link_label": "PHB", "rank": 30},
            {"gpu_a": 1, "gpu_b": 2, "link_type": "PCIE", "link_label": "PHB", "rank": 30},
        ],
    }
    assignment = {
        "gpu_assignment": {
            "version": "1.0",
            "strategy": "colocated",
            "services": {
                "llama_server": {
                    "gpus": ["AMD-card-0", "AMD-card-1"],
                    "gpu_indices": [0, 1],
                    "parallelism": {
                        "mode": "pipeline",
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 2,
                        "gpu_memory_utilization": 0.95,
                    },
                },
                "whisper": {"gpus": ["AMD-card-2"], "gpu_indices": [2]},
            },
        }
    }
    return topology, assignment


def _install_amd_gpu_contract(install_dir, env_path):
    config_dir = install_dir / "config"
    scripts_dir = install_dir / "scripts"
    config_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    planner_source = _agent_path.parents[1] / "scripts" / "assign_gpus.py"
    (scripts_dir / "assign_gpus.py").write_text(
        planner_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    topology, assignment = _amd_gpu_contract()
    (config_dir / "gpu-topology.json").write_text(
        json.dumps(topology),
        encoding="utf-8",
    )
    encoded = base64.b64encode(
        json.dumps(assignment, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    with env_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "GPU_COUNT=3\n"
            "AMD_INFERENCE_LOCATION=container\n"
            "AMD_INFERENCE_MANAGED=true\n"
            f"GPU_ASSIGNMENT_JSON_B64={encoded}\n"
            "LLAMA_SERVER_GPU_UUIDS=AMD-card-0,AMD-card-1\n"
            "LLAMA_SERVER_GPU_INDICES=0,1\n"
            "ROCR_VISIBLE_DEVICES=0,1\n"
            "LLAMA_ARG_SPLIT_MODE=layer\n"
            "LLAMA_ARG_TENSOR_SPLIT=\n"
        )
    return encoded


def _write_amd_gpu_plan_fixture(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    models_dir = install_dir / "data" / "models"
    models_dir.mkdir(parents=True)
    env_path = install_dir / ".env"
    env_path.write_text("GPU_BACKEND=amd\n", encoding="utf-8")
    _install_amd_gpu_contract(install_dir, env_path)
    target = models_dir / "target.gguf"
    target.write_bytes(b"model")
    env = _mod.load_env(env_path)
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    return install_dir, target, env


def test_amd_model_gpu_plan_expands_persisted_rocm_subset(tmp_path, monkeypatch):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)

    plan = _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    )

    assert plan is not None
    assert plan["previous_gpus"] == ["AMD-card-0", "AMD-card-1"]
    assert plan["planned_gpus"] == ["AMD-card-0", "AMD-card-1", "AMD-card-2"]
    assert plan["env_updates"]["LLAMA_SERVER_GPU_INDICES"] == "0,1,2"
    assert plan["env_updates"]["ROCR_VISIBLE_DEVICES"] == "0,1,2"


def test_amd_model_gpu_plan_preserves_sufficient_assignment(tmp_path, monkeypatch):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)

    assert _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 28, "size_mb": 24000},
        target,
    ) is None


def test_amd_selected_context_can_expand_persisted_gpu_subset(tmp_path, monkeypatch):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    model = {
        "id": "amd-context-test",
        "vram_required_gb": 28,
        "size_mb": 24000,
    }

    assert _mod._plan_amd_model_gpu_assignment(env, model, target) is None

    plan = _mod._plan_amd_model_gpu_assignment(
        env,
        model,
        target,
        context_length=131072,
    )

    assert plan is not None
    assert plan["required_mb"] == 38339
    assert plan["previous_gpus"] == ["AMD-card-0", "AMD-card-1"]
    assert plan["planned_gpus"] == ["AMD-card-0", "AMD-card-1", "AMD-card-2"]


def test_amd_model_gpu_plan_is_idempotent_after_expansion(tmp_path, monkeypatch):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    first_plan = _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    )
    env.update(first_plan["env_updates"])

    assert _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    ) is None


def test_amd_model_gpu_plan_migrates_legacy_rocm_indices(tmp_path, monkeypatch):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    env.pop("GPU_ASSIGNMENT_JSON_B64")
    env.pop("LLAMA_SERVER_GPU_UUIDS")

    plan = _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    )

    assert plan["previous_gpus"] == ["AMD-card-0", "AMD-card-1"]
    migrated = _mod._decode_gpu_assignment(
        plan["env_updates"]["GPU_ASSIGNMENT_JSON_B64"]
    )
    assert migrated["gpu_assignment"]["services"]["llama_server"]["gpu_indices"] == [
        0,
        1,
        2,
    ]


def test_amd_model_gpu_plan_rejects_invalid_legacy_rocm_indices(
    tmp_path,
    monkeypatch,
):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    env.pop("GPU_ASSIGNMENT_JSON_B64")
    env.pop("LLAMA_SERVER_GPU_UUIDS")
    env["LLAMA_SERVER_GPU_INDICES"] = "0,9"
    env["ROCR_VISIBLE_DEVICES"] = "0,9"

    with pytest.raises(RuntimeError, match="Legacy ROCm GPU assignment is invalid"):
        _mod._plan_amd_model_gpu_assignment(
            env,
            {"vram_required_gb": 40, "size_mb": 35000},
            target,
        )


def test_amd_model_gpu_plan_preserves_unrestricted_all_gpu_runtime(
    tmp_path,
    monkeypatch,
):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    for key in (
        "GPU_ASSIGNMENT_JSON_B64",
        "LLAMA_SERVER_GPU_UUIDS",
        "LLAMA_SERVER_GPU_INDICES",
        "ROCR_VISIBLE_DEVICES",
    ):
        env.pop(key, None)

    assert _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    ) is None


def test_amd_model_gpu_plan_rejects_small_manual_assignment(tmp_path, monkeypatch):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    assignment = _mod._decode_gpu_assignment(env["GPU_ASSIGNMENT_JSON_B64"])
    assignment["gpu_assignment"]["strategy"] = "manual"
    env["GPU_ASSIGNMENT_JSON_B64"] = base64.b64encode(
        json.dumps(assignment, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    with pytest.raises(RuntimeError, match="ods gpu reassign --manual"):
        _mod._plan_amd_model_gpu_assignment(
            env,
            {"vram_required_gb": 40, "size_mb": 35000},
            target,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"AMD_INFERENCE_LOCATION": "host"},
        {"AMD_INFERENCE_LOCATION": "external"},
        {"AMD_INFERENCE_MANAGED": "false"},
        {"GPU_COUNT": "1"},
    ],
)
def test_amd_model_gpu_plan_leaves_unmanaged_or_single_gpu_runtime_unchanged(
    tmp_path,
    monkeypatch,
    overrides,
):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    env.update(overrides)

    assert _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    ) is None


def test_amd_model_gpu_plan_rejects_non_amd_topology(tmp_path, monkeypatch):
    install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    topology_path = install_dir / "config" / "gpu-topology.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    topology["vendor"] = "nvidia"
    topology_path.write_text(json.dumps(topology), encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not describe AMD"):
        _mod._plan_amd_model_gpu_assignment(
            env,
            {"vram_required_gb": 40, "size_mb": 35000},
            target,
        )


def test_amd_model_gpu_plan_rejects_unknown_planned_gfx(tmp_path, monkeypatch):
    install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    topology_path = install_dir / "config" / "gpu-topology.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    topology["gpus"][2]["gfx_version"] = "unknown"
    topology_path.write_text(json.dumps(topology), encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing a gfx architecture"):
        _mod._plan_amd_model_gpu_assignment(
            env,
            {"vram_required_gb": 40, "size_mb": 35000},
            target,
        )


def test_amd_model_gpu_plan_rejects_mixed_gfx1151_runtime(tmp_path, monkeypatch):
    install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    topology_path = install_dir / "config" / "gpu-topology.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    topology["gpus"][2]["gfx_version"] = "gfx1151"
    topology_path.write_text(json.dumps(topology), encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot safely combine gfx1151"):
        _mod._plan_amd_model_gpu_assignment(
            env,
            {"vram_required_gb": 40, "size_mb": 35000},
            target,
        )


@pytest.mark.parametrize(("backend", "override"), [
    ("rocm", "11.5.1"),
    # Vulkan, the default, reads no HSA variable at all.
    ("vulkan", None),
    ("", None),
])
def test_amd_model_gpu_plan_sets_strix_halo_runtime_contract(tmp_path, monkeypatch, backend, override):
    install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    env["AMD_INFERENCE_BACKEND"] = backend
    topology_path = install_dir / "config" / "gpu-topology.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    for gpu in topology["gpus"]:
        gpu["gfx_version"] = "gfx1151"
    topology_path.write_text(json.dumps(topology), encoding="utf-8")

    plan = _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    )

    assert plan["env_updates"].get("HSA_OVERRIDE_GFX_VERSION") == override
    # The retired custom-build binary key is never written again.
    assert "LEMONADE_LLAMACPP_ROCM_BIN" not in plan["env_updates"]
    assert plan["env_removals"] == []


def test_amd_model_gpu_plan_removes_only_ods_managed_strix_overrides(
    tmp_path,
    monkeypatch,
):
    _install_dir, target, env = _write_amd_gpu_plan_fixture(tmp_path, monkeypatch)
    env.update(
        {
            "AMD_INFERENCE_BACKEND": "rocm",
            "HSA_OVERRIDE_GFX_VERSION": "11.5.1",
            "LEMONADE_LLAMACPP_ROCM_BIN": "/opt/llama-custom/llama-server",
        }
    )

    plan = _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    )

    assert set(plan["env_removals"]) == {
        "HSA_OVERRIDE_GFX_VERSION",
        "LEMONADE_LLAMACPP_ROCM_BIN",
    }

    env["HSA_OVERRIDE_GFX_VERSION"] = "10.3.0"
    env["LEMONADE_LLAMACPP_ROCM_BIN"] = "/opt/operator/llama-server"
    custom_plan = _mod._plan_amd_model_gpu_assignment(
        env,
        {"vram_required_gb": 40, "size_mb": 35000},
        target,
    )
    assert custom_plan["env_removals"] == []


def test_model_gpu_plan_expands_davep_two_gpu_assignment(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    )

    assert plan is not None
    assert plan["previous_gpus"] == ["GPU-ti-0", "GPU-1080"]
    assert plan["planned_gpus"] == ["GPU-ti-0", "GPU-1080", "GPU-ti-2"]
    assert plan["required_mb"] == 24 * 1024
    assert plan["planned_capacity_mb"] == 30 * 1024
    assert plan["split_mode"] == "layer"
    assert plan["tensor_split"] == []
    updates = plan["env_updates"]
    assert updates["LLAMA_SERVER_GPU_UUIDS"] == "GPU-ti-0,GPU-1080,GPU-ti-2"
    assert updates["LLAMA_SERVER_GPU_INDICES"] == "0,1,2"
    assert updates["LLAMA_ARG_SPLIT_MODE"] == "layer"
    assert updates["LLAMA_ARG_TENSOR_SPLIT"] == ""
    merged = _mod._decode_gpu_assignment(updates["GPU_ASSIGNMENT_JSON_B64"])
    assert merged["gpu_assignment"]["strategy"] == "colocated"
    assert merged["gpu_assignment"]["services"]["whisper"]["gpus"] == ["GPU-ti-2"]


@pytest.mark.parametrize("mode", ["tensor", "hybrid"])
def test_nvidia_model_gpu_plan_never_emits_row_split(tmp_path, monkeypatch, mode):
    # CUDA row split fails at model load from llama.cpp b9890 ("does not
    # support split buffers") and is not fleet-qualified; NVIDIA tensor and
    # hybrid assignments run with layer split.
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    planned_gpus = ["GPU-ti-0", "GPU-1080", "GPU-ti-2"]
    monkeypatch.setattr(
        _mod,
        "_run_nvidia_gpu_planner",
        lambda *_args: {
            "gpu_assignment": {
                "version": "1.0",
                "strategy": "dedicated",
                "services": {
                    "llama_server": {
                        "gpus": planned_gpus,
                        "gpu_indices": [0, 1, 2],
                        "parallelism": {
                            "mode": mode,
                            "tensor_parallel_size": 3,
                            "pipeline_parallel_size": 1,
                            "tensor_split": [1, 1, 1],
                        },
                    }
                },
            }
        },
    )

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    )

    assert plan is not None
    assert plan["split_mode"] == "layer"
    assert plan["env_updates"]["LLAMA_ARG_SPLIT_MODE"] == "layer"
    assert plan["env_updates"]["LLAMA_ARG_TENSOR_SPLIT"] == "1,1,1"


def test_model_gpu_plan_preserves_sufficient_existing_assignment(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 18, "size_mb": 15000},
        target,
    )

    assert plan is None


def test_model_gpu_plan_is_idempotent_after_expansion(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    first_plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    )
    env.update(first_plan["env_updates"])

    assert _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    ) is None


def test_model_gpu_plan_migrates_legacy_uuid_only_assignment(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    env.pop("GPU_ASSIGNMENT_JSON_B64")
    env.update(
        {
            "LLAMA_SERVER_GPU_UUIDS": "GPU-ti-0,GPU-1080",
            "LLAMA_SERVER_GPU_INDICES": "0,1",
            "LLAMA_ARG_SPLIT_MODE": "layer",
            "WHISPER_GPU_UUID": "GPU-ti-2",
        }
    )

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    )

    assert plan is not None
    assert plan["previous_gpus"] == ["GPU-ti-0", "GPU-1080"]
    assert plan["planned_gpus"] == ["GPU-ti-0", "GPU-1080", "GPU-ti-2"]
    migrated = _mod._decode_gpu_assignment(
        plan["env_updates"]["GPU_ASSIGNMENT_JSON_B64"]
    )
    assert migrated["gpu_assignment"]["services"]["whisper"] == {
        "gpus": ["GPU-ti-2"],
        "gpu_indices": [2],
    }


def test_model_gpu_plan_migrates_legacy_index_assignment(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    env.pop("GPU_ASSIGNMENT_JSON_B64")
    env["LLAMA_SERVER_GPU_UUIDS"] = "0,1"

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    )

    assert plan is not None
    assert plan["previous_gpus"] == ["GPU-ti-0", "GPU-1080"]
    assert plan["planned_gpus"] == ["GPU-ti-0", "GPU-1080", "GPU-ti-2"]


@pytest.mark.parametrize("visibility", ["all", "none", "void"])
def test_model_gpu_plan_preserves_special_nvidia_visibility_override(
    tmp_path,
    monkeypatch,
    visibility,
):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    env.pop("GPU_ASSIGNMENT_JSON_B64")
    env["LLAMA_SERVER_GPU_UUIDS"] = visibility

    assert _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    ) is None


def test_model_gpu_plan_does_not_overwrite_manual_assignment(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    assignment = _mod._decode_gpu_assignment(env["GPU_ASSIGNMENT_JSON_B64"])
    assignment["gpu_assignment"]["strategy"] = "manual"
    env["GPU_ASSIGNMENT_JSON_B64"] = base64.b64encode(
        json.dumps(assignment, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    with pytest.raises(RuntimeError, match="ods gpu reassign --manual"):
        _mod._plan_nvidia_model_gpu_assignment(
            env,
            {"vram_required_gb": 24, "size_mb": 21110},
            target,
        )


@pytest.mark.parametrize(
    "encoded",
    [
        "not-base64",
        base64.b64encode(
            json.dumps(
                {
                    "gpu_assignment": {
                        "services": {
                            "llama_server": {
                                "gpus": ["GPU-ti-0", "GPU-ti-0"],
                            }
                        }
                    }
                }
            ).encode("utf-8")
        ).decode("ascii"),
    ],
)
def test_model_gpu_plan_rejects_malformed_or_duplicate_assignment(
    tmp_path,
    monkeypatch,
    encoded,
):
    _native_nvidia_host(monkeypatch)
    install_dir = tmp_path / "install"
    target = tmp_path / "model.gguf"
    target.write_bytes(b"model")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

    with pytest.raises(RuntimeError, match="assignment is malformed"):
        _mod._plan_nvidia_model_gpu_assignment(
            {
                "GPU_BACKEND": "nvidia",
                "GPU_COUNT": "3",
                "GPU_ASSIGNMENT_JSON_B64": encoded,
            },
            {"size_mb": 22000},
            target,
        )


def test_model_gpu_plan_rejects_non_nvidia_topology(tmp_path, monkeypatch):
    install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    topology_path = install_dir / "config" / "gpu-topology.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    topology["vendor"] = "amd"
    topology_path.write_text(json.dumps(topology), encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not describe NVIDIA"):
        _mod._plan_nvidia_model_gpu_assignment(
            env,
            {"vram_required_gb": 24, "size_mb": 21110},
            target,
        )


def test_model_gpu_plan_rejects_physical_gpu_mig_topology_without_mutation(
    tmp_path,
    monkeypatch,
):
    install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    topology_path = install_dir / "config" / "gpu-topology.json"
    topology_text = topology_path.read_text(encoding="utf-8")
    topology = json.loads(topology_text)
    topology["mig_enabled"] = True
    topology_path.write_text(json.dumps(topology), encoding="utf-8")
    original_env = dict(env)
    monkeypatch.setattr(
        _mod,
        "_run_nvidia_gpu_planner",
        lambda *_args: pytest.fail("MIG rejection must happen before planning"),
    )

    with pytest.raises(RuntimeError, match="MIG hosts"):
        _mod._plan_nvidia_model_gpu_assignment(
            env,
            {"vram_required_gb": 24, "size_mb": 21110},
            target,
        )

    assert env == original_env
    assert json.loads(topology_path.read_text(encoding="utf-8")) == topology


def test_model_gpu_plan_rejects_target_larger_than_total_vram(
    tmp_path,
    monkeypatch,
):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="exceeds assignable free VRAM"):
        _mod._plan_nvidia_model_gpu_assignment(
            env,
            {"vram_required_gb": 48, "size_mb": 42500},
            target,
        )


def test_selected_context_crosses_exact_assignment_boundary(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    model = {
        "id": "qwen2.5-8b-q4",
        "vram_required_gb": 19,
        "size_mb": 16000,
    }

    assert _mod._plan_nvidia_model_gpu_assignment(env, model, target) is None

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        model,
        target,
        context_length=131072,
    )

    assert plan is not None
    assert plan["required_mb"] == 19928
    assert plan["previous_gpus"] == ["GPU-ti-0", "GPU-1080"]
    assert plan["planned_gpus"] == ["GPU-ti-0", "GPU-ti-2"]


def test_runtime_profile_memory_floor_can_trigger_expansion(tmp_path, monkeypatch):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 19, "size_mb": 15000},
        target,
        runtime_profile={"estimated_required_gb": 20},
    )

    assert plan is not None
    assert plan["required_mb"] == 20 * 1024


def test_unknown_local_model_gpu_budget_includes_runtime_headroom(tmp_path):
    target = tmp_path / "local.gguf"
    target.write_bytes(b"model")

    assert _mod._target_model_vram_budget_mb({"size_mb": 22000}, target) == 30724


def test_unknown_qwen_27b_replans_davep_assignment_for_runtime_overhead(
    tmp_path,
    monkeypatch,
):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)

    plan = _mod._plan_nvidia_model_gpu_assignment(
        env,
        {
            # DaveP's llama.cpp log reports 16.39 GiB for this exact GGUF.
            "size_mb": 16784,
            "context_length": 65536,
            "local": True,
        },
        target,
    )

    assert plan is not None
    assert plan["required_mb"] == 23683
    assert plan["previous_gpus"] == ["GPU-ti-0", "GPU-1080"]
    assert plan["planned_gpus"] == ["GPU-ti-0", "GPU-1080", "GPU-ti-2"]


def test_huggingface_import_uses_conservative_floor_over_size_estimate(tmp_path):
    target = tmp_path / "import.gguf"
    target.write_bytes(b"model")

    assert _mod._target_model_vram_budget_mb(
        {
            "source": "huggingface",
            "size_mb": 16000,
            "vram_required_gb": 19,
        },
        target,
    ) == 22624


def test_curated_model_preserves_validated_vram_contract(tmp_path):
    target = tmp_path / "curated.gguf"
    target.write_bytes(b"model")

    assert _mod._target_model_vram_budget_mb(
        {
            "size_mb": 21110,
            "vram_required_gb": 24,
        },
        target,
    ) == 24 * 1024


def test_model_weight_size_counts_complete_split_gguf(tmp_path):
    first = tmp_path / "model-00001-of-00002.gguf"
    second = tmp_path / "model-00002-of-00002.gguf"
    first.write_bytes(b"a" * (2 * 1024 * 1024))
    second.write_bytes(b"b" * (3 * 1024 * 1024))
    model = {
        "gguf_file": first.name,
        "gguf_parts": [
            {"file": first.name, "url": "https://example.invalid/first"},
            {"file": second.name, "url": "https://example.invalid/second"},
        ],
    }

    assert _mod._model_weight_size_mb(model, first) == 5


@pytest.mark.parametrize("invalid", ["nan", "inf", "-inf"])
def test_model_gpu_budget_ignores_non_finite_metadata(tmp_path, invalid):
    target = tmp_path / "model.gguf"
    target.write_bytes(b"model")

    assert _mod._target_model_vram_budget_mb(
        {
            "size_mb": invalid,
            "vram_required_gb": invalid,
        },
        target,
    ) == 3073


@pytest.mark.parametrize(
    "env",
    [
        {"GPU_BACKEND": "amd", "GPU_COUNT": "3"},
        {"GPU_BACKEND": "nvidia", "GPU_COUNT": "1"},
        {"GPU_BACKEND": "nvidia", "GPU_COUNT": "3"},
    ],
)
def test_model_gpu_plan_leaves_non_applicable_runtimes_unchanged(
    tmp_path,
    monkeypatch,
    env,
):
    install_dir = tmp_path / "install"
    target = tmp_path / "model.gguf"
    target.write_bytes(b"model")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

    assert _mod._plan_nvidia_model_gpu_assignment(env, {"size_mb": 22000}, target) is None


@pytest.mark.parametrize("wsl_signal", ["distro", "interop", "kernel"])
def test_model_gpu_plan_explicitly_skips_wsl_auto_replan(tmp_path, monkeypatch, wsl_signal):
    _install_dir, target, env = _write_nvidia_gpu_plan_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    if wsl_signal == "distro":
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    elif wsl_signal == "interop":
        monkeypatch.setenv("WSL_INTEROP", "/run/WSL/test_interop")
    else:
        monkeypatch.setattr(_mod.platform, "release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setattr(
        _mod,
        "_run_nvidia_gpu_planner",
        lambda *_args: pytest.fail("WSL must not enter automatic replanning"),
    )

    assert _mod._plan_nvidia_model_gpu_assignment(
        env,
        {"vram_required_gb": 24, "size_mb": 21110},
        target,
    ) is None


def test_managed_pixel_reconcile_is_noop_when_this_install_does_not_own_pixel(
    monkeypatch,
):
    monkeypatch.setattr(_mod, "_ods_managed_pixel_identity", lambda: None)
    monkeypatch.setattr(
        _mod.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("an unmanaged Pixel must not be touched"),
    )

    assert _mod._reconcile_ods_managed_pixel_model("safe-model", 65536) == "not_installed"


@pytest.mark.parametrize(
    ("gateway_setting", "expected_gateway_port"),
    [
        ("PIXEL_GATEWAY_PORT=18790\n", "18790"),
        ('PIXEL_GATEWAY_PORT="18790"\n', "18790"),
        ("PIXEL_GATEWAY_PORT=65535\n", "65535"),
        ("", "18789"),
    ],
)
def test_managed_pixel_reconcile_uses_positional_args_and_minimal_environment(
    tmp_path,
    monkeypatch,
    gateway_setting,
    expected_gateway_port,
):
    install_dir = tmp_path / "install"
    home = tmp_path / "owner-home"
    install_dir.mkdir()
    home.mkdir()
    (install_dir / ".env").write_text(
        "PIXEL_SOURCE_URL=bundled\n"
        "PIXEL_SOURCE_REF=2ef78e7067211a198748c5499ed5a0261f4b48b6\n"
        f"{gateway_setting}",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="reconciled\n", stderr="")

    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(
        _mod,
        "_ods_managed_pixel_identity",
        lambda: ("pixel-owner", home),
    )
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-cross-boundary")
    monkeypatch.setattr(_mod.subprocess, "run", fake_run)

    assert _mod._reconcile_ods_managed_pixel_model(
        "qwen3.5-9b",
        131072,
        max_tokens=4096,
        reasoning=True,
    ) == "reconciled"
    assert captured["argv"][-9:] == [
        str(install_dir),
        "pixel-owner",
        str(home),
        "qwen3.5-9b",
        "131072",
        "4096",
        "true",
        "",
        "unknown",
    ]
    assert captured["kwargs"]["timeout"] == 900
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["env"]["PIXEL_SOURCE_URL"] == "bundled"
    assert captured["kwargs"]["env"]["PIXEL_GATEWAY_PORT"] == expected_gateway_port
    assert "UNRELATED_SECRET" not in captured["kwargs"]["env"]


@pytest.mark.parametrize("explicit_source", [True, False])
def test_managed_pixel_reconcile_accepts_bundled_source(
    tmp_path, monkeypatch, explicit_source,
):
    install_dir = tmp_path / "install"
    home = tmp_path / "owner-home"
    install_dir.mkdir()
    home.mkdir()
    source_ref = "2ef78e7067211a198748c5499ed5a0261f4b48b6"
    source_setting = "PIXEL_SOURCE_URL=bundled\n" if explicit_source else ""
    (install_dir / ".env").write_text(
        f"{source_setting}PIXEL_SOURCE_REF={source_ref}\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="reconciled\n", stderr="")

    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(
        _mod, "_ods_managed_pixel_identity", lambda: ("pixel-owner", home),
    )
    monkeypatch.setattr(_mod.subprocess, "run", fake_run)

    assert _mod._reconcile_ods_managed_pixel_model("safe-model", 65536) == "reconciled"
    assert captured["env"]["PIXEL_SOURCE_URL"] == "bundled"


@pytest.mark.parametrize("source_setting", [
    "PIXEL_SOURCE_URL=https://github.com/Osmantic/Pixel.git\n",
    "PIXEL_SOURCE_URL=bundled\nPIXEL_SOURCE_REF=b33730436baf5d98bf58f7d57c090318fe19f433\n",
    "PIXEL_SOURCE_REF=b33730436baf5d98bf58f7d57c090318fe19f433\n",
])
def test_managed_pixel_reconcile_rejects_private_or_legacy_source_before_subprocess(
    tmp_path, monkeypatch, source_setting,
):
    install_dir = tmp_path / "install"
    home = tmp_path / "owner-home"
    install_dir.mkdir()
    home.mkdir()
    (install_dir / ".env").write_text(source_setting, encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(_mod, "_ods_managed_pixel_identity", lambda: ("pixel-owner", home))
    monkeypatch.setattr(
        _mod.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("private source must fail before subprocess"),
    )

    with pytest.raises(RuntimeError, match="Pixel source|Pixel source pin"):
        _mod._reconcile_ods_managed_pixel_model("safe-model", 65536)


def test_managed_pixel_reconcile_rejects_relative_source(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    home = tmp_path / "owner-home"
    install_dir.mkdir()
    home.mkdir()
    (install_dir / ".env").write_text("PIXEL_SOURCE_URL=../pixel\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(
        _mod, "_ods_managed_pixel_identity", lambda: ("pixel-owner", home),
    )
    monkeypatch.setattr(
        _mod.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("an invalid source must fail before subprocess"),
    )

    with pytest.raises(RuntimeError, match="configured Pixel source"):
        _mod._reconcile_ods_managed_pixel_model("safe-model", 65536)


def test_managed_pixel_reconcile_rejects_remote_source(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    home = tmp_path / "owner-home"
    install_dir.mkdir()
    home.mkdir()
    (install_dir / ".env").write_text(
        "PIXEL_SOURCE_URL=https://github.com/Osmantic/Pixel.git\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(
        _mod, "_ods_managed_pixel_identity", lambda: ("pixel-owner", home),
    )
    monkeypatch.setattr(
        _mod.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("remote source must fail before subprocess"),
    )

    with pytest.raises(RuntimeError, match="configured Pixel source"):
        _mod._reconcile_ods_managed_pixel_model("safe-model", 65536)


def test_managed_pixel_reconcile_rejects_old_ref_without_local_checkout(
    tmp_path, monkeypatch,
):
    install_dir = tmp_path / "install"
    home = tmp_path / "owner-home"
    install_dir.mkdir()
    home.mkdir()
    (install_dir / ".env").write_text(
        "PIXEL_SOURCE_REF=b33730436baf5d98bf58f7d57c090318fe19f433\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(
        _mod, "_ods_managed_pixel_identity", lambda: ("pixel-owner", home),
    )
    monkeypatch.setattr(
        _mod.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("old bundle ref must fail before subprocess"),
    )

    with pytest.raises(RuntimeError, match="reinstall the managed runtime"):
        _mod._reconcile_ods_managed_pixel_model("safe-model", 65536)


@pytest.mark.parametrize(
    "gateway_port",
    ["", "   ", "0", "01", "65536", "123456", "abc", "-1"],
)
def test_managed_pixel_reconcile_rejects_invalid_gateway_port(
    tmp_path,
    monkeypatch,
    gateway_port,
):
    install_dir = tmp_path / "install"
    home = tmp_path / "owner-home"
    install_dir.mkdir()
    home.mkdir()
    (install_dir / ".env").write_text(
        f"PIXEL_GATEWAY_PORT={gateway_port}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(
        _mod,
        "_ods_managed_pixel_identity",
        lambda: ("pixel-owner", home),
    )
    monkeypatch.setattr(
        _mod.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("an invalid port must fail before subprocess"),
    )

    with pytest.raises(RuntimeError, match="gateway port is invalid"):
        _mod._reconcile_ods_managed_pixel_model("safe-model", 65536)


def test_managed_pixel_reconcile_rejects_context_below_pixel_contract(monkeypatch):
    monkeypatch.setattr(
        _mod,
        "_ods_managed_pixel_identity",
        lambda: ("pixel-owner", Path("/safe/pixel-owner")),
    )

    with pytest.raises(RuntimeError, match="at least|between 4096"):
        _mod._reconcile_ods_managed_pixel_model("safe-model", 2048)


@pytest.mark.parametrize(
    ("context_length", "expected"),
    [
        (4096, 1024),
        (8192, 2048),
        (16384, 4096),
        (24576, 6144),
        (32768, 8192),
        (65536, 8192),
    ],
)
def test_managed_pixel_output_budget_preserves_agent_prompt_room(
    context_length,
    expected,
):
    assert _mod._pixel_max_tokens_for_context(context_length) == expected


@pytest.mark.parametrize(
    ("model", "mode", "expected"),
    [
        ("qwen3.5-9b", "off", False),
        ("jamba-reasoning-3b", "none", False),
        ("deepseek-r1-7b", "false", False),
        ("NVIDIA-Nemotron3-Nano-4B", "off", False),
        ("phi-4-mini", "off", False),
        ("phi-4-mini", "deepseek", True),
        ("qwen3.5-9b", "", False),
    ],
)
def test_pixel_reasoning_capability_follows_model_family_and_runtime_mode(
    model,
    mode,
    expected,
):
    assert _mod._pixel_model_reasoning_capable(
        model,
        {"LLAMA_REASONING": mode},
    ) is expected


class TestModelActivateRollback:

    @pytest.mark.parametrize("inspection", ["existence", "running"])
    def test_preflight_inspection_timeout_is_safe_and_releases_lifecycle(
        self, tmp_path, monkeypatch, caplog, inspection,
    ):
        install, env_path, original, models_ini, original_ini, *_ = _write_model_activation_fixture(tmp_path)
        inspections = []

        def inspect(command, **kwargs):
            if command[:2] == ["docker", "inspect"] and command[-1] == "ods-litellm":
                inspections.append(command)
                assert kwargs["timeout"] == 15
                if inspection == "existence" or "{{.State.Running}}" in command:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                return subprocess.CompletedProcess(command, 0, "fixture-container-id\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-agent-key")
        monkeypatch.setattr(_mod, "_container_exists", _container_exists)
        monkeypatch.setattr(_mod, "_capture_container_state", _capture_container_state)
        monkeypatch.setattr(_mod.subprocess, "run", inspect)
        monkeypatch.setattr(_mod, "_begin_pixel_model_transaction", lambda *_: pytest.fail("no transaction may start"))
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda *_: pytest.fail("no runtime may restart"))
        handler = _ResponseHandler(request_body={"model_id": "target-model"})
        handler._do_model_activate = lambda *args, **kwargs: _mod.AgentHandler._do_model_activate(handler, *args, **kwargs)

        _mod.AgentHandler._handle_model_activate(handler)

        result = handler.parse_response()
        assert handler.response_code == 500
        assert result["code"] == "model_preflight_inspection_timeout"
        assert "did not start this model switch" in result["error"]
        assert "Wait until Models shows no operation in progress" in result["error"]
        assert "refresh model status, then try again" in result["error"]
        assert set(result) == {"code", "error"}
        assert "ods-litellm" not in result["error"]
        assert "--format" not in result["error"]
        assert "ods-litellm" in caplog.text and "TimeoutExpired" in caplog.text
        assert len(inspections) == (1 if inspection == "existence" else 2)
        assert not _mod._model_lifecycle_status().get("lifecycleActive")
        assert _mod._model_lifecycle_status().get("activationResult") is None
        assert env_path.read_text() == original
        assert models_ini.read_text() == original_ini

    def test_post_mutation_inspection_timeout_still_rolls_back(self, tmp_path, monkeypatch):
        install, env_path, original, models_ini, original_ini, *_ = _write_model_activation_fixture(tmp_path)
        restarts = []

        def restart(env):
            restarts.append(env["GGUF_FILE"])
            if len(restarts) == 1:
                raise getattr(_mod, "ContainerInspectionTimeout", RuntimeError)("private inspection diagnostic")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", restart)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(_mod, "_staged_llama_load_failure_probe", lambda *_: None)
        monkeypatch.setattr(_mod, "_failed_llama_server_log_excerpt", lambda *_: "")
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        result = handler.parse_response()
        assert handler.response_code == 500
        assert result.get("code") != "model_preflight_inspection_timeout"
        assert "did not start" not in result["error"]
        assert result["rolled_back"] is True
        assert restarts == ["new-model.gguf", "old-model.gguf"]
        assert env_path.read_text() == original
        assert models_ini.read_text() == original_ini

    @pytest.fixture(autouse=True)
    def _successful_meaningful_completion(self, monkeypatch):
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod, "_runtime_health", lambda _env: "ok")
        monkeypatch.setattr(_mod, "_llama_runtime_props", lambda _env: (131072, ""))
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_container_exists", lambda _container: False)
        monkeypatch.setattr(_mod, "_container_running", lambda _container: False)
        monkeypatch.setattr(_mod, "_verify_hermes_dashboard_ready", lambda: None)

    @pytest.mark.parametrize('failure', [None, 'busy', 'lost-apply-ack', 'apply-refused', 'receipt-write', 'commit-unconfirmed', 'rollback-unproved', 'runtime_load_failed', 'timeout', 'identity_mismatch', 'commit-response-write', 'rollback-response-write'])
    def test_native_controller_holds_before_model_mutation_and_finishes_only_after_proof(self,tmp_path,monkeypatch,failure):
        install,env_path,original,*_=_write_model_activation_fixture(tmp_path)
        original=original.replace('CTX_SIZE=2048','CTX_SIZE=65536')+'PIXEL_OPENWEBUI_KEY=configured\n'
        env_path.write_text(original,encoding='utf-8')
        previous={'model':'old-model.gguf','contextLength':65536,'maxTokens':3072,'reasoning':True,'routeFingerprint':'d'*64}
        state={'schemaVersion':1,'status':'ready','revision':'a'*64,'contract':previous,'pending':False,'transactionId':None,'outcome':None}
        calls=[]
        proofs=[]
        restarts=[]
        def control(operation,request=None,*,config):
            calls.append(operation)
            if operation=='model-status':
                if failure=='busy' and len(calls)==1:
                    return {**state,'status':'held','pending':True,'transactionId':'f'*64}
                return dict(state)
            if operation=='model-begin':
                assert env_path.read_text(encoding='utf-8')==original
                assert restarts==[]
                state.update(status='held',pending=True,transactionId=request['transactionId'])
            elif operation=='model-apply':
                assert proofs[-1]=='new-model.gguf'
                assert 'routeFingerprint' not in request['target']
                if failure in {'apply-refused','rollback-unproved'}: raise RuntimeError('refused')
                state.update(status='applied',contract=request['target'])
                if failure=='lost-apply-ack':raise TimeoutError('ack lost')
            elif operation=='model-finish':
                if request['outcome']=='commit':
                    assert json.loads((install/'data/model-activation-receipt.json').read_text())['status']=='complete'
                    if failure=='commit-unconfirmed':raise TimeoutError('finish uncertain')
                else:
                    assert proofs[-1]=='old-model.gguf'
                    assert env_path.read_text(encoding='utf-8')==original
                    assert _mod._model_lifecycle_status()['activationPhase']=='rollback_verifying'
                    assert 'activationResult' not in _mod._model_lifecycle_status()
                    state['contract']=previous
                state.update(status='completed',pending=False,outcome=request['outcome'])
            return dict(state)
        def readiness(*args,**kwargs):
            identity=kwargs.get('gguf_file')
            proofs.append(identity)
            if failure in {'runtime_load_failed','timeout','identity_mismatch','rollback-response-write'} and identity=='new-model.gguf':
                kwargs['diagnosis']['code']=failure
                return False
            if failure=='rollback-unproved' and identity=='old-model.gguf':return False
            return _mock_verified_readiness(*args,**kwargs)
        real_write=_mod._atomic_write_json
        def write(path,value,*args,**kwargs):
            if failure=='receipt-write' and path.name=='model-activation-receipt.json':raise OSError('receipt failed')
            return real_write(path,value,*args,**kwargs)
        monkeypatch.setattr(_mod,'INSTALL_DIR',install)
        monkeypatch.setattr(_mod,'_runtime_model_control',control)
        monkeypatch.setattr(_mod,'_compose_restart_llama_server',lambda env:restarts.append(env['GGUF_FILE']))
        monkeypatch.setattr(_mod,'_wait_for_model_readiness',readiness)
        monkeypatch.setattr(_mod,'_staged_llama_load_failure_probe',lambda *_args:None)
        monkeypatch.setattr(_mod,'_failed_llama_server_log_excerpt',lambda *_args:'')
        monkeypatch.setattr(_mod,'_atomic_write_json',write)
        monkeypatch.setattr(_mod,'_reconcile_ods_managed_pixel_model',lambda *a,**kw:pytest.fail('must use coordinated native apply'))
        real_response=_mod.json_response
        response_failed=False
        def response(handler,code,body,**kwargs):
            nonlocal response_failed
            if not response_failed and (
                (failure=='commit-response-write' and code==200)
                or (failure=='rollback-response-write' and body.get('rolled_back') is True)
            ):
                response_failed=True
                raise BrokenPipeError('client disconnected after completed proof')
            return real_response(handler,code,body,**kwargs)
        monkeypatch.setattr(_mod,'json_response',response)
        handler=_ResponseHandler()
        monkeypatch.setattr(_mod,'_model_activation_result',None)
        assert _mod._begin_model_activation('target-model')[0]
        try:
            _mod.AgentHandler._do_model_activate(handler,'target-model',requested_context_length=65536)
            assert _mod._model_lifecycle_status()['lifecycleActive'] is True
            assert 'activationResult' not in _mod._model_lifecycle_status()
        finally:
            _mod._end_model_activation()
        result=_mod._model_lifecycle_status().get('activationResult')
        payload=handler.parse_response()
        assert calls.count('model-begin')<=1 and calls.count('model-apply')<=1 and calls.count('model-finish')<=1
        if failure in {'commit-response-write','rollback-response-write'}:
            assert response_failed
            committed=failure=='commit-response-write'
            assert state['status']=='completed' and state['pending'] is False
            assert state['outcome']==('commit' if committed else 'rollback')
            assert result=={'outcome':'activated' if committed else 'rolled_back',
                            'failureCode':None if committed else 'runtime_readiness_failed'}
            assert restarts==(['new-model.gguf'] if committed else ['new-model.gguf','old-model.gguf'])
            return
        if failure in {None,'lost-apply-ack'}:
            assert handler.response_code==200,payload
            assert state['status']=='completed' and state['outcome']=='commit'
            assert result=={'outcome':'activated','failureCode':None}
        elif failure=='busy':
            assert handler.response_code==500,payload
            assert env_path.read_text(encoding='utf-8')==original and restarts==[]
            assert calls==['model-status']
            assert result is None
        elif failure in {'apply-refused','receipt-write','runtime_load_failed','timeout','identity_mismatch'}:
            assert handler.response_code==500 and payload['rolled_back'] is True,payload
            assert state['contract']==previous and state['outcome']=='rollback'
            assert not payload.get('pending')
            code=('runtime_load_failed' if failure=='runtime_load_failed' else
                  'runtime_readiness_failed' if failure in {'timeout','identity_mismatch'} else
                  'consumer_verification_failed')
            assert result=={'outcome':'rolled_back','failureCode':code}
        else:
            assert handler.response_code==500 and payload['pending'] is True,payload
            assert state['pending'] is True and state['outcome'] is None
            assert result=={'outcome':'rollback_unconfirmed','failureCode':'rollback_unconfirmed'}
            if failure=='commit-unconfirmed':assert restarts==['new-model.gguf']
            else:assert 'model-finish' not in calls

    def test_activation_requires_persisted_env_before_any_mutation(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.unlink()
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda *_args: pytest.fail("missing .env must fail before restart"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "requires the persisted environment" in handler.parse_response()["error"]
        assert not env_path.exists()
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_activation_reconciles_pixel_model_context_and_receipt(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        reconciliations = []

        def reconcile(model, context, **options):
            reconciliations.append((model, context, options))
            return "reconciled"

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(_mod, "_reconcile_ods_managed_pixel_model", reconcile)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=65536,
        )

        assert handler.response_code == 200
        assert reconciliations == [(
            "new-model.gguf",
            65536,
            {"max_tokens": 8192, "reasoning": False, "image_input": "unknown"},
        )]
        response = handler.parse_response()
        assert response["consumers"]["pixel"] == "reconciled"
        assert response["consumers"]["openclaw"] == "host_gateway_reconciled"
        receipt = json.loads(
            (install_dir / "data" / "model-activation-receipt.json").read_text(
                encoding="utf-8"
            )
        )
        assert receipt["consumers"]["pixel"] == "reconciled"
        assert receipt["consumers"]["openclaw"] == "host_gateway_reconciled"

    def test_managed_pixel_requires_valid_previous_context_before_mutation(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_ods_managed_pixel_identity",
            lambda: ("pixel-owner", tmp_path / "pixel-owner"),
        )
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda _env: pytest.fail("invalid prior Pixel context must fail before restart"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "at least 4096" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_pixel_reconcile_failure_rolls_back_and_rebinds_previous_pixel_model(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8").replace("CTX_SIZE=2048", "CTX_SIZE=4096"),
            encoding="utf-8",
        )
        runtime_restarts = []
        reconciliations = []

        def restart_runtime(env):
            runtime_restarts.append(env["LLM_MODEL"])

        def reconcile(model, context, **options):
            reconciliations.append((model, context, options))
            if model == "new-model.gguf":
                raise RuntimeError("simulated Pixel reconciliation failure")
            return "reconciled"

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", restart_runtime)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(_mod, "_reconcile_ods_managed_pixel_model", reconcile)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert "simulated Pixel reconciliation failure" in response["error"]
        assert runtime_restarts == ["new-model", "old-model"]
        assert reconciliations == [
            ("new-model.gguf", 4096, {"max_tokens": 1024, "reasoning": False, "image_input": "unknown"}),
            ("old-model.gguf", 4096, {"max_tokens": 1024, "reasoning": False, "image_input": "unknown"}),
        ]
        assert _mod.load_env(env_path)["LLM_MODEL"] == "old-model"

    def test_pixel_failure_heals_stale_hermes_route_during_rollback(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8").replace(
                "CTX_SIZE=2048\n",
                "CTX_SIZE=4096\nMAX_CONTEXT=4096\n"
                "HERMES_LLM_BASE_URL=http://llama-server:8080/v1\n",
            ),
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = (
            install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        )
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        stale_hermes = (
            "model:\n"
            '  default: "stale-model.gguf"\n'
            '  base_url: "http://llama-server:8080/v1"\n'
            "  context_length: 2048\n"
        )
        hermes_live.write_text(stale_hermes, encoding="utf-8")
        hermes_template.write_text(stale_hermes, encoding="utf-8")
        states = {
            "ods-litellm": {"exists": False, "running": False},
            "ods-hermes": {"exists": True, "running": True},
            "ods-perplexica": {"exists": False, "running": False},
        }
        events = []
        reconciliations = []

        def verify_hermes(model, base_url, context):
            events.append(f"verify:{model}:{context}")
            assert _mod._hermes_config_matches(
                hermes_live.read_text(encoding="utf-8"),
                model,
                base_url,
                context,
            )

        def reconcile(model, context, **_options):
            reconciliations.append((model, context))
            if model == "new-model.gguf":
                raise RuntimeError("simulated Pixel reconciliation failure")
            return "reconciled"

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda env: events.append(
            f"runtime:{env['LLM_MODEL']}"
        ))
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None, **_options: events.append(f"target:{name}") or True,
        )
        monkeypatch.setattr(
            _mod,
            "_restore_container_state",
            lambda name, _state=None, **_options: events.append(f"rollback:{name}") or True,
        )
        monkeypatch.setattr(
            _mod,
            "_wait_for_container_health",
            lambda name: events.append(f"health:{name}"),
        )
        monkeypatch.setattr(_mod, "_verify_running_hermes_route", verify_hermes)
        monkeypatch.setattr(_mod, "_reconcile_ods_managed_pixel_model", reconcile)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert reconciliations == [
            ("new-model.gguf", 4096),
            ("old-model.gguf", 4096),
        ]
        assert events.index("health:ods-hermes") < events.index("verify:new-model.gguf:4096")
        rollback_health = len(events) - 1 - events[::-1].index("health:ods-hermes")
        rollback_verify = events.index("verify:old-model.gguf:4096")
        assert rollback_health < rollback_verify
        assert _mod._hermes_config_matches(
            hermes_live.read_text(encoding="utf-8"),
            "old-model.gguf",
            "http://llama-server:8080/v1",
            4096,
        )
        assert _mod._hermes_config_matches(
            hermes_template.read_text(encoding="utf-8"),
            "old-model.gguf",
            "http://llama-server:8080/v1",
            4096,
        )

    def test_uses_lower_live_ram_limit_instead_of_host_physical_ram(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(_mod, "_nvidia_vram_gb", lambda: 8.0)
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 15)
        monkeypatch.setattr(_mod.platform, "machine", lambda: "x86_64")
        model = {
            "runtime_profiles": [
                {
                    "id": "host-physical-ram-profile",
                    "backend": "nvidia",
                    "host_arch": ["amd64"],
                    "memory_type": "discrete",
                    "vram_min_gb": 7.5,
                    "vram_max_gb": 8.5,
                    "system_ram_min_gb": 31,
                },
                {
                    "id": "wsl-constrained-profile",
                    "backend": "nvidia",
                    "host_arch": ["amd64"],
                    "memory_type": "discrete",
                    "vram_min_gb": 7.5,
                    "vram_max_gb": 8.5,
                    "system_ram_min_gb": 15,
                },
            ]
        }

        profile = _mod._select_runtime_profile(
            model,
            {
                "GPU_BACKEND": "nvidia",
                "GPU_MEMORY_TYPE": "discrete",
                "SYSTEM_RAM_GB": "31",
            },
        )

        assert profile["id"] == "wsl-constrained-profile"

    def test_nvidia_profile_selection_fails_closed_without_vram_probe(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(_mod, "_nvidia_vram_gb", lambda: 0.0)
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 15)

        with pytest.raises(RuntimeError, match="VRAM could not be determined"):
            _mod._select_runtime_profile(
                {
                    "runtime_profiles": [
                        {
                            "id": "nvidia-profile",
                            "backend": "nvidia",
                            "vram_min_gb": 7.5,
                            "context_length": 32768,
                        }
                    ]
                },
                {"GPU_BACKEND": "nvidia"},
            )

    def test_nvidia_profile_selection_fails_closed_when_system_ram_is_too_low(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(_mod, "_nvidia_vram_gb", lambda: 8.0)
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 13)
        monkeypatch.setattr(_mod.platform, "machine", lambda: "x86_64")

        with pytest.raises(
            RuntimeError,
            match=r"available system RAM \(13GB\).*requires 15GB.*unprofiled",
        ):
            _mod._select_runtime_profile(
                {
                    "runtime_profiles": [
                        {
                            "id": "nvidia-8gb-profile",
                            "backend": "nvidia",
                            "host_arch": ["amd64"],
                            "memory_type": "discrete",
                            "vram_min_gb": 7.5,
                            "vram_max_gb": 8.5,
                            "system_ram_min_gb": 15,
                        }
                    ]
                },
                {
                    "GPU_BACKEND": "nvidia",
                    "GPU_MEMORY_TYPE": "discrete",
                    "SYSTEM_RAM_GB": "13",
                },
            )

    @pytest.mark.parametrize("gpu_backend", ["none", "unknown", "", "cpu"])
    def test_cpu_backend_aliases_select_the_catalog_cpu_profile(
        self,
        monkeypatch,
        gpu_backend,
    ):
        # Windows no-GPU installs write GPU_BACKEND=none; the installer's
        # selector reads it as cpu, so a switch must keep the CPU profile's
        # q8 KV cache and container limit instead of running unprofiled.
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 32)
        monkeypatch.setattr(_mod.platform, "machine", lambda: "x86_64")
        catalog_path = Path(__file__).resolve().parents[4] / "config" / "model-library.json"
        model = next(
            entry
            for entry in json.loads(catalog_path.read_text(encoding="utf-8"))["models"]
            if entry["id"] == "qwen3.5-9b-q4"
        )

        profile = _mod._select_runtime_profile(
            model,
            {"GPU_BACKEND": gpu_backend, "SYSTEM_RAM_GB": "32"},
        )

        assert profile is not None
        assert profile["id"] == "cpu-64k-q8-kv"
        assert profile["env"]["LLAMA_ARG_CACHE_TYPE_K"] == "q8_0"

    def test_profile_above_its_ram_ceiling_does_not_apply(self, monkeypatch):
        # model_selection.hardware_matching_profiles: a RAM ceiling scopes a
        # profile to a class of machines. Above it the profile neither
        # applies nor blocks activation as an unmet requirement.
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 64)
        monkeypatch.setattr(_mod.platform, "machine", lambda: "x86_64")
        model = {
            "runtime_profiles": [
                {
                    "id": "cpu-small-host",
                    "backend": "cpu",
                    "system_ram_min_gb": 12,
                    "system_ram_max_gb": 22,
                    "context_length": 65536,
                }
            ]
        }

        assert _mod._select_runtime_profile(
            model, {"GPU_BACKEND": "cpu", "SYSTEM_RAM_GB": "64"}
        ) is None
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 16)
        assert _mod._select_runtime_profile(
            model, {"GPU_BACKEND": "cpu", "SYSTEM_RAM_GB": "16"}
        )["id"] == "cpu-small-host"

    def test_nvidia_vram_probe_uses_wsl_bridge_outside_service_path(
        self,
        monkeypatch,
    ):
        calls = []
        real_is_file = Path.is_file

        def fake_is_file(path):
            if path.as_posix() == "/usr/lib/wsl/lib/nvidia-smi":
                return True
            return real_is_file(path)

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="8151\n", stderr="")

        monkeypatch.setattr(_mod.shutil, "which", lambda _name: None)
        monkeypatch.setattr(Path, "is_file", fake_is_file)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._nvidia_vram_gb() == pytest.approx(8151 / 1024)
        assert calls == [[
            str(Path("/usr/lib/wsl/lib/nvidia-smi")),
            "--query-gpu=memory.total",
            "--format=csv,noheader,nounits",
        ]]

    def test_env_assignment_round_trips_spaces_and_shell_metacharacters(
        self,
        tmp_path,
    ):
        env_path = tmp_path / ".env"
        value = "NVIDIA 8GB owner's $HOME `command` profile"

        env_path.write_text(
            _mod._upsert_env_text("MODEL_RUNTIME_PROFILE_LABEL=old\n", "MODEL_RUNTIME_PROFILE_LABEL", value),
            encoding="utf-8",
        )

        persisted = env_path.read_text(encoding="utf-8")
        assert persisted == f"MODEL_RUNTIME_PROFILE_LABEL={_mod.shlex.quote(value)}\n"
        assert _mod.load_env(env_path)["MODEL_RUNTIME_PROFILE_LABEL"] == value

    def test_bound_env_update_and_restore_preserve_existing_inode(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("LLM_MODEL=old\n", encoding="utf-8")
        original_inode = env_path.stat().st_ino
        snapshot = _mod._snapshot_text_file(env_path)

        _mod._write_bound_env_text(env_path, "LLM_MODEL=new\n")
        assert env_path.stat().st_ino == original_inode
        assert _mod.load_env(env_path)["LLM_MODEL"] == "new"

        _mod._restore_bound_env_file(env_path, snapshot)
        assert env_path.stat().st_ino == original_inode
        assert env_path.read_text(encoding="utf-8") == "LLM_MODEL=old\n"

    def test_malformed_model_library_cannot_fall_back_to_unverified_local_model(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            '{"models": [',
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda *_args: pytest.fail("malformed catalog must fail before restart"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "Model library is unavailable or malformed" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    @pytest.mark.parametrize(
        ("bind_addr", "expected_identity_url"),
        [
            ("0.0.0.0", "http://127.0.0.1:9090/v1/models"),
            ("::", "http://127.0.0.1:9090/v1/models"),
            ("192.168.106.1", "http://127.0.0.1:9090/v1/models"),
        ],
    )
    def test_apple_native_activation_probes_private_inference_despite_ui_bind(
        self,
        tmp_path,
        monkeypatch,
        bind_addr,
        expected_identity_url,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="apple")
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8")
            + f"BIND_ADDRESS={bind_addr}\nODS_NATIVE_LLAMA_PORT=9090\n",
            encoding="utf-8",
        )
        llama_bin = install_dir / "bin" / "llama-server"
        llama_bin.parent.mkdir(parents=True)
        llama_bin.write_text("", encoding="utf-8")
        lib_dir = install_dir / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "constants.sh").write_text("# test fixture\n", encoding="utf-8")
        (lib_dir / "bridge-manager.sh").write_text("# test fixture\n", encoding="utf-8")
        launches = []
        calls = []

        def fake_launch(*args):
            launches.append(args)

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", lambda _env_path: None)
        monkeypatch.setattr(_mod, "_launch_native_llama_server", fake_launch)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert len(launches) == 1
        curl_calls = [cmd for cmd in calls if cmd and cmd[0] == "curl"]
        assert [cmd[-1] for cmd in curl_calls] == [
            expected_identity_url,
            expected_identity_url,
        ]

    def test_apple_native_activation_applies_advanced_context_override(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="apple")
        )
        llama_bin = install_dir / "bin" / "llama-server"
        llama_bin.parent.mkdir(parents=True)
        llama_bin.write_text("", encoding="utf-8")
        lib_dir = install_dir / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "constants.sh").write_text("# test fixture\n", encoding="utf-8")
        (lib_dir / "bridge-manager.sh").write_text("# test fixture\n", encoding="utf-8")
        launched_envs = []

        def fake_launch(runtime_env_path, *_args):
            launched_envs.append(_mod.load_env(runtime_env_path))

        def fake_readiness(*_args, **kwargs):
            if kwargs.get("return_proof"):
                return {
                    "identity": "new-model.gguf",
                    "contextLength": 524288,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-25T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return "new-model.gguf"
            return True

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", lambda _env_path: None)
        monkeypatch.setattr(_mod, "_launch_native_llama_server", fake_launch)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", fake_readiness)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(
                cmd,
                0,
                stdout="",
                stderr="",
            ),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=524288,
        )

        assert handler.response_code == 200
        assert handler.parse_response()["context_length"] == 524288
        assert len(launched_envs) == 1
        assert launched_envs[0]["CTX_SIZE"] == "524288"
        assert launched_envs[0]["MAX_CONTEXT"] == "524288"
        persisted = _mod.load_env(env_path)
        assert persisted["CTX_SIZE"] == "524288"
        assert persisted["MAX_CONTEXT"] == "524288"

    def test_apple_missing_native_binary_fails_before_config_mutation(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="apple")
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_restart_macos_native_llama_server",
            lambda *_args: pytest.fail("preflight failure must not restart the runtime"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        receipt = handler.parse_response()
        assert "llama-server binary not found" in receipt["error"]
        assert "rolled_back" not in receipt
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_success_response_disconnect_does_not_roll_back(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler(wfile=_BrokenPipeWriter())

        with pytest.raises(BrokenPipeError):
            _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert "GGUF_FILE=new-model.gguf" in env_path.read_text(encoding="utf-8")
        assert "LLM_MODEL=new-model" in env_path.read_text(encoding="utf-8")
        assert "filename = new-model.gguf" in models_ini.read_text(encoding="utf-8")

    def test_larger_model_replans_and_commits_nvidia_gpu_assignment(
        self,
        tmp_path,
        monkeypatch,
    ):
        _native_nvidia_host(monkeypatch)
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        catalog_path = install_dir / "config" / "model-library.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"][0].update({
            "size_mb": 21110,
            "vram_required_gb": 24,
        })
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        _install_davep_gpu_contract(install_dir, env_path)
        restart_envs = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: restart_envs.append(dict(env)),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        response = handler.parse_response()
        assert response["gpu_assignment_changed"] is True
        assert len(restart_envs) == 1
        assert restart_envs[0]["LLAMA_SERVER_GPU_UUIDS"] == (
            "GPU-ti-0,GPU-1080,GPU-ti-2"
        )
        assert restart_envs[0]["LLAMA_ARG_TENSOR_SPLIT"] == ""
        persisted = _mod.load_env(env_path)
        assert persisted["LLAMA_SERVER_GPU_UUIDS"] == "GPU-ti-0,GPU-1080,GPU-ti-2"
        assert persisted["LLAMA_ARG_TENSOR_SPLIT"] == ""
        assert persisted["LLM_MODEL_SIZE_MB"] == "21110"
        assignment = _mod._decode_gpu_assignment(
            persisted["GPU_ASSIGNMENT_JSON_B64"]
        )
        assert assignment["gpu_assignment"]["services"]["llama_server"]["gpus"] == [
            "GPU-ti-0",
            "GPU-1080",
            "GPU-ti-2",
        ]
        receipt = json.loads(
            (install_dir / "data" / "model-activation-receipt.json").read_text(
                encoding="utf-8"
            )
        )
        assert receipt["gpuAssignment"] == {
            "changed": True,
            "previousGpus": ["GPU-ti-0", "GPU-1080"],
            "activeGpus": ["GPU-ti-0", "GPU-1080", "GPU-ti-2"],
            "requiredMiB": 24576,
            "assignedMiB": 30720,
            "splitMode": "layer",
            "tensorSplit": [],
        }

    def test_failed_activation_rolls_back_nvidia_gpu_assignment(
        self,
        tmp_path,
        monkeypatch,
    ):
        _native_nvidia_host(monkeypatch)
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        catalog_path = install_dir / "config" / "model-library.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"][0].update({
            "size_mb": 21110,
            "vram_required_gb": 24,
        })
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        original_assignment = _install_davep_gpu_contract(install_dir, env_path)
        original_env = env_path.read_text(encoding="utf-8")
        restart_envs = []

        def readiness(env, *_args, **kwargs):
            if env.get("GGUF_FILE") == "new-model.gguf":
                return None if (kwargs.get("return_identity") or kwargs.get("return_proof")) else False
            return _mock_verified_readiness(*_args, **kwargs)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: restart_envs.append(dict(env)),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert [env["GGUF_FILE"] for env in restart_envs] == [
            "new-model.gguf",
            "old-model.gguf",
        ]
        assert restart_envs[0]["LLAMA_SERVER_GPU_UUIDS"] == (
            "GPU-ti-0,GPU-1080,GPU-ti-2"
        )
        assert restart_envs[1]["LLAMA_SERVER_GPU_UUIDS"] == "GPU-ti-0,GPU-1080"
        assert env_path.read_text(encoding="utf-8") == original_env
        assert _mod.load_env(env_path)["GPU_ASSIGNMENT_JSON_B64"] == original_assignment

    def test_larger_model_replans_and_commits_amd_rocm_assignment(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        catalog_path = install_dir / "config" / "model-library.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"][0].update({
            "size_mb": 35000,
            "vram_required_gb": 40,
        })
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        _install_amd_gpu_contract(install_dir, env_path)
        restart_envs = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: restart_envs.append(dict(env)),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert handler.parse_response()["gpu_assignment_changed"] is True
        assert restart_envs[0]["ROCR_VISIBLE_DEVICES"] == "0,1,2"
        assert restart_envs[0]["LLAMA_SERVER_GPU_INDICES"] == "0,1,2"
        persisted = _mod.load_env(env_path)
        assert persisted["ROCR_VISIBLE_DEVICES"] == "0,1,2"
        assignment = _mod._decode_gpu_assignment(
            persisted["GPU_ASSIGNMENT_JSON_B64"]
        )
        assert assignment["gpu_assignment"]["services"]["llama_server"]["gpus"] == [
            "AMD-card-0",
            "AMD-card-1",
            "AMD-card-2",
        ]
        receipt = json.loads(
            (install_dir / "data" / "model-activation-receipt.json").read_text(
                encoding="utf-8"
            )
        )
        assert receipt["gpuAssignment"]["activeGpus"] == [
            "AMD-card-0",
            "AMD-card-1",
            "AMD-card-2",
        ]

    def test_failed_activation_rolls_back_amd_rocm_assignment(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        catalog_path = install_dir / "config" / "model-library.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"][0].update({
            "size_mb": 35000,
            "vram_required_gb": 40,
        })
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        original_assignment = _install_amd_gpu_contract(install_dir, env_path)
        original_env = env_path.read_text(encoding="utf-8")
        restart_envs = []

        def readiness(env, *_args, **kwargs):
            if env.get("GGUF_FILE") == "new-model.gguf":
                return None if (kwargs.get("return_identity") or kwargs.get("return_proof")) else False
            return _mock_verified_readiness(*_args, **kwargs)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: restart_envs.append(dict(env)),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert restart_envs[0]["ROCR_VISIBLE_DEVICES"] == "0,1,2"
        assert restart_envs[1]["ROCR_VISIBLE_DEVICES"] == "0,1"
        assert env_path.read_text(encoding="utf-8") == original_env
        assert _mod.load_env(env_path)["GPU_ASSIGNMENT_JSON_B64"] == original_assignment

    @pytest.mark.parametrize(
        "runtime_kind",
        [
            "compose-llama",
            "container-llama",
            "amd-compose-llama",
            "amd-container-llama",
            "windows-native-llama",
            "macos-native-llama",
        ],
    )
    def test_late_failure_restores_previous_runtime_and_config(
        self,
        tmp_path,
        monkeypatch,
        runtime_kind,
    ):
        gpu_backend = "amd" if runtime_kind.startswith(("windows-", "amd-")) else "nvidia"
        if runtime_kind == "macos-native-llama":
            gpu_backend = "apple"
        install_dir, env_path, _env_text, models_ini, _ini_text, _litellm_yaml, _ = (
            _write_model_activation_fixture(tmp_path, gpu_backend=gpu_backend)
        )

        if runtime_kind.startswith("amd-"):
            # A pre-round-F Linux AMD .env: the retired mode and backend read
            # as llama-server and the container path serves the model.
            env_path.write_text(
                "ODS_MODE=lemonade\n"
                "GPU_BACKEND=amd\n"
                "LLM_BACKEND=lemonade\n"
                "GGUF_FILE=old-model.gguf\n"
                "LLM_MODEL=old-model\n"
                "CTX_SIZE=2048\n"
                "OLLAMA_PORT=8080\n",
                encoding="utf-8",
            )
        elif runtime_kind == "windows-native-llama":
            env_path.write_text(
                "ODS_MODE=local\n"
                "GPU_BACKEND=amd\n"
                "LLM_BACKEND=llama-server\n"
                "AMD_INFERENCE_RUNTIME=llama-server\n"
                "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
                "AMD_INFERENCE_LOCATION=host\n"
                "AMD_INFERENCE_MANAGED=true\n"
                "AMD_INFERENCE_PORT=9090\n"
                "GGUF_FILE=old-model.gguf\n"
                "LLM_MODEL=old-model\n"
                "CTX_SIZE=2048\n",
                encoding="utf-8",
            )
        elif runtime_kind == "macos-native-llama":
            llama_bin = install_dir / "bin" / "llama-server"
            llama_bin.parent.mkdir(parents=True)
            llama_bin.write_text("binary", encoding="utf-8")

        local_yaml = install_dir / "config" / "litellm" / "local.yaml"
        router_endpoints = install_dir / "config" / "model-router" / "endpoints.json"
        tracked_configs = (env_path, models_ini, local_yaml, router_endpoints)

        def config_state():
            return {
                path: path.read_text(encoding="utf-8") if path.exists() else None
                for path in tracked_configs
            }

        original_config = config_state()
        runtime_restarts = []

        def record_restart(env):
            runtime_restarts.append((env["GGUF_FILE"], config_state()))

        def record_container_restart(env, override_image=""):
            record_restart(env)

        def record_native_restart(path, _env=None, *_args):
            record_restart(_mod.load_env(path))

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"litellm"})
        monkeypatch.setattr(_mod, "resolve_compose_flags", list)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-litellm",
        )
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        container_images: list = []
        if runtime_kind in {"compose-llama", "amd-compose-llama"}:
            monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
            monkeypatch.setattr(_mod, "_compose_restart_llama_server", record_restart)
        elif runtime_kind in {"container-llama", "amd-container-llama"}:
            monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
            monkeypatch.setenv("ODS_HOST_INSTALL_DIR", str(install_dir))

            def record_container_image_restart(env, override_image=""):
                container_images.append(override_image)
                record_container_restart(env, override_image)

            monkeypatch.setattr(_mod, "_recreate_llama_server", record_container_image_restart)
        elif runtime_kind == "windows-native-llama":
            monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
            monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", record_native_restart)
        else:
            monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
            monkeypatch.setattr(_mod, "_restart_macos_native_llama_server", record_native_restart)

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[-1] == "--help":
                return subprocess.CompletedProcess(cmd, 0, stdout="--ctx-size N\n--model FILE\n", stderr="")
            if cmd and cmd[0] == "curl":
                stdout = _llama_identity_response(
                    "new-model.gguf"
                    if any(str(part).endswith("/v1/models") for part in cmd)
                    else "unexpected-probe"
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            if cmd == [
                "docker", "compose", "up", "-d", "--no-deps",
                "--force-recreate", "litellm",
            ]:
                raise subprocess.TimeoutExpired(cmd, 60)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert [model for model, _state in runtime_restarts] == [
            "new-model.gguf",
            "old-model.gguf",
        ]
        assert "GGUF_FILE=new-model.gguf" in runtime_restarts[0][1][env_path]
        assert "filename = new-model.gguf" in runtime_restarts[0][1][models_ini]
        assert runtime_restarts[1][1] == original_config
        assert config_state() == original_config
        if runtime_kind == "amd-container-llama":
            # The AMD overlay's pinned image is kept; no catalog/CUDA override.
            assert container_images == ["", ""]

    def test_activation_accepts_local_gguf_without_catalog_entry(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "Research.Model-Q8_0.gguf").write_text(
            "model",
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "MAX_CONTEXT=65536\n"
            "LLAMA_ARG_SPEC_TYPE=draft-mtp\n"
            "LLAMA_ARG_SPEC_DRAFT_N_MAX=3\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = (
                _llama_identity_response("Research.Model-Q8_0.gguf")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "Research.Model-Q8_0")

        assert handler.response_code == 200
        receipt = handler.parse_response()
        assert receipt["status"] == "activated"
        assert receipt["model_id"] == "Research.Model-Q8_0"
        assert receipt["llm_model"] == "Research.Model-Q8_0"
        assert receipt["gguf_file"] == "Research.Model-Q8_0.gguf"
        assert receipt["tier"] is None
        assert receipt["context_length"] == 65536
        env_text = env_path.read_text(encoding="utf-8")
        assert "GGUF_FILE=Research.Model-Q8_0.gguf" in env_text
        assert "LLM_MODEL=Research.Model-Q8_0" in env_text
        assert "CTX_SIZE=65536" in env_text
        assert "LLAMA_ARG_SPEC_TYPE=" not in env_text
        assert "LLAMA_ARG_SPEC_DRAFT_N_MAX=" not in env_text
        assert "filename = Research.Model-Q8_0.gguf" in models_ini.read_text(encoding="utf-8")

    def test_local_gguf_activation_prefers_canonical_ctx_size_on_upgrade(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "LocalUpgrade.gguf").write_text(
            "model",
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=131072\n"
            "MAX_CONTEXT=65536\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = (
                _llama_identity_response("LocalUpgrade.gguf")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "LocalUpgrade")

        assert handler.response_code == 200
        assert handler.parse_response()["context_length"] == 131072
        persisted = _mod.load_env(env_path)
        assert persisted["CTX_SIZE"] == "131072"
        assert persisted["MAX_CONTEXT"] == "131072"

    def test_activation_resolves_local_gguf_by_stem_with_mixed_case_extension(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "MixedCaseModel.GGUF").write_text(
            "model",
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "MAX_CONTEXT=32768\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = (
                _llama_identity_response("MixedCaseModel.GGUF")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "MixedCaseModel")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "GGUF_FILE=MixedCaseModel.GGUF" in env_text
        assert "LLM_MODEL=MixedCaseModel" in env_text
        assert "filename = MixedCaseModel.GGUF" in models_ini.read_text(encoding="utf-8")

    def test_activation_accepts_sanitized_local_id_for_spaced_filename(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "My Custom Model.Q8_0.GGUF").write_text(
            "model",
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "MAX_CONTEXT=32768\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = (
                _llama_identity_response("My Custom Model.Q8_0.GGUF")
                if cmd and cmd[0] == "curl"
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "My-Custom-Model.Q8_0")

        assert handler.response_code == 200
        receipt = handler.parse_response()
        assert receipt["status"] == "activated"
        assert receipt["model_id"] == "My-Custom-Model.Q8_0"
        assert receipt["llm_model"] == "My-Custom-Model.Q8_0"
        assert receipt["gguf_file"] == "My Custom Model.Q8_0.GGUF"
        assert receipt["tier"] is None
        assert receipt["context_length"] == 32768
        env_text = env_path.read_text(encoding="utf-8")
        assert "GGUF_FILE='My Custom Model.Q8_0.GGUF'" in env_text
        assert "LLM_MODEL=My-Custom-Model.Q8_0" in env_text
        assert "[My-Custom-Model.Q8_0]" in models_ini.read_text(encoding="utf-8")
        assert "filename = My Custom Model.Q8_0.GGUF" in models_ini.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "model_id",
        [
            "../outside",
            r"..\outside",
            "nested/model",
            r"nested\model",
            "unsafe\x00model",
        ],
    )
    def test_local_gguf_resolver_rejects_path_traversal(self, tmp_path, model_id):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "outside.gguf").write_text("model", encoding="utf-8")

        assert _mod._resolve_local_gguf_filename(model_id, models_dir) is None

    def test_local_gguf_resolver_rejects_ambiguous_sanitized_id(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "My Custom Model.gguf").write_text("model", encoding="utf-8")
        (models_dir / "My@Custom@Model.gguf").write_text("model", encoding="utf-8")

        assert _mod._resolve_local_gguf_filename("My-Custom-Model", models_dir) is None

    def test_activation_rejects_empty_local_gguf_before_restart(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": []}),
            encoding="utf-8",
        )
        (install_dir / "data" / "models" / "EmptyLocal.gguf").write_text(
            "",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        def fail_restart(_env):
            raise AssertionError("empty GGUF should be rejected before restart")

        monkeypatch.setattr(_mod, "_compose_restart_llama_server", fail_restart)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "EmptyLocal")

        assert handler.response_code == 400
        assert "not downloaded or empty" in handler.parse_response()["error"]

    def test_amd_container_activation_publishes_the_llama_server_route(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        # A pre-round-F Linux AMD install, switchboard enabled. The CUDA-only
        # catalog image must never reach the AMD container (R3).
        env_path.write_text(
            "ODS_MODE=lemonade\n"
            "ODS_MODEL_SWITCHBOARD=enabled\n"
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=lemonade\n"
            "LEMONADE_MODEL=extra.old-model.gguf\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n"
            "OLLAMA_PORT=8080\n"
            "LITELLM_KEY=sk-gateway\n",
            encoding="utf-8",
        )
        catalog_path = install_dir / "config" / "model-library.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["models"][0]["llama_server_image"] = (
            "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014@sha256:" + "f" * 64
        )
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        readiness_calls: list = []

        def readiness(env, **kwargs):
            readiness_calls.append(kwargs)
            return _mock_verified_readiness(env, **kwargs)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env = _mod.load_env(env_path)
        assert env["GGUF_FILE"] == "new-model.gguf"
        assert "LLAMA_SERVER_IMAGE" not in env
        # The retired key is never rewritten or relied on.
        assert env["LEMONADE_MODEL"] == "extra.old-model.gguf"
        state = json.loads((install_dir / "data" / "model-state.json").read_text(encoding="utf-8"))
        assert state["active"]["backend"] == {
            "kind": "llama-server", "endpointId": "llama-server-default", "nativeRoute": None,
        }
        assert state["active"]["runtimeModelId"] == "new-model.gguf"
        endpoints = json.loads(
            (install_dir / "config" / "model-router" / "endpoints.json").read_text(encoding="utf-8")
        )
        assert [row["id"] for row in endpoints["endpoints"]] == ["llama-server-default"]
        assert not (install_dir / "config" / "litellm" / "lemonade.yaml").exists()
        # AMD containers poll as densely as NVIDIA after a replace.
        assert readiness_calls[0]["fast_poll_seconds"] == _mod._MODEL_READINESS_FAST_POLL_SECONDS

    def test_windows_native_final_runtime_flip_rolls_back_before_state_publish(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "ODS_MODE=local\n"
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-native-llama-server\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_PORT=18080\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        env_before = env_path.read_text(encoding="utf-8")
        state_path = install_dir / "data" / "model-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        old_state = {"active": {"runtimeModelId": "old-model.gguf"}}
        state_path.write_text(json.dumps(old_state), encoding="utf-8")
        states = {
            "ods-litellm": {"exists": True, "running": True},
            "ods-hermes": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        events = []
        target_readiness_attempts = 0
        state_records = []

        class FakeSwitchboardState:
            def record_verified_route(self, *_args, **kwargs):
                state_records.append(kwargs)

        def restart_native(_path, env):
            events.append(f"runtime:{env['GGUF_FILE']}")

        def readiness(_env, **kwargs):
            nonlocal target_readiness_attempts
            gguf_file = kwargs["gguf_file"]
            events.append(f"ready:{gguf_file}")
            if gguf_file == "new-model.gguf":
                target_readiness_attempts += 1
                if target_readiness_attempts > 1:
                    if kwargs.get("return_proof"):
                        return {}
                    if kwargs.get("return_identity"):
                        return ""
                    return False
            if kwargs.get("return_proof"):
                return {
                    "identity": gguf_file,
                    "contextLength": 4096,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-20T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return gguf_file
            return True

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_switchboard_state", FakeSwitchboardState())
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", restart_native)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None, **_kwargs: events.append(f"restart:{name}") or name == "ods-litellm",
        )
        monkeypatch.setattr(
            _mod,
            "_restore_container_state",
            lambda name, _state, recreate=False: events.append(f"restore:{name}") or True,
        )
        monkeypatch.setattr(
            _mod,
            "_verify_litellm_route",
            lambda env: events.append(f"litellm:{env['GGUF_FILE']}"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert "Final runtime proof failed" in response["error"]
        assert env_path.read_text(encoding="utf-8") == env_before
        assert json.loads(state_path.read_text(encoding="utf-8")) == old_state
        assert state_records == []
        assert events.index("runtime:new-model.gguf") < events.index("runtime:old-model.gguf")
        first_ready = events.index("ready:new-model.gguf")
        final_ready = events.index("ready:new-model.gguf", first_ready + 1)
        assert events.index("restart:ods-litellm") < final_ready

    def test_windows_native_llama_activation_uses_plain_health_and_litellm_local(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_MANAGED=true\n"
            "AMD_INFERENCE_PORT=9090\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n"
            "OLLAMA_PORT=11434\n"
            "LLAMA_SERVER_API_KEY=" + "5e" * 32 + "\n",
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"old-model.gguf\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://litellm:4000/v1\"\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        restart_calls = []

        def record_native_restart(path, env):
            restart_calls.append((path, dict(env)))

        def fail_wrong_restart(*_args, **_kwargs):
            raise AssertionError("wrong restart path for Windows native llama-server")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd and cmd[0] == sys.executable:
                return _real_subprocess_run(cmd, **kwargs)
            if cmd and cmd[0] == "curl":
                assert cmd[-1] == "http://127.0.0.1:9090/v1/models"
                assert kwargs["input"] == "Authorization: Bearer " + "5e" * 32 + "\n"
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=_llama_identity_response("new-model.gguf"),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"hermes", "litellm"})
        monkeypatch.setattr(_mod, "resolve_compose_flags", list)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", record_native_restart)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", fail_wrong_restart)
        monkeypatch.setattr(_mod, "_recreate_llama_server", fail_wrong_restart)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda _container: True,
        )
        monkeypatch.setattr(
            _mod,
            "_verify_running_hermes_route",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert restart_calls and restart_calls[0][0] == env_path
        assert restart_calls[0][1]["GGUF_FILE"] == "new-model.gguf"
        assert '  default: "new-model.gguf"' in hermes_live.read_text(encoding="utf-8")
        assert "extra.new-model.gguf" not in hermes_live.read_text(encoding="utf-8")
        local_yaml = install_dir / "config" / "litellm" / "local.yaml"
        content = local_yaml.read_text(encoding="utf-8")
        assert "model: openai/new-model.gguf" in content
        assert "api_base: http://host.docker.internal:9090/v1" in content
        assert "api_key: os.environ/LLAMA_SERVER_API_KEY" in content
        assert "5e" * 32 not in content
        endpoints = json.loads(
            (install_dir / "config" / "model-router" / "endpoints.json").read_text(encoding="utf-8")
        )
        assert endpoints["endpoints"] == [{
            "id": "llama-server-default",
            "baseUrl": "http://host.docker.internal:9090",
            "apiKeyEnv": "LLAMA_SERVER_API_KEY",
        }]
        assert [
            "docker", "compose", "up", "-d", "--no-deps",
            "--force-recreate", "litellm",
        ] in calls
        assert [
            "docker", "compose", "up", "-d", "--no-deps",
            "--force-recreate", "hermes",
        ] in calls
        assert ["docker", "restart", "ods-hermes"] not in calls

    def test_windows_native_llama_applies_advanced_context_override(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_MANAGED=true\n"
            "AMD_INFERENCE_PORT=9090\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n"
            "MAX_CONTEXT=2048\n",
            encoding="utf-8",
        )
        restart_envs = []

        def record_native_restart(_path, env):
            restart_envs.append(dict(env))

        def verified_readiness(*_args, **kwargs):
            if kwargs.get("return_proof"):
                return {
                    "identity": "new-model.gguf",
                    "contextLength": 524288,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-25T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return "new-model.gguf"
            return True

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", record_native_restart)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", verified_readiness)
        monkeypatch.setattr(_mod, "_container_exists", lambda _container: False)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=524288,
        )

        assert handler.response_code == 200
        assert handler.parse_response()["context_length"] == 524288
        assert len(restart_envs) == 1
        assert restart_envs[0]["CTX_SIZE"] == "524288"
        assert restart_envs[0]["MAX_CONTEXT"] == "524288"
        persisted = _mod.load_env(env_path)
        assert persisted["CTX_SIZE"] == "524288"
        assert persisted["MAX_CONTEXT"] == "524288"

    def test_windows_native_litellm_local_rolls_back_on_late_failure(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_MANAGED=true\n"
            "AMD_INFERENCE_PORT=9090\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        env_text = env_path.read_text(encoding="utf-8")
        litellm_local = install_dir / "config" / "litellm" / "local.yaml"
        old_local_yaml = "model_list:\n  - model_name: old\n"
        litellm_local.write_text(old_local_yaml, encoding="utf-8")

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[0] == "curl":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=_llama_identity_response("new-model.gguf"),
                    stderr="",
                )
            if cmd == [
                "docker", "compose", "up", "-d", "--no-deps",
                "--force-recreate", "litellm",
            ]:
                raise subprocess.TimeoutExpired(cmd, 60)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"litellm"})
        monkeypatch.setattr(_mod, "resolve_compose_flags", list)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", lambda *_args: None)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-litellm",
        )
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text
        assert litellm_local.read_text(encoding="utf-8") == old_local_yaml

    def test_windows_native_restart_error_restores_previous_runtime(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend="amd")
        )
        env_path.write_text(
            "GPU_BACKEND=amd\n"
            "LLM_BACKEND=llama-server\n"
            "AMD_INFERENCE_RUNTIME=llama-server\n"
            "AMD_INFERENCE_RUNTIME_MODE=windows-llama-server-fallback\n"
            "AMD_INFERENCE_LOCATION=host\n"
            "AMD_INFERENCE_MANAGED=true\n"
            "AMD_INFERENCE_PORT=9090\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        env_text = env_path.read_text(encoding="utf-8")
        restart_models = []

        def restart_then_recover(_path, env):
            restart_models.append(env["GGUF_FILE"])
            if len(restart_models) == 1:
                raise RuntimeError("simulated native launch failure")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_restart_windows_native_llama_server", restart_then_recover)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert restart_models == ["new-model.gguf", "old-model.gguf"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_activation_patches_hermes_configs_and_restarts_hermes(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://llama-server:8080/v1\"\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"hermes"})
        monkeypatch.setattr(_mod, "resolve_compose_flags", lambda: [])
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod,
            "_container_exists",
            lambda container: container == "ods-hermes",
        )
        monkeypatch.setattr(
            _mod,
            "_read_hermes_container_config",
            lambda: hermes_live.read_text(encoding="utf-8"),
        )

        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert '  default: "new-model.gguf"' in hermes_live.read_text(encoding="utf-8")
        assert '  default: "new-model.gguf"' in hermes_template.read_text(encoding="utf-8")
        assert [
            "docker", "compose", "up", "-d", "--no-deps",
            "--force-recreate", "hermes",
        ] in calls
        assert ["docker", "restart", "ods-hermes"] not in calls

    def test_activation_uses_catalog_context_instead_of_current_env_floor(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=131072\n"
            "MAX_CONTEXT=131072\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"old-model\"\n"
            "  provider: \"custom\"\n"
            "  base_url: \"http://host.docker.internal:8080/v1\"\n"
            "  context_length: 32768\n"
            "auxiliary:\n"
            "  compression:\n"
            "    context_length: 32768\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "MAX_CONTEXT=4096" in env_text
        assert "CTX_SIZE=4096" in env_text
        assert "  context_length: 4096" in hermes_live.read_text(encoding="utf-8")
        assert "    context_length: 4096" in hermes_live.read_text(encoding="utf-8")
        assert '  base_url: "http://host.docker.internal:8080/v1"' in hermes_live.read_text(encoding="utf-8")

    def test_activation_preserves_matching_recommended_context(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=Phi-4-mini-instruct-Q4_K_M.gguf\n"
            "LLM_MODEL=phi-4-mini\n"
            "CTX_SIZE=128000\n"
            "MAX_CONTEXT=128000\n"
            "MODEL_RECOMMENDED_MODEL=new-model\n"
            "MODEL_RECOMMENDED_GGUF=new-model.gguf\n"
            "MODEL_RECOMMENDED_CONTEXT=65536\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_text = (
            "model:\n"
            "  default: \"Phi-4-mini-instruct-Q4_K_M.gguf\"\n"
            "  provider: \"custom\"\n"
            "  context_length: 128000\n"
            "auxiliary:\n"
            "  compression:\n"
            "    context_length: 128000\n"
        )
        hermes_live.write_text(hermes_text, encoding="utf-8")
        hermes_template.write_text(hermes_text, encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "MAX_CONTEXT=65536" in env_text
        assert "CTX_SIZE=65536" in env_text
        assert "n-ctx = 65536" in models_ini.read_text(encoding="utf-8")
        assert "  context_length: 65536" in hermes_live.read_text(encoding="utf-8")
        assert "    context_length: 65536" in hermes_live.read_text(encoding="utf-8")
        assert "  context_length: 65536" in hermes_template.read_text(encoding="utf-8")

    def test_activation_ignores_recommended_context_for_other_model(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=131072\n"
            "MAX_CONTEXT=131072\n"
            "MODEL_RECOMMENDED_MODEL=other-model\n"
            "MODEL_RECOMMENDED_GGUF=other-model.gguf\n"
            "MODEL_RECOMMENDED_CONTEXT=65536\n"
            "OLLAMA_PORT=8080\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        assert "MAX_CONTEXT=4096" in env_text
        assert "CTX_SIZE=4096" in env_text

    def test_activation_updates_uid_owned_hermes_config_through_container(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        hermes_live.write_text("model:\n  default: \"old-live\"\n", encoding="utf-8")
        hermes_template.write_text("model:\n  default: \"old-template\"\n", encoding="utf-8")

        original_read_bytes = Path.read_bytes

        def fake_read_bytes(path, *args, **kwargs):
            if path == hermes_live:
                raise PermissionError("container-owned")
            return original_read_bytes(path, *args, **kwargs)

        container_config = {"text": hermes_live.read_text(encoding="utf-8")}
        container_writes = []

        def write_container_config(text):
            container_writes.append(text)
            container_config["text"] = text

        monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"hermes"})
        monkeypatch.setattr(_mod, "resolve_compose_flags", lambda: [])
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_container_running", lambda name: name == "ods-hermes")
        monkeypatch.setattr(_mod, "_container_exists", lambda name: name == "ods-hermes")
        monkeypatch.setattr(
            _mod,
            "_read_hermes_container_config",
            lambda: container_config["text"],
        )
        monkeypatch.setattr(_mod, "_write_hermes_container_config", write_container_config)

        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert container_writes
        assert '  default: "new-model.gguf"' in container_config["text"]
        assert '  default: "new-model.gguf"' in hermes_template.read_text(encoding="utf-8")
        assert [
            "docker", "compose", "up", "-d", "--no-deps",
            "--force-recreate", "hermes",
        ] in calls
        assert ["docker", "restart", "ods-hermes"] not in calls

    def test_capture_hermes_config_falls_back_when_stat_is_denied(
        self, tmp_path, monkeypatch,
    ):
        hermes_live = tmp_path / "data" / "hermes" / "config.yaml"
        container_text = "model:\n  default: \"old-live\"\n"
        original_lstat = Path.lstat

        def fake_lstat(path, *args, **kwargs):
            if path == hermes_live:
                raise PermissionError("container-owned directory")
            return original_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", fake_lstat)
        monkeypatch.setattr(_mod, "_container_exists", lambda name: name == "ods-hermes")
        monkeypatch.setattr(_mod, "_container_running", lambda name: name == "ods-hermes")
        monkeypatch.setattr(_mod, "_read_hermes_container_config", lambda: container_text)

        snapshot = _mod._capture_hermes_live_config(hermes_live)

        assert snapshot["exists"] is True
        assert snapshot["source"] == "container"
        assert snapshot["text"] == container_text

    @pytest.mark.parametrize("denied_method", ["lstat", "read_bytes"])
    @pytest.mark.parametrize("outcome", ["success", "rollback", "appeared"])
    def test_absent_hermes_private_state_does_not_block_pixel_activation(
        self, tmp_path, monkeypatch, denied_method, outcome,
    ):
        install_dir, env_path, env_before, *_ = _write_model_activation_fixture(tmp_path)
        env_before = env_before.replace("CTX_SIZE=2048", "CTX_SIZE=4096")
        env_path.write_text(env_before, encoding="utf-8")
        private_file = install_dir / "data" / "hermes" / "config.yaml"
        private_file.parent.mkdir(parents=True)
        private_file.write_text('model:\n  default: "old-private"\n', encoding="utf-8")
        private_file.chmod(0o600)
        original_bytes = private_file.read_bytes()
        original_stat = private_file.stat()
        original_method = getattr(Path, denied_method)

        def deny_private(path, *args, **kwargs):
            if path == private_file:
                raise PermissionError("container-owned private data")
            return original_method(path, *args, **kwargs)

        probes = []

        def exists(name):
            probes.append(name)
            return outcome == "appeared" and name == "ods-hermes" and len(probes) > 1

        def reconcile(model, _context, **_options):
            if outcome == "rollback" and model == "new-model.gguf":
                raise RuntimeError("injected Pixel reconciliation failure")
            return "reconciled"

        monkeypatch.setattr(Path, denied_method, deny_private)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_container_exists", exists)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda _name: {
            "exists": False, "running": False,
        })
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(_mod, "_reconcile_ods_managed_pixel_model", reconcile)
        monkeypatch.setattr(_mod, "_remove_hermes_live_config", lambda _path: pytest.fail(
            "must not remove unobservable private state on rollback"
        ))
        monkeypatch.setattr(_mod, "_write_hermes_live_config", lambda *_args: pytest.fail(
            "must not mutate unobservable private state"
        ))
        handler = _ResponseHandler()
        _mod.AgentHandler._do_model_activate(handler, "target-model")
        response = handler.parse_response()
        if outcome == "success":
            assert handler.response_code == 200, response
            assert response["consumers"]["hermes"] == "deferred_absent"
            assert response["consumers"]["openclaw"] == "host_gateway_reconciled"
        else:
            assert handler.response_code == 500, response
            assert env_path.read_text(encoding="utf-8") == env_before
            if outcome == "rollback":
                assert response["rolled_back"] is True
            else:
                assert "Hermes appeared" in response["error"]
        monkeypatch.setattr(Path, denied_method, original_method)
        assert private_file.read_bytes() == original_bytes
        after = private_file.stat()
        assert (after.st_mode, after.st_uid, after.st_gid) == (
            original_stat.st_mode, original_stat.st_uid, original_stat.st_gid,
        )

    @pytest.mark.parametrize("exists", [True, False])
    def test_inaccessible_hermes_snapshot_records_deferred_state_only_when_absent(
        self, monkeypatch, exists,
    ):
        monkeypatch.setattr(_mod, "_container_exists", lambda _name: exists)
        monkeypatch.setattr(_mod, "_container_running", lambda _name: False)
        if exists:
            with pytest.raises(RuntimeError, match="not running"):
                _mod._capture_inaccessible_hermes_config(exists=True)
        else:
            snapshot = _mod._capture_inaccessible_hermes_config(exists=True)
            assert snapshot["exists"] is True
            assert snapshot["source"] == "deferred_absent"
            assert snapshot["bytes"] is None

    def test_inaccessible_hermes_docker_probe_error_is_not_absence(self, monkeypatch):
        def probe(_name):
            raise RuntimeError("Docker daemon unavailable")
        monkeypatch.setattr(_mod, "_container_exists", probe)
        with pytest.raises(RuntimeError, match="Docker daemon unavailable"):
            _mod._capture_inaccessible_hermes_config()

    def test_activation_repairs_malformed_models_ini_directory(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        models_ini.unlink()
        models_ini.mkdir()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert models_ini.is_file()
        text = models_ini.read_text(encoding="utf-8")
        assert "[new-model]" in text
        assert "filename = new-model.gguf" in text

    def test_activation_applies_matching_runtime_profile_flags(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8")
            + "MODEL_RECOMMENDED_MODEL=new-model\n"
            + "MODEL_RECOMMENDED_GGUF=new-model.gguf\n"
            + "MODEL_RECOMMENDED_CONTEXT=131072\n"
            # Former checkpoint key name that no llama.cpp build reads.
            + "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS=-1\n",
            encoding="utf-8",
        )
        model_library = install_dir / "config" / "model-library.json"
        model_library.write_text(json.dumps({
            "models": [{
                "id": "target-model",
                "gguf_file": "new-model.gguf",
                "gguf_url": "https://example.test/new-model.gguf",
                "gguf_sha256": hashlib.sha256(b"model").hexdigest(),
                "llm_model_name": "new-model",
                "context_length": 131072,
                "runtime_profiles": [{
                    "id": "nvidia-8gb-test",
                    "label": "Advanced test profile",
                    "backend": "nvidia",
                    "memory_type": "discrete",
                    "vram_min_gb": 7.5,
                    "vram_max_gb": 12.5,
                    "system_ram_min_gb": 32,
                    "context_length": 65536,
                    "llama_server_image": "example.test/llama:turbo",
                    "env": {
                        "LLAMA_PARALLEL": "1",
                        "LLAMA_ARG_FLASH_ATTN": "on",
                        "LLAMA_ARG_CACHE_TYPE_K": "q8_0",
                        "LLAMA_ARG_CACHE_TYPE_V": "turbo3",
                        "LLAMA_ARG_N_CPU_MOE": "30",
                        "LLAMA_ARG_NO_CACHE_PROMPT": "1",
                        "LLAMA_ARG_CHECKPOINT_EVERY_NT": "-1",
                        "LLAMA_ARG_CTX_CHECKPOINTS": "4",
                        "LLAMA_ARG_CACHE_RAM": "1024",
                        "LLAMA_ARG_SPEC_TYPE": "draft-mtp",
                        "LLAMA_ARG_SPEC_DRAFT_N_MAX": "3",
                    },
                }],
            }]
        }), encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod, "_nvidia_vram_gb", lambda: 8.0)
        monkeypatch.setattr(_mod, "_system_ram_gb", lambda: 32)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        env_text = env_path.read_text(encoding="utf-8")
        # A hardware-specific runtime profile is the safety boundary. The
        # generic installer recommendation must not silently replace it.
        assert "MAX_CONTEXT=65536" in env_text
        assert "MODEL_RUNTIME_PROFILE=nvidia-8gb-test" in env_text
        assert "LLAMA_SERVER_IMAGE=example.test/llama:turbo" in env_text
        assert "LLAMA_ARG_CACHE_TYPE_V=turbo3" in env_text
        assert "LLAMA_ARG_N_CPU_MOE=30" in env_text
        assert "LLAMA_ARG_CHECKPOINT_EVERY_NT=-1" in env_text
        assert "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS" not in env_text
        assert "LLAMA_ARG_CTX_CHECKPOINTS=4" in env_text
        assert "LLAMA_ARG_CACHE_RAM=1024" in env_text
        assert "LLAMA_ARG_SPEC_TYPE=draft-mtp" in env_text
        assert "LLAMA_ARG_SPEC_DRAFT_N_MAX=3" in env_text

    def test_explicit_context_overrides_profile_and_installer_recommendation(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8")
            + "MODEL_RECOMMENDED_MODEL=new-model\n"
            + "MODEL_RECOMMENDED_GGUF=new-model.gguf\n"
            + "MODEL_RECOMMENDED_CONTEXT=65536\n",
            encoding="utf-8",
        )
        model_library = install_dir / "config" / "model-library.json"
        model_library.write_text(json.dumps({
            "models": [{
                "id": "target-model",
                "gguf_file": "new-model.gguf",
                "gguf_url": "https://example.test/new-model.gguf",
                "gguf_sha256": hashlib.sha256(b"model").hexdigest(),
                "llm_model_name": "new-model",
                "context_length": 8192,
                "max_context_length": 262144,
                "runtime_profiles": [{
                    "id": "nvidia-8gb-64k",
                    "backend": "nvidia",
                    "memory_type": "discrete",
                    "context_length": 65536,
                    "env": {
                        "LLAMA_ARG_CACHE_TYPE_K": "q4_0",
                        "LLAMA_ARG_CACHE_TYPE_V": "q4_0",
                        "LLAMA_SERVER_MEMORY_LIMIT": "8G",
                    },
                }],
            }]
        }), encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_select_runtime_profile",
            lambda _model, _env: {
                "id": "nvidia-8gb-64k",
                "context_length": 65536,
                "env": {
                    "LLAMA_ARG_CACHE_TYPE_K": "q4_0",
                    "LLAMA_ARG_CACHE_TYPE_V": "q4_0",
                    "LLAMA_SERVER_MEMORY_LIMIT": "8G",
                },
            },
        )
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)

        def verified_requested_context(env, *_args, **kwargs):
            context = int(env["MAX_CONTEXT"])
            if kwargs.get("return_proof"):
                return {
                    "identity": "new-model.gguf",
                    "contextLength": context,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-20T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return "new-model.gguf"
            return True

        monkeypatch.setattr(
            _mod,
            "_wait_for_model_readiness",
            verified_requested_context,
        )

        def fake_run(cmd, **_kwargs):
            stdout = _llama_identity_response("new-model.gguf") if cmd and cmd[0] == "curl" else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=524288,
        )

        assert handler.response_code == 200
        assert handler.parse_response()["context_length"] == 524288
        env = _mod.load_env(env_path)
        assert env["MAX_CONTEXT"] == "524288"
        assert env["CTX_SIZE"] == "524288"
        assert env["MODEL_RUNTIME_PROFILE"] == "nvidia-8gb-64k"
        assert env["LLAMA_ARG_CACHE_TYPE_K"] == "q4_0"
        assert env["LLAMA_ARG_CACHE_TYPE_V"] == "q4_0"
        assert env["LLAMA_SERVER_MEMORY_LIMIT"] == "8G"
        assert "n-ctx = 524288" in (
            install_dir / "config" / "llama-server" / "models.ini"
        ).read_text(encoding="utf-8")
        receipt = json.loads(
            (install_dir / "data" / "model-activation-receipt.json").read_text(
                encoding="utf-8",
            )
        )
        assert receipt["contextLength"] == 524288
        assert receipt["contextVerified"] is True
        assert receipt["consumers"]["open-webui"] == "dynamic_route"
        assert receipt["consumers"]["dashboard"] == "live_env"

    def test_unexpected_failure_rolls_back_all_config_backups(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, ini_text, local_yaml, local_text = (
            _write_model_activation_fixture(
                tmp_path, gpu_backend="amd", litellm_local_text="model_list:\n  - model_name: old\n",
            )
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)

        def fail_restart(_env):
            raise RuntimeError("restart failed")

        monkeypatch.setattr(_mod, "_compose_restart_llama_server", fail_restart)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "restart failed" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text
        assert local_yaml.read_text(encoding="utf-8") == local_text

    def test_activation_rejects_corrupt_catalog_artifact_before_mutation(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        (install_dir / "data" / "models" / "new-model.gguf").write_bytes(b"corrupt")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda _env: pytest.fail("corrupt artifact must not restart runtime"),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 400
        assert "failed catalog verification" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    @pytest.mark.parametrize(
        ("gpu_backend", "catalog_image", "blocked"),
        [
            ("nvidia", None, True),
            ("cpu", None, True),
            ("apple", "ghcr.io/ggml-org/llama.cpp:server-cuda-b11146@sha256:" + "a" * 64, True),
            ("nvidia", "ghcr.io/ggml-org/llama.cpp:server-cuda-b11146@sha256:" + "a" * 64, False),
        ],
    )
    def test_activation_refuses_model_the_default_runtime_cannot_load(
        self, tmp_path, monkeypatch, gpu_backend, catalog_image, blocked,
    ):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path, gpu_backend=gpu_backend)
        )
        library_path = install_dir / "config" / "model-library.json"
        library = json.loads(library_path.read_text(encoding="utf-8"))
        library["models"][0]["llama_server_image"] = catalog_image
        library["models"][0]["default_runtime_compatibility"] = {
            "status": "incompatible",
            "runtime": "llama.cpp b9014",
            "reason": "internal detail",
            "userNote": "This model needs a newer llama.cpp runtime than ODS installs by default.",
        }
        library_path.write_text(json.dumps(library), encoding="utf-8")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        reached_runtime = []

        def refuse(*_args, **_kwargs):
            reached_runtime.append(True)
            raise RuntimeError("stop after the runtime-compatibility gate")

        monkeypatch.setattr(_mod, "_select_runtime_profile", refuse)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        if blocked:
            assert handler.response_code == 400
            assert handler.parse_response()["error"] == (
                "This model needs a newer llama.cpp runtime than ODS installs by default."
            )
            assert reached_runtime == []
        else:
            assert reached_runtime == [True]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_identity_without_meaningful_completion_rolls_back_and_proves_previous(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        runtime_restarts = []
        completion_models = []

        def restart(env):
            runtime_restarts.append(env["GGUF_FILE"])

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "curl":
                active = _mod.load_env(env_path)["GGUF_FILE"]
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=_llama_identity_response(active),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def completion_ready(_host, _port, model_name, _prefix, **_expected):
            completion_models.append(model_name)
            return model_name == "old-model"

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", restart)
        monkeypatch.setattr(_mod, "_chat_completion_ready", completion_ready)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert runtime_restarts == ["new-model.gguf", "old-model.gguf"]
        assert "new-model" in completion_models
        assert completion_models[-1] == "old-model"
        assert _mod.load_env(env_path)["GGUF_FILE"] == "old-model.gguf"

    def test_exception_rollback_restarts_dependents_before_proving_previous_route(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        events = []
        litellm_restarts = 0

        def restart_runtime(env):
            events.append(f"runtime:{env['GGUF_FILE']}")

        def readiness(env, **kwargs):
            events.append(f"ready:{kwargs['gguf_file']}")
            if kwargs.get("return_proof"):
                return {
                    "identity": kwargs["gguf_file"],
                    "contextLength": 65536,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-20T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return kwargs["gguf_file"]
            return True

        def restart_dependent(container, _state=None, **kwargs):
            nonlocal litellm_restarts
            events.append(f"dependent:{container}:{kwargs.get('recreate')}")
            if container == "ods-litellm":
                litellm_restarts += 1
                if litellm_restarts == 1:
                    raise RuntimeError("simulated dependent restart failure")
                return True
            return False

        def restore_dependent(container, _state, **kwargs):
            events.append(f"restore:{container}:{kwargs.get('recreate')}")
            return True

        states = {
            "ods-litellm": {"exists": True, "running": True},
            "ods-hermes": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", restart_runtime)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        monkeypatch.setattr(_mod, "_restart_existing_container", restart_dependent)
        monkeypatch.setattr(_mod, "_restore_container_state", restore_dependent)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert events == [
            "runtime:new-model.gguf",
            "ready:new-model.gguf",
            "dependent:ods-litellm:True",
            "runtime:old-model.gguf",
            "restore:ods-litellm:True",
            "ready:old-model.gguf",
        ]



    def test_activation_succeeds_without_optional_dependents(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(_mod, "_container_exists", lambda _container: False)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200

    @pytest.mark.parametrize(
        ("system_name", "expected_model_id"),
        [
            ("Linux", "new-model"),
            ("Windows", "new-model"),
        ],
    )
    def test_activation_updates_both_opencode_configs_without_losing_user_settings(
        self, tmp_path, monkeypatch, system_name, expected_model_id,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        primary.write_text(
            json.dumps({
                "model": "llama-server/old-model",
                "small_model": "llama-server/old-model",
                "theme": "system",
                "agent": {
                    "build": {"model": "custom-cloud/owner-model", "temperature": 0.2},
                    "reviewer": {"model": "custom-cloud/review-model"},
                },
                "provider": {
                    "custom-cloud": {"npm": "@ai-sdk/openai"},
                    "llama-server": {
                        "options": {
                            "baseURL": "http://127.0.0.1:8080/v1",
                            "apiKey": "no-key",
                            "timeout": 900,
                        },
                        "models": {"old-model": {"name": "old-model"}},
                    },
                },
            }),
            encoding="utf-8",
        )
        compat.write_text(
            json.dumps({
                "model": "llama-server/old-model",
                "compat_only": True,
                "provider": {},
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod.platform, "system", lambda: system_name)
        monkeypatch.setattr(_mod, "_restart_managed_opencode", lambda _state=None: False)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        for path in (primary, compat):
            config = json.loads(path.read_text(encoding="utf-8"))
            assert config["model"] == f"llama-server/{expected_model_id}"
            assert config["small_model"] == f"llama-server/{expected_model_id}"
            assert config["agent"]["build"]["model"] == (
                "custom-cloud/owner-model" if path == primary
                else f"llama-server/{expected_model_id}"
            )
            assert config["agent"]["plan"]["model"] == f"llama-server/{expected_model_id}"
            provider = config["provider"]["llama-server"]
            assert provider["options"]["baseURL"] == "http://127.0.0.1:8080/v1"
            assert provider["options"]["apiKey"] == "no-key"
            assert provider["models"][expected_model_id]["limit"] == {
                "context": 4096,
                "output": 1024,
            }
        primary_config = json.loads(primary.read_text(encoding="utf-8"))
        compat_config = json.loads(compat.read_text(encoding="utf-8"))
        assert primary_config["theme"] == "system"
        assert primary_config["provider"]["custom-cloud"] == {
            "npm": "@ai-sdk/openai"
        }
        assert primary_config["provider"]["llama-server"]["options"]["timeout"] == 900
        assert primary_config["agent"]["build"]["temperature"] == 0.2
        assert primary_config["agent"]["reviewer"] == {
            "model": "custom-cloud/review-model"
        }
        assert compat_config["compat_only"] is True

    def test_switchboard_activation_routes_opencode_through_stable_alias(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_text
            + "ODS_MODEL_SWITCHBOARD=enabled\n"
            + "LITELLM_KEY=switchboard-secret\n"
            + "LITELLM_PORT=4100\n",
            encoding="utf-8",
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        stale = {
            "model": "llama-server/qwen3-coder-next",
            "small_model": "llama-server/qwen3-coder-next",
            "theme": "system",
            "provider": {
                "llama-server": {
                    "options": {
                        "baseURL": "http://127.0.0.1:11434/v1",
                        "apiKey": "no-key",
                    },
                    "models": {
                        "qwen3-coder-next": {"name": "Qwen 3 Coder Next"},
                    },
                },
            },
        }
        primary.write_text(json.dumps(stale), encoding="utf-8")
        compat.write_text(json.dumps(stale), encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_restart_managed_opencode", lambda _state=None: False)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_render_model_router_runtime_configs", lambda *_a, **_k: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        for path in (primary, compat):
            config = json.loads(path.read_text(encoding="utf-8"))
            assert config["theme"] == "system"
            assert config["model"] == "llama-server/ods/current"
            assert config["small_model"] == "llama-server/ods/current"
            assert config["agent"]["build"]["model"] == "llama-server/ods/current"
            assert config["agent"]["plan"]["model"] == "llama-server/ods/current"
            provider = config["provider"]["llama-server"]
            assert provider["name"] == "ODS switchboard"
            assert provider["options"] == {
                "baseURL": "http://127.0.0.1:4100/v1",
                "apiKey": "switchboard-secret",
            }
            assert "qwen3-coder-next" not in provider["models"]
            assert provider["models"]["ods/current"]["limit"] == {
                "context": 4096,
                "output": 1024,
            }

    def test_opencode_update_failure_restores_exact_files(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        original = '{"model":"llama-server/old-model","provider":{}}\n'
        primary.write_text(original, encoding="utf-8")
        perplexica_snapshot = TestPerplexicaModelRoute._snapshot()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod,
            "_capture_perplexica_config",
            lambda _env, _state=None: perplexica_snapshot,
        )
        monkeypatch.setattr(
            _mod,
            "_update_perplexica_model",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("simulated downstream failure")
            ),
        )
        monkeypatch.setattr(_mod, "_restore_perplexica_config", lambda _snapshot: None)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert primary.read_text(encoding="utf-8") == original
        assert not compat.exists()

    def test_activation_rejects_unrecoverable_opencode_config(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "home" / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        primary.write_text("not-json", encoding="utf-8")
        compat.write_text("[]", encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "OpenCode config is malformed" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text

    def test_cli_activation_metadata_persists_tier_and_bounded_context(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod,
            "_resolve_requested_tier_contract",
            lambda _tier, _env: {
                "GGUF_FILE": "new-model.gguf",
                "MAX_CONTEXT": "4096",
                "LLM_MODEL": "new-model",
            },
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=2048,
            requested_tier="0",
        )

        assert handler.response_code == 200
        receipt = handler.parse_response()
        assert receipt["status"] == "activated"
        assert receipt["model_id"] == "target-model"
        assert receipt["llm_model"] == "new-model"
        assert receipt["gguf_file"] == "new-model.gguf"
        assert receipt["tier"] == "0"
        assert receipt["context_length"] == 2048
        assert receipt["consumers"]["dashboard"] == "live_env"
        assert receipt["consumers"]["open-webui"] == "dynamic_route"
        completion_receipt = json.loads(
            (install_dir / "data" / "model-activation-receipt.json").read_text(
                encoding="utf-8"
            )
        )
        assert completion_receipt["schema"] == "ods.model-activation-receipt.v1"
        assert completion_receipt["status"] == "complete"
        assert completion_receipt["modelId"] == "target-model"
        assert completion_receipt["ggufFile"] == "new-model.gguf"
        assert completion_receipt["runtimeModelId"] == "new-model.gguf"
        assert completion_receipt["consumers"] == receipt["consumers"]
        env = _mod.load_env(env_path)
        assert env["TIER"] == "0"
        assert env["CTX_SIZE"] == "2048"
        assert env["MAX_CONTEXT"] == "2048"
        assert env["GGUF_URL"] == "https://example.test/new-model.gguf"
        assert env["GGUF_SHA256"] == hashlib.sha256(b"model").hexdigest()

    def test_activation_rejects_context_above_explicit_tier_limit(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        monkeypatch.setattr(
            _mod,
            "_resolve_requested_tier_contract",
            lambda _tier, _env: {
                "GGUF_FILE": "new-model.gguf",
                "LLM_MODEL": "new-model",
                "MAX_CONTEXT": "4096",
            },
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=8192,
            requested_tier="1",
        )

        assert handler.response_code == 400
        assert handler.parse_response()["code"] == "tier_context_mismatch"
        assert "exceeds tier 1 limit 4096" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == env_text
        assert restarts == []

    def test_activation_updates_perplexica_after_model_readiness(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        snapshot = TestPerplexicaModelRoute._snapshot()
        updates = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod, "_capture_perplexica_config", lambda _env, _state=None: snapshot
        )
        monkeypatch.setattr(
            _mod,
            "_update_perplexica_model",
            lambda env, captured, **kwargs: updates.append((dict(env), captured, kwargs)),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert len(updates) == 1
        assert updates[0][1] is snapshot
        assert updates[0][2] == {"gguf_file": "new-model.gguf"}
        assert updates[0][0]["GGUF_FILE"] == "new-model.gguf"



    def test_perplexica_update_failure_restores_snapshot_during_rollback(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        snapshot = TestPerplexicaModelRoute._snapshot()
        restores = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod, "_capture_perplexica_config", lambda _env, _state=None: snapshot
        )

        def fail_update(*_args, **_kwargs):
            raise RuntimeError("simulated Perplexica update failure")

        monkeypatch.setattr(_mod, "_update_perplexica_model", fail_update)
        monkeypatch.setattr(
            _mod,
            "_restore_perplexica_config",
            lambda captured: restores.append(captured),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert "Perplexica update failure" in response["error"]
        assert restores == [snapshot]

    def test_context_round_trip_restores_each_catalog_value(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        models_dir = install_dir / "data" / "models"
        model_a = b"model a"
        model_b = b"model b"
        (models_dir / "model-a.gguf").write_bytes(model_a)
        (models_dir / "model-b.gguf").write_bytes(model_b)
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": [
                {
                    "id": "model-a",
                    "gguf_file": "model-a.gguf",
                    "gguf_url": "https://example.test/model-a.gguf",
                    "gguf_sha256": hashlib.sha256(model_a).hexdigest(),
                    "llm_model_name": "model-a",
                    "context_length": 8192,
                },
                {
                    "id": "model-b",
                    "gguf_file": "model-b.gguf",
                    "gguf_url": "https://example.test/model-b.gguf",
                    "gguf_sha256": hashlib.sha256(model_b).hexdigest(),
                    "llm_model_name": "model-b",
                    "context_length": 32768,
                },
            ]}),
            encoding="utf-8",
        )
        env_path.write_text(
            "GPU_BACKEND=nvidia\nGGUF_FILE=model-a.gguf\nLLM_MODEL=model-a\n"
            "CTX_SIZE=8192\nMAX_CONTEXT=8192\nOLLAMA_PORT=8080\n",
            encoding="utf-8",
        )
        restarted_contexts = []

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: restarted_contexts.append(int(env["MAX_CONTEXT"])),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)

        handler_b = _ResponseHandler()
        _mod.AgentHandler._do_model_activate(handler_b, "model-b")
        handler_a = _ResponseHandler()
        _mod.AgentHandler._do_model_activate(handler_a, "model-a")

        assert handler_b.response_code == 200
        assert handler_a.response_code == 200
        assert restarted_contexts == [32768, 8192]
        assert _mod.load_env(env_path)["MAX_CONTEXT"] == "8192"

    def test_in_container_activation_preserves_host_specific_image_without_override(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_path.write_text(
            env_path.read_text(encoding="utf-8")
            + "MAX_CONTEXT=2048\nLLAMA_SERVER_IMAGE=host.example/llama:custom\n",
            encoding="utf-8",
        )
        recreates = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setenv("ODS_HOST_INSTALL_DIR", str(install_dir))
        monkeypatch.setattr(
            _mod,
            "_recreate_llama_server",
            lambda env, override_image="": recreates.append((dict(env), override_image)),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert recreates[0][1] == "host.example/llama:custom"
        assert recreates[0][0]["LLAMA_SERVER_IMAGE"] == "host.example/llama:custom"
        assert _mod.load_env(env_path)["LLAMA_SERVER_IMAGE"] == "host.example/llama:custom"

    def test_pre_snapshot_failure_does_not_overwrite_configs(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        def fail_load_env(_path):
            raise OSError("cannot read env")

        monkeypatch.setattr(_mod, "load_env", fail_load_env)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert env_path.read_text(encoding="utf-8") == env_text
        assert models_ini.read_text(encoding="utf-8") == ini_text

    def test_activation_preserves_stopped_consumers(self, tmp_path, monkeypatch):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        states = {
            name: {"exists": True, "running": False}
            for name in (
                "ods-litellm",
                "ods-hermes",
                "ods-perplexica",
            )
        }
        docker_calls = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod, "_wait_for_model_readiness", _mock_verified_readiness
        )
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda cmd, **_kwargs: (
                docker_calls.append(cmd)
                or subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            ),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        receipt = handler.parse_response()["consumers"]
        assert receipt["litellm"] == "stopped"
        # Only Pixel's host gateway is reported under this key now.
        assert receipt["openclaw"] == "not_installed"
        assert receipt["perplexica"] == "stopped"
        assert not any(call[:2] in (["docker", "restart"], ["docker", "stop"]) for call in docker_calls)

    def test_transient_hermes_unhealthy_recreates_once_without_rollback(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = (
            install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        )
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        old_config = (
            "model:\n"
            '  default: "old-model.gguf"\n'
            "  context_length: 2048\n"
        )
        hermes_live.write_text(old_config, encoding="utf-8")
        hermes_template.write_text(old_config, encoding="utf-8")
        states = {
            "ods-litellm": {"exists": False, "running": False},
            "ods-hermes": {"exists": True, "running": True},
            "ods-perplexica": {"exists": False, "running": False},
        }
        runtime_models = []
        restart_calls = []
        health_checks = []

        def restart(container, _state=None, **kwargs):
            restart_calls.append((container, kwargs.get("recreate")))
            return container == "ods-hermes"

        def check_health(container):
            health_checks.append(container)
            if len(health_checks) == 1:
                raise _mod.ContainerUnhealthyError("simulated transient unhealthy Hermes")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: runtime_models.append(env["GGUF_FILE"]),
        )
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(_mod, "_restart_existing_container", restart)
        monkeypatch.setattr(_mod, "_verify_running_hermes_route", lambda *_args: None)
        monkeypatch.setattr(_mod, "_wait_for_container_health", check_health)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert runtime_models == ["new-model.gguf"]
        assert [call for call in restart_calls if call[0] == "ods-hermes"] == [
            ("ods-hermes", True),
            ("ods-hermes", True),
        ]
        assert health_checks == ["ods-hermes", "ods-hermes"]
        assert _mod.load_env(env_path)["GGUF_FILE"] == "new-model.gguf"
        assert handler.parse_response()["consumers"]["hermes"] == "restarted"

    def test_repeated_hermes_unhealthy_rolls_back_previous_route(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        hermes_live = install_dir / "data" / "hermes" / "config.yaml"
        hermes_template = (
            install_dir / "extensions" / "services" / "hermes" / "cli-config.yaml.template"
        )
        hermes_live.parent.mkdir(parents=True)
        hermes_template.parent.mkdir(parents=True)
        old_config = (
            "model:\n"
            '  default: "old-model.gguf"\n'
            "  context_length: 2048\n"
        )
        hermes_live.write_text(old_config, encoding="utf-8")
        hermes_template.write_text(old_config, encoding="utf-8")
        completion_receipt = install_dir / "data" / "model-activation-receipt.json"
        old_receipt = {
            "schema": "ods.model-activation-receipt.v1",
            "status": "complete",
            "modelId": "old-model",
            "ggufFile": "old-model.gguf",
            "runtimeModelId": "old-model.gguf",
            "consumers": {"hermes": "restarted"},
        }
        completion_receipt.write_text(json.dumps(old_receipt), encoding="utf-8")
        states = {
            "ods-litellm": {"exists": False, "running": False},
            "ods-hermes": {"exists": True, "running": True},
            "ods-perplexica": {"exists": False, "running": False},
        }
        runtime_models = []
        health_checks = 0

        def check_health(container):
            nonlocal health_checks
            assert container == "ods-hermes"
            health_checks += 1
            if health_checks <= 2:
                raise _mod.ContainerUnhealthyError("simulated unhealthy Hermes")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_compose_restart_llama_server",
            lambda env: runtime_models.append(env["GGUF_FILE"]),
        )
        monkeypatch.setattr(
            _mod, "_wait_for_model_readiness", _mock_verified_readiness
        )
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None, **_kwargs: name == "ods-hermes",
        )
        monkeypatch.setattr(
            _mod,
            "_restore_container_state",
            lambda name, _state, **_kwargs: name == "ods-hermes",
        )
        monkeypatch.setattr(_mod, "_verify_running_hermes_route", lambda *_args: None)
        monkeypatch.setattr(_mod, "_wait_for_container_health", check_health)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert runtime_models == ["new-model.gguf", "old-model.gguf"]
        assert health_checks == 3
        assert env_path.read_text(encoding="utf-8") == env_text
        assert hermes_live.read_text(encoding="utf-8") == old_config
        assert json.loads(completion_receipt.read_text(encoding="utf-8")) == old_receipt

    def test_runtime_profile_cannot_exceed_requested_tier_context(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_resolve_requested_tier_contract",
            lambda _tier, _env: {
                "GGUF_FILE": "new-model.gguf",
                "LLM_MODEL": "new-model",
                "MAX_CONTEXT": "4096",
            },
        )
        monkeypatch.setattr(
            _mod,
            "_select_runtime_profile",
            lambda _model, _env: {
                "id": "larger-context-profile",
                "label": "Larger context",
                "context_length": 8192,
                "env": {},
            },
        )
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod, "_wait_for_model_readiness", _mock_verified_readiness
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_tier="1",
        )

        assert handler.response_code == 200
        assert handler.parse_response()["context_length"] == 4096
        assert _mod.load_env(env_path)["MAX_CONTEXT"] == "4096"

    def test_tier_model_identity_mismatch_fails_before_mutation(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_resolve_requested_tier_contract",
            lambda _tier, _env: {
                "GGUF_FILE": "new-model.gguf",
                "LLM_MODEL": "different-model",
                "MAX_CONTEXT": "4096",
            },
        )
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_tier="1",
        )

        assert handler.response_code == 400
        assert handler.parse_response()["code"] == "tier_model_mismatch"
        assert env_path.read_text(encoding="utf-8") == env_text
        assert restarts == []

    def test_managed_pixel_rejects_context_below_openclaw_minimum_before_mutation(
        self, tmp_path, monkeypatch,
    ):
        install_dir, env_path, env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        env_text = env_text.replace("CTX_SIZE=2048", "CTX_SIZE=65536")
        env_path.write_text(env_text, encoding="utf-8")
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod,
            "_ods_managed_pixel_identity",
            lambda: ("pixel-owner", tmp_path / "pixel-owner"),
        )
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(
            handler,
            "target-model",
            requested_context_length=2048,
        )

        assert handler.response_code == 400
        response = handler.parse_response()
        assert "at least 4096" in response["error"]
        assert response["code"] == "pixel_context_too_small"
        assert "rolled_back" not in response
        assert env_path.read_text(encoding="utf-8") == env_text
        assert restarts == []

    def test_symlinked_config_is_rejected_before_mutation(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        outside = tmp_path / "outside.ini"
        outside.write_text("outside\n", encoding="utf-8")
        models_ini.unlink()
        try:
            models_ini.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")
        restarts = []
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert "symlinked configuration file" in response["error"]
        assert "rolled_back" not in response
        assert env_path.read_text(encoding="utf-8") == env_text
        assert outside.read_text(encoding="utf-8") == "outside\n"
        assert restarts == []

    def test_concurrent_env_change_is_not_overwritten(self, tmp_path, monkeypatch):
        install_dir, env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        edited = False
        restarts = []

        def capture_state(name):
            nonlocal edited
            # Edit .env while the pre-activation snapshot is being captured;
            # Perplexica is the last container that snapshot records.
            if name == "ods-perplexica" and not edited:
                edited = True
                env_path.write_text("GPU_BACKEND=nvidia\nLLM_MODEL=external-edit\n", encoding="utf-8")
            return {"exists": False, "running": False}

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", capture_state)
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", lambda _env: restarts.append(True)
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert "Configuration changed during model activation" in response["error"]
        assert "rolled_back" not in response
        assert "LLM_MODEL=external-edit" in env_path.read_text(encoding="utf-8")
        assert restarts == []

    def test_rollback_restores_absent_models_ini(self, tmp_path, monkeypatch):
        install_dir, env_path, env_text, models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        models_ini.unlink()
        readiness = iter((False, True))
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
        monkeypatch.setattr(
            _mod,
            "_wait_for_model_readiness",
            lambda *_args, **_kwargs: next(readiness),
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert handler.parse_response()["rolled_back"] is True
        assert env_path.read_text(encoding="utf-8") == env_text
        assert not models_ini.exists()

    def test_jsonc_opencode_update_preserves_settings_and_removes_stale_model(
        self, tmp_path, monkeypatch,
    ):
        config_path = tmp_path / "opencode.jsonc"
        config_path.write_text(
            """{
              // User preference must survive.
              "theme": "system",
              "model": "llama-server/old-model",
              "provider": {
                "llama-server": {
                  "models": {"old-model": {"name": "Old"}},
                },
              },
            }
            """,
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (config_path,))
        snapshot = _mod._capture_opencode_config()

        _mod._update_opencode_config(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"},
            snapshot,
            "new-model",
            4096,
        )

        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["theme"] == "system"
        assert config["model"] == "llama-server/new-model"
        models = config["provider"]["llama-server"]["models"]
        assert "new-model" in models
        assert "old-model" not in models

    def test_current_opencode_config_wins_when_seeding_compat_file(
        self, tmp_path, monkeypatch,
    ):
        current = tmp_path / ".config" / "opencode" / "opencode.json"
        compat = current.parent / "config.json"
        legacy = tmp_path / ".local" / "share" / "opencode" / "opencode.jsonc"
        current.parent.mkdir(parents=True)
        legacy.parent.mkdir(parents=True)
        current.write_text('{"theme":"current"}', encoding="utf-8")
        legacy.write_text('{"theme":"legacy"}', encoding="utf-8")
        monkeypatch.setattr(
            _mod,
            "_opencode_config_paths",
            lambda: (current, compat, legacy),
        )
        snapshot = _mod._capture_opencode_config()

        _mod._update_opencode_config(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080"},
            snapshot,
            "new-model",
            4096,
        )

        assert json.loads(compat.read_text(encoding="utf-8"))["theme"] == "current"

    @pytest.mark.parametrize(
        ("context_length", "expected_output"),
        [(4096, 1024), (32768, 8192), (65536, 16384), (131072, 32768)],
    )
    def test_model_switch_opencode_output_reserves_prompt_context(
        self, tmp_path, monkeypatch, context_length, expected_output,
    ):
        config_path = tmp_path / "config.json"
        config_path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (config_path,))
        snapshot = _mod._capture_opencode_config()

        _mod._update_opencode_config(
            {"ODS_MODEL_SWITCHBOARD": "enabled", "LITELLM_KEY": "test-key"},
            snapshot,
            "qwen3.5-27b-q4",
            context_length,
        )

        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["model"] == "llama-server/ods/current"
        assert config["small_model"] == config["model"]
        limit = config["provider"]["llama-server"]["models"]["ods/current"]["limit"]
        assert limit == {"context": context_length, "output": expected_output}
        assert limit["output"] < limit["context"]

    def test_litellm_is_verified_before_active_opencode_restarts(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        config_dir = tmp_path / "opencode-order"
        config_dir.mkdir()
        primary = config_dir / "opencode.json"
        compat = config_dir / "config.json"
        primary.write_text("{}", encoding="utf-8")
        events = []
        states = {
            "ods-litellm": {"exists": True, "running": True},
            "ods-hermes": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_opencode_config_paths", lambda: (primary, compat))
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_capture_managed_opencode_state",
            lambda: {"system": "Linux", "active": True},
        )
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: events.append("runtime"))
        def readiness(*_args, **kwargs):
            events.append("runtime-ready")
            if kwargs.get("return_proof"):
                return {
                    "identity": kwargs.get("gguf_file"),
                    "contextLength": 65536,
                    "contextVerified": True,
                    "verifiedAt": "2026-07-20T00:00:00+00:00",
                }
            if kwargs.get("return_identity"):
                return kwargs.get("gguf_file")
            return True

        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None, **kwargs: events.append(
                f"restart:{name}:{kwargs.get('recreate')}"
            ) or name == "ods-litellm",
        )
        monkeypatch.setattr(
            _mod,
            "_wait_for_container_health",
            lambda name: events.append(f"health:{name}"),
        )
        monkeypatch.setattr(_mod, "_verify_litellm_route", lambda _env: events.append("litellm-ready"))
        monkeypatch.setattr(
            _mod,
            "_restart_managed_opencode",
            lambda _state=None: events.append("opencode-restart") or True,
        )
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        assert "restart:ods-litellm:True" in events
        assert events.index("restart:ods-litellm:True") < events.index("health:ods-litellm")
        assert events.index("health:ods-litellm") < events.index("litellm-ready")
        assert events.index("litellm-ready") < events.index("opencode-restart")

    def test_rollback_waits_for_restored_litellm_health_before_route_probe(
        self, tmp_path, monkeypatch,
    ):
        install_dir, _env_path, _env_text, _models_ini, _ini_text, _yaml, _yaml_text = (
            _write_model_activation_fixture(tmp_path)
        )
        events = []
        states = {
            "ods-litellm": {"exists": True, "running": True},
            "ods-hermes": {"exists": False, "running": False},
            "ods-perplexica": {"exists": False, "running": False},
        }
        route_probes = 0

        def verify_litellm(_env):
            nonlocal route_probes
            route_probes += 1
            events.append(f"litellm-ready:{route_probes}")
            if route_probes == 1:
                raise RuntimeError("simulated target LiteLLM route failure")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_capture_container_state", lambda name: states[name])
        monkeypatch.setattr(
            _mod,
            "_capture_managed_opencode_state",
            lambda: {"system": "Linux", "active": False},
        )
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: events.append("runtime"))
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
        monkeypatch.setattr(
            _mod,
            "_restart_existing_container",
            lambda name, _state=None, **kwargs: events.append(
                f"target-restart:{name}:{kwargs.get('recreate')}"
            ) or name == "ods-litellm",
        )
        monkeypatch.setattr(
            _mod,
            "_restore_container_state",
            lambda name, _state=None, **kwargs: events.append(
                f"rollback-restart:{name}:{kwargs.get('recreate')}"
            ) or name == "ods-litellm",
        )
        monkeypatch.setattr(
            _mod,
            "_wait_for_container_health",
            lambda name: events.append(f"health:{name}:{route_probes}"),
        )
        monkeypatch.setattr(_mod, "_verify_litellm_route", verify_litellm)
        handler = _ResponseHandler()

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        response = handler.parse_response()
        assert response["rolled_back"] is True
        assert "simulated target LiteLLM route failure" in response["error"]
        rollback_restart = events.index("rollback-restart:ods-litellm:True")
        rollback_health = events.index("health:ods-litellm:1")
        rollback_probe = events.index("litellm-ready:2")
        assert rollback_restart < rollback_health < rollback_probe


class TestNvidiaHealthUnchanged:
    """Ensure the NVIDIA health check still uses the simple '"ok"' check."""

    def test_ok_response_is_healthy(self):
        """llama.cpp health response contains "ok" — should be detected."""
        body = '{"status": "ok"}'
        # The NVIDIA path checks: '"ok"' in body
        assert '"ok"' in body


@pytest.mark.parametrize("fail_consumer", [False, True])
def test_enabled_router_published_before_consumer_probe_and_rollback(tmp_path, monkeypatch, fail_consumer):
    install, env_path, env_text, *_ = _write_model_activation_fixture(tmp_path)
    env_path.write_text(env_text + "ODS_MODEL_SWITCHBOARD=enabled\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install)
    monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _: None)
    monkeypatch.setattr(_mod, "_render_model_router_runtime_configs", lambda *a, **k: None)
    monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
    monkeypatch.setattr(_mod, "_capture_container_state", lambda name: {"exists": name == "ods-litellm", "running": name == "ods-litellm"})
    monkeypatch.setattr(_mod, "_restart_existing_container", lambda name, *a, **k: name == "ods-litellm")
    monkeypatch.setattr(_mod, "_restore_container_state", lambda name, *a, **k: name == "ods-litellm")
    observed = []
    def verify(env):
        state = json.loads((install / "data/model-state.json").read_text())
        assert state["active"]["runtimeModelId"] == env["GGUF_FILE"]
        observed.append((env["GGUF_FILE"], state["routeSeq"]))
        if fail_consumer and env["GGUF_FILE"] == "new-model.gguf":
            raise RuntimeError("consumer failed after route publication")
    monkeypatch.setattr(_mod, "_verify_litellm_route", verify)
    handler = _ResponseHandler()
    _mod.AgentHandler._do_model_activate(handler, "target-model")
    assert handler.response_code == (500 if fail_consumer else 200)
    assert observed[0][0] == "new-model.gguf"
    if fail_consumer:
        assert observed[1][0] == "old-model.gguf"
        assert observed[1][1] > observed[0][1]
        assert env_path.read_text() == env_text + "ODS_MODEL_SWITCHBOARD=enabled\n"


def test_router_publication_rejects_unverified_context(tmp_path, monkeypatch):
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="unverified"):
        _mod._publish_activation_route({}, "target", {"identity": "target", "contextVerified": False}, {})
    assert not (tmp_path / "data/model-state.json").exists()


@pytest.mark.parametrize("failure", ["missing", "permission", "os-error", "invalid-running"])
def test_container_inspection_preserves_non_timeout_outcomes(monkeypatch, failure):
    calls = []

    def inspect(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 15
        if failure == "os-error":
            raise OSError("docker unavailable")
        if failure == "missing":
            return subprocess.CompletedProcess(command, 1, "", "No such container")
        if failure == "permission":
            return subprocess.CompletedProcess(command, 1, "", "permission denied")
        return subprocess.CompletedProcess(command, 0, "fixture-id" if len(calls) == 1 else "invalid", "")

    monkeypatch.setattr(_mod, "_container_exists", _container_exists)
    monkeypatch.setattr(_mod.subprocess, "run", inspect)
    if failure == "missing":
        assert _capture_container_state("ods-litellm") == {"exists": False, "running": False}
        assert len(calls) == 1
    else:
        with pytest.raises(RuntimeError) as error:
            _capture_container_state("ods-litellm")
        assert type(error.value).__name__ != "ContainerInspectionTimeout"
        assert len(calls) == (2 if failure == "invalid-running" else 1)
