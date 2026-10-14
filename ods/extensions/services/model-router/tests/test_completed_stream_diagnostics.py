"""Static rejection evidence; fake upstreams never contact a model runtime."""
import asyncio
import json
import logging

import httpx
import pytest

from test_router import router as router
from app.completed_tool_sse import (
    CompletionStreamError, assemble_chat_completion_sse, completion_stream_rejection_code,
)

PRIVATE = "PRIVATE_PROMPT_ARGUMENT_AND_CREDENTIAL_SENTINEL"
MODEL = "Concrete.gguf"
TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {
    "type": "object", "properties": {"offset": {"type": "integer"}},
}}}]


def frame(delta, finish=None, model=MODEL):
    return ("data: " + json.dumps({"model": model, "choices": [{"index": 0,
        "delta": delta, "finish_reason": finish}]}) + "\n\n").encode()


def raw_case(kind):
    initial = frame({"role": "assistant", "content": PRIVATE})
    terminal = frame({}, "stop") + b"data: [DONE]\n\n"
    if kind == "missing_done":return initial + frame({}, "stop")
    if kind == "unsupported_delta":return initial + frame({PRIVATE: PRIVATE}) + terminal
    if kind == "invalid_arguments":
        return frame({"role": "assistant", "tool_calls": [{"index": 0, "id": PRIVATE,
            "type": "function", "function": {"name": "read", "arguments": PRIVATE}}]},
            "tool_calls") + b"data: [DONE]\n\n"
    if kind == "invalid_finish":return initial + frame({}, PRIVATE) + b"data: [DONE]\n\n"
    if kind == "model_mismatch":return frame({"role": "assistant"}, "stop", PRIVATE) + b"data: [DONE]\n\n"
    if kind == "malformed_json":return b'data: {"private":"' + PRIVATE.encode() + b'",}\n\n'
    if kind == "truncated":return initial + b'data: ' + PRIVATE.encode()
    if kind == "utf8":return b'data: \xff' + PRIVATE.encode() + b'\n\n'
    raise AssertionError(kind)


CASES = [
    ("missing_done", "sse_missing_done"),
    ("unsupported_delta", "sse_unsupported_delta_field"),
    ("invalid_arguments", "sse_invalid_tool_arguments_json"),
    ("invalid_finish", "sse_invalid_finish_reason_for_content"),
    ("model_mismatch", "sse_model_mismatch"),
    ("malformed_json", "sse_malformed_json"),
    ("truncated", "sse_truncated_stream_unterminated_data_event"),
    ("utf8", "sse_invalid_utf_8"),
]


@pytest.mark.parametrize("kind,code", CASES)
def test_parser_preserves_only_static_rejection(kind, code):
    with pytest.raises(ValueError) as raised:
        assemble_chat_completion_sse(raw_case(kind), MODEL)
    assert completion_stream_rejection_code(raised.value) == code
    assert PRIVATE not in str(raised.value) + repr(raised.value)


def test_unknown_and_forged_exceptions_cannot_export_messages_or_codes():
    assert completion_stream_rejection_code(ValueError(PRIVATE)) == "sse_unclassified"
    typed = CompletionStreamError(PRIVATE)
    assert PRIVATE not in str(typed)
    typed.code = PRIVATE
    assert completion_stream_rejection_code(typed) == "sse_unclassified"
    typed.code = [PRIVATE]
    assert completion_stream_rejection_code(typed) == "sse_unclassified"


def install_upstream(mod, payload):
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return sent


def ask(client):
    return client.post("/v1/chat/completions", json={"model": "ods/current", "stream": True,
        "messages": [{"role": "user", "content": PRIVATE}], "tools": TOOLS})


@pytest.mark.parametrize("kind,code", CASES)
def test_router_retains_static_reason_without_retry_or_private_output(router, caplog, kind, code):
    mod, client, write_state, _ = router
    write_state()  # tools=false is advisory; the original requested tools remain offered.
    sent = install_upstream(mod, raw_case(kind))
    with caplog.at_level(logging.WARNING, logger="ods-model-router"):
        response = ask(client)
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["diagnosticCode"] == code
    assert error["type"] == ("response_identity_mismatch" if kind == "model_mismatch" else "upstream_invalid_response")
    assert error["code"] == "502"
    assert error["message"].endswith("[" + code + "]")
    assert len(sent) == 1 and sent[0]["stream"] is True and sent[0]["tools"] == TOOLS
    assert "x-ods-tool-stream-fallback" not in response.headers
    assert "x-ods-tool-stream-repair" not in response.headers
    records = [r for r in caplog.records if r.name == "ods-model-router"]
    assert [r.getMessage() for r in records] == ["Completed tool stream rejected: " + code]
    assert PRIVATE not in response.text + "\n".join(r.getMessage() for r in records)
    assert all(r.exc_info is None for r in records)


def test_unclassified_parser_error_is_static_at_router_boundary(router, caplog, monkeypatch):
    mod, client, write_state, _ = router
    write_state()
    sent = install_upstream(mod, raw_case("missing_done"))
    def rejected(*args, **kwargs):raise ValueError(PRIVATE)
    monkeypatch.setattr(mod, "assemble_chat_completion_sse", rejected)
    with caplog.at_level(logging.WARNING, logger="ods-model-router"):
        response = ask(client)
    assert response.json()["error"]["diagnosticCode"] == "sse_unclassified"
    assert PRIVATE not in response.text + caplog.text
    assert len(sent) == 1


@pytest.mark.parametrize("as_json", [False, True])
def test_valid_answer_or_same_response_json_fallback_is_not_a_rejection(router, caplog, as_json):
    mod, client, write_state, _ = router
    write_state()
    if as_json:
        payload = json.dumps({"model": MODEL, "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}]}).encode()
    else:
        payload = frame({"role": "assistant", "content": "42"}, "stop") + b"data: [DONE]\n\n"
    sent = install_upstream(mod, payload)
    with caplog.at_level(logging.WARNING, logger="ods-model-router"):
        response = ask(client)
    assert response.status_code == 200 and "42" in response.text
    assert len(sent) == 1 and sent[0]["tools"] == TOOLS
    assert "diagnosticCode" not in response.text
    assert not [r for r in caplog.records if r.name == "ods-model-router"]


def test_json_arguments_shape_is_not_a_tool_schema_compatibility_claim():
    payload = frame({"role": "assistant", "tool_calls": [{"index": 0, "id": "c1",
        "type": "function", "function": {"name": "read", "arguments": '{"offset":"0"}'}}]},
        "tool_calls") + b"data: [DONE]\n\n"
    result = assemble_chat_completion_sse(payload, MODEL)
    # Syntactically complete JSON is accepted here; caller schema validation is
    # separate. This recreates the boundary in the retained Strixy probe.
    assert json.loads(result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])["offset"] == "0"
