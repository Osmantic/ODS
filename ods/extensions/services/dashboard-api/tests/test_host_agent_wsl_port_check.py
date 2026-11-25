"""After a WSL VM restart, the host agent restarts containers whose published port died.

`wsl --shutdown` restarts Docker Desktop's VM. ODS's containers come back
healthy, yet some published 127.0.0.1 ports accept a connection and close it
at once until the container restarts (fleet test, Docker Desktop 4.68,
2026-10-18). These tests drive the agent's check with Docker, the probe
results and the clock faked; the probe itself runs against real loopback
sockets. Nothing here starts Docker, bash or WSL.
"""

import hashlib
import inspect
import json
import logging
import socket
import struct
import subprocess
import sys
import threading
from typing import Any

import pytest

from test_host_agent_install_rollback import _mod

BOOT = "11111111-2222-3333-4444-555555555555"
NEXT_BOOT = "66666666-7777-8888-9999-000000000000"
# container name -> (service id, in-container HTTP port, published host port)
SERVICES = {
    "ods-whisper": ("whisper", 8000, 9000),
    "ods-webui": ("open-webui", 8080, 3000),
    "ods-llama-server": ("llama-server", 8080, 11434),
}


def _row(install, name, *, port=8080, host_port=8999, health="healthy", project="ods",
         running=True, host_ip="127.0.0.1", working_dir=None) -> dict[str, Any]:
    """One `docker inspect` row, shaped as Docker reports it."""
    state: dict[str, Any] = {"Running": running}
    if health is not None:
        state["Health"] = {"Status": health}
    return {
        "Id": hashlib.sha256(name.encode()).hexdigest(),
        "Name": "/" + name,
        "Config": {"Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.project.working_dir": str(working_dir or install),
        }},
        "State": state,
        "NetworkSettings": {"Ports": {
            f"{port}/tcp": [{"HostIp": host_ip, "HostPort": str(host_port)}],
        }},
    }


class _Stack:
    """Docker, the published ports and the clock, as the agent's check sees them."""

    def __init__(self, tmp_path, monkeypatch, names, dead):
        self.install = tmp_path / "ods"
        self.install.mkdir()
        self.record = tmp_path / "data" / "wsl-port-check.json"
        self.rows = [self.row(name) for name in names]
        self.outcomes = {SERVICES[name][2]: "closed" if name in dead else "answering"
                         for name in names}
        self.restart_fixes = True
        self.events: list[tuple] = []
        self.now = 0.0
        monkeypatch.setattr(_mod, "INSTALL_DIR", self.install)
        monkeypatch.setattr(_mod, "_declared_http_ports", lambda: {
            name: (service, port) for name, (service, port, _host) in SERVICES.items()
        })
        monkeypatch.setattr(_mod, "_running_ods_container_rows", lambda: list(self.rows))
        monkeypatch.setattr(_mod, "_probe_loopback_http", self.probe)
        monkeypatch.setattr(_mod, "_container_running", lambda _container_id: True)
        monkeypatch.setattr(_mod, "_pixel_model_transition_pending_for_route_proof", lambda: False)
        monkeypatch.setattr(_mod.subprocess, "run", self.docker)

    def row(self, name, **overrides):
        _service, port, host_port = SERVICES[name]
        return _row(self.install, name, port=port, host_port=host_port, **overrides)

    def probe(self, port):
        self.events.append(("probe", port, self.outcomes[port]))
        return self.outcomes[port]

    def docker(self, argv, **_kwargs):
        assert argv[:2] == ["docker", "restart"] and len(argv) == 3, argv
        name = next(row["Name"].lstrip("/") for row in self.rows if row["Id"] == argv[2])
        self.events.append(("restart", name, _mod._model_lifecycle_status().get("activeOperation")))
        if self.restart_fixes:
            self.outcomes[SERVICES[name][2]] = "answering"
        return subprocess.CompletedProcess(argv, 0, "", "")

    def sleep(self, seconds):
        self.now += seconds

    def check(self, boot=BOOT, sleep=None):
        return _mod._wsl_port_check(boot, self.record, clock=lambda: self.now,
                                    sleep=sleep or self.sleep)

    def restarts(self):
        return [event[1] for event in self.events if event[0] == "restart"]


def _lifecycle_free():
    return _mod._model_lifecycle_status().get("lifecycleActive") is None


