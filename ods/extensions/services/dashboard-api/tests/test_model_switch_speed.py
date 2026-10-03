"""Model-switch latency guards in ods-host-agent.py.

Covers the two activation phases that dominated the dashboard Run button on
the fleet: a no-op LiteLLM recreate and the fixed readiness sleep. Each test
pins both the faster path and the safety contract it must keep.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_real_subprocess_run = subprocess.run

_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent_switch_speed", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent_switch_speed"] = _mod
_spec.loader.exec_module(_mod)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_model_activate import (  # noqa: E402
    _ResponseHandler,
    _llama_identity_response,
    _mock_verified_readiness,
    _write_model_activation_fixture,
)

_INSPECT = ["docker", "inspect", "--type", "container", "--format", "{{json .}}"]


def _container_json(container_id, mounts, *, running=True, health="healthy"):
    state = {"Running": running}
    if health is not None:
        state["Health"] = {"Status": health}
    return json.dumps({"Id": container_id, "State": state, "Mounts": mounts})


def _bind(path, destination="/app/config.yaml"):
    return {"Type": "bind", "Source": str(path), "Destination": destination}


class _Docker:
    """Scripted Docker CLI: inspect answers in order, compose calls recorded."""

    def __init__(self, inspections):
        self.inspections = list(inspections)
        self.calls = []

    def run(self, cmd, **kwargs):
        if cmd[:1] != ["docker"]:
            return _real_subprocess_run(cmd, **kwargs)
        self.calls.append(list(cmd))
        if cmd[:6] == _INSPECT:
            payload = self.inspections.pop(0) if self.inspections else ""
            return subprocess.CompletedProcess(cmd, 0 if payload else 1, payload, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def compose_ups(self):
        return [call for call in self.calls if call[:2] == ["docker", "compose"]]


@pytest.fixture(autouse=True)
def _isolate_host(monkeypatch, tmp_path):
    """Never touch the developer's OpenCode config or real Compose services."""
    config_dir = tmp_path / "isolated-home" / ".config" / "opencode"
    monkeypatch.setattr(
        _mod,
        "_opencode_config_paths",
        lambda: (config_dir / "opencode.json", config_dir / "config.json"),
    )
    monkeypatch.setattr(
        _mod,
        "_capture_managed_opencode_state",
        lambda: {"system": _mod.platform.system(), "active": False},
    )
    monkeypatch.setattr(_mod, "_opencode_installed", lambda: False)
    monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"litellm"})
    monkeypatch.setattr(_mod, "resolve_compose_flags", list)


@pytest.fixture
def running_litellm(monkeypatch):
    monkeypatch.setattr(
        _mod,
        "_capture_container_state",
        lambda name: {"exists": name == "ods-litellm", "running": name == "ods-litellm"},
    )


class TestDependentBindInputs:
    def test_fingerprints_running_instance_and_bind_file_bytes(self, tmp_path, monkeypatch):
        config = tmp_path / "switchboard.yaml"
        config.write_text("model_list: []\n", encoding="utf-8")
        docker = _Docker([_container_json("c1", [
            _bind(config),
            {"Type": "volume", "Source": "/var/lib/docker/volumes/x", "Destination": "/data"},
        ])])
        monkeypatch.setattr(_mod.subprocess, "run", docker.run)

        inputs = _mod._dependent_bind_inputs("ods-litellm")

        assert inputs["id"] == "c1"
        assert inputs["health"] == "healthy"
        assert list(inputs["files"]) == [str(config)]
        assert len(inputs["files"][str(config)]) == 64

    @pytest.mark.parametrize("case", ["missing-source", "directory-source", "stopped", "inspect-fails"])
    def test_unprovable_views_return_none(self, tmp_path, monkeypatch, case):
        source = tmp_path / "absent.yaml"
        running = True
        if case == "directory-source":
            source = tmp_path
        if case == "stopped":
            source = tmp_path / "config.yaml"
            source.write_text("x", encoding="utf-8")
            running = False
        inspections = [] if case == "inspect-fails" else [
            _container_json("c1", [_bind(source)], running=running)
        ]
        monkeypatch.setattr(_mod.subprocess, "run", _Docker(inspections).run)

        assert _mod._dependent_bind_inputs("ods-litellm") is None


