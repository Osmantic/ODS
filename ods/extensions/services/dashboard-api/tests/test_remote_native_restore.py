"""Remote deactivation restores a stopped Mac producer without replacing a live one."""
import subprocess
import types

import pytest

from test_host_agent import _mod


@pytest.fixture
def route(monkeypatch, tmp_path):
    root = tmp_path / "ods"
    cloud = root / "config/litellm/cloud.yaml"
    cloud.parent.mkdir(parents=True)
    data = root / "data"
    data.mkdir()
    env = root / ".env"
    env.write_text("ODS_MODE=cloud\nLLM_API_URL=http://litellm:4000\nGPU_BACKEND=apple\nODS_MACOS_LLM_BRIDGE_ENABLED=0\n")
    cloud.write_text("remote cloud config")
    monkeypatch.setattr(_mod, "INSTALL_DIR", root)
    monkeypatch.setattr(_mod, "DATA_DIR", data)
    monkeypatch.setattr(_mod, "platform", types.SimpleNamespace(system=lambda: "Darwin"))
    monkeypatch.setattr(_mod, "_write_bound_env_text", lambda p, t: p.write_text(t))
    monkeypatch.setattr(_mod, "_restore_bound_env_file", _mod._restore_text_file)
    local = dict(model="local.gguf", contextLength=65536, maxTokens=8192, reasoning=False)
    remote = dict(model="remote", contextLength=65536, maxTokens=8192, reasoning=False, routeFingerprint="a"*64)
    previous = dict(odsMode="local", llmApiUrl="http://llama-server:8080", pixel=local,
                    cloudConfig=dict(exists=True, text="local cloud config", mode=0o600))
    monkeypatch.setattr(_mod, "_read_remote_provider_activation_state", lambda: dict(previous=previous))
    paths = [env, cloud, _mod._remote_provider_activation_state_path(), _mod._remote_provider_activation_public_path()]
    for p in paths[2:]:
        p.parent.mkdir(exist_ok=True)
        p.write_text("remote activation receipt")
    originals = {p: p.read_bytes() for p in paths}
    events: list[str] = []
    state = types.SimpleNamespace(root=root, env=env, previous=previous, events=events, present=False,
                                  originals=originals, fault=None, stop_fails=False, local=local)

    def stage(name):
        events.append(name)
        if state.fault == name:
            raise RuntimeError("injected " + name)

    def bridge(_path):
        mode = _mod.load_env(env)["ODS_MODE"]
        stage("bridge:" + mode)
        text = env.read_text().replace("ODS_MACOS_LLM_BRIDGE_ENABLED=0", "ODS_MACOS_LLM_BRIDGE_ENABLED=1") if mode == "local" else env.read_text()
        env.write_text(text)

    def stop(_path):
        stage("stop")
        if state.stop_fails:
            raise RuntimeError("injected stop failure")

    def prove(_env, contract):
        stage("proof:" + ("remote" if contract == remote else "local"))
        return True

    class Transaction:
        completed = False
        previous = remote

        def apply(self, contract):
            assert contract == local
            assert "proof:local" in events
            stage("apply")
            return "reconciled"

        def verify_held(self):
            events.append("held")

        def finish(self, action):
            events.append("finish:" + action)
            self.completed = True

    state.tx = Transaction()
    monkeypatch.setattr(_mod, "_managed_pixel_runtime_contract", lambda: remote)
    monkeypatch.setattr(_mod, "_begin_pixel_model_transaction", lambda _: state.tx)
    monkeypatch.setattr(_mod, "_capture_container_state", lambda _: dict(exists=True, running=True))
    monkeypatch.setattr(_mod, "_restart_existing_container", lambda *a, **kw: stage("consumer-start") or True)
    monkeypatch.setattr(_mod, "_restore_container_state", lambda *a, **kw: stage("consumer-rollback"))
    monkeypatch.setattr(_mod, "_wait_for_container_health", lambda _: None)
    monkeypatch.setattr(_mod, "_verify_litellm_route", lambda *a, **kw: stage("consumer-proof"))
    monkeypatch.setattr(_mod, "_restore_pixel_model_relay", lambda _: stage("relay"))
    monkeypatch.setattr(_mod, "_prove_pixel_model_contract", prove)
    monkeypatch.setattr(_mod, "_macos_native_llama_present", lambda: state.present)
    monkeypatch.setattr(_mod, "_require_macos_bridge_manager", lambda _: stage("bridge-validation"))
    monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", bridge)
    state.identity = (123, 42, 0, 501, 20, 501, 20, 501, 20, str(root / "bin/llama-server"))
    def ensure(*args, **kwargs):
        assert kwargs == {"preserve_existing": True}
        stage("native-ensure")
        if not state.present:
            stage("launch")
        return {"created": not state.present, "identity": state.identity}
    monkeypatch.setattr(_mod, "_launch_native_llama_server", ensure)
    monkeypatch.setattr(_mod, "_rollback_macos_native_llama_start", stop)
    monkeypatch.setattr(_mod, "_stop_macos_native_llama_server", lambda *a: pytest.fail("broad rollback stop"))
    def wait_health(_env, **kw):
        state.wait_identity = kw.get("expected_identity")
        stage("health")
    monkeypatch.setattr(_mod, "_wait_for_macos_native_llama_health", wait_health)
    return state


