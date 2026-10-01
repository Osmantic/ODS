"""A de-streamed tool decision is bounded by its own generation budget.

llama-server answers a non-streamed Chat request only when the whole
completion exists, so the router's read timeout is the total generation time.
The simulated backend below honours the read timeout the router sends: it
raises ``ReadTimeout`` when its generation would outlast it. A router clock
advances by the simulated generation time, so repair budgets are measured
without waiting.
"""
import asyncio
import json
import time

import httpx
import pytest
from test_router import router as router  # noqa: F401

TOOLS = [{"type": "function", "function": {"name": "write", "parameters": {
    "type": "object", "required": ["path"],
    "properties": {"path": {"type": "string"}}}}}]
# About 8,192 output tokens at 13.5 tokens/s plus prompt processing: a
# length-capped page write on a slower local host.
LONG_DECISION_SECONDS = 640


class _Clock:
    """The router's time module, advanced only by simulated generation."""

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return time.monotonic() + self.offset

    def __getattr__(self, name):
        return getattr(time, name)


def _completion(message, finish_reason, completion_tokens):
    return {"id": "backend-call", "object": "chat.completion", "created": 1,
            "model": "Concrete.gguf",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 900, "completion_tokens": completion_tokens,
                      "total_tokens": 900 + completion_tokens}}


LENGTH_CAPPED = _completion(
    {"role": "assistant", "content": "<!doctype html><html><body>"},
    "length", 8192)
NATIVE_UNKNOWN_TOOL = _completion(
    {"role": "assistant", "content": (
        "<tool_call>\n<function=pixel_ods_write>\n<parameter=path>\n"
        "index.html\n</parameter>\n</function>\n</tool_call>")},
    "stop", 40)
REPAIRED_CALL = _completion(
    {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function",
        "function": {"name": "write", "arguments": '{"path":"index.html"}'}}]},
    "tool_calls", 20)


def _use_backend(mod, monkeypatch, steps):
    """steps: (generation seconds, completed response) per backend request."""
    clock = _Clock()
    monkeypatch.setattr(mod, "time", clock)
    sent = []

    def handler(request):
        seconds, completion = steps[len(sent)]
        timeout = request.extensions["timeout"]
        sent.append({"body": json.loads(request.content), "timeout": timeout})
        if seconds > timeout["read"]:
            clock.offset += timeout["read"]
            raise httpx.ReadTimeout("no response bytes yet", request=request)
        clock.offset += seconds
        return httpx.Response(200, json=completion)

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return sent


def _tool_stream_request():
    return {"model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "Make a one-page site."}],
            "tools": TOOLS}


def _sse_chunks(response):
    frames = [frame for frame in response.text.split("\n\n") if frame]
    assert frames[-1] == "data: [DONE]"
    return [json.loads(frame.removeprefix("data: ")) for frame in frames[:-1]]


def test_long_tool_decision_is_not_cut_at_the_plain_upstream_timeout(
        router, monkeypatch):
    mod, client, write_state, _calls = router
    write_state()
    sent = _use_backend(mod, monkeypatch, [(LONG_DECISION_SECONDS, LENGTH_CAPPED)])

    response = client.post("/v1/chat/completions", json=_tool_stream_request())

    assert response.status_code == 200
    assert "x-should-retry" not in response.headers
    chunks = _sse_chunks(response)
    assert chunks[-1]["choices"][0]["finish_reason"] == "length"
    assert chunks[-1]["usage"]["completion_tokens"] == 8192
    assert len(sent) == 1 and sent[0]["body"]["stream"] is False
    # Only the wait for the completed decision is extended.
    assert sent[0]["timeout"]["read"] == mod.TOOL_COMPLETION_TIMEOUT_SECONDS == 1500
    assert sent[0]["timeout"]["connect"] == mod.UPSTREAM_TIMEOUT_SECONDS == 600


def test_repair_uses_what_remains_of_the_tool_decision_budget(router, monkeypatch):
    mod, client, write_state, _calls = router
    write_state()
    sent = _use_backend(mod, monkeypatch, [
        (700, NATIVE_UNKNOWN_TOOL), (100, REPAIRED_CALL)])

    response = client.post("/v1/chat/completions", json=_tool_stream_request())

    assert response.status_code == 200
    call = _sse_chunks(response)[0]["choices"][0]["delta"]["tool_calls"][0]
    assert call["function"] == {"name": "write", "arguments": '{"path":"index.html"}'}
    assert len(sent) == 2
    assert 799 < sent[1]["timeout"]["read"] <= 800


@pytest.mark.parametrize("case", ["plain-request", "tool-decision", "tool-repair"])
def test_upstream_timeout_tells_sdk_clients_not_to_regenerate(
        router, monkeypatch, case):
    mod, client, write_state, _calls = router
    write_state()
    if case == "plain-request":
        # Requests the router does not de-stream keep the existing budget.
        steps = [(LONG_DECISION_SECONDS, LENGTH_CAPPED)]
        request = {"model": "ods/current",
                   "messages": [{"role": "user", "content": "Write a long page."}]}
        message = "Upstream model runtime timed out"
    elif case == "tool-decision":
        steps = [(1600, LENGTH_CAPPED)]
        request = _tool_stream_request()
        message = "Upstream model runtime timed out"
    else:
        steps = [(1400, NATIVE_UNKNOWN_TOOL), (200, REPAIRED_CALL)]
        request = _tool_stream_request()
        message = "Upstream model runtime timed out during tool protocol repair"
    sent = _use_backend(mod, monkeypatch, steps)

    response = client.post("/v1/chat/completions", json=request)

    assert response.status_code == 504
    assert response.headers["x-should-retry"] == "false"
    assert response.headers["X-ODS-Routed-Model"] == "Concrete.gguf"
    assert response.json() == {"error": {
        "message": message, "type": "upstream_timeout", "code": "504"}}
    assert len(sent) == len(steps)
    assert "[DONE]" not in response.text
