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


# RTX Spark, 2026-10-25: three completions in a row failed "after one protocol
# repair" with HTTP 502, which ended the Portal turn, while the engine answered
# 200 each time. Nothing said which call or rule was wrong, and the generic
# repair text did not tell the model either.
INSPECT = [{"type": "function", "function": {"name": "inspect", "parameters": {
    "type": "object", "additionalProperties": False, "required": ["siteId", "steps"],
    "properties": {"siteId": {"type": "string"}, "steps": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["action", "locator"],
        "properties": {"action": {"type": "string", "enum": ["click", "assert-text"]},
                       "expectedText": {"type": "string"},
                       "locator": {"oneOf": [
                           {"type": "object", "additionalProperties": False, "required": ["selector"],
                            "properties": {"selector": {"type": "string"}}},
                           {"type": "object", "additionalProperties": False,
                            "required": ["role", "name", "exact"],
                            "properties": {"role": {"type": "string"}, "name": {"type": "string"},
                                           "exact": {"const": True}}}]}}}}}}}}]
SECRET = "PRIVATE-PAGE-TEXT"


def inspect_args(locator, **step):
    return json.dumps({"siteId": "site-1", "steps": [{"action": "click", "locator": locator, **step}]})


def sequence(router, decisions, *, stream=True, tools=TOOLS):
    """Serve `decisions` in order; more sends than decisions fail the test."""
    mod, client, write_state, _ = router
    write_state()
    sent = []
    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        assert len(sent) <= len(decisions), "more protocol repairs than allowed"
        return wire_response(decisions[len(sent) - 1], body.get("stream", False))
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = client.post("/v1/chat/completions", json={"model": "ods/current", "stream": stream,
        "tools": tools, "messages": [{"role": "user", "content": "Check the page."}]})
    return response, sent


@pytest.mark.parametrize("stream", [False, True])
def test_a_schema_mistake_gets_a_second_repair_that_names_its_rule(router, stream):
    missing = decision("inspect", inspect_args({"role": "button", "name": SECRET}))
    extra = decision("inspect", inspect_args({"selector": "#start"}, description=SECRET))
    fixed = decision("inspect", inspect_args({"role": "button", "name": "Start", "exact": True}))
    response, sent = sequence(router, [missing, extra, fixed], stream=stream, tools=INSPECT)
    assert response.status_code == 200 and len(sent) == 3
    assert "Start" in response.text and SECRET not in response.text, "the fixed call reaches the agent"
    first, second = (body["messages"][-1]["content"] for body in sent[1:])
    assert all(body["messages"][:-1] == sent[0]["messages"] for body in sent[1:]), "one feedback message each"
    assert "discarded before any tool ran" in first and "discarded before any tool ran" in second
    assert "inspect" in first and "at steps[0].locator: it is missing the required property exact" in first
    assert "at steps[0]: it has a property its schema does not allow; use only action, expectedText, locator" in second
    for feedback in (first, second):
        assert SECRET not in feedback and "description" not in feedback, "never an argument value or invented key"


@pytest.mark.parametrize("stream", [False, True])
def test_schema_repairs_are_bounded_and_expose_no_call(router, stream):
    wrong = decision("inspect", inspect_args({"role": "button", "name": "Start", "exact": "true"}))
    response, sent = sequence(router, [wrong, wrong, wrong], stream=stream, tools=INSPECT)
    assert response.status_code == 502 and len(sent) == 3
    assert response.json()["error"] == {"message": "Model returned an invalid tool decision after 2 protocol repairs",
                                        "type": "tool_protocol_invalid", "code": "502"}
    assert "call-1" not in response.text
    assert "steps[0].locator.exact: the value must be exactly true" in sent[2]["messages"][-1]["content"]


