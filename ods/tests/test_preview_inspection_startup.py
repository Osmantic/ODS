"""Exercise broker readiness using real local sockets; never installed services."""
import importlib.util
from pathlib import Path
import socket
import threading
import time

import pytest


SPEC = importlib.util.spec_from_file_location(
    "inspection_startup",
    Path(__file__).resolve().parents[1] / "installers/lib/pixel-preview-inspection.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize("delay", [0, 6])
def test_waits_for_real_socket_beyond_old_five_second_window(tmp_path, monkeypatch, delay):
    path = tmp_path / "control.sock"
    monkeypatch.setattr(module, "OWNER_SOCKET", path)
    stopped = threading.Event()
    errors = []

    def server():
        try:
            if stopped.wait(delay):
                return
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                listener.listen(1)
                listener.settimeout(3)
                connection, _ = listener.accept()
                connection.close()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=server)
    started = time.monotonic()
    thread.start()
    try:
        module.wait_owner_socket()
        assert time.monotonic() - started >= delay
    finally:
        stopped.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors


@pytest.mark.parametrize("state", ["missing", "not-a-socket", "permission-denied"])
def test_unavailable_socket_fails_with_bounded_actionable_error(tmp_path, monkeypatch, state):
    path = tmp_path / "control.sock"
    monkeypatch.setattr(module, "OWNER_SOCKET", path)
    monkeypatch.setattr(module, "OWNER_SOCKET_WAIT_SECONDS", .15)
    if state == "not-a-socket":
        path.write_text("not a socket")
    if state == "permission-denied":
        real_socket = socket.socket

        class DeniedSocket(real_socket):
            def connect(self, address):
                raise PermissionError("fixture owner cannot connect")

        monkeypatch.setattr(module.socket, "socket", DeniedSocket)
    started = time.monotonic()
    with pytest.raises(SystemExit, match="did not become accessible to its owner") as error:
        module.wait_owner_socket()
    assert .15 <= time.monotonic() - started < 2
    assert "journalctl -u pixel-preview-inspection.service" in str(error.value)
