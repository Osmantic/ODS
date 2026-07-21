"""ODS model-router contract tests (Switchboard PR 3). No sockets: the
upstream is an httpx.MockTransport and state/endpoints are temp files."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import importlib
import json
import sys
import threading
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

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
    mod._endpoints_cache.update({"mtime": None, "endpoints": {}})
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
                    queue=False, route_seq=7, seq=None, mutate=None):
        doc = {
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
        }
        doc["seq"] = route_seq if seq is None else seq
        if mutate:
            mutate(doc)
        state_path.write_text(json.dumps(doc), encoding="utf-8")
        mod._state_cache["mtime"] = None

    client = TestClient(mod.app)
    client.__enter__()
    mod.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler)
    )
    mod._inflight = 0
    yield mod, client, write_state, calls
    client.__exit__(None, None, None)


def _signed_marker(probe_id: str, key: str = "probe-secret") -> str:
    sig = base64.urlsafe_b64encode(
        hmac.new(key.encode(), probe_id.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"[ODS_PROBE id={probe_id} sig={sig}]"


def test_internal_key_falls_back_to_dashboard_api_key(monkeypatch):
    monkeypatch.delenv("ODS_ROUTER_INTERNAL_KEY", raising=False)
    monkeypatch.setenv("DASHBOARD_API_KEY", "dashboard-secret")
    import app.main as mod

    mod = importlib.reload(mod)

    assert mod.INTERNAL_KEY == "dashboard-secret"


class _ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None, started=None, release=None):
        self.chunks = chunks
        self.error = error
        self.started = started
        self.release = release

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            yield chunk
            if index == 0 and self.started is not None:
                self.started.set()
                await asyncio.to_thread(self.release.wait, 5)
        if self.error is not None:
            raise self.error

    async def aclose(self):
        return None


def _set_stream_upstream(mod, chunks, *, model_error=None, started=None,
                         release=None):
    def handler(_request):
        return httpx.Response(
            200,
            stream=_ChunkedStream(chunks, model_error, started, release),
            headers={"content-type": "text/event-stream"},
        )

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))


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

    def test_chat_template_artifacts_stripped_from_json_content(self, router):
        mod, client, write_state, calls = router
        write_state()

        def handler(_request):
            return httpx.Response(200, json={
                "id": "c1",
                "model": "Concrete.gguf",
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "O<|im_start|>assistant<|im_end|>DSVAL",
                    },
                }],
            })

        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )

        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "ODSVAL"
        assert "<|im_start|>" not in resp.text
        assert body["model"] == "ods/current"

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

    def test_chat_template_artifacts_stripped_from_sse_delta(self, router):
        mod, client, write_state, calls = router
        write_state()
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","model":"Concrete.gguf",'
            b'"choices":[{"delta":{"content":"O<|im_start|>assistant<|im_end|>DSVAL"}}]}\n\n',
            b"data: [DONE]\n\n",
        ])

        with client.stream("POST", "/v1/chat/completions", json={
            "model": "ods/current",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            raw = b"".join(resp.iter_bytes())

        assert resp.status_code == 200
        assert b'"content":"ODSVAL"' in raw
        assert b"<|im_start|>" not in raw
        assert b"Concrete.gguf" not in raw

    def test_chat_template_artifacts_stripped_from_model_less_sse_delta(self, router):
        mod, client, write_state, calls = router
        write_state()
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","model":"Concrete.gguf","choices":[]}\n\n',
            b'data: {"id":"c1","choices":[{"delta":{"content":"A<|start_header_id|>assistant<|end_header_id|>B"}}]}\n\n',
            b"data: [DONE]\n\n",
        ])

        with client.stream("POST", "/v1/chat/completions", json={
            "model": "ods/current",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            raw = b"".join(resp.iter_bytes())

        assert resp.status_code == 200
        assert b'"content":"AB"' in raw
        assert b"<|start_header_id|>" not in raw
        assert b"Concrete.gguf" not in raw

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

    def test_fragmented_sse_is_framed_before_rewrite(self, router):
        mod, client, write_state, calls = router
        write_state()
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","mod',
            b'el":"Concrete.gguf","choices":[{"delta":{"content":"hi"}}]}\r',
            b'\n\r\ndata: [DONE]\r\n\r\n',
        ])
        with client.stream("POST", "/v1/chat/completions", json={
            "model": "default", "stream": True, "messages": [],
        }) as resp:
            raw = b"".join(resp.iter_bytes())
        assert resp.status_code == 200
        assert b'"model":"default"' in raw
        assert b"Concrete.gguf" not in raw
        assert raw.endswith(b"data: [DONE]\r\n\r\n")

    def test_stream_holds_admission_until_teardown(self, router, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        started = threading.Event()
        release = threading.Event()
        monkeypatch.setattr(mod, "MAX_QUEUE_DEPTH", 1)
        _set_stream_upstream(
            mod,
            [b'data: {"model":"Concrete.gguf"}\n\n', b"data: [DONE]\n\n"],
            started=started,
            release=release,
        )
        result = {}

        def consume_stream():
            result["response"] = client.post("/v1/chat/completions", json={
                "model": "ods/current", "stream": True, "messages": [],
            })

        worker = threading.Thread(target=consume_stream)
        worker.start()
        assert started.wait(5)
        assert mod._inflight == 1
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert result["response"].status_code == 200
        assert mod._inflight == 0


class TestStateTrust:
    def test_schema_invalid_state_is_not_routable(self, router):
        mod, client, write_state, calls = router
        write_state(mutate=lambda doc: doc.update({"unexpected": True}))
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 503
        assert calls == []

    @pytest.mark.parametrize("mutation", [
        lambda doc: doc["active"].update({"reconstructed": True}),
        lambda doc: doc["active"]["proof"].update({"completion": False}),
        lambda doc: doc["active"]["proof"].update({"identity": "Other.gguf"}),
        lambda doc: doc["active"].update({"verifiedAt": None}),
        lambda doc: doc.update({"routeSeq": doc["routeSeq"] + 1}),
    ])
    def test_unverified_state_is_not_routable(self, router, mutation):
        mod, client, write_state, calls = router
        write_state(mutate=mutation)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 503
        assert calls == []

    def test_regressed_state_retains_verified_last_known_good(self, router):
        mod, client, write_state, calls = router
        write_state(runtime="New.gguf", route_seq=9)
        assert client.get("/v1/models").json()["ods"]["routedModel"] == "New.gguf"
        write_state(runtime="Old.gguf", route_seq=8)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 200
        assert calls[-1]["model"] == "New.gguf"

    def test_invalid_update_retains_verified_last_known_good(self, router):
        mod, client, write_state, calls = router
        write_state(runtime="Good.gguf", route_seq=9)
        assert client.get("/v1/models").status_code == 200
        write_state(
            runtime="Unproved.gguf", route_seq=10,
            mutate=lambda doc: doc["active"]["proof"].update({"completion": False}),
        )
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 200
        assert calls[-1]["model"] == "Good.gguf"

    def test_same_sequence_cannot_mutate_route(self, router):
        mod, client, write_state, calls = router
        write_state(runtime="Original.gguf", route_seq=9)
        assert client.get("/v1/models").status_code == 200
        write_state(runtime="Replacement.gguf", route_seq=9)
        client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert calls[-1]["model"] == "Original.gguf"


class TestIngressLimits:
    def test_chunked_body_stops_at_limit(self, router, monkeypatch):
        mod, client, write_state, calls = router
        monkeypatch.setattr(mod, "MAX_BODY_BYTES", 5)
        messages = iter([
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ])

        async def receive():
            return next(messages)

        request = Request({"type": "http", "method": "POST", "path": "/",
                           "headers": []}, receive)
        with pytest.raises(mod.RouterError) as exc:
            asyncio.run(mod._read_bounded_body(request))
        assert exc.value.status == 413


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

    def test_completed_verified_stream_records_evidence(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        marker = _signed_marker(probe_id)
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","model":"Con',
            b'crete.gguf","choices":[{"delta":{"content":"ok"}}]}\n\n',
            b"data: [DONE]\n\n",
        ])
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": marker}],
        })
        assert resp.status_code == 200
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 200
        assert ev.json()["responseModel"] == "Concrete.gguf"

    @pytest.mark.parametrize("sse", [
        b'data: {"model":"Wrong.gguf","choices":[]}\n\ndata: [DONE]\n\n',
        b'data: {"choices":[{"delta":{"content":"no identity"}}]}\n\n'
        b'data: [DONE]\n\n',
    ])
    def test_stream_without_matching_concrete_identity_records_no_evidence(
            self, router, sse):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        _set_stream_upstream(mod, [sse])
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": _signed_marker(probe_id)}],
        })
        assert resp.status_code == 200
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 404

    def test_failed_stream_records_no_evidence_and_releases_admission(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        _set_stream_upstream(
            mod,
            [b'data: {"model":"Concrete.gguf"}\n\n'],
            model_error=httpx.ReadError("truncated stream"),
        )
        with pytest.raises(httpx.ReadError, match="truncated stream"):
            client.post("/v1/chat/completions", json={
                "model": "ods/current", "stream": True,
                "messages": [
                    {"role": "user", "content": _signed_marker(probe_id)}
                ],
            })
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 404
        assert mod._inflight == 0

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


class TestProbeKeyLifecycleAndInstance:
    """File-based probe key: rotate/remove without recreating the router,
    with the env value as fallback; instance identity is stable and stamped
    into evidence so a mid-cycle replacement is detectable."""

    def _use_key_file(self, mod, tmp_path, monkeypatch, env_fallback=""):
        key_path = tmp_path / "fleet-probe.key"
        monkeypatch.setattr(mod, "PROBE_KEY_PATH", key_path)
        monkeypatch.setattr(mod, "PROBE_KEY", env_fallback)
        mod._probe_key_cache.update({"mtime": None, "key": ""})
        return key_path

    def test_file_key_rotates_without_restart(self, router, tmp_path, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        key_path = self._use_key_file(mod, tmp_path, monkeypatch)
        key_path.write_text("key-a\n", encoding="utf-8")

        instance_before = client.get("/health").json()["instanceId"]
        probe_a = str(uuid.uuid4())
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(probe_a, key="key-a")}],
        })
        assert resp.status_code == 200
        ev = client.get(f"/internal/route-evidence/{probe_a}",
                        headers={"Authorization": "Bearer internal-secret"})
        assert ev.status_code == 200
        assert ev.json()["instanceId"] == instance_before

        key_path.write_text("key-b-rotated\n", encoding="utf-8")
        stale = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(stale, key="key-a")}],
        })
        assert client.get(
            f"/internal/route-evidence/{stale}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 404

        fresh = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(fresh, key="key-b-rotated")}],
        })
        assert client.get(
            f"/internal/route-evidence/{fresh}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 200

        health = client.get("/health").json()
        assert health["instanceId"] == instance_before
        assert health["probeKeyConfigured"] is True

    def test_deleted_key_file_stops_collection_then_env_fallback(
            self, router, tmp_path, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        key_path = self._use_key_file(mod, tmp_path, monkeypatch)
        key_path.write_text("key-a", encoding="utf-8")
        assert client.get("/health").json()["probeKeyConfigured"] is True

        key_path.unlink()
        assert client.get("/health").json()["probeKeyConfigured"] is False
        orphan = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(orphan, key="key-a")}],
        })
        assert client.get(
            f"/internal/route-evidence/{orphan}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 404

        monkeypatch.setattr(mod, "PROBE_KEY", "env-fallback-key")
        assert client.get("/health").json()["probeKeyConfigured"] is True
        fallback = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(fallback,
                                                    key="env-fallback-key")}],
        })
        assert client.get(
            f"/internal/route-evidence/{fallback}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 200

    def test_empty_key_file_falls_back_to_env(self, router, tmp_path, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        key_path = self._use_key_file(mod, tmp_path, monkeypatch,
                                      env_fallback="env-key")
        key_path.write_text("   \n", encoding="utf-8")
        assert client.get("/health").json()["probeKeyConfigured"] is True
        probe = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(probe, key="env-key")}],
        })
        assert client.get(
            f"/internal/route-evidence/{probe}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 200

    def test_health_exposes_stable_instance_and_key_state(self, router):
        mod, client, write_state, calls = router
        first = client.get("/health").json()
        second = client.get("/health").json()
        assert first["instanceId"] == second["instanceId"] == mod.INSTANCE_ID
        assert uuid.UUID(first["instanceId"])
        assert first["probeKeyConfigured"] is True  # fixture env key


class TestEndpointsReload:
    """endpoints.json is re-read on mtime/size change (activation re-renders
    it); missing or invalid content retains the last good allowlist."""

    def _endpoints_path(self, tmp_path):
        return tmp_path / "endpoints.json"

    def test_new_endpoint_visible_without_restart(self, router, tmp_path):
        mod, client, write_state, calls = router
        write_state(endpoint="added-later")
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "endpoint_not_allowlisted"

        self._endpoints_path(tmp_path).write_text(json.dumps({
            "endpoints": [
                {"id": "llama-server-default", "baseUrl": "http://upstream:8080"},
                {"id": "added-later", "baseUrl": "http://late:7000"},
            ]
        }), encoding="utf-8")
        ok = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert ok.status_code == 200
        assert calls[-1]["url"].startswith("http://late:7000")

    def test_invalid_rewrite_retains_last_good_allowlist(self, router, tmp_path):
        mod, client, write_state, calls = router
        write_state()
        assert client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        }).status_code == 200

        self._endpoints_path(tmp_path).write_text("{not-json", encoding="utf-8")
        still_ok = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert still_ok.status_code == 200
        assert calls[-1]["url"].startswith("http://upstream:8080")

    def test_health_reports_allowlist_health(self, router, tmp_path):
        mod, client, write_state, calls = router
        healthy = client.get("/health")
        assert healthy.status_code == 200
        assert healthy.json()["status"] == "ok"
        assert healthy.json()["endpointCount"] == 2

        self._endpoints_path(tmp_path).write_text(
            json.dumps({"endpoints": []}), encoding="utf-8")
        degraded = client.get("/health")
        # HTTP 200 on purpose: the compose healthcheck must not turn a
        # config gap into a restart cascade; the body carries the signal.
        assert degraded.status_code == 200
        assert degraded.json()["status"] == "degraded"
        assert degraded.json()["endpointCount"] == 0
