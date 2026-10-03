import http.client
import io
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def agent_module():
    path = Path(__file__).resolve().parents[2] / "bin/ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("_pr6807_agent", path)
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    spec.loader.exec_module(agent)
    agent.AGENT_API_KEY = "synthetic-pr6807-key"
    yield agent
    sys.modules.pop(spec.name, None)


@pytest.fixture
def live_server(agent_module, tmp_path):
    agent = agent_module
    agent.DATA_DIR = tmp_path
    listener = agent.ThreadedHTTPServer(("127.0.0.1", 0), agent.AgentHandler)
    listener.request_socket_timeout = 1
    listener.request_body_timeout = 0.15
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        yield agent, listener
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _auth_headers():
    return {"Authorization": "Bearer synthetic-pr6807-key"}


def _raw_response(client):
    chunks = []
    while chunk := client.recv(4096):
        chunks.append(chunk)
    return b"".join(chunks)


def test_trickled_incomplete_body_hits_total_deadline(live_server):
    agent, listener = live_server
    entered = threading.Event()
    exited = threading.Event()
    original = agent._read_request_body_bytes

    def watched(handler, length):
        entered.set()
        try:
            return original(handler, length)
        finally:
            exited.set()

    agent._read_request_body_bytes = watched
    try:
        client = socket.create_connection(listener.server_address, timeout=2)
        client.settimeout(2)
        try:
            client.sendall(
                b"POST /v1/extension/start HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Authorization: Bearer synthetic-pr6807-key\r\n"
                b"Content-Length: 100\r\nConnection: close\r\n\r\n{"
            )
            assert entered.wait(2), "handler did not enter body reader"
            begin = time.monotonic()
            while not exited.wait(0.03) and time.monotonic() - begin < 0.8:
                try:
                    client.sendall(b" ")
                except OSError:
                    break
            assert exited.wait(2), "body reader did not exit under total deadline"
            elapsed = time.monotonic() - begin
            assert elapsed < 0.6, f"reader held past total deadline: {elapsed:.3f}s"
            response = _raw_response(client).decode("ascii", errors="replace")
            status_line = response.split("\r\n", 1)[0]
            assert "408" in status_line, status_line
            assert "close" in response.lower()
        finally:
            client.close()
    finally:
        agent._read_request_body_bytes = original


