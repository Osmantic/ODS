"""Measure outgoing tool definitions without retaining their private contents."""

import asyncio
import copy
import hashlib
import json
import uuid

import httpx
import pytest

from test_router import router as router, _signed_marker


def tools():
    return [{"type": "function", "function": {
        "name": "private_tool", "description": "private schema context",
        "parameters": {"type": "object", "properties": {
            "secret_field": {"type": "string", "enum": ["private-enum-😀"]}},
            "required": ["secret_field"], "additionalProperties": False},
    }}]


def expected(payload):
    # Verifier owns the canonical encoding of the mock backend's actual input.
    value = {"present": "tools" in payload, "tools": payload.get("tools", [])}
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
                     sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def test_canonical_keys_preserve_array_order_and_every_tool_field(router):
    mod, *_ = router
    value = tools()
    reordered = [{"function": dict(reversed(list(value[0]["function"].items()))),
                  "type": "function"}]
    first = mod._offered_tool_evidence({"tools": value})
    assert first == mod._offered_tool_evidence({"tools": reordered})
    changed = copy.deepcopy(value)
    changed[0]["function"]["description"] += " correction"
    assert first["sha256"] != mod._offered_tool_evidence({"tools": changed})["sha256"]
    assert mod._offered_tool_evidence({"tools": value + changed})["sha256"] != (
        mod._offered_tool_evidence({"tools": changed + value})["sha256"])
    assert first["sha256"] == expected({"tools": value})


def test_missing_and_empty_are_distinct_observations(router):
    mod, *_ = router
    missing, empty = [mod._offered_tool_evidence(p) for p in ({}, {"tools": []})]
    assert missing["count"] == empty["count"] == 0
    assert missing["state"] == empty["state"] == "observed"
    assert missing["sha256"] != empty["sha256"]


def test_tool_count_boundary_and_unrelated_prompt_data(router):
    mod, *_ = router
    value = {"tools": [{}] * 256}
    measured = mod._offered_tool_evidence(value)
    assert measured["state"] == "observed" and measured["count"] == 256
    assert measured == mod._offered_tool_evidence({**value,
        "messages": [{"role": "user", "content": "private prompt"}],
        "model": "another-model", "authorization": "private credential"})


@pytest.mark.parametrize("value", [None, False, {}, "private", [float("nan")],
                                   [float("inf")], [set()], [{}] * 257])
def test_unavailable_is_closed_and_contains_no_private_content(router, value):
    mod, *_ = router
    result = mod._offered_tool_evidence({"tools": value})
    assert result == {"schemaVersion": 1,
        "boundary": "router-forwarded-tools-not-execution-proof",
        "encoding": "json-sort-keys-ascii-v1", "state": "unavailable",
        "count": None, "sha256": None}


def test_cycles_and_size_limits_do_not_escape_diagnostic_helper(router, monkeypatch):
    mod, *_ = router
    cyclic = []
    cyclic.append(cyclic)
    assert mod._offered_tool_evidence({"tools": cyclic})["state"] == "unavailable"
    value = {"tools": tools()}
    raw = json.dumps({"present": True, "tools": value["tools"]}, sort_keys=True,
                     separators=(",", ":"), ensure_ascii=True).encode("ascii")
    monkeypatch.setattr(mod, "TOOL_EVIDENCE_MAX_BYTES", len(raw))
    assert mod._offered_tool_evidence(value)["state"] == "observed"
    monkeypatch.setattr(mod, "TOOL_EVIDENCE_MAX_BYTES", len(raw) - 1)
    assert mod._offered_tool_evidence(value)["state"] == "unavailable"


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_evidence_matches_actual_forwarded_payload_and_request_id(router, path, stream):
    mod, client, write_state, _ = router
    write_state()
    captured = []

    def backend(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if payload.get("stream"):
            data = {"type": "response.completed", "response": {
                "model": "Concrete.gguf", "status": "completed", "output": []}}
            return httpx.Response(200, content=("event: response.completed\ndata: " +
                json.dumps(data) + "\n\n").encode(),
                headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"id": "c1", "model": "Concrete.gguf",
            "choices": [{"message": {"role": "assistant", "content": "hello"},
                         "finish_reason": "stop"}]})

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    probe = str(uuid.uuid4())
    payload = {"model": "ods/current", "tools": tools(), "stream": stream}
    if path.endswith("responses"):
        payload["input"] = _signed_marker(probe)
    else:
        payload["messages"] = [{"role": "user", "content": _signed_marker(probe)}]
    response = client.post(path, json=payload)
    assert response.status_code == 200
    record = client.get(f"/internal/route-evidence/{probe}", headers={
        "Authorization": "Bearer internal-secret"}).json()
    assert record["requestId"] == response.headers["X-ODS-Request-Id"]
    assert captured[-1]["tools"] == payload["tools"]
    assert record["offeredTools"]["sha256"] == expected(captured[-1])
    assert record["offeredTools"]["count"] == 1
    assert record["offeredTools"]["state"] == "observed"
    serialized = json.dumps(record)
    for private in ("private_tool", "private schema context", "secret_field", "private-enum"):
        assert private not in serialized
    assert "tools" not in record and "messages" not in record
    assert client.get(f"/internal/route-evidence/{probe}").status_code == 401


@pytest.mark.parametrize("marker", ["ordinary prompt", "[ODS_PROBE id=bad sig=bad]"])
def test_unsigned_requests_do_not_compute_or_retain_tool_evidence(router, monkeypatch, marker):
    mod, client, write_state, _ = router
    write_state()
    def forbidden(_):
        raise AssertionError("Unsigned request must not fingerprint tools")
    monkeypatch.setattr(mod, "_offered_tool_evidence", forbidden)
    response = client.post("/v1/chat/completions", json={"model": "ods/current",
        "messages": [{"role": "user", "content": marker}], "tools": tools()})
    assert response.status_code == 200 and not mod._evidence


def test_limits_leave_inference_unchanged_and_latest_probe_is_not_history(router, monkeypatch):
    mod, client, write_state, calls = router
    write_state()
    monkeypatch.setattr(mod, "TOOL_EVIDENCE_MAX_BYTES", 8)
    probe = str(uuid.uuid4())
    payload = {"model": "ods/current", "messages": [{"role": "user",
        "content": _signed_marker(probe)}], "tools": tools()}
    first = client.post("/v1/chat/completions", json=payload)
    second = client.post("/v1/chat/completions", json=payload)
    assert first.status_code == second.status_code == 200 and len(calls) == 2
    assert first.headers["X-ODS-Request-Id"] != second.headers["X-ODS-Request-Id"]
    record = mod._evidence[probe]
    assert record["requestId"] == second.headers["X-ODS-Request-Id"]
    assert record["offeredTools"]["state"] == "unavailable"
    assert len(mod._evidence) == 1
