"""Service lifecycle commands the host agent runs, recorded exactly.

Start, stop, restart, status, logs and health go through one runtime backend.
These tests pin the argv and subprocess options of every lifecycle path so a
change to that seam cannot change what the host agent asks Docker to do.
"""

import contextlib
import json
import types

import pytest

from test_host_agent_install_rollback import _mod, host  # noqa: F401  (fixture)

FLAGS = ["-f", "docker-compose.base.yml"]


def _completed(stdout="", returncode=0, stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _options(kwargs):
    """Subprocess options, with the environment reduced to whether one was passed."""
    recorded = {key: value for key, value in kwargs.items() if key != "env"}
    if "env" in kwargs:
        recorded["env"] = None if kwargs["env"] is None else "environment"
    return recorded


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    replies = {}

    def run(command, **kwargs):
        calls.append((list(command), _options(kwargs)))
        for prefix, reply in replies.items():
            if tuple(command[:len(prefix)]) == prefix:
                return reply(command) if callable(reply) else reply
        return _completed()

    monkeypatch.setattr(_mod.subprocess, "run", run)
    return types.SimpleNamespace(calls=calls, replies=replies)


@pytest.fixture
def lifecycle(tmp_path, monkeypatch, recorded):
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "resolve_compose_flags", lambda **_kwargs: list(FLAGS))
    monkeypatch.setattr(_mod, "_precreate_data_dirs", lambda service_id: None)
    monkeypatch.setattr(_mod, "_repair_rootless_data_ownership", lambda service_id: None)
    monkeypatch.setattr(_mod, "_network_auth_required", lambda env: False)
    monkeypatch.setattr(_mod, "load_env", lambda path: {})
    ext_dir = tmp_path / "extensions" / "gotify"
    ext_dir.mkdir(parents=True)
    (ext_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "_find_ext_dir", lambda service_id: ext_dir)
    monkeypatch.setattr(_mod, "_read_manifest", lambda directory: {})

    class Selector:
        class SelectionError(Exception):
            pass

        @staticmethod
        def _selection_lock(install_dir, timeout):
            return contextlib.nullcontext()

    monkeypatch.setattr(_mod, "_load_extension_selector", lambda: Selector)
    return recorded


class _Handler:
    def __init__(self):
        self.responses = []


@pytest.fixture
def handler(monkeypatch):
    target = _Handler()
    monkeypatch.setattr(_mod, "check_auth", lambda _handler: True)
    monkeypatch.setattr(_mod, "json_response",
                        lambda _handler, status, body: _handler.responses.append((status, body)))
    return target


def test_core_service_start_runs_compose_up(lifecycle):
    assert _mod.docker_compose_action("dashboard", "start") == (True, "")

    assert lifecycle.calls == [(
        ["docker", "compose", *FLAGS, "up", "-d", "dashboard"],
        {"cwd": str(_mod.INSTALL_DIR), "capture_output": True, "text": True,
         "timeout": _mod.SUBPROCESS_TIMEOUT_START, "env": "environment"},
    )]


def test_extension_start_runs_compose_up_under_the_selection_lock(lifecycle):
    assert _mod.docker_compose_action("gotify", "start") == (True, "")

    assert lifecycle.calls == [(
        ["docker", "compose", *FLAGS, "up", "-d", "gotify"],
        {"cwd": str(_mod.INSTALL_DIR), "capture_output": True, "text": True,
         "timeout": _mod.SUBPROCESS_TIMEOUT_START, "env": "environment"},
    )]


def test_stop_runs_compose_stop_on_every_target(lifecycle, monkeypatch):
    monkeypatch.setattr(_mod, "_extension_stop_targets", lambda service_id: ["gotify", "gotify-db"])

    assert _mod.docker_compose_action("gotify", "stop") == (True, "")

    assert lifecycle.calls == [(
        ["docker", "compose", *FLAGS, "stop", "gotify", "gotify-db"],
        {"cwd": str(_mod.INSTALL_DIR), "capture_output": True, "text": True,
         "timeout": _mod.SUBPROCESS_TIMEOUT_STOP, "env": "environment"},
    )]


def test_failed_start_still_reports_the_compose_reason(lifecycle):
    lifecycle.replies[("docker", "compose")] = _completed(returncode=1, stderr="no such image")

    ok, error = _mod.docker_compose_action("dashboard", "start")

    assert ok is False and "no such image" in error
    assert [command for command, _ in lifecycle.calls] == [
        ["docker", "compose", *FLAGS, "up", "-d", "dashboard"]]


LOG_OPTIONS = {"stdout": _mod.subprocess.PIPE, "stderr": _mod.subprocess.STDOUT, "text": True, "timeout": 5}


def test_extension_logs_read_the_named_container(recorded, handler, monkeypatch):
    monkeypatch.setattr(_mod, "read_json_body", lambda _handler: {"service_id": "gotify", "tail": 40})
    monkeypatch.setattr(_mod, "validate_service_id", lambda _handler, body: body["service_id"])
    recorded.replies[("docker", "logs")] = _completed("line one\n")

    _mod.AgentHandler._handle_logs(handler)

    assert recorded.calls == [(["docker", "logs", "--tail", "40", "ods-gotify"], LOG_OPTIONS)]
    assert handler.responses == [(200, {"service_id": "gotify", "logs": "line one\n", "lines": 40})]