def test_dead_port_of_a_healthy_container_is_restarted_once_then_verified(
    tmp_path, monkeypatch, caplog,
):
    caplog.set_level(logging.INFO, logger="ods-host-agent")
    stack = _Stack(tmp_path, monkeypatch, ["ods-whisper", "ods-webui"], dead={"ods-whisper"})

    states = stack.check()

    assert states == {"ods-whisper": "repaired", "ods-webui": "answering"}
    # Two dead polls in a row, one restart outside any model lifecycle, then a reply.
    assert [event for event in stack.events if event[1] in (9000, "ods-whisper")] == [
        ("probe", 9000, "closed"), ("probe", 9000, "closed"),
        ("restart", "ods-whisper", None), ("probe", 9000, "answering"),
    ]
    assert json.loads(stack.record.read_text(encoding="utf-8")) == {
        "bootId": BOOT, "restarted": ["ods-whisper"],
    }
    assert ("restarted ods-whisper because its published port stopped forwarding "
            "(127.0.0.1:9000 closed)") in caplog.text
    assert "WSL port check" in caplog.text
    # Settled early, but only after watching for containers Docker starts late.
    assert _mod._WSL_PORT_CHECK_MIN_WATCH_SECONDS <= stack.now < _mod._WSL_PORT_CHECK_WINDOW_SECONDS


def test_restart_happens_at_most_once_per_wsl_boot_across_agent_starts(tmp_path, monkeypatch):
    stack = _Stack(tmp_path, monkeypatch, ["ods-whisper"], dead={"ods-whisper"})
    stack.restart_fixes = False

    assert stack.check() == {"ods-whisper": "still-dead"}
    # The agent starts again in the same WSL boot: the port is still dead.
    assert stack.check() == {"ods-whisper": "restarted-earlier"}
    assert stack.restarts() == ["ods-whisper"]

    # A new WSL VM boot may restart it once more.
    assert stack.check(NEXT_BOOT) == {"ods-whisper": "still-dead"}
    assert stack.restarts() == ["ods-whisper", "ods-whisper"]


def test_model_operation_defers_llama_server_but_not_other_services(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="ods-host-agent")
    stack = _Stack(tmp_path, monkeypatch, ["ods-llama-server", "ods-whisper"],
                   dead={"ods-llama-server", "ods-whisper"})
    assert _mod._begin_model_lifecycle("model_activation", "qwen")[0]
    try:
        states = stack.check()
        # The check never takes over or releases the switch's lifecycle.
        assert _mod._model_lifecycle_status()["activeOperation"] == "model_activation"
    finally:
        _mod._end_model_lifecycle("model_activation")

    assert states == {"ods-llama-server": "deferred", "ods-whisper": "repaired"}
    assert stack.restarts() == ["ods-whisper"]
    assert caplog.text.count(
        "not restarting ods-llama-server yet: model_activation is in progress") == 1
    # Still bounded: it stopped at the end of its window.
    assert _mod._WSL_PORT_CHECK_WINDOW_SECONDS <= stack.now
    assert stack.now < _mod._WSL_PORT_CHECK_WINDOW_SECONDS + _mod._WSL_PORT_CHECK_POLL_SECONDS


def test_llama_server_restart_waits_for_the_model_lifecycle_and_holds_it(tmp_path, monkeypatch):
    stack = _Stack(tmp_path, monkeypatch, ["ods-llama-server"], dead={"ods-llama-server"})
    assert _mod._begin_model_lifecycle("model_download", "big.gguf")[0]
    polls: list[float] = []

    def sleep(seconds):
        polls.append(seconds)
        stack.now += seconds
        if len(polls) == 3:
            _mod._end_model_lifecycle("model_download")

    try:
        states = stack.check(sleep=sleep)
    finally:
        if _mod._model_lifecycle_status().get("activeOperation") == "model_download":
            _mod._end_model_lifecycle("model_download")

    assert states == {"ods-llama-server": "repaired"}
    assert [event for event in stack.events if event[0] == "restart"] == [
        ("restart", "ods-llama-server", "wsl_port_restart"),
    ]
    assert _lifecycle_free()