class TestReuseUnchangedDependent:
    def _before(self, tmp_path, monkeypatch):
        config = tmp_path / "switchboard.yaml"
        config.write_text("model_list: []\n", encoding="utf-8")
        monkeypatch.setattr(
            _mod.subprocess, "run", _Docker([_container_json("c1", [_bind(config)])]).run
        )
        return config, _mod._dependent_bind_inputs("ods-litellm")

    def test_keeps_same_healthy_instance_after_non_forced_compose(
        self, tmp_path, monkeypatch, running_litellm,
    ):
        config, before = self._before(tmp_path, monkeypatch)
        # The activation atomically re-renders identical bytes (new inode).
        config.unlink()
        config.write_text("model_list: []\n", encoding="utf-8")
        docker = _Docker([
            _container_json("c1", [_bind(config)]),
            _container_json("c1", [_bind(config)]),
        ])
        monkeypatch.setattr(_mod.subprocess, "run", docker.run)

        assert _mod._reuse_unchanged_dependent("ods-litellm", before) == "reused"
        assert docker.compose_ups() == [
            ["docker", "compose", "up", "-d", "--no-deps", "litellm"],
        ]

    def test_changed_bytes_require_the_forced_recreate(
        self, tmp_path, monkeypatch, running_litellm,
    ):
        config, before = self._before(tmp_path, monkeypatch)
        config.write_text("model_list: [changed]\n", encoding="utf-8")
        docker = _Docker([_container_json("c1", [_bind(config)])])
        monkeypatch.setattr(_mod.subprocess, "run", docker.run)

        assert _mod._reuse_unchanged_dependent("ods-litellm", before) is None
        assert docker.compose_ups() == []

    @pytest.mark.parametrize("health", ["starting", "unhealthy"])
    def test_unhealthy_instance_requires_the_forced_recreate(
        self, tmp_path, monkeypatch, running_litellm, health,
    ):
        config, before = self._before(tmp_path, monkeypatch)
        docker = _Docker([_container_json("c1", [_bind(config)], health=health)])
        monkeypatch.setattr(_mod.subprocess, "run", docker.run)

        assert _mod._reuse_unchanged_dependent("ods-litellm", before) is None
        assert docker.compose_ups() == []

    def test_replaced_instance_requires_the_forced_recreate(
        self, tmp_path, monkeypatch, running_litellm,
    ):
        config, before = self._before(tmp_path, monkeypatch)
        docker = _Docker([_container_json("someone-else", [_bind(config)])])
        monkeypatch.setattr(_mod.subprocess, "run", docker.run)

        assert _mod._reuse_unchanged_dependent("ods-litellm", before) is None

    def test_unproved_capture_requires_the_forced_recreate(self, monkeypatch, running_litellm):
        docker = _Docker([])
        monkeypatch.setattr(_mod.subprocess, "run", docker.run)

        assert _mod._reuse_unchanged_dependent("ods-litellm", None) is None
        assert docker.calls == []

    def test_compose_definition_drift_is_reported_as_recreated(
        self, tmp_path, monkeypatch, running_litellm,
    ):
        config, before = self._before(tmp_path, monkeypatch)
        docker = _Docker([
            _container_json("c1", [_bind(config)]),
            _container_json("c2", [_bind(config)], health="starting"),
        ])
        monkeypatch.setattr(_mod.subprocess, "run", docker.run)

        assert _mod._reuse_unchanged_dependent("ods-litellm", before) == "recreated"

    def test_stopped_dependent_fails_closed(self, tmp_path, monkeypatch):
        _config, before = self._before(tmp_path, monkeypatch)
        monkeypatch.setattr(
            _mod, "_capture_container_state", lambda _name: {"exists": True, "running": False}
        )

        with pytest.raises(RuntimeError, match="ods-litellm stopped during model activation"):
            _mod._reuse_unchanged_dependent("ods-litellm", before)

    def test_compose_failure_fails_closed(self, tmp_path, monkeypatch, running_litellm):
        config, before = self._before(tmp_path, monkeypatch)
        docker = _Docker([_container_json("c1", [_bind(config)])])

        def fail_compose(cmd, **kwargs):
            if cmd[:2] == ["docker", "compose"]:
                return subprocess.CompletedProcess(cmd, 1, "", "compose broke")
            return docker.run(cmd, **kwargs)

        monkeypatch.setattr(_mod.subprocess, "run", fail_compose)

        with pytest.raises(RuntimeError, match="Could not reconcile ods-litellm: compose broke"):
            _mod._reuse_unchanged_dependent("ods-litellm", before)


def test_forced_recreate_command_is_unchanged(monkeypatch):
    docker = _Docker([])
    monkeypatch.setattr(_mod.subprocess, "run", docker.run)
    monkeypatch.setattr(_mod, "resolve_compose_flags", list)

    assert _mod.docker_compose_recreate(["litellm"]) == (True, "")
    assert docker.compose_ups() == [
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "litellm"],
    ]


