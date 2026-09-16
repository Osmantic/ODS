"""Public request-filter regressions for tool-only assistant messages."""
import copy

import pytest

from test_filters import apply_filters


def conversation(assistant_fields):
    return [
        {"role": "system", "content": "Keep this policy."},
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "tool_calls": [
            {"id": "old-call", "type": "function",
             "function": {"name": "lookup", "arguments": "{}"}}
        ], **assistant_fields},
        {"role": "tool", "tool_call_id": "old-call", "content": "Old result"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Current question"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "current-call", "type": "function",
             "function": {"name": "lookup", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "current-call", "content": "Current result"},
    ]


def filtered(messages, enabled=True):
    return apply_filters({"model": "test", "messages": copy.deepcopy(messages)}, {
        "enabled": True,
        "history": {"enabled": True, "drop_old_tool_calls": enabled,
                    "drop_old_tool_calls_after_pairs": 1, "always_keep_last_n": 0},
    })


@pytest.mark.parametrize("fields", [{}, {"content": None}])
def test_retiring_tool_calls_removes_assistant_with_no_remaining_payload(fields):
    original = conversation(fields)
    body, metrics = filtered(original)
    assert body["messages"] == original[:2] + original[4:]
    assert metrics.messages_removed == 2
    assert metrics.messages_kept == len(body["messages"])
    assert metrics.tool_chains_dropped == 2
    assert original[2]["tool_calls"][0]["id"] == "old-call"


@pytest.mark.parametrize("fields", [
    {"content": "Looking that up."},
    {"content": ""},
    {"content": [{"type": "text", "text": "Looking that up."}]},
    {"content": None, "refusal": "Cannot help with part of that request."},
    {"content": None, "audio": {"id": "audio-reply"}},
    {"content": None, "function_call": {"name": "legacy", "arguments": "{}"}},
])
def test_retiring_tools_preserves_other_assistant_payloads(fields):
    original = conversation(fields)
    body, metrics = filtered(original)
    assert body["messages"][2] == {"role": "assistant", **fields}
    assert body["messages"][-3:] == original[-3:]
    assert metrics.messages_removed == 1


def test_disabled_retirement_preserves_tool_only_assistant_and_results():
    original = conversation({"content": None})
    body, metrics = filtered(original, enabled=False)
    assert body["messages"] == original
    assert metrics.messages_removed == 0