@pytest.mark.parametrize("present", [False, True])
def test_deactivation_proves_local_before_rebind_and_preserves_live_engine(route, present):
    route.present = present
    result = _mod._deactivate_remote_provider_route()
    assert result["restored"] and result["proven"]
    assert _mod.load_env(route.env)["ODS_MODE"] == "local"
    assert _mod.load_env(route.env)["ODS_MACOS_LLM_BRIDGE_ENABLED"] == "1"
    assert ("launch" in route.events) is not present
    assert "stop" not in route.events
    assert route.wait_identity == route.identity
    order = ["health", "consumer-start", "consumer-proof", "proof:local", "apply", "finish:commit"]
    assert [route.events.index(x) for x in order] == sorted(route.events.index(x) for x in order)
    if not present:
        assert route.events.index("launch") < route.events.index("health")
    assert not _mod._remote_provider_activation_state_path().exists()


@pytest.mark.parametrize("fault", ["health", "consumer-start", "consumer-proof", "proof:local", "apply"])
def test_failed_return_cleans_attempted_start_and_restores_remote(route, fault):
    route.fault = fault
    with pytest.raises(RuntimeError, match="injected"):
        _mod._deactivate_remote_provider_route()
    assert {p: p.read_bytes() for p in route.originals} == route.originals
    assert route.events.count("stop") == 1
    assert route.events.index("stop") < route.events.index("bridge:cloud")
    assert route.events[-2:] == ["proof:remote", "finish:rollback"]


def test_failed_return_does_not_stop_preexisting_engine(route):
    route.present = True
    route.fault = "proof:local"
    with pytest.raises(RuntimeError, match="injected"):
        _mod._deactivate_remote_provider_route()
    assert "launch" not in route.events and "stop" not in route.events
    assert {p: p.read_bytes() for p in route.originals} == route.originals


def test_failed_cleanup_keeps_maintenance_held(route):
    route.fault = "health"
    route.stop_fails = True
    with pytest.raises(_mod._PixelModelTransactionUncertain, match="rollback is incomplete"):
        _mod._deactivate_remote_provider_route()
    assert not route.tx.completed
    assert not any(e.startswith("finish:") for e in route.events)


def test_engine_started_during_bridge_setup_is_preserved(route, monkeypatch):
    original = _mod._configure_macos_llm_bridge
    def bridge(path):
        original(path)
        route.present = True
    monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", bridge)
    _mod._deactivate_remote_provider_route()
    assert "native-ensure" in route.events
    assert "launch" not in route.events and "stop" not in route.events


def test_env_restore_failure_does_not_reconfigure_bridge_with_wrong_mode(route, monkeypatch):
    route.fault = "health"

    def fail(*_args):
        raise RuntimeError("env restore failed")

    monkeypatch.setattr(_mod, "_restore_bound_env_file", fail)
    with pytest.raises(_mod._PixelModelTransactionUncertain):
        _mod._deactivate_remote_provider_route()
    assert route.events.count("bridge:local") == 1
    assert "bridge:cloud" not in route.events
    assert not route.tx.completed


