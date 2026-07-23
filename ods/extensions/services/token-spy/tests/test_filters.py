"""Tests for the token-spy request filters.

The history filter trims a live chat request before it reaches llama-server.
Its own comment states the invariant: an assistant message carrying tool_calls
and the tool results answering it are one atomic unit and "we must not split
these or the API contract breaks".
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOKEN_SPY_DIR))

from filters import apply_filters  # noqa: E402


def _tool_call_conversation():
    """system + three units, the first of which is a tool-call exchange."""
    return {
        "messages": [
            {"role": "system", "content": "you are a helpful assistant"},
            {"role": "user", "content": "what is the weather?"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "weather", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "x" * 400},
            {"role": "assistant", "content": "It is sunny."},
            {"role": "user", "content": "and tomorrow?"},
            {"role": "assistant", "content": "Also sunny."},
            {"role": "user", "content": "thanks"},
        ],
    }


def _orphaned_tool_indexes(messages):
    """Tool messages with no preceding assistant tool_calls."""
    orphans = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        has_parent = any(
            prev.get("role") == "assistant" and prev.get("tool_calls")
            for prev in messages[:i]
        )
        if not has_parent:
            orphans.append(i)
    return orphans


def _run(body, history_cfg):
    return apply_filters(
        copy.deepcopy(body),
        {"enabled": True, "history": {"enabled": True, **history_cfg}},
    )


class TestHistoryKeepsToolChainsIntact:
    def test_max_total_chars_never_orphans_a_tool_result(self):
        """Trimming by size must drop whole units.

        620 is the size at which the old message-at-a-time pop stopped right
        after removing the assistant tool_calls turn but before its result.
        """
        out, _ = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "always_keep_last_n": 2,
            "max_total_chars": 620,
        })

        assert _orphaned_tool_indexes(out["messages"]) == [], (
            "a tool result survived without the assistant tool_calls it answers: "
            f"{[m['role'] for m in out['messages']]}"
        )

    @pytest.mark.parametrize("max_total", [150, 300, 450, 620, 700])
    def test_no_orphans_at_any_size_boundary(self, max_total):
        out, _ = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "always_keep_last_n": 2,
            "max_total_chars": max_total,
        })
        assert _orphaned_tool_indexes(out["messages"]) == []

    def test_max_total_chars_still_trims(self):
        body = _tool_call_conversation()
        before = len(json.dumps(body, separators=(",", ":")))
        out, result = _run(body, {
            "always_keep_system": True,
            "always_keep_last_n": 1,
            "max_total_chars": 300,
        })
        after = len(json.dumps(out, separators=(",", ":")))
        assert after < before
        assert result.messages_removed > 0

    def test_system_messages_are_preserved(self):
        out, _ = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "always_keep_last_n": 1,
            "max_total_chars": 200,
        })
        assert out["messages"][0]["role"] == "system"


class TestAlwaysKeepLastN:
    def test_max_pairs_does_not_cut_below_the_floor(self):
        """always_keep_last_n is documented as a floor for every trim path."""
        out, _ = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "always_keep_last_n": 6,
            "max_pairs": 1,
        })
        conv = [m for m in out["messages"] if m["role"] not in ("system", "developer")]
        assert len(conv) >= 6, (
            f"always_keep_last_n=6 but only {len(conv)} conversation messages kept"
        )

    def test_max_pairs_still_trims_when_the_floor_allows(self):
        out, result = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "always_keep_last_n": 2,
            "max_pairs": 2,
        })
        conv = [m for m in out["messages"] if m["role"] != "system"]
        assert len(conv) == 3  # the two most recent units
        assert result.messages_removed == 4

    def test_max_total_chars_does_not_cut_below_the_floor(self):
        out, _ = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "always_keep_last_n": 7,
            "max_total_chars": 10,
        })
        conv = [m for m in out["messages"] if m["role"] != "system"]
        assert len(conv) == 7


class TestDropOldToolCalls:
    def test_stripped_assistant_turn_is_not_left_empty(self):
        """A pure tool-call turn has content: null.

        Removing tool_calls from it leaves a message with neither content nor
        tool_calls, which is not a valid assistant turn.
        """
        body = _tool_call_conversation()
        out, _ = _run(body, {
            "always_keep_system": True,
            "always_keep_last_n": 0,
            "drop_old_tool_calls": True,
            "drop_old_tool_calls_after_pairs": 1,
        })
        empties = [
            m for m in out["messages"]
            if m.get("role") == "assistant"
            and not m.get("content") and not m.get("tool_calls")
        ]
        assert empties == []

    def test_tool_results_are_dropped_with_their_call(self):
        out, result = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "always_keep_last_n": 0,
            "drop_old_tool_calls": True,
            "drop_old_tool_calls_after_pairs": 1,
        })
        assert not any(m["role"] == "tool" for m in out["messages"])
        assert result.tool_chains_dropped > 0
        assert _orphaned_tool_indexes(out["messages"]) == []


class TestUnchangedBehaviour:
    def test_disabled_filters_are_a_noop(self):
        body = _tool_call_conversation()
        out, result = apply_filters(copy.deepcopy(body), {"enabled": False})
        assert out == body
        assert result.messages_removed == 0

    def test_tool_results_are_truncated(self):
        out, result = _run(_tool_call_conversation(), {
            "always_keep_system": True,
            "truncate_tool_results_chars": 50,
        })
        tool_msg = next(m for m in out["messages"] if m["role"] == "tool")
        assert "truncated from 400" in tool_msg["content"]
        assert result.tool_results_truncated == 1

    def test_tool_schemas_are_filtered_by_blocklist(self):
        body = {
            "tools": [
                {"function": {"name": "keep_me"}},
                {"function": {"name": "drop_me"}},
            ],
            "messages": [{"role": "user", "content": "hi"}],
        }
        out, result = apply_filters(copy.deepcopy(body), {
            "enabled": True,
            "tools": {"enabled": True, "mode": "blocklist", "blocklist": ["drop_me"]},
        })
        assert [t["function"]["name"] for t in out["tools"]] == ["keep_me"]
        assert result.tools_removed == 1
        assert result.tools_kept == 1
