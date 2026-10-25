from __future__ import annotations

import asyncio
import json

import httpx

from test_router import router as _router_fixture

router = _router_fixture


NEMO = "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf"
QWEN = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"


def _is_strict9(value: str) -> bool:
    return isinstance(value, str) and len(value) == 9 and value.isascii() and value.isalnum()


def _history_ids_ok(messages) -> bool:
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        tcs = msg.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                if isinstance(tc, dict) and not _is_strict9(tc.get("id")):
                    return False
        if msg.get("role") == "tool":
            if not _is_strict9(msg.get("tool_call_id")):
                return False
    return True


def _install_upstream(mod, calls, *, reject_bad_ids: bool):
    """Replace the fixture's MockTransport with one that captures full JSON
    and (optionally) simulates Nemo's 400 on non-strict9 history ids."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append({"url": str(request.url), "body": body})
        model = body.get("model")
        if reject_bad_ids and model == NEMO and not _history_ids_ok(body.get("messages")):
            return httpx.Response(400, json={"error": {
                "message": "tool_call_id must be 9 alphanumeric characters",
                "type": "invalid_request_error",
            }})
        if body.get("stream"):
            frames = [
                {"id": "c1", "model": model, "created": 1,
                 "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
                {"id": "c1", "model": model, "created": 1,
                 "choices": [{"index": 0, "delta": {"content": "hi"}}]},
                {"id": "c1", "model": model, "created": 1,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            sse = b"".join(
                b"data: " + json.dumps(f).encode() + b"\n\n" for f in frames
            ) + b"data: [DONE]\n\n"
            return httpx.Response(200, content=sse,
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "c1", "model": model, "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant",
                                                   "content": "hello"},
                          "finish_reason": "stop"}],
        })

    old = mod.app.state.http
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(old.aclose())


def _nemo_state(write_state, *, backend_kind="llama-server", runtime=NEMO, route_seq=7):
    def mutate(doc):
        doc["active"]["runtimeModelId"] = runtime
        doc["active"]["backend"]["kind"] = backend_kind
        doc["active"]["proof"]["identity"] = runtime
    write_state(runtime=runtime, mutate=mutate, route_seq=route_seq)


def _bad_history():
    return [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "bZJ86vYjPtftKihZKZweE3wjFwbdfACD", "type": "function",
             "function": {"name": "read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "bZJ86vYjPtftKihZKZweE3wjFwbdfACD", "content": "ok"},
    ]


def _read_tool():
    return [{"type": "function", "function": {
        "name": "read", "description": "read",
        "parameters": {"type": "object", "properties": {}},
    }}]


def _post(client, body):
    return client.post("/v1/chat/completions", json=body,
                       headers={"authorization": "Bearer internal-secret"})


def test_nemo_nonstream_normalizes_history_and_preserves_original(router):
    mod, client, write_state, calls = router
    _install_upstream(mod, calls, reject_bad_ids=True)
    _nemo_state(write_state)
    history = _bad_history()
    original = json.loads(json.dumps(history))
    resp = _post(client, {"model": "ods/current", "messages": history,
                          "tools": _read_tool(), "tool_choice": "none",
                          "stream": False})
    assert resp.status_code == 200, resp.text
    assert history == original
    assert calls and _history_ids_ok(calls[-1]["body"]["messages"])
    data = resp.json()
    assert data["model"] == "ods/current"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "hello"


def test_nemo_stream_normalizes_history_and_preserves_original(router):
    mod, client, write_state, calls = router
    _install_upstream(mod, calls, reject_bad_ids=True)
    _nemo_state(write_state)
    history = _bad_history()
    original = json.loads(json.dumps(history))
    resp = _post(client, {"model": "ods/current", "messages": history,
                          "tools": _read_tool(), "tool_choice": "none",
                          "stream": True})
    assert resp.status_code == 200, resp.text
    assert history == original
    assert calls and _history_ids_ok(calls[-1]["body"]["messages"])
    text = resp.text
    assert "data: [DONE]" in text
    assert '"role": "assistant"' in text or '"role":"assistant"' in text
    assert '"content": "hi"' in text or '"content":"hi"' in text
    assert '"finish_reason": "stop"' in text or '"finish_reason":"stop"' in text


def test_new_tool_call_ids_not_mapped_back_to_history(router):
    mod, client, write_state, calls = router

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append({"url": str(request.url), "body": body})
        if not _history_ids_ok(body.get("messages")):
            return httpx.Response(400, json={"error": {"message": "bad id"}})
        return httpx.Response(200, json={
            "id": "c1", "model": body.get("model"), "created": 1,
            "choices": [{"index": 0, "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "NEWTOOL01", "type": "function",
                                 "function": {"name": "read",
                                              "arguments": "{}"}}],
            }, "finish_reason": "tool_calls"}],
        })

    old = mod.app.state.http
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(old.aclose())
    _nemo_state(write_state)
    resp = _post(client, {"model": "ods/current", "messages": _bad_history(),
                          "tools": _read_tool(), "stream": False})
    assert resp.status_code == 200, resp.text
    tcs = resp.json()["choices"][0]["message"]["tool_calls"]
    assert tcs[0]["id"] == "NEWTOOL01"


def test_route_change_qwen_to_nemo_and_back_is_atomic(router):
    mod, client, write_state, calls = router
    _install_upstream(mod, calls, reject_bad_ids=True)

    def qwen(doc):
        doc["active"]["runtimeModelId"] = QWEN
        doc["active"]["proof"]["identity"] = QWEN

    write_state(runtime=QWEN, mutate=qwen)
    r1 = _post(client, {"model": "ods/current", "messages": _bad_history(),
                        "tools": _read_tool(), "stream": False})
    assert r1.status_code == 200, r1.text
    assert not _history_ids_ok(calls[-1]["body"]["messages"])

    _nemo_state(write_state, route_seq=8)
    r2 = _post(client, {"model": "ods/current", "messages": _bad_history(),
                        "tools": _read_tool(), "stream": False})
    assert r2.status_code == 200, r2.text
    assert _history_ids_ok(calls[-1]["body"]["messages"])

    write_state(runtime=QWEN, mutate=qwen, route_seq=9)
    r3 = _post(client, {"model": "ods/current", "messages": _bad_history(),
                        "tools": _read_tool(), "stream": False})
    assert r3.status_code == 200, r3.text
    assert not _history_ids_ok(calls[-1]["body"]["messages"])


def test_hipfire_backend_no_format_mapping(router):
    mod, client, write_state, calls = router
    _install_upstream(mod, calls, reject_bad_ids=False)
    _nemo_state(write_state, backend_kind="hipfire")
    resp = _post(client, {"model": "ods/current", "messages": _bad_history(),
                          "tools": _read_tool(), "stream": False})
    assert resp.status_code == 200, resp.text
    assert not _history_ids_ok(calls[-1]["body"]["messages"])


def test_orphan_history_passes_through_unchanged(router):
    mod, client, write_state, calls = router
    _install_upstream(mod, calls, reject_bad_ids=True)
    _nemo_state(write_state)
    orphan = [
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "orphan_xx", "content": "ok"},
    ]
    original = json.loads(json.dumps(orphan))
    resp = _post(client, {"model": "ods/current", "messages": orphan,
                          "tools": _read_tool(), "stream": False})
    assert resp.status_code == 400
    assert orphan == original
    assert calls[-1]["body"]["messages"] == original