def test_normal_complete_request_succeeds(agent_module, tmp_path):
    agent = agent_module
    agent.DATA_DIR = tmp_path

    class InertHandler(agent.AgentHandler):
        def do_POST(self):
            body = agent._read_request_body_bytes(self, int(self.headers["Content-Length"]))
            agent.json_response(self, 200, {"received": len(body)})

    listener = agent.ThreadedHTTPServer(("127.0.0.1", 0), InertHandler)
    listener.request_socket_timeout = 1
    listener.request_body_timeout = 0.5
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            payload = json.dumps({"hello": "world"}).encode()
            connection.request("POST", "/inert", body=payload, headers=_auth_headers())
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"received": len(payload)}
            # A pooled HTTP/1.1 connection gets a fresh body budget per request.
            time.sleep(0.6)
            connection.request("POST", "/inert", body=payload, headers=_auth_headers())
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"received": len(payload)}
        finally:
            connection.close()
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def test_incomplete_eof_body_returns_partial(agent_module, tmp_path):
    agent = agent_module
    agent.DATA_DIR = tmp_path
    captured = {}

    class EofHandler(agent.AgentHandler):
        def do_POST(self):
            captured["body"] = agent._read_request_body_bytes(self, 100)
            agent.json_response(self, 200, {"received": len(captured["body"])})

    listener = agent.ThreadedHTTPServer(("127.0.0.1", 0), EofHandler)
    listener.request_socket_timeout = 1
    listener.request_body_timeout = 0.5
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        client = socket.create_connection(listener.server_address, timeout=2)
        client.settimeout(2)
        try:
            client.sendall(
                b"POST /eof HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Authorization: Bearer synthetic-pr6807-key\r\n"
                b"Content-Length: 100\r\nConnection: close\r\n\r\n{\"partial\":"
            )
            client.shutdown(socket.SHUT_WR)
            response = _raw_response(client).decode("ascii", errors="replace")
            assert "200" in response.split("\r\n", 1)[0]
            assert captured["body"] == b'{"partial":'
        finally:
            client.close()
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def test_repeated_reads_share_deadline(agent_module, tmp_path):
    agent = agent_module
    agent.DATA_DIR = tmp_path
    observed = {}

    class RepeatHandler(agent.AgentHandler):
        def do_POST(self):
            first = agent._read_request_body_bytes(self, 4)
            deadline_after_first = self._body_deadline
            time.sleep(0.05)
            second = agent._read_request_body_bytes(self, 4)
            observed["deadline_stable"] = self._body_deadline == deadline_after_first
            observed["body"] = first + second
            agent.json_response(self, 200, {"received": len(observed["body"])})

    listener = agent.ThreadedHTTPServer(("127.0.0.1", 0), RepeatHandler)
    listener.request_socket_timeout = 1
    listener.request_body_timeout = 0.5
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            connection.request("POST", "/repeat", body=b"abcdefgh", headers=_auth_headers())
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            assert observed["deadline_stable"] is True
            assert observed["body"] == b"abcdefgh"
        finally:
            connection.close()
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def test_synchronous_response_delay_after_valid_body(agent_module, tmp_path):
    agent = agent_module
    agent.DATA_DIR = tmp_path

    class SlowHandler(agent.AgentHandler):
        def do_POST(self):
            body = agent._read_request_body_bytes(self, int(self.headers["Content-Length"]))
            time.sleep(0.4)
            agent.json_response(self, 200, {"received": len(body)})

    listener = agent.ThreadedHTTPServer(("127.0.0.1", 0), SlowHandler)
    listener.request_socket_timeout = 1
    listener.request_body_timeout = 0.15
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            payload = b'{"ok":true}'
            connection.request("POST", "/slow", body=payload, headers=_auth_headers())
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"received": len(payload)}
        finally:
            connection.close()
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def test_settings_partial_body_uses_helper_deadline(live_server):
    agent, listener = live_server
    entered = threading.Event()
    exited = threading.Event()
    original = agent._read_request_body_bytes

    def watched(handler, length):
        entered.set()
        try:
            return original(handler, length)
        finally:
            exited.set()

    agent._read_request_body_bytes = watched
    try:
        client = socket.create_connection(listener.server_address, timeout=2)
        client.settimeout(2)
        try:
            client.sendall(
                b"POST /v1/pixel/settings/save HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Authorization: Bearer synthetic-pr6807-key\r\n"
                b"Content-Length: 100\r\nConnection: close\r\n\r\n{\"partial\":"
            )
            assert entered.wait(2), "settings handler did not enter body reader"
            assert exited.wait(2), "settings body reader did not exit under total deadline"
            response = _raw_response(client).decode("ascii", errors="replace")
            status_line = response.split("\r\n", 1)[0]
            # Settings retains its existing unavailable-response policy on
            # IO failure; the worker must still exit without any persistence.
            assert "503" in status_line, status_line
            assert list(agent.DATA_DIR.iterdir()) == []
        finally:
            client.close()
    finally:
        agent._read_request_body_bytes = original


def test_blocking_socket_timeout_is_restored(agent_module):
    class Connection:
        timeout = None
        def gettimeout(self):
            return self.timeout
        def settimeout(self, value):
            self.timeout = value

    connection = Connection()
    handler = SimpleNamespace(connection=connection, rfile=io.BytesIO(b"payload"))
    assert agent_module._read_request_body_bytes(handler, 7) == b"payload"
    assert connection.gettimeout() is None


@pytest.mark.parametrize("oversized", [False, True])
def test_common_json_reader_rejects_incomplete_or_oversized_frame(live_server, oversized):
    agent, listener = live_server
    client = socket.create_connection(listener.server_address, timeout=2)
    client.settimeout(2)
    try:
        declared = agent.MAX_BODY + 1 if oversized else 100
        client.sendall((
            "POST /v1/extension/start HTTP/1.1\r\nHost: localhost\r\n"
            "Authorization: Bearer synthetic-pr6807-key\r\n"
            f"Content-Length: {declared}\r\nConnection: close\r\n\r\n{{}}"
        ).encode())
        client.shutdown(socket.SHUT_WR)
        response = _raw_response(client)
        assert str(413 if oversized else 400).encode() in response.split(b"\r\n", 1)[0]
        assert list(agent.DATA_DIR.iterdir()) == []
    finally:
        client.close()
