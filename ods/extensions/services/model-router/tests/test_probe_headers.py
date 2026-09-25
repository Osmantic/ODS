"""Header correlation never enters model input; response measurements are bounded."""
import asyncio
import base64
import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from test_router import router as router
from test_probe_attempts import arm, observed
from app.probe_response import ProbeStream, response_metrics


def signed(probe, body, call=None):
    call = call or uuid.uuid4().hex
    raw = b"ods.probe-header.v1\0" + probe.encode() + b"\0" + call.encode() + b"\0" + hashlib.sha256(body).digest()
    sig = base64.urlsafe_b64encode(hmac.new(b"probe-secret", raw, hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{probe}.{call}.{sig}"


@pytest.mark.parametrize("kind", ["stream", "buffered-tool", "repair"])
def test_signed_header_body_unchanged_response_metrics_all_paths(router, kind):
    mod, client, write, _ = router
    write()
    probe = str(uuid.uuid4())
    arm(client, probe)
    payload = {"model": "ods/current", "stream": True, "messages": [{"role": "user", "content": "same private task"}]}
    if kind != "stream":
        payload["tools"] = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}}}]
    body = json.dumps(payload).encode()
    header = signed(probe, body)
    requests = []
    metrics = {"usage": {"prompt_tokens": 107, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 100}, "completion_tokens_details": {"reasoning_tokens": 2}, "private": "secret"}, "timings": {"prompt_n": 7, "prompt_ms": 2.5, "predicted_n": 7, "predicted_ms": 5.5, "cache_n": 100, "private": "secret"}}
    def backend(request):
        requests.append(request)
        assert not any(k.startswith("x-ods-probe") for k in request.headers)
        forwarded = json.loads(request.content)
        assert forwarded["messages"][:1] == payload["messages"]
        assert probe not in request.content.decode()
        if kind == "stream":
            events = [": keepalive\n\n", 'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n', 'data: {"id":"reply-1","model":"Concrete.gguf","choices":[{"delta":{"content":"private answer"}}]}\n\n', "data: " + json.dumps(metrics) + "\n\n", "data: [DONE]\n\n"]
            return httpx.Response(200, content="".join(events).encode(), headers={"content-type": "text/event-stream"})
        text = "<tool_call>\n<function=missing_tool>\n<parameter=key>\nx\n</parameter>\n</function>\n</tool_call>" if kind == "repair" and len(requests) == 1 else "private answer"
        return httpx.Response(200, json={"id": "reply-1", "model": "Concrete.gguf", **metrics, "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    response = client.post("/v1/chat/completions", content=body, headers={"Content-Type": "application/json", "X-ODS-Probe": header, "X-ODS-Probe-Secret": "do-not-forward"})
    assert response.status_code == 200
    rows = observed(client, probe)["attempts"]
    assert len(rows) == len(requests) == (2 if kind == "repair" else 1)
    for row in rows:
        assert row["binding"] == "signed-http-header" and row["correlationId"] == header.split('.')[1]
        assert row["routerAdmissionMs"] >= 0 and row["routeReadyWaitMs"] >= 0
        assert row["responseMetrics"]["usage"]["prompt_tokens_details"]["cached_tokens"] == 100
        assert row["responseMetrics"]["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2
        assert row["responseMetrics"]["timings"]["prompt_ms"] == 2.5
        assert row["responseMetrics"]["responseId"] == "reply-1"
        assert ("firstContentDeltaMs" in row) == (kind == "stream")
    if kind == "repair": assert rows[1]["repairReason"] == "native-tool-protocol-repair"
    rendered = json.dumps(rows)
    for private in ("same private task", "private answer", "do-not-forward", "secret", header): assert private not in rendered


def test_header_single_use_wrong_body_expiry_and_cross_scope(router):
    mod, client, write, _ = router
    write()
    probe = str(uuid.uuid4())
    arm(client, probe)
    body = b'{"model":"ods/current","messages":[]}'
    token = signed(probe, body)
    assert mod._probe_attempts.accept_header(token, 'probe-secret', body+b' ') is None
    assert mod._probe_attempts.accept_header(token, 'wrong', body) is None
    assert mod._probe_attempts.accept_header(token, 'probe-secret', body)[0] == probe
    assert mod._probe_attempts.accept_header(token, 'probe-secret', body) is None
    other = str(uuid.uuid4())
    assert mod._probe_attempts.accept_header(signed(other, body), 'probe-secret', body) is None
    mod._probe_attempts.scopes[probe]['expires'] = 0
    assert mod._probe_attempts.accept_header(signed(probe, body), 'probe-secret', body) is None


@pytest.mark.parametrize('headers', [{'X-ODS-Probe':'invalid'}, [('X-ODS-Probe','a'),('X-ODS-Probe','b')]])
def test_invalid_or_duplicate_header_does_not_capture_or_break_task(router, headers):
    mod, client, write, calls = router
    write()
    probe = str(uuid.uuid4())
    arm(client, probe)
    assert client.post('/v1/chat/completions', json={'model':'ods/current','messages':[]}, headers=headers).status_code == 200
    assert len(calls) == 1 and observed(client, probe)['attempts'] == []


def test_response_metrics_unknowns_bounds_and_no_content():
    result = response_metrics({'id':'private response with spaces','usage':{'prompt_tokens':True,'completion_tokens':float('nan'),'total_tokens':-1,'completion_tokens_details':{'reasoning_tokens':'not evidence'}},'choices':[{'message':{'content':'private'}}],'timings':{'prompt_ms':1e30}})
    assert 'responseId' not in result and 'choices' not in result
    assert all(result['usage'][k] is None for k in ('prompt_tokens','completion_tokens','total_tokens'))
    assert result['usage']['completion_tokens_details']['reasoning_tokens'] is None
    assert result['timings']['prompt_ms'] is None


def test_sse_chunk_boundaries_and_overflow_do_not_retain_raw_content():
    rows=[]
    observer=ProbeStream(rows.append)
    for chunk in [b':keepalive\n\nda',b'ta: {"usage":{"prompt_tokens":1}}\r',b'\n\ndata: [DONE]\n\n']:observer.feed(chunk)
    assert rows == [{'usage':{'prompt_tokens':1}}]
    observer.feed(b'x'*(observer.MAX_PENDING+1))
    assert not observer.available and observer.pending == b''
