"""Per-send identities survive signed owner/title overlap; no model or sockets."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid

import httpx
import pytest

from test_probe_attempts import AUTH, arm, expected_digest, observed, payload
from test_router import router as router, _ChunkedStream


def reply(model, stream=False):
    body = {"model": model, "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "synthetic response"},
        "finish_reason": "stop"}]}
    if not stream:
        return httpx.Response(200, json=body)
    body["choices"][0]["delta"] = body["choices"][0].pop("message")
    return httpx.Response(200, content=b"data: " + json.dumps(body).encode()
        + b"\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "overlap"])
@pytest.mark.parametrize("owner_stream,title_stream", [(False, False), (True, False),
    (False, True), (True, True)])
def test_signed_owner_and_title_keep_distinct_actual_identities(
        router, concurrent, owner_stream, title_stream):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    assert arm(client, probe).status_code == 201
    owner = payload(probe, owner_stream)
    title = payload(probe, title_stream)
    title["messages"][0]["content"] = "Synthetic title instruction"
    title["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "session_title", "strict": True, "schema": {"type": "object"}}}
    started, release = threading.Event(), threading.Event()
    sent = {}

    async def upstream(request):
        body = json.loads(request.content)
        kind = "title" if "response_format" in body else "owner"
        sent[kind] = request.content
        if concurrent and kind == "owner":
            started.set()
            assert await asyncio.to_thread(release.wait, 5)
        # Both routes selected Concrete.gguf; title actually answered elsewhere.
        return reply("Title-other.gguf" if kind == "title" else "Concrete.gguf",
                     body["stream"])

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    if concurrent:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, "/v1/chat/completions", json=owner)
            try:
                assert started.wait(5)
                responses = {"title": client.post("/v1/chat/completions", json=title)}
                rows = observed(client, probe)["attempts"]
                assert rows[0]["status"] == "pending"
                assert rows[0]["responseModel"] is None
                assert rows[1]["responseModel"] == "Title-other.gguf"
            finally:
                release.set()
            responses["owner"] = pending.result(timeout=5)
    else:
        responses = {kind: client.post("/v1/chat/completions", json=body)
                     for kind, body in [("owner", owner), ("title", title)]}
    rows = observed(client, probe)["attempts"]
    assert len(rows) == len(sent) == 2
    for kind, response in responses.items():
        assert response.status_code == 200
        row, = [r for r in rows if r["requestId"] == response.headers["X-ODS-Request-Id"]]
        assert row["responseModel"] == ("Title-other.gguf" if kind == "title" else "Concrete.gguf")
        assert row["routedModel"] == "Concrete.gguf" and row["attempt"] == 1
        assert row["status"] == "complete" and row["httpStatus"] == 200
        assert row["fingerprints"]["bodyDigest"] == expected_digest(probe, sent[kind])
    latest = client.get(f"/internal/route-evidence/{probe}", headers=AUTH).json()
    assert latest["responseModel"] == ("Concrete.gguf" if concurrent else "Title-other.gguf")
    assert "Synthetic title instruction" not in json.dumps(rows)
    assert "private user" not in json.dumps(rows)


@pytest.mark.parametrize("kind", ["missing", "non-string", "oversized", "http-error", "json-error",
    "malformed", "stream-missing", "stream-conflict", "stream-incomplete", "stream-error",
    "stream-http-error", "stream-inband-error", "stream-response-failed", "timeout"])
def test_unproven_response_identity_is_not_filled_from_selected_route(router, kind):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)

    def upstream(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("synthetic failure", request=request)
        if kind.startswith("stream-"):
            first = b'data: {"model":"Concrete.gguf","choices":[{"delta":{"content":"x"}}]}\n\n'
            if kind == "stream-missing":
                first = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            chunks = [first]
            if kind == "stream-conflict":
                chunks.append(b'data: {"model":"Other.gguf","choices":[]}\n\n')
            if kind == "stream-inband-error":
                chunks.append(b'data: {"error":{"message":"synthetic"}}\n\n')
            if kind == "stream-response-failed":
                chunks.append(b'data: {"type":"response.failed","response":{"status":"failed"}}\n\n')
            if kind not in {"stream-incomplete", "stream-error"}:
                chunks.append(b'data: [DONE]\n\n')
            return httpx.Response(500 if kind == "stream-http-error" else 200,
                stream=_ChunkedStream(chunks, httpx.ReadError("synthetic failure")
                    if kind == "stream-error" else None),
                headers={"content-type": "text/event-stream"})
        if kind == "malformed":
            return httpx.Response(200, content=b"invalid json")
        body = {"choices": []}
        if kind == "non-string":
            body["model"] = {"private": "body"}
        elif kind == "oversized":
            body["model"] = "x" * 257
        elif kind in {"http-error", "json-error"}:
            body = {"model": "Concrete.gguf", "error": {"message": "synthetic"}}
        return httpx.Response(500 if kind == "http-error" else 200, json=body)

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    if kind == "stream-error":
        with pytest.raises(httpx.ReadError):
            client.post("/v1/chat/completions", json=payload(probe, True))
    else:
        client.post("/v1/chat/completions", json=payload(probe, kind.startswith("stream-")))
    row, = observed(client, probe)["attempts"]
    assert row["responseModel"] is None
    assert row["routedModel"] == "Concrete.gguf"
    assert row["status"] != "pending"


def test_response_identity_survives_only_its_own_capture_lease(router):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    now = [1.0]
    mod._probe_attempts.clock = lambda: now[0]
    arm(client, probe, ttlSeconds=1)
    client.post("/v1/chat/completions", json=payload(probe))
    row, = observed(client, probe)["attempts"]
    assert row["responseModel"] == "Concrete.gguf"
    row["responseModel"] = "forged"
    assert observed(client, probe)["attempts"][0]["responseModel"] == "Concrete.gguf"
    now[0] = 2.0
    assert client.get(f"/internal/route-evidence/{probe}", headers=AUTH).json().get("upstreamAttempts") is None
