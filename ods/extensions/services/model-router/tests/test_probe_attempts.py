"""Exact upstream bytes and bounded signed ownership; no model or sockets."""
import asyncio
import hashlib
import hmac
import json
import uuid

import httpx
import pytest

from test_router import router as router, _signed_marker

AUTH = {"Authorization": "Bearer internal-secret"}


def arm(client, probe, **changes):
    return client.post(f"/internal/route-evidence/{probe}/capture", headers=AUTH,
                       json={"ttlSeconds": 60, "maxAttempts": 16, **changes})


def observed(client, probe):
    return client.get(f"/internal/route-evidence/{probe}", headers=AUTH).json()["upstreamAttempts"]


def expected_digest(probe, raw):
    key = hmac.new(b"probe-secret", b"ods.probe-attempt.v1\0" + probe.encode(), hashlib.sha256).digest()
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


def payload(probe, stream=False, tools=False):
    result = {"model": "ods/current", "stream": stream, "messages": [
        {"role": "system", "content": "private system date 😀"},
        {"role": "user", "content": "private user " + _signed_marker(probe)}]}
    if tools:
        result["tools"] = [{"type": "function", "function": {"name": "lookup",
            "description": "private schema", "parameters": {"type": "object", "properties": {}}}}]
    return result


@pytest.mark.parametrize("kind", ["stream", "buffered-tool", "repair"])
def test_each_upstream_send_matches_wire_and_preserves_attempts(router, kind):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    assert arm(client, probe).status_code == 201
    sent = []

    def upstream(request):
        sent.append(request.content)
        body = json.loads(request.content)
        if kind == "stream":
            assert body["stream"] is True
            return httpx.Response(200, content=(
                'data: {"model":"Concrete.gguf","choices":[{"delta":{"content":"ok"}}]}\n\n'
                'data: [DONE]\n\n'), headers={"content-type": "text/event-stream"})
        assert body["stream"] is False
        text = ("<tool_call>\n<function=missing_tool>\n<parameter=key>\nx\n</parameter>\n</function>\n</tool_call>"
                if kind == "repair" and len(sent) == 1 else "ok")
        return httpx.Response(200, json={"model": "Concrete.gguf", "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    response = client.post("/v1/chat/completions", json=payload(probe, True, kind != "stream"))
    assert response.status_code == 200
    rows = observed(client, probe)["attempts"]
    assert len(rows) == len(sent) == (2 if kind == "repair" else 1)
    assert [r["attempt"] for r in rows] == list(range(1, len(rows) + 1))
    for row, raw in zip(rows, sent):
        assert row["requestId"] == response.headers["X-ODS-Request-Id"]
        assert row["fingerprints"]["bodyDigest"] == expected_digest(probe, raw)
        assert row["fingerprints"]["bodyBytes"] == len(raw)
        assert row["status"] == "complete" and row["httpStatus"] == 200
        assert row["elapsedMs"] >= 0 and row["capturePrepareMs"] >= 0
        actual = json.loads(raw)
        for name in ["messages", "tools"]:
            canonical = json.dumps({"present": name in actual, "value": actual.get(name)},
                ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("ascii")
            assert row["fingerprints"]["components"][name]["digest"] == expected_digest(probe, canonical)
    encoded = json.dumps(rows)
    for private in ["private system", "private user", "private schema", "lookup", "probe-secret", "ODS_PROBE"]:
        assert private not in encoded
    assert client.get(f"/internal/route-evidence/{probe}").status_code == 401


def test_unsigned_unarmed_and_other_scopes_do_not_capture(router):
    mod, client, write_state, _ = router
    write_state()
    own, other = str(uuid.uuid4()), str(uuid.uuid4())
    assert arm(client, own).status_code == 201
    for value in [payload(other), {"model": "ods/current", "messages": [
            {"role": "user", "content": _signed_marker(own, "wrong-key")}]}]:
        assert client.post("/v1/chat/completions", json=value).status_code == 200
    assert observed(client, own)["attempts"] == []
    assert mod._probe_attempts.public(other, "probe-secret") is None
    assert arm(client, own).status_code == 409


def test_capture_lease_requires_auth_valid_bounds_and_current_key(router, monkeypatch):
    mod, client, *_ = router
    probe = str(uuid.uuid4())
    assert client.post(f"/internal/route-evidence/{probe}/capture", json={}).status_code == 401
    for bad in [{"ttlSeconds": 0}, {"ttlSeconds": 1801}, {"ttlSeconds": True},
                {"maxAttempts": 17}, {"maxAttempts": False}, {"extra": "private"}]:
        assert arm(client, probe, **bad).status_code == 400
    assert arm(client, "not-uuid").status_code == 400
    assert client.post(f"/internal/route-evidence/{probe}/capture", headers=AUTH,
                       content=b"x" * 513).status_code == 413
    assert arm(client, probe).status_code == 201
    monkeypatch.setattr(mod, "PROBE_KEY", "rotated-key")
    assert client.get(f"/internal/route-evidence/{probe}", headers=AUTH).status_code == 404


def test_expiry_limit_and_old_completion_cannot_revive_evidence(router):
    mod, client, write_state, _ = router
    write_state()
    now = [10.0]
    mod._probe_attempts.clock = lambda: now[0]
    probe = str(uuid.uuid4())
    assert arm(client, probe, ttlSeconds=2, maxAttempts=1).status_code == 201
    for _ in range(2):
        assert client.post("/v1/chat/completions", json=payload(probe)).status_code == 200
    assert len(observed(client, probe)["attempts"]) == 1
    assert observed(client, probe)["limitReached"] is True
    scope = mod._probe_attempts.scopes[probe]
    stale_handle = (probe, scope, scope["records"][0])
    now[0] = 12.0
    assert mod._probe_attempts.public(probe, "probe-secret") is None
    assert arm(client, probe).status_code == 201
    mod._probe_attempts.finish(stale_handle, "complete", 200)
    assert observed(client, probe)["attempts"] == []


def test_ordered_components_detect_schema_and_message_changes(router):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)
    original = payload(probe, tools=True)
    original["tools"].append({"type": "function", "function": {"name": "other"}})
    changed = json.loads(json.dumps(original))
    changed["tools"].reverse()
    changed["messages"][0]["content"] += " changed-time"
    for body in [original, changed]:
        assert client.post("/v1/chat/completions", json=body).status_code == 200
    a, b = [r["fingerprints"]["components"] for r in observed(client, probe)["attempts"]]
    assert a["tools"]["digest"] != b["tools"]["digest"]
    assert a["messages"]["entries"][0]["content"] != b["messages"]["entries"][0]["content"]
    assert a["messages"]["entries"][1] == b["messages"]["entries"][1]


@pytest.mark.parametrize("failure", ["timeout", "transport-error"])
def test_failed_sends_are_observable_without_retries_or_exception_text(router, failure):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)
    sent = []
    def fail(request):
        sent.append(request)
        error = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
        raise error("private backend detail", request=request)
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    response = client.post("/v1/chat/completions", json=payload(probe))
    assert response.status_code in [502, 504] and len(sent) == 1
    rows = observed(client, probe)["attempts"]
    assert len(rows) == 1 and rows[0]["status"] == failure
    assert "private backend detail" not in json.dumps(rows)


def test_diagnostic_failure_cannot_change_successful_task(router, monkeypatch):
    mod, client, write_state, calls = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)
    def broken(*args):
        raise RuntimeError("diagnostic fixture failure")
    monkeypatch.setattr(mod._probe_attempts, "begin", broken)
    monkeypatch.setattr(mod._probe_attempts, "finish", broken)
    response = client.post("/v1/chat/completions", json=payload(probe))
    assert response.status_code == 200 and len(calls) == 1


