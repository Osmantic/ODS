from __future__ import annotations

import http.client
import importlib.util
import json
import socket
import socketserver
import sys
import threading
import urllib.request
from pathlib import Path


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
    left, right = socket.socketpair()
    try:
        bridge._enable_tcp_keepalive(left)
        assert (
            left.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
        )
    finally:
        left.close()
        right.close()


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
