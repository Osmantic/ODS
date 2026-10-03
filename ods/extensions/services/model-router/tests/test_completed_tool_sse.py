"""Contract tests for assemble_chat_completion_sse."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from app.completed_tool_sse import assemble_chat_completion_sse  # noqa: E402

MODEL = "Qwen2.5-35B-Instruct"


def _frame(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj) + "\n\n").encode("utf-8")


def _done() -> bytes:
    return b"data: [DONE]\n\n"


def _base(**kw) -> dict:
    d = {"id": "chatcmpl-1", "object": "chat.completion.chunk",
         "created": 1700000000, "model": MODEL}
    d.update(kw)
    return d


def _chunk(delta: dict, finish=None, index=0) -> dict:
    return _base(choices=[{"index": index, "delta": delta, "finish_reason": finish}])


def test_fragmented_tool_call_with_usage():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                       "function": {"name": "add_numbers", "arguments": ""}}]})),
        _frame(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"a\":"}}]})),
        _frame(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": "1,\"b\":2}"}}]})),
        _frame(_chunk({}, finish="tool_calls")),
        _frame(_base(choices=[], usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12})),
        _done(),
    ])
    out = assemble_chat_completion_sse(raw, MODEL)
    assert out["id"] == "chatcmpl-1"
    assert out["created"] == 1700000000
    assert out["model"] == MODEL
    assert out["usage"]["total_tokens"] == 12
    ch = out["choices"][0]
    assert ch["index"] == 0
    assert ch["finish_reason"] == "tool_calls"
    msg = ch["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] is None
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "add_numbers"
    assert json.loads(tc["function"]["arguments"]) == {"a": 1, "b": 2}


def test_content_response():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"content": "Hel"})),
        _frame(_chunk({"content": "lo"})),
        _frame(_chunk({}, finish="stop")),
        _done(),
    ])
    out = assemble_chat_completion_sse(raw, MODEL)
    ch = out["choices"][0]
    assert ch["finish_reason"] == "stop"
    assert ch["message"]["content"] == "Hello"
    assert "tool_calls" not in ch["message"]


def test_thinking_tool_call_preserves_reasoning_and_usage():
    raw = b"".join([
        _frame(_chunk({"role": "assistant", "reasoning_content": "First "})),
        _frame(_chunk({"reasoning_content": "check."})),
        _frame(_chunk({"tool_calls": [{"index": 0, "id": "call_1",
            "type": "function", "function": {"name": "add_numbers",
            "arguments": '{"a":17,"b":19}'}}]})),
        _frame(_chunk({}, finish="tool_calls")),
        _frame(_base(choices=[], usage={"prompt_tokens": 9,
            "completion_tokens": 6, "total_tokens": 15})),
        _done(),
    ])
    out = assemble_chat_completion_sse(raw, MODEL)
    message = out["choices"][0]["message"]
    assert message["reasoning_content"] == "First check."
    assert message["tool_calls"][0]["function"]["name"] == "add_numbers"
    assert out["usage"]["total_tokens"] == 15


def test_thinking_length_preserved_for_caller_to_reject_as_tool_decision():
    raw = b"".join([
        _frame(_chunk({"role": "assistant", "reasoning_content": "unfinished"})),
        _frame(_chunk({}, finish="length")),
        _done(),
    ])
    out = assemble_chat_completion_sse(raw, MODEL)
    assert out["choices"][0]["finish_reason"] == "length"
    assert out["choices"][0]["message"]["reasoning_content"] == "unfinished"


@pytest.mark.parametrize("delta,match", [
    ({"reasoning_content": 1}, "reasoning_content not string"),
    ({"refusal": "cannot comply"}, "unsupported delta field"),
    ({"reasoning": "unverified key"}, "unsupported delta field"),
])
def test_unverified_or_invalid_thinking_fields_rejected(delta, match):
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk(delta)),
        _frame(_chunk({}, finish="stop")),
        _done(),
    ])
    with pytest.raises(ValueError, match=match):
        assemble_chat_completion_sse(raw, MODEL)


def test_reasoning_after_finish_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({}, finish="stop")),
        _frame(_chunk({"reasoning_content": "late"})),
        _done(),
    ])
    with pytest.raises(ValueError, match="reasoning delta after finish_reason"):
        assemble_chat_completion_sse(raw, MODEL)


def test_usage_on_terminal_choice_is_preserved():
    terminal = _chunk({}, finish="stop")
    terminal["usage"] = {"prompt_tokens": 3, "completion_tokens": 2,
                         "total_tokens": 5}
    raw = b"".join([
        _frame(_chunk({"role": "assistant", "content": "ok"})),
        _frame(terminal),
        _done(),
    ])
    out = assemble_chat_completion_sse(raw, MODEL)
    assert out["usage"] == terminal["usage"]


def test_usage_on_preterminal_choice_is_rejected():
    premature = _chunk({"role": "assistant", "content": "partial"})
    premature["usage"] = {"prompt_tokens": 999,
                          "completion_tokens": 999, "total_tokens": 1998}
    raw = b"".join([
        _frame(premature),
        _frame(_chunk({}, finish="stop")),
        _done(),
    ])
    with pytest.raises(ValueError, match="invalid usage frame"):
        assemble_chat_completion_sse(raw, MODEL)


def test_truncated_stream_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        b"data: " + json.dumps(_chunk({"content": "partial"})).encode("utf-8"),
    ])
    with pytest.raises(ValueError, match="truncated"):
        assemble_chat_completion_sse(raw, MODEL)


def test_missing_done_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"content": "hi"})),
        _frame(_chunk({}, finish="stop")),
    ])
    with pytest.raises(ValueError, match="DONE"):
        assemble_chat_completion_sse(raw, MODEL)


def test_model_mismatch_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"content": "hi"})),
        _frame(_chunk({}, finish="stop")),
        _done(),
    ]).replace(MODEL.encode(), b"other-model")
    with pytest.raises(ValueError, match="model mismatch"):
        assemble_chat_completion_sse(raw, MODEL)


def test_lemonade_gguf_filename_identity_is_one_way_and_stream_consistent():
    selected = "Qwen3.6-35B-A3B-UD-Q4_K_M"
    wire = selected + ".gguf"
    raw = b"".join([
        _frame(_base(model=wire, choices=[{"index": 0, "delta": {
            "role": "assistant"}, "finish_reason": None}])),
        _frame(_base(model=wire, choices=[{"index": 0, "delta": {
            "tool_calls": [{"index": 0, "id": "call-1", "type": "function",
                            "function": {"name": "add_numbers",
                                         "arguments": '{"a":1,"b":2}'}}]},
            "finish_reason": None}])),
        _frame(_base(model=wire, choices=[{"index": 0, "delta": {},
                                          "finish_reason": "tool_calls"}])),
        _done(),
    ])
    with pytest.raises(ValueError, match="model mismatch"):
        assemble_chat_completion_sse(raw, selected)
    complete = assemble_chat_completion_sse(
        raw, selected, allow_gguf_filename_alias=True)
    assert complete["model"] == selected
    assert complete["choices"][0]["message"]["tool_calls"][0][
        "function"]["name"] == "add_numbers"
    with pytest.raises(ValueError, match="model mismatch"):
        assemble_chat_completion_sse(
            raw.replace(wire.encode(), b"OtherModel.gguf"),
            selected, allow_gguf_filename_alias=True)
    with pytest.raises(ValueError, match="model mismatch"):
        assemble_chat_completion_sse(
            raw.replace(wire.encode(), (selected + ".gguf.gguf").encode()),
            selected, allow_gguf_filename_alias=True)
    with pytest.raises(ValueError, match="model mismatch"):
        assemble_chat_completion_sse(
            raw.replace(wire.encode(), selected.encode()),
            wire, allow_gguf_filename_alias=True)
    with pytest.raises(ValueError, match="model changed within stream"):
        assemble_chat_completion_sse(
            raw.replace(wire.encode(), selected.encode(), 1),
            selected, allow_gguf_filename_alias=True)


def test_captured_strixy_lemonade_tool_stream_replays_with_selected_model():
    raw = (Path(__file__).parent / "fixtures" /
           "strixy_lemonade_gguf_tool_stream.sse").read_bytes()
    selected = "Qwen3.6-35B-A3B-UD-Q4_K_M"
    complete = assemble_chat_completion_sse(
        raw, selected, allow_gguf_filename_alias=True)
    assert complete["model"] == selected
    assert complete["choices"][0]["finish_reason"] == "tool_calls"
    call = complete["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "add_numbers"
    assert json.loads(call["function"]["arguments"]) == {"a": 17, "b": 19}
    assert complete["usage"]["total_tokens"] == 336


def test_malformed_tool_arguments_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                       "function": {"name": "f", "arguments": "{not json"}}]})),
        _frame(_chunk({}, finish="tool_calls")),
        _done(),
    ])
    with pytest.raises(ValueError, match="invalid tool arguments"):
        assemble_chat_completion_sse(raw, MODEL)


def test_terminal_after_content_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"content": "hi"})),
        _frame(_chunk({}, finish="stop")),
        _frame(_chunk({"content": "late"})),
        _done(),
    ])
    with pytest.raises(ValueError, match="after finish_reason"):
        assemble_chat_completion_sse(raw, MODEL)


def test_size_bound_rejected_before_decode():
    raw = b"x" * 100
    with pytest.raises(ValueError, match="max_bytes"):
        assemble_chat_completion_sse(raw, MODEL, max_bytes=10)


def test_tool_calls_without_finish_tool_calls_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                       "function": {"name": "f", "arguments": "{}"}}]})),
        _frame(_chunk({}, finish="stop")),
        _done(),
    ])
    with pytest.raises(ValueError, match="tool_calls"):
        assemble_chat_completion_sse(raw, MODEL)


def test_finish_tool_calls_with_no_calls_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({}, finish="tool_calls")),
        _done(),
    ])
    with pytest.raises(ValueError, match="no tool calls"):
        assemble_chat_completion_sse(raw, MODEL)


def test_event_after_done_rejected():
    raw = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"content": "hi"})),
        _frame(_chunk({}, finish="stop")),
        _done(),
        _frame(_chunk({"content": "late"})),
    ])
    with pytest.raises(ValueError, match="after \\[DONE\\]"):
        assemble_chat_completion_sse(raw, MODEL)


def test_top_level_error_rejected():
    raw = b"".join([
        _frame({"error": {"message": "boom"}}),
        _done(),
    ])
    with pytest.raises(ValueError, match="top-level error"):
        assemble_chat_completion_sse(raw, MODEL)


def test_crlf_line_endings_accepted():
    body = b"".join([
        _frame(_chunk({"role": "assistant"})),
        _frame(_chunk({"content": "hi"})),
        _frame(_chunk({}, finish="stop")),
        _done(),
    ])
    raw = body.replace(b"\n", b"\r\n")
    out = assemble_chat_completion_sse(raw, MODEL)
    assert out["choices"][0]["message"]["content"] == "hi"


def test_refusal_delta_requires_complete_response_fallback():
    raw = b"".join([
        _frame(_chunk({"role": "assistant", "refusal": "Cannot comply"})),
        _frame(_chunk({}, finish="stop")),
        _done(),
    ])
    with pytest.raises(ValueError, match="unsupported delta field"):
        assemble_chat_completion_sse(raw, MODEL)
