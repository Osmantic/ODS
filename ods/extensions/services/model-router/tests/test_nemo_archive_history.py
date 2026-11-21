"""Regression from actual pinned SDK ingress -> import -> skipped compact wire.

The fixture's user blocks are captured synthetic Portal traffic, not a guessed
history shape. Its unrelated generated system prompt is replaced with a label.
The original request was independently rejected by llama.cpp b11429 applying
Mistral-Nemo's upstream template (04d8a90549d23fc6bd7f642064003592df51e9b3).
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from test_router import router as _router_fixture
from test_nemo_tool_history import (
    _nemo_state, _post, _history_ids_ok, _bad_history, _install_upstream, NEMO, QWEN,
)
from app.nemo_history_compat import coalesce_plain_user_messages

router = _router_fixture


def captured_history():
    return json.loads((Path(__file__).parent / "fixtures/nemo_archive_messages.json").read_text(encoding="utf-8"))


def text_blocks(content):
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


@pytest.mark.parametrize("stream", [False, True])
def test_import_after_native_skipped_compaction_reaches_nemo(router, stream):
    mod, client, write_state, calls = router
    _nemo_state(write_state)

    def upstream(request):
        body = json.loads(request.content)
        calls.append(body)
        # Independent live template gate reproduces this exact refusal on main.
        roles = [m["role"] for m in body["messages"]]
        if any(a == b == "user" for a, b in zip(roles, roles[1:])):
            return httpx.Response(400, json={"error": {"message": "conversation roles must alternate"}})
        if stream:
            frames = [{"id": "fixture", "created": 1, "model": NEMO, "choices": [
                {"index": 0, "delta": delta, "finish_reason": finish}]} for delta, finish in [
                    ({"role": "assistant", "content": "retained"}, None), ({}, "stop")]]
            data = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)
        return httpx.Response(200, json={"model": NEMO, "choices": [{"message": {"role": "assistant", "content": "retained"}}]})

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    history = captured_history()
    original = deepcopy(history)
    response = _post(client, {"model": "ods/current", "messages": history, "stream": stream,
        "tools": [{"type": "function", "function": {"name": "pixel_ods_history", "parameters": {"type": "object"}}}]})
    assert response.status_code == 200, response.text
    assert history == original
    assert calls[0]["messages"] == [history[0], {"role": "user", "content": [
        *text_blocks(history[1]["content"]), *text_blocks(history[2]["content"]),
    ]}]
    assert "retained" in response.text


@pytest.mark.parametrize("backend,runtime", [("llama-server", QWEN), ("hipfire", NEMO)])
def test_archive_projection_is_only_for_confirmed_nemo_llama_route(router, backend, runtime):
    mod, client, write_state, calls = router
    _nemo_state(write_state, backend_kind=backend, runtime=runtime)
    def upstream(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"model": runtime, "choices": []})
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    history = captured_history()
    assert _post(client, {"model": "ods/current", "messages": history}).status_code == 200
    assert calls[0]["messages"] == history


def test_adjacent_strings_and_typed_text_keep_every_block_in_order():
    history = [
        {"role": "system", "content": "Trusted policy"},
        {"role": "user", "content": "Earlier\nrequest 😀"},
        {"role": "user", "content": [{"type": "text", "text": "Archived response"}]},
        {"role": "user", "content": "Current request"},
        {"role": "assistant", "content": "A real answer"},
        {"role": "user", "content": "A separate turn"},
    ]
    original = deepcopy(history)
    projected, changed = coalesce_plain_user_messages(history)
    assert changed
    assert projected == [history[0], {"role": "user", "content": [
        {"type": "text", "text": "Earlier\nrequest 😀"},
        {"type": "text", "text": "Archived response"},
        {"type": "text", "text": "Current request"},
    ]}, *history[4:]]
    assert history == original
    assert coalesce_plain_user_messages(projected) == (projected, False)


@pytest.mark.parametrize("unsupported", [
    None,
    {"role": "user"},
    {"role": "user", "content": None},
    {"role": "user", "content": []},
    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "fixture"}}]},
    {"role": "user", "content": [{"type": "text", "text": 7}]},
    {"role": "user", "content": [{"type": "text", "text": "text", "cache_control": {"type": "ephemeral"}}]},
    {"role": "user", "content": "text", "name": "owner"},
    {"role": "user", "content": "text", "tool_calls": []},
])
def test_unknown_content_or_metadata_is_not_rewritten(unsupported):
    history = [*captured_history(), unsupported]
    original = deepcopy(history)
    projected, changed = coalesce_plain_user_messages(history)
    assert projected is history
    assert not changed
    assert history == original


def test_no_adjacent_user_turns_are_a_noop():
    history = [{"role": "user", "content": "Request"},
               {"role": "assistant", "content": "Answer"},
               {"role": "user", "content": "Followup"}]
    assert coalesce_plain_user_messages(history) == (history, False)


def test_archive_projection_keeps_strict9_tool_pairing_and_real_assistant_turns(router):
    mod, client, write_state, calls = router
    _nemo_state(write_state)
    _install_upstream(mod, calls, reject_bad_ids=True)
    history = [*captured_history(), *_bad_history()[1:],
               {"role": "assistant", "content": "Read complete."},
               {"role": "user", "content": "Verify without editing."}]
    original = deepcopy(history)
    response = _post(client, {"model": "ods/current", "messages": history})
    assert response.status_code == 200, response.text
    forwarded = calls[-1]["body"]["messages"]
    assert _history_ids_ok(forwarded)
    assert [message["role"] for message in forwarded] == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert forwarded[2]["tool_calls"][0]["id"] == forwarded[3]["tool_call_id"]
    assert forwarded[2]["tool_calls"][0]["function"] == history[3]["tool_calls"][0]["function"]
    assert forwarded[4:] == history[5:]
    assert history == original
