"""ODS model-router contract tests (Switchboard PR 3). No sockets: the
upstream is an httpx.MockTransport and state/endpoints are temp files."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


@pytest.fixture()
def router(tmp_path, monkeypatch):
    """Fresh app instance wired to temp state/endpoints and a mock upstream."""
    state_path = tmp_path / "model-state.json"
    endpoints_path = tmp_path / "endpoints.json"
    endpoints_path.write_text(json.dumps({
        "endpoints": [
            {"id": "llama-server-default", "baseUrl": "http://upstream:8080"},
            {"id": "keyed", "baseUrl": "http://keyed:9000", "apiKeyEnv": "KEYED_API_KEY"},
        ]
    }), encoding="utf-8")

    import app.main as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "STATE_PATH", state_path)
    monkeypatch.setattr(mod, "ENDPOINTS_PATH", endpoints_path)
    monkeypatch.setattr(mod, "INTERNAL_KEY", "internal-secret")
    monkeypatch.setattr(mod, "PROBE_KEY", "probe-secret")
    mod._endpoints_cache.update({"loaded": False, "endpoints": {}})
    mod._state_cache.update({"mtime": None, "doc": None})
    mod._evidence.clear()

    calls: list[dict] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append({
            "url": str(request.url),
            "model": body.get("model"),
            "auth": request.headers.get("authorization"),
            "stream": bool(body.get("stream")),
        })
        if body.get("stream"):
            sse = (
                b'data: {"id":"c1","model":"Concrete.gguf","choices":[{"delta":{"content":"hi"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
            return httpx.Response(200, content=sse,
                                  headers={"content-type": "text/event-stream",
                                           "x-lemonade-route": "route-a"})
        return httpx.Response(200, json={
            "id": "c1", "model": "Concrete.gguf",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        }, headers={"x-lemonade-route": "route-a"})

    def write_state(runtime="Concrete.gguf", endpoint="llama-server-default",
                    queue=False, route_seq=7):
        state_path.write_text(json.dumps({
            "schema": "ods.model-state.v1", "seq": route_seq, "routeSeq": route_seq,
            "operation": None, "desired": {"catalogId": "concrete"},
            "active": {
                "routeSeq": route_seq, "catalogId": "concrete",
                "runtimeModelId": runtime, "publicModel": "ods/current",
                "backend": {"kind": "llama-server", "endpointId": endpoint,
                            "nativeRoute": None},
                "contextLength": 4096,
                "capabilities": {"chat": True, "tools": False, "vision": False,
                                 "agentViable": False},
                "verifiedAt": "2026-07-20T00:00:00Z",
                "proof": {"identity": runtime, "completion": True},
            },
            "history": [],
            "availability": {"mode": "queue" if queue else "serve_active",
                             "queueDeadline": None},
        }), encoding="utf-8")
        mod._state_cache.update({"mtime": None, "doc": None})

    client = TestClient(mod.app)
    client.__enter__()
    mod.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler)
    )
    yield mod, client, write_state, calls
    client.__exit__(None, None, None)


def _signed_marker(probe_id: str, key: str = "probe-secret") -> str:
    sig = base64.urlsafe_b64encode(
        hmac.new(key.encode(), probe_id.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"[ODS_PROBE id={probe_id} sig={sig}]"


class TestForwarding:
    def test_alias_rewritten_in_and_out(self, router):
        mod, client, write_state, calls = router
        write_state()
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        assert calls[-1]["model"] == "Concrete.gguf"
        body = resp.json()
        assert body["model"] == "ods/current"
        assert resp.headers["X-ODS-Requested-Model"] == "ods/current"
        assert resp.headers["X-ODS-Routed-Model"] == "Concrete.gguf"
        assert resp.headers["X-ODS-Route-Seq"] == "7"
        assert resp.headers["X-Lemonade-Route"] == "route-a"

    def test_sse_chunks_restore_alias(self, router):
        mod, client, write_state, calls = router
        write_state()
        with client.stream("POST", "/v1/chat/completions", json={
            "model": "default", "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            assert resp.status_code == 200
            raw = b"".join(resp.iter_bytes())
        assert b'"model": "default"' in raw or b'"model":"default"' in raw
        assert b"Concrete.gguf" not in raw
        assert b"[DONE]" in raw

    def test_client_authorization_stripped_and_backend_key_injected(self, router):
        mod, client, write_state, calls = router
        import os
        os.environ["KEYED_API_KEY"] = "backend-secret"
        write_state(endpoint="keyed")
        resp = client.post("/v1/chat/completions",
                           headers={"Authorization": "Bearer client-secret"},
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 200
        assert calls[-1]["auth"] == "Bearer backend-secret"

    def test_unknown_path_rejected(self, router):
        mod, client, write_state, calls = router
        write_state()
        assert client.post("/v1/embeddings", json={}).status_code == 404
        assert client.get("/v1/chat/completions").status_code == 404
        assert calls == []

    def test_oversized_body_rejected(self, router):
        mod, client, write_state, calls = router
        write_state()
        monkey_big = "x" * (mod.MAX_BODY_BYTES + 10)
        resp = client.post("/v1/chat/completions",
                           content=monkey_big.encode(),
                           headers={"content-type": "application/json"})
        assert resp.status_code == 413

    def test_malformed_json_rejected(self, router):
        mod, client, write_state, calls = router
        write_state()
        resp = client.post("/v1/chat/completions", content=b"{nope",
                           headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_no_route_yields_503(self, router):
        mod, client, write_state, calls = router
        resp = client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "no_active_route"

    def test_unlisted_endpoint_yields_503(self, router):
        mod, client, write_state, calls = router
        write_state(endpoint="not-in-allowlist")
        resp = client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "endpoint_not_allowlisted"

    def test_queue_mode_times_out_with_swap_code(self, router, monkeypatch):
        mod, client, write_state, calls = router
        write_state(queue=True)
        monkeypatch.setattr(mod, "QUEUE_WAIT_SECONDS", 0)
        resp = client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "model_swap_in_progress"
        assert "Retry-After" in resp.headers


class TestModelsAndEvidence:
    def test_models_lists_aliases_with_ods_metadata(self, router):
        mod, client, write_state, calls = router
        write_state()
        body = client.get("/v1/models").json()
        ids = [m["id"] for m in body["data"]]
        assert ids == ["ods/current", "default"]
        assert body["ods"]["routedModel"] == "Concrete.gguf"

    def test_probe_marker_records_evidence(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        marker = _signed_marker(probe_id)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": f"hello {marker}"}],
        })
        assert resp.status_code == 200
        ev = client.get(f"/internal/route-evidence/{probe_id}",
                        headers={"Authorization": "Bearer internal-secret"})
        assert ev.status_code == 200
        record = ev.json()
        assert record["requestedModel"] == "ods/current"
        assert record["routedModel"] == "Concrete.gguf"
        assert record["routeSeq"] == 7
        assert record["responseModel"] == "Concrete.gguf"
        assert "messages" not in record and "content" not in record

    def test_forged_marker_records_nothing(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        marker = f"[ODS_PROBE id={probe_id} sig=Zm9yZ2Vk]"
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": marker}],
        })
        ev = client.get(f"/internal/route-evidence/{probe_id}",
                        headers={"Authorization": "Bearer internal-secret"})
        assert ev.status_code == 404

    def test_evidence_requires_bearer(self, router):
        mod, client, write_state, calls = router
        assert client.get("/internal/route-evidence/x").status_code == 401
        wrong = client.get("/internal/route-evidence/x",
                           headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401

    def test_health_reports_route_presence(self, router):
        mod, client, write_state, calls = router
        assert client.get("/health").json()["hasRoute"] is False
        write_state()
        assert client.get("/health").json()["hasRoute"] is True


class TestStreamingConcurrencyBound:
    """MAX_QUEUE_DEPTH must bound streaming traffic too.

    A StreamingResponse is still open when its handler returns — the ASGI
    server pulls its body afterwards. Releasing the slot on handler return
    frees it while the upstream request is live, so the bound only ever
    applied to non-streaming requests, the short ones.

    These call ``forward`` directly: TestClient consumes a mocked stream
    eagerly, which closes the window before it can be observed.
    """

    @staticmethod
    def _setup(tmp_path, upstream_body=None):
        """A module wired to temp state/endpoints and an in-memory upstream."""
        import app.main as mod
        mod = importlib.reload(mod)

        state_path = tmp_path / "model-state.json"
        endpoints_path = tmp_path / "endpoints.json"
        endpoints_path.write_text(json.dumps({"endpoints": [
            {"id": "llama-server-default", "baseUrl": "http://upstream:8080"},
        ]}), encoding="utf-8")
        state_path.write_text(json.dumps({
            "schema": "ods.model-state.v1", "seq": 1, "routeSeq": 1,
            "operation": None, "desired": {"catalogId": "concrete"},
            "active": {
                "routeSeq": 1, "catalogId": "concrete",
                "runtimeModelId": "Concrete.gguf", "publicModel": "ods/current",
                "backend": {"kind": "llama-server",
                            "endpointId": "llama-server-default",
                            "nativeRoute": None},
                "contextLength": 4096,
                "capabilities": {"chat": True, "tools": False,
                                 "vision": False, "agentViable": False},
                "verifiedAt": "2026-01-01T00:00:00Z", "reconstructed": False,
                "proof": {"identity": "Concrete.gguf", "completion": True},
            },
            "history": [],
            "availability": {"mode": "serve_active", "queueDeadline": None},
        }), encoding="utf-8")

        mod.STATE_PATH = state_path
        mod.ENDPOINTS_PATH = endpoints_path
        mod._endpoints_cache.update({"loaded": False, "endpoints": {}})
        mod._state_cache.update({"mtime": None, "doc": None})

        sse = upstream_body if upstream_body is not None else (
            b'data: {"id":"c1","model":"Concrete.gguf","choices":[]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            if body.get("stream"):
                return httpx.Response(
                    200, content=sse,
                    headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"id": "c1", "model": "Concrete.gguf",
                                             "choices": []})

        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        return mod

    @staticmethod
    def _request(mod, payload: dict):
        """A minimal Starlette Request carrying ``payload`` as its body."""
        from starlette.requests import Request

        raw = json.dumps(payload).encode("utf-8")
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "path": "/v1/chat/completions",
            "root_path": "", "scheme": "http", "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234), "server": ("router", 80),
            "app": mod.app,
        }

        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}

        return Request(scope, receive)

    def test_slot_is_held_until_the_stream_body_completes(self, tmp_path):
        mod = self._setup(tmp_path)

        async def scenario():
            payload = {"model": "default", "stream": True, "messages": []}
            response = await mod.forward(
                "v1/chat/completions", self._request(mod, payload))
            assert isinstance(response, StreamingResponse)
            # Handler has returned; the ASGI server has not pulled the body
            # yet and the upstream request is still open.
            held = mod._inflight
            async for _ in response.body_iterator:
                pass
            return held, mod._inflight

        held, after = asyncio.run(scenario())
        assert held == 1, "slot freed before the stream body was consumed"
        assert after == 0, "slot leaked after the stream completed"

    def test_non_streaming_request_releases_its_slot(self, tmp_path):
        mod = self._setup(tmp_path)

        async def scenario():
            response = await mod.forward(
                "v1/chat/completions",
                self._request(mod, {"model": "ods/current", "messages": []}))
            return response.status_code, mod._inflight

        status, inflight = asyncio.run(scenario())
        assert status == 200
        assert inflight == 0

    def test_release_is_idempotent(self, tmp_path):
        """Both owners may call it; the count must move exactly once."""
        mod = self._setup(tmp_path)

        async def scenario():
            mod._inflight = 1
            slot = mod._InflightSlot()
            await slot.release()
            await slot.release()
            return mod._inflight

        assert asyncio.run(scenario()) == 0
