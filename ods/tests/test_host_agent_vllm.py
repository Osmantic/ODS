"""The host agent on a vLLM install (LLM_BACKEND=vllm, docker-compose.vllm.yml).

vllm-server serves the one model on the ODS network without a host port, so
the agent proves it through the owned model-router container against the
router's vllm-default endpoint: /health is an empty 200, /v1/models lists the
served id with max_model_len (vLLM has no /props), and the completion names
that exact id. A cold start reads as loading while Docker reports the
container starting. Model switching, profile checks and every path that
would name llama-server to Compose refuse with a clear message instead.
No Docker, network or real install is used.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ods_host_agent_vllm_test", ROOT / "bin" / "ods-host-agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent_vllm_test"] = agent
_spec.loader.exec_module(agent)

SERVED = "GLM-5.3-Flash-NVFP4"
ENV = {
    "ODS_MODE": "local", "GPU_BACKEND": "nvidia", "LLM_BACKEND": "vllm",
    "LLM_MODEL": "glm-5.3-flash-nvfp4", "VLLM_SERVED_MODEL": SERVED,
    "VLLM_MODEL_DIR": "nvidia--GLM-5.3-Flash-NVFP4@da920bb0",
    "GGUF_FILE": "", "CTX_SIZE": "131072", "MAX_CONTEXT": "131072",
}
# GLM-5.3's template has no enable_thinking; its catalog "off" is this.
GLM_OFF = {"reasoning_effort": "low"}


def models(model=SERVED, context=131072):
    return json.dumps({"object": "list", "data": [{
        "id": model, "object": "model", "owned_by": "vllm", "root": "/model", "max_model_len": context}]})


def completion(model=SERVED, content="READY"):
    return json.dumps({"model": model, "choices": [{"message": {"role": "assistant", "content": content},
                                                    "finish_reason": "stop"}]})


class FakeVllm:
    """vllm-server as the router transport reaches it."""

    def __init__(self, *, health=200, listed=None, answer=None, container="healthy"):
        self.health, self.listed, self.answer, self.container = health, listed, answer, container
        self.calls = []

    def request(self, install_dir, origin, path, **kwargs):
        self.calls.append((origin, path, kwargs))
        if path == "/v1/models":
            return self.listed or models()
        if path == "/v1/chat/completions":
            return self.answer or completion()
        # vLLM has no /props: the transport fails an HTTP 404.
        raise OSError(f"Docker runtime transport failed (exit 1): runtime returned HTTP 404 for {path}")

    def exchange(self, install_dir, origin, path, **kwargs):
        self.calls.append((origin, path, kwargs))
        if isinstance(self.health, Exception):
            raise self.health
        return self.health, ""


@pytest.fixture()
def vllm(monkeypatch, tmp_path):
    fake = FakeVllm()
    monkeypatch.setattr(agent, "_router_transport_request", fake.request)
    monkeypatch.setattr(agent, "_router_transport_exchange", fake.exchange)
    monkeypatch.setattr(agent, "_container_health", lambda container: fake.container)
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    (tmp_path / "config").mkdir()
    shutil.copyfile(ROOT / "config" / "model-library.json", tmp_path / "config" / "model-library.json")
    return fake


def prove(env=ENV, **kwargs):
    return agent._wait_for_model_readiness(
        env, model_id="glm-5.3-flash-nvfp4-vllm", gguf_file=SERVED, llm_model_name=env["LLM_MODEL"],
        attempts=kwargs.pop("attempts", 1), initial_delay=0, interval=0, return_proof=True, **kwargs)


def test_route_backend_and_endpoint_follow_llm_backend():
    assert agent._initial_switchboard_backend(ENV) == ("vllm", "vllm-default", None)
    assert agent._switchboard_state.default_endpoint_id("vllm") == "vllm-default"
    for backend in ("", "llama-server", "external", "lemonade"):
        assert agent._initial_switchboard_backend({**ENV, "LLM_BACKEND": backend}) == (
            "llama-server", "llama-server-default", None), backend


def test_vllm_is_proven_through_the_routers_vllm_endpoint(vllm, monkeypatch):
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: pytest.fail("no host port to probe"))
    proof = prove()

    assert proof["identity"] == SERVED
    assert proof["contextLength"] == 131072 and proof["contextVerified"] is True
    assert "vision" not in proof
    assert [call[1] for call in vllm.calls] == ["/health", "/v1/models", "/v1/chat/completions"]
    assert all(call[0] == "http://vllm-server:8000" for call in vllm.calls)
    assert all(call[2]["endpoint_id"] == "vllm-default" and call[2]["api_key"] == "" for call in vllm.calls)
    payload = vllm.calls[-1][2]["payload"]
    # vLLM answers 404 for any name but the served one.
    assert payload["model"] == SERVED
    assert payload["chat_template_kwargs"] == GLM_OFF


def test_qwen_turns_thinking_off_with_its_own_template_argument(vllm):
    env = {**ENV, "LLM_MODEL": "qwen3.8-flash-next-nvfp4", "VLLM_SERVED_MODEL": "Qwen3.8-Flash-Next-NVFP4"}
    vllm.listed = models("Qwen3.8-Flash-Next-NVFP4")
    vllm.answer = completion("Qwen3.8-Flash-Next-NVFP4")
    assert prove(env)["identity"] == "Qwen3.8-Flash-Next-NVFP4"
    assert vllm.calls[-1][2]["payload"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_short_or_missing_served_context_never_proves_the_route(vllm):
    for listed in (models(context=32768), json.dumps({"data": [{"id": SERVED}]}), models("Other-Model")):
        vllm.listed, vllm.calls = listed, []
        diagnosis = {}
        assert prove(diagnosis=diagnosis) == {}
        assert "/v1/chat/completions" not in [call[1] for call in vllm.calls]


def test_cold_start_reads_as_loading_while_docker_reports_starting(vllm):
    vllm.health = OSError("Docker runtime transport failed (exit 1): runtime transport failed: URLError")
    vllm.container = "starting"
    assert agent._runtime_health(ENV) == "loading"
    diagnosis = {}
    assert prove(diagnosis=diagnosis) == {}
    assert diagnosis["reason"].startswith("vLLM is still starting")
    assert "final" not in diagnosis

    vllm.health = 503
    assert agent._runtime_health(ENV) == "loading"
    # Past its start period Docker says unhealthy: an error, not loading.
    vllm.container = "unhealthy"
    assert agent._runtime_health(ENV) == "error"
    vllm.health = OSError("connection refused")
    with pytest.raises(OSError):
        agent._runtime_health(ENV)
    vllm.health, vllm.container = 200, "healthy"
    assert agent._runtime_health(ENV) == "ok"


def test_llama_server_health_is_unchanged(monkeypatch):
    monkeypatch.setattr(agent, "_runtime_http", lambda env, path, **kwargs: "")
    monkeypatch.setattr(agent, "_router_transport_exchange", lambda *a, **k: pytest.fail("vLLM probe"))
    assert agent._runtime_health({"LLM_BACKEND": "llama-server"}) == "error"


def test_catalog_entry_comes_from_llm_model_not_a_leftover_gguf(vllm):
    leftover = {**ENV, "GGUF_FILE": "Qwen3.5-9B-Q4_K_M.gguf"}
    model_id, model = agent._catalog_model_for_current_env(leftover)
    assert model_id == "glm-5.3-flash-nvfp4-vllm" and model["runtime"] == "vllm"
    # A llama-server route never resolves to a vLLM entry.
    llama = {"LLM_BACKEND": "llama-server", "LLM_MODEL": "glm-5.3-flash-nvfp4", "GGUF_FILE": ""}
    assert agent._catalog_model_for_current_env(llama) == ("glm-5.3-flash-nvfp4", {})
    assert agent._vllm_probe_thinking_off(ENV) == GLM_OFF
    assert agent._vllm_probe_thinking_off(llama) is None


def test_compose_resolution_reads_llm_backend_from_the_install(monkeypatch, tmp_path):
    assert "LLM_BACKEND" in agent._HOST_LLM_COMPOSE_SELECTORS
    assert "LLM_BACKEND" in agent._PREPARE_COMPOSE_SELECTORS
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "resolve-compose-stack.sh").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_BACKEND=vllm\nODS_MODE=local\n", encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "_find_usable_bash", lambda: "bash")
    monkeypatch.setattr(agent.platform, "system", lambda: "Linux")
    # A stale value exported when the agent started must not win.
    monkeypatch.setenv("LLM_BACKEND", "llama-server")
    seen = {}

    def run(cmd, **kwargs):
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout="-f docker-compose.base.yml", stderr="")

    monkeypatch.setattr(agent.subprocess, "run", run)
    agent._run_compose_resolver()
    assert seen["LLM_BACKEND"] == "vllm"


def test_router_configs_render_with_the_installed_backend(monkeypatch, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "render-runtime-configs.py").write_text("", encoding="utf-8")
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(agent.subprocess, "run", run)
    monkeypatch.setenv("LLM_BACKEND", "llama-server")
    agent._render_model_router_runtime_configs(
        tmp_path, {**ENV, "ODS_MODEL_SWITCHBOARD": "enabled"},
        model=ENV["LLM_MODEL"], gguf_file="", context_length=131072)
    assert commands and all(cmd[cmd.index("--llm-backend") + 1] == "vllm" for cmd in commands)
    assert all(cmd[cmd.index("--model") + 1] == "glm-5.3-flash-nvfp4" for cmd in commands)
    commands.clear()
    agent._render_model_router_runtime_configs(
        tmp_path, {"ODS_MODEL_SWITCHBOARD": "enabled", "GGUF_FILE": "m.gguf"},
        model="m", gguf_file="m.gguf", context_length=8192)
    assert all(cmd[cmd.index("--llm-backend") + 1] == "llama-server" for cmd in commands)


class Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.response_code = None

    def send_response(self, code):
        self.response_code = code

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _install(tmp_path, monkeypatch, env_text):
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    (tmp_path / "config").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "config" / "model-library.json", tmp_path / "config" / "model-library.json")
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "STARTUP_ODS_MODE", "local")
    monkeypatch.setattr(agent, "_managed_wsl_runtime", lambda env: {"managed": False})
    monkeypatch.setattr(agent, "_recreate_llama_server", lambda *a, **k: pytest.fail("llama-server recreated"))
    monkeypatch.setattr(agent, "_compose_restart_llama_server", lambda *a, **k: pytest.fail("llama-server started"))


def test_a_vllm_install_refuses_model_switches_before_any_change(monkeypatch, tmp_path):
    _install(tmp_path, monkeypatch, "ODS_MODE=local\nLLM_BACKEND=vllm\nLLM_MODEL=glm-5.3-flash-nvfp4\n")
    monkeypatch.setattr(agent, "_load_model_library_records", lambda: pytest.fail("refused before lookup"))
    before = (tmp_path / ".env").read_bytes()
    handler = Handler()
    agent.AgentHandler._do_model_activate(handler, "qwen3.5-9b-q4")
    assert handler.response_code == 409
    body = handler.body()
    assert body["code"] == "vllm_runtime_installer_managed"
    assert "vLLM engine" in body["error"] and "installer" in body["hint"]
    assert "discord.gg" in body["hint"]
    assert (tmp_path / ".env").read_bytes() == before


def test_a_llama_install_refuses_a_vllm_catalog_model(monkeypatch, tmp_path):
    _install(tmp_path, monkeypatch, "ODS_MODE=local\nLLM_BACKEND=llama-server\nGGUF_FILE=a.gguf\n")
    handler = Handler()
    agent.AgentHandler._do_model_activate(handler, "glm-5.3-flash-nvfp4-vllm")
    assert handler.response_code == 409
    body = handler.body()
    assert body["code"] == "vllm_model_installer_only"
    assert body["requestedModelId"] == "glm-5.3-flash-nvfp4-vllm"
    assert body["error"].startswith("GLM 5.3 Flash NVFP4 (vLLM) runs only on the vLLM engine")


def test_llama_server_is_never_named_to_compose_on_a_vllm_install(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("LLM_BACKEND=vllm\n", encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", {"llama-server", "litellm", "vllm-server"})
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: pytest.fail("compose ran"))
    ok, error = agent.docker_compose_recreate(["litellm", "llama-server"])
    assert not ok and "vllm-server" in error
    assert "vllm-server" in agent.ALWAYS_ON_SERVICES