def test_interleaved_scopes_eviction_and_returned_copy_are_isolated(router):
    mod, *_ = router
    captures = mod._probe_attempts
    captures.MAX_SCOPES = 2
    own, other, third = [str(uuid.uuid4()) for _ in range(3)]
    route = {'routeSeq': 7, 'runtimeModelId': 'model', 'endpointId': 'local', 'backendKind': 'llama-server'}
    for probe in (own, other):
        captures.arm(probe, 60, 2, 'probe-secret')
    first = captures.begin(own, 'probe-secret', 'own-request', 1, b'{"messages":[]}', route)
    second = captures.begin(other, 'probe-secret', 'other-request', 1, b'{"messages":[]}', route)
    captures.finish(second, 'complete', 200)
    assert captures.public(own, 'probe-secret')['attempts'][0]['status'] == 'pending'
    copy = captures.public(other, 'probe-secret')
    copy['attempts'][0]['status'] = 'forged'
    assert captures.public(other, 'probe-secret')['attempts'][0]['status'] == 'complete'
    # Identical bodies have separate keyed digests across capture owners.
    assert first[2]['fingerprints']['bodyDigest'] != second[2]['fingerprints']['bodyDigest']
    captures.arm(third, 60, 2, 'probe-secret')
    captures.finish(first, 'complete', 200)
    assert captures.public(own, 'probe-secret') is None
    assert captures.public(third, 'probe-secret')['attempts'] == []
    assert captures.begin(other, 'wrong-key', 'wrong-request', 1, b'{}', route) is None


@pytest.mark.parametrize('body', [b'not-json', b'[]', b'{"messages":null}',
    b'{"messages":[{"role":[]}]}', b'{"messages":[null]}',
    json.dumps({'messages': [{}] * 257}).encode(), b' ' * (2 * 1024 * 1024 + 1)],
    ids=['malformed-json', 'non-object', 'non-list', 'invalid-role', 'null-message', 'message-limit', 'body-limit'])
def test_unavailable_fingerprints_remain_bounded_metadata(router, body):
    mod, *_ = router
    captures = mod._probe_attempts
    probe = str(uuid.uuid4())
    captures.arm(probe, 60, 1, 'probe-secret')
    handle = captures.begin(probe, 'probe-secret', 'request', 1, body,
        {'routeSeq': 7, 'runtimeModelId': 'model', 'endpointId': 'local', 'backendKind': 'llama-server'})
    captures.finish(handle, 'complete', 200)
    row = captures.public(probe, 'probe-secret')['attempts'][0]
    assert row['fingerprints'] == {'state': 'unavailable'}
    assert row['status'] == 'complete' and row['capturePrepareMs'] >= 0
    assert len(json.dumps(row)) < 1000