@pytest.mark.parametrize("gateway_input_changes", [False, True])
def test_activation_recreates_litellm_only_when_its_inputs_change(
    tmp_path, monkeypatch, gateway_input_changes,
):
    install, env_path, env_text, *_ = _write_model_activation_fixture(tmp_path)
    env_path.write_text(env_text + "ODS_MODEL_SWITCHBOARD=enabled\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install)
    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    # The model-independent switchboard map already exists, exactly as the
    # renderer writes it; a stale copy stands in for a real input change.
    _mod._render_model_router_runtime_configs(
        install,
        _mod.load_env(env_path),
        model="old-model",
        gguf_file="old-model.gguf",
        lemonade_model_id="",
        context_length=2048,
    )
    switchboard = install / "config" / "litellm" / "switchboard.yaml"
    rendered = switchboard.read_bytes()
    if gateway_input_changes:
        switchboard.write_text("model_list: []\n", encoding="utf-8")

    def gateway_view():
        return _container_json("litellm-1", [_bind(switchboard, "/app/switchboard.yaml")])

    docker = _Docker([gateway_view(), gateway_view(), gateway_view()])
    events = []
    monkeypatch.setattr(_mod.subprocess, "run", docker.run)
    monkeypatch.setattr(_mod, "resolve_compose_flags", list)
    monkeypatch.setattr(
        _mod,
        "_capture_container_state",
        lambda name: {"exists": name == "ods-litellm", "running": name == "ods-litellm"},
    )
    monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
    monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
    monkeypatch.setattr(
        _mod, "_wait_for_container_health", lambda name: events.append(("health", name))
    )
    monkeypatch.setattr(
        _mod, "_verify_litellm_route", lambda env: events.append(("route", env["GGUF_FILE"]))
    )
    handler = _ResponseHandler()

    _mod.AgentHandler._do_model_activate(handler, "target-model")

    assert handler.response_code == 200, handler.parse_response()
    assert switchboard.read_bytes() == rendered
    receipt = json.loads((install / "data" / "model-activation-receipt.json").read_text())
    if gateway_input_changes:
        assert docker.compose_ups() == [
            ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "litellm"],
        ]
        assert events == [("health", "ods-litellm"), ("route", "new-model.gguf")]
        assert receipt["consumers"]["litellm"] == "restarted"
    else:
        assert docker.compose_ups() == [
            ["docker", "compose", "up", "-d", "--no-deps", "litellm"],
        ]
        # The kept gateway still has to route a completion to the new model.
        assert events == [("route", "new-model.gguf")]
        assert receipt["consumers"]["litellm"] == "unchanged"