def test_pending_pixel_model_transaction_defers_only_llama_server(tmp_path, monkeypatch):
    stack = _Stack(tmp_path, monkeypatch, ["ods-llama-server", "ods-whisper"],
                   dead={"ods-llama-server", "ods-whisper"})
    monkeypatch.setattr(_mod, "_pixel_model_transition_pending_for_route_proof", lambda: True)

    assert stack.check() == {"ods-llama-server": "deferred", "ods-whisper": "repaired"}
    assert stack.restarts() == ["ods-whisper"]
    assert _lifecycle_free()


def test_service_operation_in_progress_defers_the_restart(tmp_path, monkeypatch):
    stack = _Stack(tmp_path, monkeypatch, ["ods-whisper"], dead={"ods-whisper"})
    lock = _mod._service_locks["whisper"]
    assert lock.acquire(blocking=False)
    try:
        assert stack.check() == {"ods-whisper": "deferred"}
    finally:
        lock.release()
    assert stack.restarts() == []
    assert not stack.record.exists()


@pytest.mark.parametrize("health", ["starting", "unhealthy", None])
def test_container_not_reported_healthy_is_never_probed_and_the_wait_is_bounded(
    tmp_path, monkeypatch, health,
):
    stack = _Stack(tmp_path, monkeypatch, ["ods-whisper"], dead={"ods-whisper"})
    stack.rows = [stack.row("ods-whisper", health=health)]

    assert stack.check() == {"ods-whisper": "waiting"}
    assert stack.events == []
    assert _mod._WSL_PORT_CHECK_WINDOW_SECONDS <= stack.now
    assert stack.now < _mod._WSL_PORT_CHECK_WINDOW_SECONDS + _mod._WSL_PORT_CHECK_POLL_SECONDS


def test_dead_probes_must_be_consecutive_and_a_timeout_is_no_evidence(tmp_path, monkeypatch):
    stack = _Stack(tmp_path, monkeypatch, ["ods-whisper"], dead={"ods-whisper"})
    replies = iter(["closed", "timeout"] * 100)
    monkeypatch.setattr(_mod, "_probe_loopback_http", lambda _port: next(replies))

    assert stack.check() == {"ods-whisper": "waiting"}
    assert stack.restarts() == []


def test_container_stopped_before_its_restart_is_left_alone(tmp_path, monkeypatch):
    stack = _Stack(tmp_path, monkeypatch, ["ods-whisper"], dead={"ods-whisper"})
    monkeypatch.setattr(_mod, "_container_running", lambda _container_id: False)

    assert stack.check() == {"ods-whisper": "gone"}
    assert stack.restarts() == []
    assert not stack.record.exists()


def test_failed_restart_counts_as_this_boots_restart(tmp_path, monkeypatch, caplog):
    stack = _Stack(tmp_path, monkeypatch, ["ods-whisper"], dead={"ods-whisper"})
    attempts: list[list[str]] = []

    def failing(argv, **_kwargs):
        attempts.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "Error response from daemon: boom\n")

    monkeypatch.setattr(_mod.subprocess, "run", failing)

    assert stack.check() == {"ods-whisper": "restart-failed"}
    assert attempts == [["docker", "restart", stack.rows[0]["Id"]]]
    assert json.loads(stack.record.read_text(encoding="utf-8"))["restarted"] == ["ods-whisper"]
    assert "could not restart ods-whisper: Error response from daemon: boom" in caplog.text
    assert stack.check() == {"ods-whisper": "restarted-earlier"}
    assert len(attempts) == 1


def test_only_this_installations_declared_http_ports_on_loopback_are_targets(tmp_path):
    install = tmp_path / "ods"
    other_install = tmp_path / "other-ods"
    http_ports = {name: ("svc-" + name, 8080) for name in (
        "ods-whisper", "ods-webui", "ods-other-dir", "ods-other-project",
        "ods-stopped", "ods-lan-only", "ods-ipv6-only", "ods-bad-id", "ods-unpublished",
    )}
    unpublished = _row(install, "ods-unpublished")
    unpublished["NetworkSettings"]["Ports"] = {"8080/tcp": None}
    bad_id = _row(install, "ods-bad-id")
    bad_id["Id"] = "not-a-container-id"
    rows = [
        _row(install, "ods-whisper", host_port=9000),
        _row(install, "ods-webui", host_port=3000, host_ip="0.0.0.0"),
        _row(install, "ods-other-dir", working_dir=other_install),
        _row(install, "ods-other-project", project="someone-else"),
        _row(install, "ods-stopped", running=False),
        _row(install, "ods-lan-only", host_ip="192.168.1.20"),
        _row(install, "ods-ipv6-only", host_ip="::"),
        _row(install, "ods-postgres", port=5432, host_port=5432),  # no HTTP manifest port
        _row(install, "unrelated-app"),
        bad_id,
        unpublished,
    ]

    targets = _mod._wsl_port_check_targets(rows, http_ports, install)

    assert [(target["name"], target["service"], target["ports"], target["health"])
            for target in targets] == [
        ("ods-whisper", "svc-ods-whisper", [9000], "healthy"),
        ("ods-webui", "svc-ods-webui", [3000], "healthy"),
    ]


