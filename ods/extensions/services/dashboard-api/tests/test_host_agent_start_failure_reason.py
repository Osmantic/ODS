"""A failed extension start answers and logs the reason the owner acts on.

On a Windows + WSL2 + Docker Desktop host, a native Windows app already held
127.0.0.1:9000 when Whisper was added from the Extensions Library. Docker
Desktop refused to publish the port, and /v1/extension/start answered a bare
500: its error was the first 500 characters of Compose output, which can end
before Docker's own error line, and the agent logged nothing but the request.
A Hermes prepare step failed the same silent way. These tests drive the real
docker_compose_action and request handler with Docker faked.
"""

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_host_agent import _FakeHandler, _mod

_WHISPER_MANIFEST = Path(__file__).resolve().parents[2] / "whisper" / "manifest.yaml"
# Compose lists the containers it touches before Docker's error. Enough of
# them push that error past the first 500 characters.
_PROGRESS = "".join(f" Container ods-dependency-{index:02d}  Running\n" for index in range(16))
_DESKTOP_EXPOSE = ("Error response from daemon: ports are not available: exposing port TCP "
                   "127.0.0.1:9000 -> 127.0.0.1:0: /forwards/expose returned unexpected status: 500")
_PORT_REASON = ("Host port 9000 is already in use, so whisper could not start. Set WHISPER_PORT "
                "in .env to a free port (ods config edit), or stop the program using port 9000, "
                "then retry.")
_HERMES_REASON = "Could not read or write Hermes route files; check installation permissions"
# Assembled at runtime so this file holds no token-shaped literal.
_TOKEN = "hf_" + hashlib.sha256(b"ODS start failure fixture; never issued").hexdigest()[:34]


@pytest.fixture
def host(tmp_path, monkeypatch):
    """An installation with Whisper and Hermes definitions and faked Compose."""
    builtins = tmp_path / "extensions" / "services"
    (builtins / "whisper").mkdir(parents=True)
    (builtins / "whisper" / "manifest.yaml").write_bytes(_WHISPER_MANIFEST.read_bytes())
    (builtins / "hermes").mkdir()
    (builtins / "hermes" / "manifest.yaml").write_text(
        "service:\n  id: hermes\n  port: 8642\n", encoding="utf-8")
    (tmp_path / ".env").write_text("WHISPER_PORT=9000\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtins)
    monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", tmp_path / "data" / "user-extensions")
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    monkeypatch.setattr(_mod, "resolve_compose_flags", lambda: ["-f", "docker-compose.base.yml"])
    monkeypatch.setattr(_mod, "_precreate_data_dirs", lambda _sid: None)
    monkeypatch.setattr(_mod, "_repair_rootless_data_ownership", lambda _sid: None)
    for service_id in ("whisper", "hermes"):
        monkeypatch.delitem(_mod._service_locks, service_id, raising=False)
    starts = []

    def compose_up(stderr):
        def up(service_id, flags, *, env=None):
            starts.append(service_id)
            return subprocess.CompletedProcess(["docker", "compose", "up"], 1, "", stderr)
        monkeypatch.setattr(_mod, "_run_selected_extension_up", up)

    return SimpleNamespace(root=tmp_path, starts=starts, compose_up=compose_up)


def _start(service_id):
    handler = _FakeHandler(json.dumps({"service_id": service_id}).encode("utf-8"))
    _mod.AgentHandler._handle_extension(handler, "start")
    return handler.response_code, handler.parse_response()


@pytest.mark.parametrize("docker_error", [
    _DESKTOP_EXPOSE,
    # Docker Desktop on Windows, when its own listen on the Windows side fails.
    "Error response from daemon: ports are not available: exposing port TCP 127.0.0.1:9000 -> "
    "127.0.0.1:0: listen tcp 127.0.0.1:9000: bind: Only one usage of each socket address "
    "(protocol/network address/port) is normally permitted.",
    # Docker Engine's userland proxy.
    "Error response from daemon: driver failed programming external connectivity on endpoint "
    "ods-whisper (0123abcd): Error starting userland proxy: listen tcp4 127.0.0.1:9000: bind: "
    "address already in use",
    # Docker Engine, port held by another container.
    "Error response from daemon: driver failed programming external connectivity on endpoint "
    "ods-whisper (0123abcd): Bind for 127.0.0.1:9000 failed: port is already allocated",
    # Docker Engine 28.
    "Error response from daemon: failed to set up container networking: driver failed programming "
    "external connectivity on endpoint ods-whisper (0123abcd): failed to bind host port for "
    "127.0.0.1:9000:172.18.0.5:8000/tcp: address already in use",
    # An IPv6 loopback bind.
    "Error response from daemon: ports are not available: exposing port TCP [::1]:9000 -> [::]:0: "
    "listen tcp6 [::1]:9000: bind: address already in use",
], ids=["desktop-expose", "desktop-windows-bind", "engine-proxy", "engine-allocated",
        "engine-28", "ipv6-loopback"])
def test_taken_host_port_names_the_port_and_its_setting(host, docker_error):
    host.compose_up(_PROGRESS + docker_error + "\n")

    ok, error = _mod.docker_compose_action("whisper", "start")

    assert ok is False
    assert error == _PORT_REASON + "\n" + docker_error
    assert host.starts == ["whisper"]


def test_a_port_the_service_does_not_publish_is_named_without_its_setting(host):
    (host.root / ".env").write_text("WHISPER_PORT=9100\n", encoding="utf-8")
    host.compose_up(_DESKTOP_EXPOSE.replace("9000", "8880") + "\n")

    ok, error = _mod.docker_compose_action("whisper", "start")

    assert ok is False
    assert error.startswith("Host port 8880 is already in use, so whisper could not start. "
                            "Stop the program using port 8880")
    assert "WHISPER_PORT" not in error


def test_other_failures_keep_dockers_last_lines_redacted(host):
    final = f"Error response from daemon: pull access denied for example/whisper, HF_TOKEN={_TOKEN}"
    host.compose_up(_PROGRESS + final + "\n")

    ok, error = _mod.docker_compose_action("whisper", "start")

    assert ok is False
    assert _TOKEN not in error
    assert error.endswith("Error response from daemon: pull access denied for example/whisper, "
                          "HF_TOKEN=[REDACTED]")
    # The end of Compose's output, starting at a whole line.
    assert error.startswith(" Container ods-dependency-")
    assert len(error) <= 500


def test_failed_start_answers_and_logs_the_reason(host, caplog):
    host.compose_up(_PROGRESS + _DESKTOP_EXPOSE + "\n")

    with caplog.at_level(logging.WARNING, logger="ods-host-agent"):
        status, body = _start("whisper")

    assert status == 500
    assert body == {"error": _PORT_REASON + "\n" + _DESKTOP_EXPOSE}
    assert f"Extension start failed for whisper: {_PORT_REASON}" in caplog.text


def test_hermes_prepare_step_reason_reaches_the_answer_and_the_log(host, monkeypatch, caplog):
    # The route files cannot be read: the same branch an unwritable route
    # file takes. Compose must not be asked to start Hermes.
    (host.root / ".env").unlink()
    (host.root / ".env").mkdir()
    monkeypatch.setattr(_mod, "_hermes_compose_plan_error", lambda _flags: "")
    host.compose_up("")

    with caplog.at_level(logging.WARNING, logger="ods-host-agent"):
        status, body = _start("hermes")

    assert status == 500
    assert body == {"error": _HERMES_REASON}
    assert f"Extension start failed for hermes: {_HERMES_REASON}" in caplog.text
    assert host.starts == []
