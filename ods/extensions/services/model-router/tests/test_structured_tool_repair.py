"""One repair for a complete rejected model decision, before any tool is exposed."""
import asyncio
import json
import httpx
import pytest
from test_router import router as router

TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {
    "type": "object", "properties": {"key": {"type": "string"}},
    "required": ["key"], "additionalProperties": False}}}]


def decision(name="lookup", args='{"key":"ok"}', *, model="Concrete.gguf", finish="tool_calls"):
    return {"id": "decision-1", "model": model, "choices": [{"index": 0,
        "finish_reason": finish, "message": {"role": "assistant", "content": None,
        "tool_calls": [{"id": "call-1", "type": "function",
                        "function": {"name": name, "arguments": args}}]}}]}


def wire_response(doc, stream, *, incomplete=False):
    if not stream:
        return httpx.Response(200, json=doc)
    choice = doc["choices"][0]
    delta = dict(choice["message"])
    delta["tool_calls"] = [{**call, "index": i} for i, call in enumerate(delta["tool_calls"])]
    frame = {"id": doc["id"], "model": doc["model"], "choices": [{"index": 0,
             "delta": delta, "finish_reason": None if incomplete else choice["finish_reason"]}]}
    data = b"data: " + json.dumps(frame).encode() + b"\n\n"
    if not incomplete:
        data += b"data: [DONE]\n\n"
    return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})


def exchange(router, first, second=None, *, stream=True, incomplete=False, **options):
    mod, client, write_state, _ = router
    write_state()
    sent = []
    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        assert len(sent) <= 2, "more than one protocol repair"
        doc = first if len(sent) == 1 else (second or first)
        return wire_response(doc, body.get("stream", False), incomplete=incomplete)
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    body = {"model": "ods/current", "stream": stream, "tools": TOOLS,
            "messages": [{"role": "user", "content": "Find the requested key."}],
            "temperature": 0.1, "max_tokens": 256, **options}
    response = client.post("/v1/chat/completions", json=body)
    return response, sent, body


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("first", [decision("unknown-model-function"), decision(args='{"key":'),
                                     decision(args='{"key":7}')])
def test_invalid_complete_call_can_be_repaired_once(router, stream, first):
    response, sent, original = exchange(router, first, decision(), stream=stream)
    if stream and first["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"key":':
        # Invalid argument JSON never forms a completed SSE decision. Preserve
        # the assembler's fail-closed boundary without a compatibility retry.
        assert response.status_code == 502 and len(sent) == 1
        assert response.json()["error"]["type"] == "upstream_invalid_response"
        return
    assert response.status_code == 200
    assert len(sent) == 2
    assert sent[1]["model"] == "Concrete.gguf"
    assert sent[1]["stream"] is False
    assert sent[1]["tools"] == TOOLS
    assert sent[1]["messages"][:-1] == original["messages"]
    assert sent[1]["temperature"] == 0.1 and sent[1]["max_tokens"] == 256
    assert "stream_options" not in sent[1]
    feedback = sent[1]["messages"][-1]["content"]
    assert "unknown-model-function" not in feedback
    assert '"key":7' not in feedback
    assert "discarded before any tool ran" in feedback
    assert "lookup" in response.text and "unknown-model-function" not in response.text
    if stream:
        assert response.headers["x-ods-tool-stream-repair"] == "true"
        assert response.text.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("stream", [False, True])
def test_failed_repair_is_bounded_and_exposes_no_call(router, stream):
    response, sent, _ = exchange(router, decision("unknown-model-function"), stream=stream)
    assert response.status_code == 502 and len(sent) == 2
    assert response.json()["error"]["type"] == "tool_protocol_invalid"
    assert "unknown-model-function" not in response.text and "call-1" not in response.text


@pytest.mark.parametrize("stream", [False, True])
def test_wrong_initial_model_is_never_repaired(router, stream):
    response, sent, _ = exchange(router, decision("unknown", model="Other.gguf"), stream=stream)
    assert response.status_code == 502 and len(sent) == 1
    assert response.json()["error"]["type"] == "response_identity_mismatch"


@pytest.mark.parametrize("stream", [False, True])
def test_wrong_repair_model_is_rejected(router, stream):
    response, sent, _ = exchange(router, decision("unknown"), decision(model="Other.gguf"), stream=stream)
    assert response.status_code == 502 and len(sent) == 2
    assert response.json()["error"]["type"] == "response_identity_mismatch"
    assert "call-1" not in response.text


@pytest.mark.parametrize("stream", [False, True])
def test_valid_call_needs_no_repair(router, stream):
    response, sent, _ = exchange(router, decision(), stream=stream)
    assert response.status_code == 200 and len(sent) == 1
    assert "x-ods-tool-stream-repair" not in response.headers


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_noncomplete_decision_fails_without_repair(router, finish):
    response, sent, _ = exchange(router, decision("unknown", finish=finish))
    assert response.status_code == 502 and len(sent) == 1
    assert "call-1" not in response.text


def test_unfinished_stream_fails_without_repair(router):
    response, sent, _ = exchange(router, decision("unknown"), incomplete=True)
    assert response.status_code == 502 and len(sent) == 1
    assert response.json()["error"]["type"] == "upstream_invalid_response"


def test_forced_tool_and_parallel_policy_survive_repair(router):
    first = decision("unknown")
    response, sent, _ = exchange(router, first, decision(), tool_choice={"type": "function",
                               "function": {"name": "lookup"}}, parallel_tool_calls=False)
    assert response.status_code == 200 and len(sent) == 2
    assert sent[1]["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}
    assert sent[1]["parallel_tool_calls"] is False