def test_declared_http_ports_skip_non_http_and_host_services(tmp_path, monkeypatch):
    builtin = tmp_path / "extensions"
    user = tmp_path / "user-extensions"
    user.mkdir()
    manifests = {
        "whisper": "service:\n  id: whisper\n  container_name: ods-whisper\n"
                   "  port: 8000\n  health: /health\n  type: docker\n",
        "pgvector": "service:\n  id: pgvector\n  port: 5432\n  health: ''\n  type: docker\n",
        "opencode": "service:\n  id: opencode\n  port: 3003\n  health: /\n  type: host-systemd\n",
        "one-shot": "service:\n  id: one-shot\n  port: 0\n  health: /\n",
    }
    for service_id, text in manifests.items():
        (builtin / service_id).mkdir(parents=True)
        (builtin / service_id / "manifest.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin)
    monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user)

    assert _mod._declared_http_ports() == {"ods-whisper": ("whisper", 8000)}


def _serve_once(behavior):
    """Accept one loopback connection and answer, close, reset or hold it."""
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    release = threading.Event()

    def run():
        connection, _address = server.accept()
        with connection:
            if behavior == "answer":
                connection.recv(1024)
                connection.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            elif behavior == "reset":
                linger = struct.pack("hh" if sys.platform == "win32" else "ii", 1, 0)
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            elif behavior == "hold":
                release.wait(10)
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, release, thread


@pytest.mark.parametrize(("behavior", "expected"), [
    ("answer", "answering"),  # any HTTP status proves the forward works
    ("close", "closed"),
    ("reset", "closed"),
])
def test_probe_classifies_real_loopback_replies(behavior, expected):
    port, release, thread = _serve_once(behavior)
    try:
        assert _mod._probe_loopback_http(port) == expected
    finally:
        release.set()
        thread.join(5)


def test_probe_reports_a_refused_port():
    unused = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unused.bind(("127.0.0.1", 0))
    port = unused.getsockname()[1]
    unused.close()

    assert _mod._probe_loopback_http(port) == "refused"


def test_probe_timeout_is_no_evidence(monkeypatch):
    monkeypatch.setattr(_mod, "_WSL_PORT_PROBE_TIMEOUT_SECONDS", 0.3)
    port, release, thread = _serve_once("hold")
    try:
        assert _mod._probe_loopback_http(port) == "timeout"
    finally:
        release.set()
        thread.join(5)


@pytest.mark.parametrize(("outcomes", "verdict"), [
    (["answering"], "answering"),
    (["closed", "answering"], "answering"),
    (["refused"], "dead"),
    (["closed", "timeout"], "dead"),
    (["timeout"], "unknown"),
    ([], "unknown"),
])
def test_one_reply_proves_a_containers_forward_works(outcomes, verdict):
    assert _mod._wsl_port_verdict(outcomes) == verdict


@pytest.mark.parametrize(("state", "verdict", "dead_polls", "restarted_earlier", "decision"), [
    ("waiting", "answering", 0, False, "answering"),
    ("waiting", "dead", 1, False, None),
    ("waiting", "dead", 2, False, "restart"),
    ("deferred", "dead", 3, False, "restart"),
    ("waiting", "dead", 2, True, "restarted-earlier"),
    ("waiting", "unknown", 0, False, None),
    ("restarted", "answering", 0, True, "repaired"),
    ("restarted", "dead", 1, True, "still-dead"),
    ("restarted", "unknown", 0, True, None),
])
def test_decision_restarts_only_after_consecutive_dead_polls(
    state, verdict, dead_polls, restarted_earlier, decision,
):
    assert _mod._wsl_port_check_decision(state, verdict, dead_polls, restarted_earlier) == decision


