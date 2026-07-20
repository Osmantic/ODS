"""ODS model-router contract tests (Switchboard PR 3). No sockets: the
upstream is an httpx.MockTransport and state/endpoints are temp files."""

from __future__ import annotations

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


class TestEndpointAllowlistRecovery:
    """A failed allowlist read must not be cached.

    endpoints.json is a read-only bind mount that
    scripts/render-runtime-configs.py rewrites in place with a non-atomic
    write_text (:315). A read landing in that window, or before the mount is
    ready, used to cache an empty allowlist for the life of the process:
    every request answered endpoint_not_allowlisted until the container was
    restarted, while /health kept reporting 200.
    """

    def test_transient_read_failure_recovers_on_the_next_request(self, router):
        mod, client, write_state, calls = router
        write_state()
        good = mod.ENDPOINTS_PATH.read_text(encoding="utf-8")

        # Mid-rewrite: the file is truncated, so json.loads raises.
        mod.ENDPOINTS_PATH.write_text("", encoding="utf-8")
        mod._endpoints_cache.update({"loaded": False, "endpoints": {}})
        blocked = client.post("/v1/chat/completions",
                              json={"model": "ods/current", "messages": []})
        assert blocked.status_code == 503
        assert blocked.json()["error"]["type"] == "endpoint_not_allowlisted"

        # The rewrite lands; the very next request must route.
        mod.ENDPOINTS_PATH.write_text(good, encoding="utf-8")
        recovered = client.post("/v1/chat/completions",
                                json={"model": "ods/current", "messages": []})
        assert recovered.status_code == 200

    def test_missing_file_recovers_once_it_appears(self, router):
        mod, client, write_state, calls = router
        write_state()
        good = mod.ENDPOINTS_PATH.read_text(encoding="utf-8")

        mod.ENDPOINTS_PATH.unlink()
        mod._endpoints_cache.update({"loaded": False, "endpoints": {}})
        assert client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []}
                           ).status_code == 503

        mod.ENDPOINTS_PATH.write_text(good, encoding="utf-8")
        assert client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []}
                           ).status_code == 200

    def test_a_good_allowlist_is_still_cached(self, router):
        """Recovery must not turn every request into a disk read."""
        mod, client, write_state, calls = router
        write_state()
        assert client.get("/health").status_code == 200

        # Cache is warm; removing the file must not change the answer.
        mod.ENDPOINTS_PATH.unlink()
        assert client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []}
                           ).status_code == 200

    def test_health_reports_degraded_without_an_allowlist(self, router):
        mod, client, write_state, calls = router
        write_state()
        mod.ENDPOINTS_PATH.write_text(json.dumps({"endpoints": []}),
                                      encoding="utf-8")
        mod._endpoints_cache.update({"loaded": False, "endpoints": {}})

        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"
        assert resp.json()["endpointCount"] == 0

    def test_health_is_ok_with_an_allowlist(self, router):
        mod, client, write_state, calls = router
        write_state()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["endpointCount"] == 2
