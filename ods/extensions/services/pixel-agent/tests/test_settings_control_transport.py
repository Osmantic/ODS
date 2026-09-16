"""Actual disposable Unix transport, not the installed root daemon."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import hashlib
import json
from pathlib import Path
import socket
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
import pixel_access_client as client
from pixel_access_protocol import control_request


@pytest.mark.parametrize("operation", ["status", "change", "settings-status", "settings-change"])
def test_client_uses_fixed_socket_and_only_data_directory_fingerprint(tmp_path, monkeypatch, operation):
    path = str(tmp_path / "control.sock")
    original = socket.socket
    listener = original(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    listener.settimeout(2)
    seen, errors = [], []
    def serve():
        try:
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as stream:
                request = control_request(json.loads(stream.readline(2049)))
                seen.append(request)
                connection.sendall(b'{"status":200,"body":{"fixture":true}}\n')
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=serve)
    thread.start()
    class Connection:
        def __init__(self, *args, **kwargs): self.real = original(*args, **kwargs)
        def __enter__(self): return self
        def __exit__(self, *_args): self.real.close()
        def __getattr__(self, name): return getattr(self.real, name)
        def connect(self, address):
            assert address == "/run/ods-pixel-access/control.sock"
            self.real.connect(path)
        def settimeout(self, seconds):
            assert seconds == 335  # Verify production budget; bound this fixture.
            self.real.settimeout(2)
    monkeypatch.setattr(client.socket, "socket", Connection)
    try:
        status, result = client.request_access(operation, {}, settings_data_dir="/opt/custom data")
        assert status == 200 and result == {"fixture": True}
        thread.join(timeout=2)
        assert not thread.is_alive() and not errors
        assert seen[0]["operation"] == operation
        if operation.startswith("settings-"):
            assert seen[0]["data_dir_id"] == hashlib.sha256(b"/opt/custom data").hexdigest()
            assert "/opt/custom data" not in json.dumps(seen[0])
        else: assert "data_dir_id" not in seen[0]
    finally: listener.close()


@pytest.mark.parametrize("operation,directory", [("exec", "/opt/data"), ("settings-status", None),
                                               ("settings-change", "relative")])
def test_unqualified_requests_do_not_open_socket(monkeypatch, operation, directory):
    def unexpected(*_args, **_kwargs): pytest.fail("unexpected socket creation")
    monkeypatch.setattr(client.socket, "socket", unexpected)
    with pytest.raises(ValueError): client.request_access(operation, {}, settings_data_dir=directory)