def test_restart_record_belongs_to_one_boot(tmp_path, caplog):
    record = tmp_path / "wsl-port-check.json"
    assert _mod._read_wsl_port_restarts(record, BOOT) == set()

    record.write_text(json.dumps({"bootId": BOOT, "restarted": ["ods-whisper"]}), encoding="utf-8")
    assert _mod._read_wsl_port_restarts(record, BOOT) == {"ods-whisper"}
    assert _mod._read_wsl_port_restarts(record, NEXT_BOOT) == set()

    record.write_text("{not json", encoding="utf-8")
    assert _mod._read_wsl_port_restarts(record, BOOT) == set()
    assert "ignoring unreadable restart record" in caplog.text


def test_boot_id_must_be_a_kernel_uuid(tmp_path):
    boot_id = tmp_path / "boot_id"
    boot_id.write_text(BOOT + "\n", encoding="utf-8")
    assert _mod._linux_boot_id(boot_id) == BOOT

    boot_id.write_text("not-a-boot-id\n", encoding="utf-8")
    assert _mod._linux_boot_id(boot_id) is None
    assert _mod._linux_boot_id(tmp_path / "missing") is None


def test_running_rows_come_from_the_ods_compose_project(monkeypatch):
    calls: list[list[str]] = []
    first, second = "a" * 64, "b" * 64

    def docker(argv, **_kwargs):
        calls.append(argv)
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, f"{first}\n{second}\n", "")
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"Id": first}, {"Id": second}]), "")

    monkeypatch.setattr(_mod.subprocess, "run", docker)
    assert _mod._running_ods_container_rows() == [{"Id": first}, {"Id": second}]
    assert calls == [
        ["docker", "ps", "--no-trunc", "--quiet", "--filter", "status=running",
         "--filter", "label=com.docker.compose.project=ods"],
        ["docker", "inspect", "--type", "container", first, second],
    ]

    def unavailable(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(_mod.subprocess, "run", unavailable)
    assert _mod._running_ods_container_rows() == []


def test_thread_body_needs_a_boot_id_and_logs_one_summary(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="ods-host-agent")
    calls: list[tuple] = []

    def check(boot_id, record_path):
        calls.append((boot_id, record_path))
        return {"ods-webui": "repaired", "ods-whisper": "repaired",
                "ods-dashboard": "answering", "ods-llama-server": "deferred"}

    monkeypatch.setattr(_mod, "_wsl_port_check", check)
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_mod, "_linux_boot_id", lambda: None)
    _mod._run_wsl_port_check()
    assert calls == []
    assert "WSL port check skipped" in caplog.text

    monkeypatch.setattr(_mod, "_linux_boot_id", lambda: BOOT)
    _mod._run_wsl_port_check()
    assert calls == [(BOOT, tmp_path / "wsl-port-check.json")]
    assert ("WSL port check finished: answering ods-dashboard; deferred ods-llama-server; "
            "repaired ods-webui, ods-whisper") in caplog.text


def test_check_starts_only_under_wsl_and_never_inside_a_container(monkeypatch):
    started = threading.Event()
    monkeypatch.setattr(_mod, "_run_wsl_port_check", started.set)
    monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)

    monkeypatch.setattr(_mod, "_is_wsl_linux", lambda: False)
    assert _mod._start_wsl_port_check() is None

    monkeypatch.setattr(_mod, "_is_wsl_linux", lambda: True)
    monkeypatch.setenv("ODS_HOST_INSTALL_DIR", "/srv/ods")
    assert _mod._start_wsl_port_check() is None
    assert not started.is_set()

    monkeypatch.delenv("ODS_HOST_INSTALL_DIR")
    thread = _mod._start_wsl_port_check()
    thread.join(5)
    assert started.is_set()
    assert thread.daemon and thread.name == "ods-wsl-port-check"


def test_agent_starts_the_check_after_binding_and_before_serving():
    source = inspect.getsource(_mod.main)
    assert (source.index("_create_host_agent_server(")
            < source.index("_start_wsl_port_check()")
            < source.index("server.serve_forever()"))
