"""Explicit Responses failures never become per-attempt success identities."""
import asyncio
import json
import uuid

import httpx
import pytest

from test_probe_attempts import arm, observed, payload
from test_router import router as router, _signed_marker


def set_upstream(mod, upstream):
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: upstream))


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled"])
def test_responses_json_failure_status_with_model_stays_unknown(router, status):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)
    set_upstream(mod, httpx.Response(200, json={"model": "Concrete.gguf", "status": status}))
    response = client.post("/v1/responses", json={"model": "ods/current", "input": _signed_marker(probe)})
    assert response.status_code == 200 and response.json()["status"] == status
    row, = observed(client, probe)["attempts"]
    assert row["status"] == "complete" and row["httpStatus"] == 200
    assert row.get("responseModel") is None


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled"])
@pytest.mark.parametrize("done", [False, True], ids=["without-done", "with-done"])
def test_responses_sse_type_only_failure_with_model_stays_unknown(router, status, done):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)
    # Compatibility backends may append a Chat-style [DONE] to Responses events.
    # An explicit failed event must win over that terminal framing marker.
    event = {"type": "response." + status, "response": {"model": "Concrete.gguf"}}
    raw = b"data: " + json.dumps(event).encode() + b"\n\n"
    if done:
        raw += b"data: [DONE]\n\n"
    set_upstream(mod, httpx.Response(200, content=raw, headers={"content-type": "text/event-stream"}))
    response = client.post("/v1/responses", json={"model": "ods/current", "stream": True,
                                                 "input": _signed_marker(probe)})
    assert response.status_code == 200 and "response." + status in response.text
    row, = observed(client, probe)["attempts"]
    assert row["status"] == "complete"
    assert row.get("responseModel") is None


@pytest.mark.parametrize("stream", [False, True])
def test_completed_responses_null_error_is_not_a_failure(router, stream):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)
    completed = {"model": "Concrete.gguf", "status": "completed", "error": None}
    if stream:
        upstream = httpx.Response(200, content=b"data: " + json.dumps({
            "type": "response.completed", "response": completed,
            "error": None}).encode() + b"\n\n", headers={"content-type": "text/event-stream"})
    else:
        upstream = httpx.Response(200, json=completed)
    set_upstream(mod, upstream)
    response = client.post("/v1/responses", json={"model": "ods/current", "stream": stream,
                                                 "input": _signed_marker(probe)})
    assert response.status_code == 200
    row, = observed(client, probe)["attempts"]
    assert row.get("responseModel") == "Concrete.gguf"


@pytest.mark.parametrize("failure", [{"error": {"message": "synthetic failure"}},
    {"type": "response.failed", "response": {"model": "Concrete.gguf"}},
    {"type": "response.incomplete", "response": {"model": "Concrete.gguf"}}],
    ids=["chat-error", "responses-failed", "responses-incomplete"])
def test_buffered_chat_tool_stream_keeps_unproven_identity_unknown(router, failure):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    arm(client, probe)
    first = {"model": "Concrete.gguf", "choices": [{"index": 0,
        "delta": {"role": "assistant", "content": "synthetic"}, "finish_reason": "stop"}]}
    raw = b"".join(b"data: " + json.dumps(value).encode() + b"\n\n" for value in [first, failure])
    set_upstream(mod, httpx.Response(200, content=raw + b"data: [DONE]\n\n",
                                   headers={"content-type": "text/event-stream"}))
    response = client.post("/v1/chat/completions", json=payload(probe, stream=True, tools=True))
    # Existing forwarding rejects Chat errors, but ignores unfamiliar Responses
    # lifecycle frames. This patch only changes diagnostic identity attribution.
    assert response.status_code == (502 if "error" in failure else 200)
    if "error" in failure:
        assert response.json()["error"]["type"] == "upstream_invalid_response"
    row, = observed(client, probe)["attempts"]
    assert row["status"] == ("stream-error" if "error" in failure else "complete")
    assert row.get("responseModel") is None
