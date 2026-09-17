"""Actual loopback sockets/process/pipe deadlines; no installed services."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
import pixel_access_bridge as bridge


@contextlib.contextmanager
def local_server(mode):
    seen = []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            seen.append(self.request.recv(8192))
            response_mode = mode[min(len(seen) - 1, len(mode) - 1)] if isinstance(mode, list) else mode
            try:
                if response_mode == "reset":
                    self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    return
                if response_mode == "disconnect": return
                if response_mode == "refused":
                    self.request.sendall(b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n")
                    return
                if response_mode == "incomplete":
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\n{")
                    return
                if response_mode == "headers":
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                elif response_mode == "body":
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 99999\r\n\r\n{")
                elif response_mode == "redirect":
                    self.request.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/steal\r\nContent-Length: 0\r\n\r\n")
                    return
                else:
                    raw = json.dumps({"ok": True}).encode()
                    if response_mode == "duplicate": raw = b'{"ok":true,"ok":false}'
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(raw)).encode() + b"\r\n\r\n" + raw)
                    return
                for _ in range(30):
                    time.sleep(0.025)
                    self.request.sendall(b" ")
            except (BrokenPipeError, ConnectionResetError):
                return  # The test deliberately cancels this per-call socket.
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        try: yield "http://127.0.0.1:%d" % server.server_address[1], seen
        finally:
            server.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.mark.parametrize("mode", ["headers", "body"])
def test_slow_trickle_is_bounded_by_absolute_http_deadline(tmp_path, mode):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    with local_server(mode) as (origin, seen):
        start = time.monotonic()
        with pytest.raises(bridge.AccessError, match="runtime-operation-timeout"):
            adapter.http(origin, "/health", "synthetic-key", timeout=0.10)
        assert time.monotonic() - start < 0.55
        assert len(seen) == 1


@pytest.mark.parametrize("mode", ["redirect", "duplicate", "refused", "incomplete"])
def test_http_does_not_follow_redirect_or_accept_duplicate_json(tmp_path, mode):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    with local_server(mode) as (origin, seen):
        with pytest.raises(bridge.AccessError): adapter.http(origin, "/health", "synthetic-key")
        assert len(seen) == 1


def test_successful_http_keeps_key_out_of_response(tmp_path):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    with local_server("ok") as (origin, seen):
        assert adapter.http(origin, "/health", "synthetic-key") == {"ok": True}
        assert b"Authorization: Bearer synthetic-key" in seen[0]


@pytest.mark.parametrize("mode", ["reset", "disconnect"])
def test_read_reset_gets_two_fresh_connections_and_uses_only_complete_response(tmp_path, mode):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    with local_server([mode, mode, "ok"]) as (origin, seen):
        assert adapter.http(origin, "/pixel-ods/access-runtime", "synthetic-key") == {"ok": True}
        assert len(seen) == 3
        assert all(request.startswith(b"GET /pixel-ods/access-runtime ") for request in seen)


def test_read_reset_stops_after_three_connections(tmp_path):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    with local_server("reset") as (origin, seen):
        with pytest.raises(bridge.AccessError, match="runtime-unavailable-or-busy"):
            adapter.http(origin, "/pixel-ods/access-runtime", "synthetic-key")
        assert len(seen) == 3


@pytest.mark.parametrize("mode", ["reset", "disconnect"])
def test_mutation_reset_is_never_replayed(tmp_path, mode):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    with local_server([mode, "ok"]) as (origin, seen):
        with pytest.raises(bridge.AccessError, match="runtime-unavailable-or-busy"):
            adapter.http(origin, "/pixel-ods/access-runtime", "synthetic-key", {"operation": "acquire"})
        assert len(seen) == 1
        assert seen[0].startswith(b"POST /pixel-ods/access-runtime ")


def test_read_retries_share_deadline_and_close_each_previous_connection(tmp_path, monkeypatch):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    now = [100.0]
    connections = []
    class ResetConnection:
        def __init__(self, host, port, timeout):
            assert not connections or connections[-1].closed
            self.timeout, self.closed, self.sock = timeout, False, self
            connections.append(self)
        def connect(self): pass
        def request(self, method, *args, **kwargs): assert method == "GET"
        def getresponse(self):
            now[0] += 0.3
            raise ConnectionResetError()
        def close(self): self.closed = True
        def shutdown(self, how): pass
    monkeypatch.setattr(bridge.http.client, "HTTPConnection", ResetConnection)
    monkeypatch.setattr(bridge.time, "monotonic", lambda: now[0])
    with pytest.raises(bridge.AccessError, match="runtime-operation-timeout"):
        adapter.http("http://127.0.0.1:1", "/pixel-ods/access-runtime", "synthetic-key", timeout=0.5)
    assert len(connections) == 2
    assert [connection.timeout for connection in connections] == pytest.approx([0.5, 0.2])
    assert all(connection.closed for connection in connections)


def test_nested_and_concurrent_deadlines_do_not_leak_between_requests(tmp_path):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    barrier = threading.Barrier(2)
    def request(seconds):
        try:
            with adapter.bounded(seconds):
                barrier.wait(timeout=1)
                time.sleep(0.07)
                return bridge.remaining(10) > 0
        except bridge.AccessError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        short = pool.submit(request, 0.025)
        long = pool.submit(request, 1)
        assert short.result() is False and long.result() is True
    with adapter.bounded(1):
        with pytest.raises(bridge.AccessError):
            with adapter.bounded(0.001): time.sleep(0.01)
        assert bridge.remaining(1) > 0.5
    assert bridge.remaining(7) == 7


def test_command_uses_operation_budget_and_expiry_does_not_execute(tmp_path):
    adapter = bridge.SystemdAccessBridge(tmp_path, "key")
    marker = tmp_path / "must-not-exist"
    with pytest.raises(bridge.AccessError, match="operation-deadline-exceeded"):
        with adapter.bounded(0.08):
            with pytest.raises(bridge.AccessError, match="host-command-failed"):
                adapter.command([sys.executable, "-c", "import time; time.sleep(2)"], timeout=30)
            with pytest.raises(bridge.AccessError, match="operation-deadline-exceeded"):
                adapter.command([sys.executable, "-c", "import sys; from pathlib import Path; Path(sys.argv[1]).touch()", str(marker)])
    assert not marker.exists()


def test_nonreading_pipe_cannot_block_before_response_timer():
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(write_fd, "w") as stream:
            start = time.monotonic()
            with pytest.raises(bridge.AccessError, match="owner-worker-timeout"):
                bridge._pipe_send(stream, "x" * (2 * 1024 * 1024), start + 0.08)
            assert time.monotonic() - start < 0.5
    finally: os.close(read_fd)
