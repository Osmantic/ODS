from __future__ import annotations

import http.client
import importlib.util
import json
import socket
import socketserver
import struct
import sys
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "ods-macos-llm-bridge.py"
SPEC = importlib.util.spec_from_file_location("ods_macos_llm_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)

AGENT_MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "ods-host-agent.py"
AGENT_SPEC = importlib.util.spec_from_file_location("ods_host_agent_bridge_test", AGENT_MODULE_PATH)
assert AGENT_SPEC and AGENT_SPEC.loader
agent = importlib.util.module_from_spec(AGENT_SPEC)
sys.modules["ods_host_agent_bridge_test"] = agent
AGENT_SPEC.loader.exec_module(agent)


@contextmanager
def _live_tunnel():
    class ObservedBridge(bridge.LlmBridgeServer):
        max_connections = 1

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                finished.set()

    finished = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(3)
        proxy = ObservedBridge(("127.0.0.1", 0), listener.getsockname())
        worker = threading.Thread(target=proxy.serve_forever, daemon=True)
        worker.start()
        client = socket.create_connection(proxy.server_address, timeout=3)
        upstream, _ = listener.accept()
        upstream.settimeout(3)
        try:
            # Ensure both directions actually crossed the production tunnel.
            client.sendall(b"request")
            assert upstream.recv(7) == b"request"
            upstream.sendall(b"ready")
            assert client.recv(5) == b"ready"
            yield client, upstream, proxy, finished
        finally:
            for connection in (client, upstream):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            proxy.shutdown()
            proxy.server_close()
            worker.join(timeout=3)
            assert finished.wait(3), "bridge handler did not exit after cleanup"


@pytest.mark.parametrize("reset_peer", ["client", "upstream"])
def test_reset_releases_tunnel_while_other_peer_remains_open(reset_peer):
    with _live_tunnel() as (client, upstream, proxy, finished):
        connection = client if reset_peer == "client" else upstream
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        connection.close()
        assert finished.wait(2), "reset tunnel retained its handler and connection slot"
        assert proxy._connection_slots.acquire(blocking=False)
        proxy._connection_slots.release()


def test_request_half_close_still_receives_complete_response():
    with _live_tunnel() as (client, upstream, _proxy, finished):
        client.shutdown(socket.SHUT_WR)
        assert upstream.recv(1) == b""
        assert not finished.is_set()
        response = b"response after request EOF" * 10000
        sender = threading.Thread(target=upstream.sendall, args=(response,), daemon=True)
        sender.start()
        received = bytearray()
        while len(received) < len(response):
            chunk = client.recv(65536)
            assert chunk
            received.extend(chunk)
        sender.join(timeout=3)
        assert not sender.is_alive()
        assert bytes(received) == response
        upstream.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""
        assert finished.wait(2)


class _HttpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.recv(4096)
        body = b'{"status":"ok"}'
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )


def test_peer_allowlist_is_loopback_only():
    assert bridge.peer_is_allowed("127.0.0.1") is True
    assert bridge.peer_is_allowed("::1") is True
    assert bridge.peer_is_allowed("192.168.5.2") is False
    assert bridge.peer_is_allowed("192.168.1.50") is False
    assert bridge.peer_is_allowed("not-an-ip") is False


def test_peer_allowlist_accepts_explicit_vm_address_or_subnet():
    exact = bridge.parse_allowed_networks(["192.168.64.2"])
    subnet = bridge.parse_allowed_networks(["192.168.64.0/24"])

    assert bridge.peer_is_allowed("192.168.64.2", exact) is True
    assert bridge.peer_is_allowed("192.168.64.3", exact) is False
    assert bridge.peer_is_allowed("192.168.64.3", subnet) is True
    assert bridge.peer_is_allowed("192.168.65.2", subnet) is False


def test_bridge_forwards_loopback_http():
    upstream = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _HttpHandler)
    proxy = bridge.LlmBridgeServer(
        ("127.0.0.1", 0),
        ("127.0.0.1", upstream.server_address[1]),
    )
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    upstream_thread.start()
    proxy_thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{proxy.server_address[1]}/health",
            timeout=5,
        ) as response:
            assert response.read() == b'{"status":"ok"}'
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()


def test_bridge_backlog_handles_dashboard_poll_bursts():
    assert bridge.LlmBridgeServer.request_queue_size >= 64
    assert bridge.LlmBridgeServer.max_connections <= 8


def test_bridge_enables_tcp_keepalive():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        assert connection.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 0
        bridge._enable_tcp_keepalive(connection)
        # BSD may return the enabled option bit (8), rather than the integer 1.
        assert connection.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0


def test_bridge_reuses_real_host_agent_gets_and_safely_closes_posts(monkeypatch):
    class _CountingAgentServer(agent.ThreadedHTTPServer):
        accepted_connections = 0

        def get_request(self):
            request = super().get_request()
            self.accepted_connections += 1
            return request

    class _CountingBridgeServer(bridge.LlmBridgeServer):
        accepted_connections = 0

        def get_request(self):
            request = super().get_request()
            self.accepted_connections += 1
            return request

    monkeypatch.setattr(agent, "AGENT_API_KEY", "bridge-test-key")
    upstream = _CountingAgentServer(("127.0.0.1", 0), agent.AgentHandler)
    proxy = _CountingBridgeServer(
        ("127.0.0.1", 0),
        ("127.0.0.1", upstream.server_port),
    )
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    upstream_thread.start()
    proxy_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", proxy.server_address[1], timeout=5)
    try:
        for _ in range(20):
            connection.request("GET", "/health")
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok", "version": agent.VERSION}
        assert proxy.accepted_connections == 1
        assert upstream.accepted_connections == 1

        connection.request(
            "POST",
            "/v1/model/download/cancel",
            body="{}",
            headers={
                "Authorization": "Bearer bridge-test-key",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Connection") == "close"
        assert json.loads(response.read()) == {"status": "no_download"}

        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok", "version": agent.VERSION}
        assert proxy.accepted_connections == 2
        assert upstream.accepted_connections == 2
    finally:
        connection.close()
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()
        proxy_thread.join(timeout=5)
        upstream_thread.join(timeout=5)