def test_rollback_still_restores_a_kept_litellm(tmp_path, monkeypatch):
    install, env_path, env_text, *_ = _write_model_activation_fixture(tmp_path)
    env_path.write_text(env_text + "ODS_MODEL_SWITCHBOARD=enabled\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install)
    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    _mod._render_model_router_runtime_configs(
        install,
        _mod.load_env(env_path),
        model="old-model",
        gguf_file="old-model.gguf",
        lemonade_model_id="",
        context_length=2048,
    )
    switchboard = install / "config" / "litellm" / "switchboard.yaml"
    view = _container_json("litellm-1", [_bind(switchboard, "/app/switchboard.yaml")])
    docker = _Docker([view, view, view])
    restored = []
    monkeypatch.setattr(_mod.subprocess, "run", docker.run)
    monkeypatch.setattr(_mod, "resolve_compose_flags", list)
    monkeypatch.setattr(
        _mod,
        "_capture_container_state",
        lambda name: {"exists": name == "ods-litellm", "running": name == "ods-litellm"},
    )
    monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
    monkeypatch.setattr(_mod, "_wait_for_model_readiness", _mock_verified_readiness)
    monkeypatch.setattr(_mod, "_wait_for_container_health", lambda _name: None)
    monkeypatch.setattr(
        _mod,
        "_restore_container_state",
        lambda name, _state, **kwargs: restored.append((name, kwargs)) or True,
    )

    def route(env):
        if env["GGUF_FILE"] == "new-model.gguf":
            raise RuntimeError("gateway route failed")

    monkeypatch.setattr(_mod, "_verify_litellm_route", route)
    handler = _ResponseHandler()

    _mod.AgentHandler._do_model_activate(handler, "target-model")

    assert handler.response_code == 500
    assert "gateway route failed" in handler.parse_response()["error"]
    assert ("ods-litellm", {"recreate": True}) in restored
    assert _mod.load_env(env_path)["GGUF_FILE"] == "old-model.gguf"


class TestReadinessFastWindow:
    @staticmethod
    def _probe_runtime(monkeypatch, ready_on_probe):
        probes = []
        sleeps = []

        def fake_run(cmd, **_kwargs):
            probes.append(cmd)
            identity = "new-model.gguf" if len(probes) >= ready_on_probe else "old-model.gguf"
            return subprocess.CompletedProcess(cmd, 0, _llama_identity_response(identity), "")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "_llama_runtime_context_length", lambda *_args: 4096)
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod.time, "sleep", sleeps.append)
        return probes, sleeps

    def _wait(self, **kwargs):
        return _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "nvidia", "OLLAMA_PORT": "8080", "CTX_SIZE": "4096"},
            model_id="target-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            return_proof=True,
            **kwargs,
        )

    def test_default_cadence_still_sleeps_the_initial_delay(self, monkeypatch):
        probes, sleeps = self._probe_runtime(monkeypatch, ready_on_probe=1)

        assert self._wait()["identity"] == "new-model.gguf"
        assert sleeps == [5]
        assert len(probes) == 1

    def test_fast_window_probes_densely_instead_of_sleeping(self, monkeypatch):
        probes, sleeps = self._probe_runtime(monkeypatch, ready_on_probe=3)

        proof = self._wait(fast_poll_seconds=30)

        assert proof["identity"] == "new-model.gguf"
        assert proof["contextVerified"] is True
        assert sleeps == [0.5, 0.5]
        assert len(probes) == 3

    def test_exhausted_fast_window_keeps_the_full_regular_schedule(self, monkeypatch):
        probes, sleeps = self._probe_runtime(monkeypatch, ready_on_probe=10**9)
        clock = iter(range(0, 10**6))
        monkeypatch.setattr(_mod.time, "monotonic", lambda: float(next(clock)))

        assert self._wait(fast_poll_seconds=1, fast_poll_interval=0.5, attempts=3) == {}
        # Two dense probes, then every regular attempt: a slow load can never
        # fail earlier than it did before the fast window existed.
        assert len(probes) == 2 + 3
        assert sleeps[:1] == [0.5]
        assert sleeps[-2:] == [5, 5]

    def test_lemonade_ignores_the_fast_window(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod, "_lemonade_runtime_base_url", lambda _env: "http://127.0.0.1:8080")
        monkeypatch.setattr(_mod.subprocess, "run", lambda cmd, **_kw: (
            calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "{}", "")
        ))
        monkeypatch.setattr(_mod, "_send_lemonade_warmup", lambda *_a, **_k: True)
        sleeps = []
        monkeypatch.setattr(_mod.time, "sleep", sleeps.append)

        result = _mod._wait_for_model_readiness(
            {"GPU_BACKEND": "amd", "LLM_BACKEND": "lemonade", "ODS_MODE": "lemonade"},
            model_id="target-model",
            gguf_file="new-model.gguf",
            llm_model_name="new-model",
            lemonade_model_id="extra.new-model.gguf",
            attempts=2,
            fast_poll_seconds=30,
        )

        assert result is False
        assert sleeps == [5, 5]


@pytest.mark.parametrize(
    ("runtime_kind", "fast"),
    [
        ("compose-llama", True),
        ("container-llama", True),
        ("macos-native-llama", False),
    ],
)
def test_activation_uses_fast_readiness_only_for_replaced_containers(
    tmp_path, monkeypatch, runtime_kind, fast,
):
    install, env_path, env_text, *_ = _write_model_activation_fixture(
        tmp_path,
        gpu_backend="apple" if runtime_kind == "macos-native-llama" else "nvidia",
    )
    monkeypatch.setattr(_mod, "INSTALL_DIR", install)
    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
    monkeypatch.setattr(
        _mod, "_capture_container_state", lambda _name: {"exists": False, "running": False}
    )
    if runtime_kind == "compose-llama":
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod, "_compose_restart_llama_server", lambda _env: None)
    elif runtime_kind == "container-llama":
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setenv("ODS_HOST_INSTALL_DIR", str(install))
        monkeypatch.setattr(_mod, "_recreate_llama_server", lambda _env, override_image="": None)
    else:
        llama_bin = install / "bin" / "llama-server"
        llama_bin.parent.mkdir(parents=True)
        llama_bin.write_text("binary", encoding="utf-8")
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(_mod, "_restart_macos_native_llama_server", lambda *_args: None)
    readiness_calls = []

    def readiness(*args, **kwargs):
        readiness_calls.append(kwargs)
        return _mock_verified_readiness(*args, **kwargs)

    monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
    monkeypatch.setattr(_mod.subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(
        cmd, 0, "--ctx-size N\n--model FILE\n" if cmd[-1:] == ["--help"] else "", ""
    ))
    handler = _ResponseHandler()

    _mod.AgentHandler._do_model_activate(handler, "target-model")

    assert handler.response_code == 200, handler.parse_response()
    first = readiness_calls[0]
    if fast:
        assert first["fast_poll_seconds"] == _mod._MODEL_READINESS_FAST_POLL_SECONDS
    else:
        assert "fast_poll_seconds" not in first
    # The closing proof after consumer refresh keeps its own explicit cadence.
    assert all("fast_poll_seconds" not in call for call in readiness_calls[1:])
