#!/usr/bin/env python3
"""Token Spy request-filter contracts.

The history filter rewrites the `messages` array before it reaches
llama-server. Whatever it emits must still be a valid OpenAI chat request:
every `role: "tool"` message has to answer a `tool_calls` entry on an earlier
assistant message, or the upstream returns 400 and the whole request is lost.

Run: python3 tests/test-token-spy-filters.py
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FILTERS_PY = ROOT / "extensions" / "services" / "token-spy" / "filters.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")
        if detail:
            print(f"       {detail}")


def load_filters():
    spec = importlib.util.spec_from_file_location("token_spy_filters", FILTERS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tool_chain_error(messages: list[dict]) -> str:
    """Return why *messages* is an invalid tool chain, or "" when it is valid."""
    announced: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                announced.add(call.get("id"))
        elif role == "tool":
            call_id = msg.get("tool_call_id")
            if call_id not in announced:
                return f"tool message {call_id!r} answers no preceding tool_calls"
    return ""


def turn(index: int, padding: int = 0) -> list[dict]:
    """One user turn that goes through a tool call and back."""
    return [
        {"role": "user", "content": f"u{index}" + "x" * padding},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{index}",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": f"call_{index}", "content": f"r{index}"},
        {"role": "assistant", "content": f"a{index}"},
    ]


def run(filters, messages: list[dict], history_cfg: dict):
    body = {"model": "test", "messages": copy.deepcopy(messages)}
    settings = {"enabled": True, "history": dict({"enabled": True}, **history_cfg)}
    return filters.apply_filters(body, settings)


def main() -> int:
    filters = load_filters()

    # --- the regression: trimming by size used to split a unit ---------------
    single = turn(1)
    body, _ = run(filters, single, {"always_keep_last_n": 2, "max_total_chars": 150})
    check(
        "a lone oversized turn is kept whole rather than sliced",
        body["messages"] == single,
        f"got {body['messages']}",
    )
    check(
        "a lone oversized turn stays a valid tool chain",
        tool_chain_error(body["messages"]) == "",
        tool_chain_error(body["messages"]),
    )
    check(
        "the request never opens with an orphaned tool reply",
        body["messages"][0].get("role") != "tool",
        f"first message is {body['messages'][0].get('role')!r}",
    )

    # --- multi-turn: whole turns leave, partial turns never do ---------------
    many = turn(1, padding=400) + turn(2, padding=400) + turn(3)
    body, result = run(
        filters, many, {"always_keep_last_n": 2, "max_total_chars": 400}
    )
    check(
        "oversized history is trimmed",
        len(body["messages"]) < len(many),
        f"kept {len(body['messages'])} of {len(many)}",
    )
    check(
        "trimmed history is still a valid tool chain",
        tool_chain_error(body["messages"]) == "",
        tool_chain_error(body["messages"]),
    )
    check(
        "only whole turns are dropped",
        len(body["messages"]) % 4 == 0,
        f"kept {len(body['messages'])} messages — not a whole number of turns",
    )
    check(
        "the most recent turn survives",
        body["messages"][-1] == {"role": "assistant", "content": "a3"},
        f"last message is {body['messages'][-1]}",
    )
    check(
        "messages_removed counts every dropped message",
        result.messages_removed == len(many) - len(body["messages"]),
        f"reported {result.messages_removed}, actually dropped "
        f"{len(many) - len(body['messages'])}",
    )
    check(
        "messages_kept matches the emitted array",
        result.messages_kept == len(body["messages"]),
        f"reported {result.messages_kept}, emitted {len(body['messages'])}",
    )

    # --- always_keep_last_n still bounds how far back trimming reaches ------
    body, _ = run(
        filters,
        turn(1, padding=400) + turn(2, padding=400),
        {"always_keep_last_n": 8, "max_total_chars": 10},
    )
    check(
        "always_keep_last_n stops the trim before the protected tail",
        len(body["messages"]) == 8,
        f"kept {len(body['messages'])}, expected 8",
    )

    # --- system messages are not part of the trimmed conversation -----------
    with_system = [{"role": "system", "content": "you are helpful"}] + turn(
        1, padding=400
    ) + turn(2)
    body, _ = run(
        filters, with_system, {"always_keep_last_n": 2, "max_total_chars": 200}
    )
    check(
        "the system prompt is preserved and stays first",
        body["messages"][0] == {"role": "system", "content": "you are helpful"},
        f"first message is {body['messages'][0]}",
    )
    check(
        "the oldest turn is dropped, not the system prompt",
        len(body["messages"]) == 5,
        f"kept {len(body['messages'])}, expected system + one turn",
    )

    # --- no max_total_chars: nothing is trimmed by size ----------------------
    untouched = turn(1, padding=4000)
    body, result = run(filters, untouched, {"always_keep_last_n": 2})
    check(
        "history is untouched when max_total_chars is unset",
        body["messages"] == untouched and result.messages_removed == 0,
        f"removed {result.messages_removed}",
    )

    # --- filters disabled: body is returned as-is ---------------------------
    original = turn(1)
    body = {"model": "test", "messages": copy.deepcopy(original)}
    body, result = filters.apply_filters(body, {"enabled": False})
    check(
        "a disabled filter set is a no-op",
        body["messages"] == original and result.messages_removed == 0,
        f"got {body['messages']}",
    )

    print()
    print(f"Passed: {PASS}  Failed: {FAIL}")
    if FAIL:
        return 1
    print("[PASS] token-spy request filter contracts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