def test_service_logs_read_the_resolved_container(recorded, handler, monkeypatch):
    monkeypatch.setattr(_mod, "read_json_body", lambda _handler: {"service_id": "llama-server", "tail": 900})
    recorded.replies[("docker", "ps")] = _completed("ods-llama-server-1\n")
    recorded.replies[("docker", "logs")] = _completed("ready\n")

    _mod.AgentHandler._handle_service_logs(handler)

    assert recorded.calls == [
        (["docker", "ps", "--filter", "label=com.docker.compose.service=llama-server",
          "--filter", "label=com.docker.compose.project=ods", "--format", "{{.Names}}"],
         {"capture_output": True, "text": True, "timeout": 5}),
        (["docker", "logs", "--tail", "500", "ods-llama-server-1"], LOG_OPTIONS),
    ]
    assert handler.responses[0][0] == 200


def _restart(recorded, handler, monkeypatch, delay):
    monkeypatch.setattr(_mod, "read_json_body",
                        lambda _handler: {"service_id": "open-webui", "delay_seconds": delay})
    monkeypatch.setattr(_mod, "_service_has_docker_container", lambda sid: (True, ""))
    _mod._service_locks.pop("open-webui", None)
    recorded.replies[("docker", "ps")] = _completed("ods-open-webui\n")

    _mod.AgentHandler._handle_service_restart(handler)
    lock = _mod._service_locks["open-webui"]
    assert lock.acquire(timeout=5), "the restart releases the service lock"
    lock.release()


RESTART_CALLS = [
    (["docker", "ps", "--filter", "label=com.docker.compose.service=open-webui",
      "--filter", "label=com.docker.compose.project=ods", "--format", "{{.Names}}"],
     {"capture_output": True, "text": True, "timeout": 5}),
    (["docker", "restart", "ods-open-webui"], {"capture_output": True, "text": True, "timeout": 60}),
]


def test_service_restart_restarts_the_resolved_container(recorded, handler, monkeypatch):
    _restart(recorded, handler, monkeypatch, 0)

    assert recorded.calls == RESTART_CALLS
    assert handler.responses == [(200, {"status": "ok", "service_id": "open-webui",
                                        "container_name": "ods-open-webui", "action": "restart"})]


def test_delayed_service_restart_runs_the_same_command(recorded, handler, monkeypatch):
    _restart(recorded, handler, monkeypatch, 0.01)

    assert recorded.calls == RESTART_CALLS
    assert handler.responses[0][0] == 202


def test_health_snapshot_inspects_ods_containers(recorded, monkeypatch):
    monkeypatch.setattr(_mod, "_service_health_cache", (0.0, None))
    monkeypatch.setattr(_mod, "_declared_docker_containers", lambda: {"perplexica": "perplexica"})
    recorded.replies[("docker", "ps")] = _completed("ods-dashboard\nunrelated\nperplexica\n")
    recorded.replies[("docker", "inspect")] = _completed(json.dumps([
        {"Name": "/ods-dashboard", "State": {"Status": "running", "Health": {"Status": "healthy"}},
         "Config": {"Labels": {"com.docker.compose.service": "dashboard"}}},
        {"Name": "/perplexica", "State": {"Status": "exited"}, "Config": {"Labels": {}}},
    ]))

    snapshot = _mod._docker_service_health_snapshot()

    assert recorded.calls == [
        (["docker", "ps", "-a", "--format", "{{.Names}}"], {"capture_output": True, "text": True, "timeout": 8}),
        (["docker", "inspect", "ods-dashboard", "perplexica"],
         {"capture_output": True, "text": True, "timeout": 12}),
    ]
    assert snapshot["containers"] == [
        {"service_id": "dashboard", "container_name": "ods-dashboard", "state": "running", "health": "healthy"},
        {"service_id": "perplexica", "container_name": "perplexica", "state": "exited", "health": "none"},
    ]


def test_health_snapshot_still_fails_loudly_when_docker_does(recorded, monkeypatch):
    monkeypatch.setattr(_mod, "_service_health_cache", (0.0, None))
    recorded.replies[("docker", "ps")] = _completed(returncode=1, stderr="daemon down")

    with pytest.raises(RuntimeError, match="daemon down"):
        _mod._docker_service_health_snapshot()


STATUS_CALL = (["docker", "inspect", "--format", "{{.State.Status}}|{{.State.Error}}", "ods-gotify"],
               {"capture_output": True, "text": True, "timeout": 5})


def _status_calls(host):  # noqa: F811
    return [(command, kwargs) for command, kwargs in host.recorded
            if command[:3] == ["docker", "inspect", "--format"]]


@pytest.fixture
def status_host(host, monkeypatch):  # noqa: F811
    host.extension("gotify")
    host.docker()
    fake = _mod.subprocess.run
    host.recorded = []

    def run(command, **kwargs):
        host.recorded.append((list(command), _options(kwargs)))
        return fake(command, **kwargs)

    monkeypatch.setattr(_mod.subprocess, "run", run)
    return host


def test_install_waits_on_the_container_status(status_host):
    status_host.install("gotify")

    assert status_host.progress("gotify")["status"] == "started"
    assert _status_calls(status_host) == [STATUS_CALL]


def test_enable_retry_waits_on_the_container_status(status_host, monkeypatch):
    monkeypatch.setattr(_mod, "docker_compose_action", lambda sid, action: (True, ""))

    _mod._enable_retry_work("gotify")

    assert status_host.progress("gotify")["status"] == "started"
    assert _status_calls(status_host) == [STATUS_CALL]