@pytest.mark.parametrize("second", [decision("unknown-model-function"), decision(args='{"key":')])
def test_only_a_schema_mistake_earns_the_second_repair(router, second):
    response, sent = sequence(router, [decision(args='{"key":7}'), second, decision()], stream=False)
    assert response.status_code == 502 and len(sent) == 2
    assert response.json()["error"]["message"] == "Model returned an invalid tool decision after one protocol repair"


def test_an_unknown_function_repair_lists_the_offered_names_but_never_the_invented_one(router):
    response, sent = sequence(router, [decision("unknown-model-function"), decision()], stream=False)
    assert response.status_code == 200 and len(sent) == 2
    feedback = sent[1]["messages"][-1]["content"]
    assert "Available function names: lookup." in feedback and "unknown-model-function" not in feedback


def test_every_rejected_decision_is_logged_by_shape_never_by_value(router, caplog):
    missing = decision("inspect", inspect_args({"role": "button", "name": SECRET}))
    extra = decision("inspect", inspect_args({"selector": "#start"}, **{"note": SECRET}))
    with caplog.at_level("WARNING", logger="ods-model-router"):
        response, sent = sequence(router, [missing, extra, extra], stream=False, tools=INSPECT)
    assert response.status_code == 502 and len(sent) == 3
    lines = [record.getMessage() for record in caplog.records if "Rejected model tool decision" in record.getMessage()]
    assert len(lines) == 3
    assert all(SECRET not in line and "Start" not in line and "#start" not in line for line in lines)
    initial = json.loads(lines[0].split(": ", 1)[1])
    assert lines[0].startswith("Rejected model tool decision (initial)")
    assert initial == {"kind": "schema", "channel": "structured", "function": "inspect", "functions": ["inspect"],
                       "rule": "required", "at": "steps/0/locator", "missing": ["exact"],
                       "keys": ["siteId", "steps"], "keysAt": ["name", "role"]}
    repaired = json.loads(lines[2].split(": ", 1)[1])
    assert lines[2].startswith("Rejected model tool decision (repair 2)")
    assert repaired["rule"] == "additionalProperties" and repaired["at"] == "steps/0"
    assert repaired["unexpected"] == ["note"], "argument key names may be logged"


def test_an_unknown_function_and_bad_arguments_are_logged_without_their_text(router, caplog):
    with caplog.at_level("WARNING", logger="ods-model-router"):
        response, _ = sequence(router, [decision("unknown function " + SECRET), decision(args='{"key":"' + SECRET)],
                               stream=False)
    assert response.status_code == 502
    lines = [record.getMessage() for record in caplog.records if "Rejected model tool decision" in record.getMessage()]
    assert [json.loads(line.split(": ", 1)[1]) for line in lines] == [
        {"kind": "unknown-function", "channel": "structured", "functions": ["?"]},
        {"kind": "arguments", "channel": "structured", "functions": ["lookup"]}]
    assert all(SECRET not in line for line in lines)


def test_a_native_envelope_with_a_schema_mistake_also_gets_the_second_repair(router):
    def native(parameters):
        content = "<tool_call>\n<function=lookup>\n" + "".join(
            f"<parameter={key}>\n{value}\n</parameter>\n" for key, value in parameters.items()) + "</function>\n</tool_call>"
        return {"id": "native", "model": "Concrete.gguf", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": content}}]}
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object",
              "required": ["key", "limit"], "properties": {"key": {"type": "string"}, "limit": {"type": "integer"}}}}}]
    response, sent = sequence(router, [native({"key": SECRET}), native({"key": "ok", "limit": '"ten"'}),
                                       native({"key": "ok", "limit": 3})], stream=False, tools=tools)
    assert response.status_code == 200 and len(sent) == 3
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "lookup"
    assert "at the top level: it is missing the required property limit" in sent[1]["messages"][-1]["content"]
    assert "at limit: the value must be of type integer" in sent[2]["messages"][-1]["content"]
    assert all(SECRET not in body["messages"][-1]["content"] for body in sent[1:])
