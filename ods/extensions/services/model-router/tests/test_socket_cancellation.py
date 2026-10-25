"""Exercise disconnect propagation through Uvicorn, HTTPX and real TCP sockets.

The ASGI/MockTransport matrix remains responsible for exhaustive phase coverage.
These tests protect the transport boundary behind the fixes in #6425 and #7135;
the upstream is a private HTTP fixture, not an inference runtime.
"""
import asyncio
from contextlib import suppress
import importlib
import json
from pathlib import Path
import socket
import sys

import httpx
import pytest
import uvicorn

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class Upstream:
    def __init__(self, phase):
        self.phase = phase
        self.requests = []
        self.started = asyncio.Queue()
        self.tasks = set()
        self.writers = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            headers = {name.lower(): value for name, value in
                       (line.split(": ", 1) for line in lines[1:] if line)}
            body = json.loads(await reader.readexactly(int(headers["content-length"])))
            self.requests.append((lines[0], headers, body))
            if body["messages"][0]["content"] == "successor":
                payload = json.dumps({"model": "Concrete.gguf", "choices": [
                    {"message": {"role": "assistant", "content": "recovered"},
                     "finish_reason": "stop"}]}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                             + payload)
                await writer.drain()
                return
            closed = asyncio.Event()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Transfer-Encoding: chunked\r\n\r\n")
            if self.phase != "silent":
                delta = ({"tool_calls": [{"index": 0, "id": "call-1", "type": "function",
                                          "function": {"name": "lookup", "arguments": "{"}}]}
                         if self.phase == "tool-decision" else {"content": "first"})
                chunk = ("data: " + json.dumps({"model": "Concrete.gguf", "choices": [
                    {"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n").encode()
                writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            await writer.drain()
            await self.started.put(closed)
            # No completion or periodic writes can accidentally drive cleanup.
            # The router must close this actual socket after its client leaves.
            assert await reader.read() == b""
            closed.set()
        finally:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 1)
            finally:
                self.writers.discard(writer)

    async def stop(self):
        tasks = list(self.tasks)
        for writer in list(self.writers):
            writer.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        # Drain every owned handler, including already-finished handlers whose
        # assertions must propagate. One failing handler must not skip the rest.
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        self.tasks.difference_update(tasks)
        assert not self.tasks and not self.writers
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                raise outcome


@pytest.mark.parametrize("phase", ["silent", "active", "tool-decision"])
def test_disconnect_closes_real_upstream_and_reuses_single_connection(tmp_path, monkeypatch, phase):
    import app.main as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(mod, "ENDPOINTS_PATH", tmp_path / "endpoints.json")
    monkeypatch.setattr(mod, "UPSTREAM_MAX_CONNECTIONS", 1)
    monkeypatch.setattr(mod, "TOKEN_SPY_URL", "")
    monkeypatch.setenv("SOCKET_TEST_UPSTREAM_KEY", "private-upstream-key")

    async def exercise():
        upstream = Upstream(phase)
        listener = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        mod.ENDPOINTS_PATH.write_text(json.dumps({"endpoints": [{
            "id": "owned-upstream", "baseUrl": f"http://127.0.0.1:{port}",
            "apiKeyEnv": "SOCKET_TEST_UPSTREAM_KEY"}]}))
        mod.STATE_PATH.write_text(json.dumps({
            "schema": "ods.model-state.v1", "seq": 7, "routeSeq": 7,
            "operation": None, "desired": {"catalogId": "concrete"},
            "active": {"routeSeq": 7, "catalogId": "concrete",
                       "runtimeModelId": "Concrete.gguf", "publicModel": "ods/current",
                       "backend": {"kind": "llama-server", "endpointId": "owned-upstream",
                                   "nativeRoute": None},
                       "contextLength": 4096, "capabilities": {
                           "chat": True, "tools": True, "vision": False, "agentViable": True},
                       "verifiedAt": "2026-10-25T00:00:00Z",
                       "proof": {"identity": "Concrete.gguf", "completion": True}},
            "history": [], "availability": {"mode": "serve_active", "queueDeadline": None}}))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        router_port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            mod.app, log_level="error", access_log=False, lifespan="on",
            timeout_graceful_shutdown=1))
        # This in-process server must not install handlers for the test runner.
        server.install_signal_handlers = lambda: None
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async def ready():
                while not server.started:
                    if serving.done():
                        await serving
                        raise AssertionError("router stopped before startup")
                    await asyncio.sleep(.01)
            await asyncio.wait_for(ready(), 3)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{router_port}",
                                         trust_env=False, timeout=3) as client:
                # Repeat so an abandoned first generation cannot hide behind
                # spare HTTPX pool capacity on the next request.
                for _ in range(2):
                    payload = {"model": "ods/current", "stream": True,
                               "messages": [{"role": "user", "content": "cancel-me"}]}
                    if phase == "tool-decision":
                        payload["tools"] = [{"type": "function", "function": {"name": "lookup"}}]
                    request = client.build_request("POST", "/v1/chat/completions", json=payload,
                                                   headers={"Authorization": "Bearer client-key"})
                    pending = asyncio.create_task(client.send(request, stream=True))
                    response = None
                    try:
                        closed = await asyncio.wait_for(upstream.started.get(), 3)
                        if phase == "tool-decision":
                            # A partial decision must remain buffered; Stop must
                            # still reach upstream before downstream headers exist.
                            assert not pending.done()
                            pending.cancel()
                            with pytest.raises(asyncio.CancelledError):
                                await pending
                        else:
                            response = await pending
                            assert response.status_code == 200
                            assert response.headers["x-ods-routed-model"] == "Concrete.gguf"
                            if phase == "active":
                                first = await anext(response.aiter_bytes())
                                assert b"first" in first and b"Concrete.gguf" not in first
                            await response.aclose()
                        await asyncio.wait_for(closed.wait(), 3)
                        async def drained():
                            while True:
                                health = (await client.get("/health")).json()
                                if health["activeRequests"] == health["queuedRequests"] == 0:
                                    return
                                await asyncio.sleep(.01)
                        await asyncio.wait_for(drained(), 3)
                    finally:
                        if response is not None:
                            await response.aclose()
                        if not pending.done():
                            pending.cancel()
                            with suppress(asyncio.CancelledError):
                                await pending
                    successor = await client.post("/v1/chat/completions", json={
                        "model": "ods/current", "messages": [{"role": "user", "content": "successor"}]})
                    assert successor.status_code == 200
                    assert successor.json()["model"] == "ods/current"
                    assert successor.json()["choices"][0]["message"]["content"] == "recovered"
                assert len(upstream.requests) == 4
                for line, headers, body in upstream.requests:
                    assert line == "POST /v1/chat/completions HTTP/1.1"
                    assert body["model"] == "Concrete.gguf"
                    assert headers["authorization"] == "Bearer private-upstream-key"
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, 4)
            finally:
                sock.close()
                listener.close()
                await listener.wait_closed()
                await asyncio.wait_for(upstream.stop(), 3)
                assert serving.done()

    asyncio.run(exercise())
