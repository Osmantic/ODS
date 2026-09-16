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
import socketserver
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
            try:
                if mode == "headers":
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                elif mode == "body":
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 99999\r\n\r\n{")
                elif mode == "redirect":
                    self.request.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/steal\r\nContent-Length: 0\r\n\r\n")
                    return
                else:
                    raw = json.dumps({"ok": True}).encode()
                    if mode == "duplicate": raw = b'{"ok":true,"ok":false}'
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


@pytest.mark.parametrize("mode", ["redirect", "duplicate"])
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
