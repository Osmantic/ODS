"""Streaming / binary / websocket proxy behavior tests (#1268).

Regression suite for the privacy-shield response-buffering bug.

Pre-fix proxy.py did ``resp.content.decode('utf-8')`` then returned one
buffered ``Response`` — buffering the whole body (broke SSE streaming),
hard-crashing on gzip/binary, mangling PII tokens split across an SSE chunk
boundary, and offering no websocket upgrade lane.

Post-fix contract asserted here (httpx ``MockTransport`` upstream modelling a
real *chunked* llama-server + Starlette ``TestClient``):

  1. The proxy uses httpx's streaming API and returns a ``StreamingResponse``
     (it does NOT buffer the whole upstream body via ``resp.content``).
  2. A PII token split across SSE chunk boundaries is restored intact.
  3. gzip / non-UTF-8 / binary bodies pass through byte-for-byte, no crash.
  4. A websocket upgrade has a working passthrough lane.

Note: Starlette's TestClient collects a streaming response on the client side
before handing bytes to the test, so wall-clock "first byte" timing is not a
reliable streaming probe here. We assert streaming *structurally* (the proxy
calls ``http_client.stream`` and returns a ``StreamingResponse``) plus the
observable byte-exactness of streamed bodies.
"""

import asyncio
import gzip
import json
import os
import re
import sys
import threading
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_KEY = "test-shield-key-abcdef0123456789"
os.environ["SHIELD_API_KEY"] = TEST_KEY
os.environ.setdefault("PII_CACHE_ENABLED", "false")  # deterministic PII map

from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import proxy  # noqa: E402

AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
EMAIL = "alice.streaming@example.com"