def test_failed_inspection_precedes_any_mutation(route, monkeypatch):
    def fail():
        raise RuntimeError("inspection failed")

    monkeypatch.setattr(_mod, "_macos_native_llama_present", fail)
    with pytest.raises(RuntimeError, match="inspection failed"):
        _mod._deactivate_remote_provider_route()
    assert not route.events
    assert {p: p.read_bytes() for p in route.originals} == route.originals


def test_caller_keeps_transaction_ownership(route):
    _mod._deactivate_remote_provider_route(transaction=route.tx)
    assert "apply" in route.events
    assert not route.tx.completed
    assert not any(e.startswith("finish:") for e in route.events)


@pytest.mark.parametrize("kind", ["platform", "backend", "previous-cloud"])
def test_other_routes_do_not_touch_native_engine(route, monkeypatch, kind):
    if kind == "platform":
        monkeypatch.setattr(_mod, "platform", types.SimpleNamespace(system=lambda: "Linux"))
    elif kind == "backend":
        route.env.write_text(route.env.read_text().replace("GPU_BACKEND=apple", "GPU_BACKEND=nvidia"))
    else:
        route.previous["odsMode"] = "cloud"
    _mod._deactivate_remote_provider_route()
    assert not {"launch", "stop", "health", "bridge:local", "bridge:cloud"}.intersection(route.events)


@pytest.mark.parametrize("job_code,present", [(0, True), (113, False)])
def test_presence_preserves_loaded_job_or_checks_native_processes(tmp_path, monkeypatch, job_code, present):
    root = tmp_path / "path with spaces"
    (root / "data").mkdir(parents=True)
    pid_file = root / "data/.llama-server.pid"
    pid_file.write_text("12")
    monkeypatch.setattr(_mod, "INSTALL_DIR", root)
    monkeypatch.setattr(_mod.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(_mod.subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, job_code, "", ""))
    monkeypatch.setattr(_mod, "_macos_native_llama_processes", lambda env: ())
    assert _mod._macos_native_llama_present() is present
    assert pid_file.read_text() == "12"


@pytest.mark.parametrize("failure", ["launchctl", "ps", "timeout"])
def test_presence_inspection_fails_closed(monkeypatch, failure):
    monkeypatch.setattr(_mod.os, "getuid", lambda: 501, raising=False)

    def run(cmd, **kw):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 10)
        return subprocess.CompletedProcess(cmd, 1 if cmd[0] == failure else 113, "", "")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        _mod._macos_native_llama_present()


@pytest.mark.parametrize("eventually_ready", [False, True])
def test_cold_health_wait_is_bounded_and_retries_refused_connection(monkeypatch, eventually_ready):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(_mod.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))

    def health(_env):
        calls.append(clock[0])
        if len(calls) == 1:
            raise OSError("listener not bound")
        return "ok" if eventually_ready and len(calls) == 3 else "loading"

    monkeypatch.setattr(_mod, "_runtime_health", health)
    if eventually_ready:
        _mod._wait_for_macos_native_llama_health({})
        assert calls == [0, 2, 4]
    else:
        with pytest.raises(RuntimeError, match="did not become healthy"):
            _mod._wait_for_macos_native_llama_health({})
        assert clock[0] == 300 and len(calls) == 151


def test_launch_failure_uses_manager_cleanup_without_broad_stop(route):
    route.fault = "launch"
    with pytest.raises(RuntimeError, match="injected"):
        _mod._deactivate_remote_provider_route()
    assert "stop" not in route.events
    assert {p: p.read_bytes() for p in route.originals} == route.originals
    assert route.events[-1] == "finish:rollback"


def test_uncertain_startup_cleanup_restores_config_and_holds_maintenance(route, monkeypatch):
    from macos_native_service import NativeServiceUncertain
    def fail(*args, **kwargs):
        raise NativeServiceUncertain("owned producer could not be stopped")
    monkeypatch.setattr(_mod, "_launch_native_llama_server", fail)
    with pytest.raises(_mod._PixelModelTransactionUncertain, match="rollback is incomplete"):
        _mod._deactivate_remote_provider_route()
    assert "stop" not in route.events
    assert not route.tx.completed
    assert {p: p.read_bytes() for p in route.originals} == route.originals
