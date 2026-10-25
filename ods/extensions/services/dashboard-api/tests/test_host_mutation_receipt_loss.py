"""Real accepted POSTs must not be replayed when their HTTP receipt is lost.

The fake host persists accepted operations before dropping the connection.
No Docker, native service manager, installed host agent, or real service runs.
"""

import asyncio
import json
import os
import socket
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import host_agent_client as agent
from routers import opencode_app, resources

AUTH = {"Authorization": "Bearer test-key-12345"}
AGENT_KEY = "host-mutation-receipt-fixture"
ROUTES = [
    ("/api/services/receipt-fixture/restart", "/v1/service/restart", {"service_id": "receipt-fixture"}),
    ("/api/apps/opencode/start", "/v1/opencode/start", None),
]


@pytest.fixture
def host(tmp_path, monkeypatch):
    state = SimpleNamespace(mode="success", received=[], failures=0, handler_threads=set())
    receipt = tmp_path / "accepted.jsonl"
    write_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle_request(self):
            self.connection.settimeout(2)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            assert len(raw) == length
            assert self.headers.get("Authorization") == f"Bearer {AGENT_KEY}"
            body = json.loads(raw) if raw else None
            with write_lock:
                state.handler_threads.add(threading.current_thread())
                state.received.append((self.command, self.path, body))
                if self.command == "POST" and state.mode != "reject":
                    # Durable evidence that the server accepted the entire
                    # authenticated operation before losing its reply.
                    with receipt.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"path": self.path, "body": body}) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                lose_reply = state.failures == 0 and state.mode in ("disconnect", "truncate")
                if lose_reply:
                    state.failures += 1
            if lose_reply:
                if state.mode == "truncate":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "128")
                    self.end_headers()
                    self.wfile.write(b'{"status":')
                    self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            payload = ({"error": "operation already busy"} if state.mode == "reject" else
                       {"status": {"state": "running", "installed": True}, "success": True})
            raw_reply = json.dumps(payload).encode()
            self.send_response(409 if state.mode == "reject" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw_reply)))
            self.end_headers()
            self.wfile.write(raw_reply)
            self.wfile.flush()

        do_POST = handle_request
        do_GET = handle_request

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    monkeypatch.setattr(agent, "AGENT_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(agent, "ODS_AGENT_KEY", AGENT_KEY)
    monkeypatch.setattr(agent, "_sync_client", None)
    monkeypatch.setattr(agent, "_async_client", None)
    monkeypatch.setattr(opencode_app, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(opencode_app, "get_cached_services", lambda: None)
    monkeypatch.setattr(resources, "_current_service_configs", lambda: {
        "receipt-fixture": {"name": "Receipt fixture", "container_name": "ods-receipt-fixture"},
    })
    application = FastAPI()
    application.include_router(resources.router)
    application.include_router(opencode_app.router)

    def accepted():
        return [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []

    state.accepted = accepted
    state.app = application
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive(), "fixture listener did not stop"
        for handler in state.handler_threads:
            handler.join(timeout=3)
            assert not handler.is_alive(), "fixture connection did not close"


@asynccontextmanager
async def dashboard(host):
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host.app), base_url="http://dashboard") as client:
            yield client
    finally:
        # Close the real pools on their creating loop before fixture teardown.
        await agent.shutdown_clients()


@pytest.mark.asyncio
@pytest.mark.parametrize("route,upstream,payload", ROUTES)
@pytest.mark.parametrize("failure", ["disconnect", "truncate"])
async def test_accepted_mutation_is_not_replayed_after_receipt_loss(host, route, upstream, payload, failure):
    host.mode = failure
    async with dashboard(host) as client:
        failed = await client.post(route, headers=AUTH)
        assert host.accepted() == [{"path": upstream, "body": payload}], "accepted POST was replayed"
        assert failed.status_code == 503
        assert host.received == [("POST", upstream, payload)]
        # Only a new owner request may issue a second operation. The failed
        # connection must not poison either pool's subsequent healthy request.
        recovered = await client.post(route, headers=AUTH)
        assert recovered.status_code == 200
        assert host.accepted() == [{"path": upstream, "body": payload}] * 2
        assert host.received == [("POST", upstream, payload)] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("route,upstream,payload", ROUTES)
async def test_successful_mutation_has_one_authenticated_receipt(host, route, upstream, payload):
    async with dashboard(host) as client:
        response = await client.post(route, headers=AUTH)
    assert response.status_code == 200
    assert host.accepted() == [{"path": upstream, "body": payload}]
    assert host.received == [("POST", upstream, payload)]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,upstream,payload", ROUTES)
async def test_http_rejection_is_not_retried_or_counted_as_accepted(host, route, upstream, payload):
    host.mode = "reject"
    async with dashboard(host) as client:
        response = await client.post(route, headers=AUTH)
    assert response.status_code == 409
    assert host.accepted() == []
    assert host.received == [("POST", upstream, payload)]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,upstream,payload", ROUTES)
async def test_unauthenticated_route_never_reaches_host(host, route, upstream, payload):
    async with dashboard(host) as client:
        response = await client.post(route)
    assert response.status_code == 401
    assert host.accepted() == []
    assert host.received == []


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sync", "async"])
@pytest.mark.parametrize("failure", ["disconnect", "truncate"])
async def test_read_only_transport_retains_one_safe_receipt_loss_retry(host, transport, failure):
    host.mode = failure
    async with dashboard(host):
        if transport == "sync":
            result = await asyncio.to_thread(agent.request_json, "GET", "/health")
        else:
            result = await agent.async_request_json("GET", "/health")
    assert result["success"] is True
    assert host.received == [("GET", "/health", None)] * 2
    assert host.accepted() == []