class _AsyncByteStream(httpx.AsyncByteStream):
    """httpx async response stream over a list of byte chunks — models a real
    chunked upstream (llama-server uses chunked transfer for SSE & bodies)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def __aiter__(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass


def _resp(status, headers, chunks):
    return httpx.Response(status, headers=headers, stream=_AsyncByteStream(chunks))


@pytest.fixture
def install_upstream(monkeypatch):
    """Swap in a MockTransport upstream and spy on http_client.stream so we
    can prove the proxy used the streaming API (not buffered .post().content).
    """
    state = {"stream_called": False}

    def _set(handler):
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        real_stream = mock.stream

        def spy_stream(*a, **k):
            state["stream_called"] = True
            return real_stream(*a, **k)

        mock.stream = spy_stream  # type: ignore[assignment]
        monkeypatch.setattr(proxy, "http_client", mock)
        return state

    return _set


@pytest.fixture
def client():
    return TestClient(proxy.app)


# ── 1. Streaming API used; response is a StreamingResponse ──────────────────

class TestStreamingNotBuffered:
    def test_proxy_uses_streaming_api_not_buffered(self, client, install_upstream):
        state = install_upstream(
            lambda r: _resp(
                200, {"content-type": "text/event-stream"},
                [b"data: a\n\n", b"data: b\n\n", b"data: [DONE]\n\n"],
            )
        )
        with client.stream(
            "POST", "/v1/chat/completions",
            headers=AUTH, json={"stream": True, "messages": []},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = b"".join(resp.iter_bytes())
        assert state["stream_called"], (
            "proxy did NOT use http_client.stream() — still buffering"
        )
        assert body == b"data: a\n\ndata: b\n\ndata: [DONE]\n\n"

    def test_proxy_handler_returns_streamingresponse(self):
        """The catch-all proxy route must hand back a StreamingResponse so the
        body is yielded incrementally rather than buffered into one Response.
        """
        import inspect
        src = inspect.getsource(proxy.proxy)
        assert "StreamingResponse" in src, (
            "proxy() no longer returns a StreamingResponse — buffering risk"
        )
        assert "resp.content" not in src and ".content.decode(" not in src, (
            "proxy() still reads the whole upstream body via resp.content"
        )


# ── 2. PII token split across SSE chunk boundary round-trips ───────────────

class TestSSEBoundaryScrub:
    def _email_token(self, request):
        sent = request.content.decode("utf-8", "replace")
        m = re.search(r"<PII_email_[0-9a-f]{12}>", sent)
        assert m, f"email not scrubbed before forward: {sent!r}"
        return m.group(0)

    def test_pii_split_across_two_chunks(self, client, install_upstream):
        def handler(request):
            t = self._email_token(request)
            mid = len(t) // 2
            return _resp(
                200, {"content-type": "text/event-stream"},
                [f"data: see {t[:mid]}".encode(),
                 f"{t[mid:]} end\n\n".encode(), b"data: [DONE]\n\n"],
            )

        install_upstream(handler)
        with client.stream(
            "POST", "/v1/chat/completions", headers=AUTH,
            json={"messages": [{"role": "user", "content": f"mail {EMAIL}"}]},
        ) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8", "replace")
        assert EMAIL in body, f"PII not restored across boundary: {body!r}"
        assert "<PII_email_" not in body, f"raw token leaked: {body!r}"

    def test_pii_split_across_three_chunks(self, client, install_upstream):
        def handler(request):
            t = self._email_token(request)
            return _resp(
                200, {"content-type": "text/event-stream"},
                [f"d {t[:4]}".encode(), t[4:8].encode(),
                 f"{t[8:]} z\n\n".encode(), b"data: [DONE]\n\n"],
            )

        install_upstream(handler)
        with client.stream(
            "POST", "/v1/chat/completions", headers=AUTH,
            json={"messages": [{"content": f"e {EMAIL}"}]},
        ) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8", "replace")
        assert EMAIL in body
        assert "<PII_email_" not in body


# ── 3. gzip / binary passthrough, no utf-8 crash ───────────────────────────

class TestBinaryPassthrough:
    def test_gzip_response_passthrough(self, client, install_upstream):
        payload = json.dumps({"r": "ok", "d": "z" * 64}).encode()
        gz = gzip.compress(payload)
        install_upstream(
            lambda r: _resp(
                200,
                {"content-type": "application/json", "content-encoding": "gzip"},
                [gz[: len(gz) // 2], gz[len(gz) // 2 :]],
            )
        )
        with client.stream(
            "POST", "/v1/embeddings", headers=AUTH, json={"input": "hi"}
        ) as resp:
            assert resp.status_code == 200, "gzip body crashed the proxy"
            raw = b"".join(resp.iter_raw())
        # The proxy must forward the compressed bytes byte-for-byte (it must
        # not utf-8-decode or PII-restore a gzip body). iter_raw() bypasses
        # httpx client-side auto-decompression so we see exactly what the
        # proxy emitted.
        assert raw == gz, "gzip body not passed through byte-for-byte"
        assert gzip.decompress(raw) == payload

    def test_non_utf8_binary_passthrough(self, client, install_upstream):
        blob = bytes(range(256)) * 8  # invalid utf-8
        install_upstream(
            lambda r: _resp(
                200, {"content-type": "application/octet-stream"},
                [blob[:300], blob[300:]],
            )
        )
        resp = client.post("/v1/audio/speech", headers=AUTH, json={"t": "x"})
        assert resp.status_code == 200, "non-utf8 body raised instead of passthrough"
        assert resp.content == blob

    def test_non_utf8_request_body_forwarded(self, client, install_upstream):
        seen = {}

        def handler(request):
            seen["body"] = request.content
            return _resp(200, {"content-type": "application/json"},
                         [b'{"ok":true}'])

        install_upstream(handler)
        bad = bytes([0xFF, 0xFE, 0x00, 0x80]) * 4
        resp = client.post("/v1/x", headers=AUTH, content=bad)
        assert resp.status_code == 200, "non-utf8 request body 500'd"
        assert seen["body"] == bad, "non-utf8 request body not forwarded verbatim"


# ── 4. WebSocket upgrade lane ───────────────────────────────────────────────

class TestWebSocketLane:
    def test_websocket_route_registered(self):
        ws_routes = [
            r for r in proxy.app.router.routes
            if r.__class__.__name__ in ("WebSocketRoute", "APIWebSocketRoute")
        ]
        assert ws_routes, "no websocket route — Upgrade: websocket has no lane"

    def test_websocket_passthrough_echo(self, client, monkeypatch):
        try:
            import websockets
        except ModuleNotFoundError:
            pytest.skip("websockets lib unavailable; route-registered test covers lane")

        ready = threading.Event()
        box = {}

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stop = asyncio.Event()
            box["loop"] = loop
            box["stop"] = stop

            async def echo(ws):
                async for msg in ws:
                    await ws.send(f"echo:{msg}")

            async def main():
                srv = await websockets.serve(echo, "127.0.0.1", 0)
                box["port"] = srv.sockets[0].getsockname()[1]
                ready.set()
                await stop.wait()
                srv.close()
                await srv.wait_closed()

            loop.run_until_complete(main())
            loop.close()

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        assert ready.wait(timeout=5), "ws echo server didn't start"

        monkeypatch.setattr(proxy, "TARGET_API_BASE",
                            f"http://127.0.0.1:{box['port']}")
        try:
            with client.websocket_connect("/v1/realtime", headers=AUTH) as ws:
                ws.send_text("hello")
                assert ws.receive_text() == "echo:hello"
        finally:
            loop = box.get("loop")
            stop = box.get("stop")
            if loop and stop:
                loop.call_soon_threadsafe(stop.set)
            t.join(timeout=5)


# ── 5. WebSocket auth gate (#1268 critic blocker) ──────────────────────────
#
# The ws passthrough lane attaches TARGET_API_KEY to the upstream model. It
# MUST authenticate the handshake the same way the HTTP lane does
# (Depends(verify_api_key) -> SHIELD_API_KEY) before accept() and before any
# upstream socket is opened. Pre-fix the handler called client_ws.accept()
# unconditionally — an unauthenticated proxy straight to the backend model.

class TestWebSocketAuth:
    def _block_upstream(self, monkeypatch):
        """Fail loudly if the handler tries to open an upstream WS while
        unauthenticated — proves auth runs *before* the upstream connection."""
        import websockets

        async def _boom(*a, **k):  # pragma: no cover - must never be reached
            raise AssertionError(
                "upstream websockets.connect() called for an unauthenticated "
                "client — auth gate ran too late or not at all"
            )

        monkeypatch.setattr(websockets, "connect", _boom)

    def test_websocket_no_token_rejected_no_upstream(self, client, monkeypatch):
        try:
            import websockets  # noqa: F401
        except ModuleNotFoundError:
            pytest.skip("websockets lib unavailable")
        self._block_upstream(monkeypatch)

        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/v1/realtime"):
                pass  # pragma: no cover - connect must be rejected
        # 1008 = policy violation (unauthenticated handshake).
        assert exc.value.code == 1008

    def test_websocket_invalid_token_rejected_no_upstream(self, client, monkeypatch):
        try:
            import websockets  # noqa: F401
        except ModuleNotFoundError:
            pytest.skip("websockets lib unavailable")
        self._block_upstream(monkeypatch)

        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/v1/realtime", headers={"Authorization": "Bearer wrong-key"}
            ):
                pass  # pragma: no cover - connect must be rejected
        assert exc.value.code == 1008

    def test_websocket_non_ascii_token_rejected_no_upstream(self, client, monkeypatch):
        """A non-ASCII ``?token=`` (here ``café``) must be a clean 1008
        policy-violation reject — never a TypeError out of
        secrets.compare_digest on the pre-auth path — and must not reach
        upstream."""
        try:
            import websockets  # noqa: F401
        except ModuleNotFoundError:
            pytest.skip("websockets lib unavailable")
        self._block_upstream(monkeypatch)

        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc:
            # caf%C3%A9 == "café" URL-encoded.
            with client.websocket_connect("/v1/realtime?token=caf%C3%A9"):
                pass  # pragma: no cover - connect must be rejected
        assert exc.value.code == 1008

    def test_websocket_valid_token_query_param_round_trips(self, client, monkeypatch):
        """A valid ``?token=`` connects and a frame round-trips end-to-end
        (browser WS clients can't set an Authorization header)."""
        try:
            import websockets
        except ModuleNotFoundError:
            pytest.skip("websockets lib unavailable")

        ready = threading.Event()
        box = {}

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stop = asyncio.Event()
            box["loop"] = loop
            box["stop"] = stop

            async def echo(ws):
                async for msg in ws:
                    await ws.send(f"echo:{msg}")

            async def main():
                srv = await websockets.serve(echo, "127.0.0.1", 0)
                box["port"] = srv.sockets[0].getsockname()[1]
                ready.set()
                await stop.wait()
                srv.close()
                await srv.wait_closed()

            loop.run_until_complete(main())
            loop.close()

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        assert ready.wait(timeout=5), "ws echo server didn't start"

        monkeypatch.setattr(proxy, "TARGET_API_BASE",
                            f"http://127.0.0.1:{box['port']}")
        try:
            with client.websocket_connect(
                f"/v1/realtime?token={TEST_KEY}"
            ) as ws:
                ws.send_text("hi")
                assert ws.receive_text() == "echo:hi"
        finally:
            loop = box.get("loop")
            stop = box.get("stop")
            if loop and stop:
                loop.call_soon_threadsafe(stop.set)
            t.join(timeout=5)
