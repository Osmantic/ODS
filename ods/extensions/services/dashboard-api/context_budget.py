"""Context budgeting and KV-cache prefix reuse for native Windows local chat.

When chatting with llama-server.exe, sliding the context window by 1 message on
every turn invalidates the entire KV cache and forces llama.cpp to re-evaluate
thousands of prompt tokens on every follow-up message.

This module anchors the conversation window at ``context_start`` so that the
prompt prefix sent to llama-server stays byte-identical across turns until the
window fills. Only when the budgeted prompt exceeds the usable context window
does ``context_start`` advance to a clean user-turn boundary, reclaiming space
so the next several turns reuse the new prefix in <1s.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

DEFAULT_CONTEXT_WINDOW = 8192
MIN_CONTEXT_WINDOW = 2048
DEFAULT_RESERVE_OUTPUT_TOKENS = 1024
MIN_RESERVE_OUTPUT_TOKENS = 256
MESSAGE_FRAMING_TOKENS = 6


@dataclass(frozen=True)
class BudgetedContext:
    """Result of planning a conversation turn's prompt within a context window."""

    messages: list[dict[str, Any]]
    context_start: int
    estimated_prompt_tokens: int
    max_completion_tokens: int
    context_window: int
    usable_prompt_budget: int
    truncated: bool
    dropped_messages: int


def estimate_text_tokens(text: str) -> int:
    """Conservative token estimate for English/code/multilingual UTF-8 text."""
    if not text:
        return 0
    utf8_len = len(text.encode("utf-8", errors="replace"))
    # ~3.2 UTF-8 bytes per token on average for Qwen/Llama tokenizers; use 3.0
    # so we never underestimate and overflow llama-server's n_ctx.
    return max(1, math.ceil(utf8_len / 3.0))


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate the token cost of a single OpenAI-format chat message."""
    content = message.get("content", "")
    if isinstance(content, str):
        return MESSAGE_FRAMING_TOKENS + estimate_text_tokens(content)
    if isinstance(content, list):
        total = MESSAGE_FRAMING_TOKENS
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type", "")
            if part_type == "text":
                total += estimate_text_tokens(str(part.get("text", "")))
            elif part_type == "image_url":
                # Multimodal vision embeddings typically consume ~256-512 tokens
                total += 384
        return total
    return MESSAGE_FRAMING_TOKENS + estimate_text_tokens(str(content))


def _truncate_text_to_token_budget(text: str, max_tokens: int) -> str:
    """Truncate a single oversized message text so it fits within max_tokens."""
    if max_tokens <= 0:
        return ""
    if estimate_text_tokens(text) <= max_tokens:
        return text
    max_bytes = max(32, int(max_tokens * 2.8))
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    marker = "\n\n[... content truncated to fit model context window ...]\n\n"
    marker_bytes = len(marker.encode("utf-8"))
    keep_bytes = max(16, max_bytes - marker_bytes)
    head_bytes = int(keep_bytes * 0.7)
    tail_bytes = max(8, keep_bytes - head_bytes)
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
    return f"{head}{marker}{tail}"


def _truncate_message_to_budget(message: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """Return a copy of message truncated to fit within max_tokens."""
    content = message.get("content", "")
    text_budget = max(16, max_tokens - MESSAGE_FRAMING_TOKENS)
    if isinstance(content, str):
        return {
            **message,
            "content": _truncate_text_to_token_budget(content, text_budget),
        }
    if isinstance(content, list):
        new_parts = []
        remaining = text_budget
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                new_parts.append(part)
                remaining = max(16, remaining - 384)
            elif isinstance(part, dict) and part.get("type") == "text":
                truncated_text = _truncate_text_to_token_budget(str(part.get("text", "")), remaining)
                new_parts.append({**part, "text": truncated_text})
                remaining = max(16, remaining - estimate_text_tokens(truncated_text))
        return {**message, "content": new_parts}
    return message


def plan_context_budget(
    history: list[dict[str, Any]],
    *,
    system_prompt: str = "",
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    context_start: int = 0,
    reserve_output_tokens: int = DEFAULT_RESERVE_OUTPUT_TOKENS,
) -> BudgetedContext:
    """Plan the prompt messages and KV-cache anchor index for a chat turn.

    Parameters
    ----------
    history:
        Chronological user/assistant messages (excluding the system prompt).
    system_prompt:
        System instructions prepended at index 0 when non-empty.
    context_window:
        Total token capacity (`n_ctx`) of the active `llama-server.exe` runtime.
    context_start:
        Index into `history` where the current KV-cache prefix begins.
    reserve_output_tokens:
        Tokens reserved for the assistant's completion.
    """
    effective_window = max(MIN_CONTEXT_WINDOW, int(context_window or DEFAULT_CONTEXT_WINDOW))
    # Never reserve more than 35% of the context window for output
    effective_reserve = min(
        max(MIN_RESERVE_OUTPUT_TOKENS, int(reserve_output_tokens or DEFAULT_RESERVE_OUTPUT_TOKENS)),
        int(effective_window * 0.35),
    )

    system_msg: list[dict[str, Any]] = []
    system_tokens = 0
    if system_prompt and system_prompt.strip():
        sys_entry = {"role": "system", "content": system_prompt.strip()}
        system_msg = [sys_entry]
        system_tokens = estimate_message_tokens(sys_entry)

    usable_prompt_budget = max(256, effective_window - effective_reserve - system_tokens)

    clean_history = [
        {"role": str(m.get("role", "user")), "content": m.get("content", "")}
        for m in history
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    total_messages = len(clean_history)
    if total_messages == 0:
        return BudgetedContext(
            messages=list(system_msg),
            context_start=0,
            estimated_prompt_tokens=system_tokens,
            max_completion_tokens=effective_reserve,
            context_window=effective_window,
            usable_prompt_budget=usable_prompt_budget,
            truncated=False,
            dropped_messages=0,
        )

    anchor = max(0, min(int(context_start or 0), total_messages - 1))
    # Ensure anchor starts on a user message when possible so conversation role alternation stays valid
    while anchor < total_messages - 1 and clean_history[anchor]["role"] != "user":
        anchor += 1

    msg_costs = [estimate_message_tokens(m) for m in clean_history]
    window_tokens = sum(msg_costs[anchor:])

    if window_tokens <= usable_prompt_budget:
        selected = clean_history[anchor:]
        return BudgetedContext(
            messages=system_msg + selected,
            context_start=anchor,
            estimated_prompt_tokens=system_tokens + window_tokens,
            max_completion_tokens=effective_reserve,
            context_window=effective_window,
            usable_prompt_budget=usable_prompt_budget,
            truncated=anchor > 0,
            dropped_messages=anchor,
        )

    # The current prefix overflowed usable_prompt_budget.
    # Advance context_start to reclaim at least 25% headroom so the next several
    # follow-up turns reuse the new KV-cache prefix without shifting every turn.
    target_budget = max(192, int(usable_prompt_budget * 0.75))
    new_anchor = anchor
    while new_anchor < total_messages - 1 and sum(msg_costs[new_anchor:]) > target_budget:
        new_anchor += 1
        while new_anchor < total_messages - 1 and clean_history[new_anchor]["role"] != "user":
            new_anchor += 1

    # If even starting at the latest message exceeds usable_prompt_budget,
    # truncate the latest message so llama-server never rejects with HTTP 400.
    selected = list(clean_history[new_anchor:])
    selected_tokens = sum(estimate_message_tokens(m) for m in selected)
    if selected_tokens > usable_prompt_budget and len(selected) > 1:
        new_anchor = total_messages - 1
        selected = [clean_history[-1]]
        selected_tokens = estimate_message_tokens(selected[0])

    if selected_tokens > usable_prompt_budget and selected:
        selected[-1] = _truncate_message_to_budget(selected[-1], usable_prompt_budget)
        selected_tokens = sum(estimate_message_tokens(m) for m in selected)

    return BudgetedContext(
        messages=system_msg + selected,
        context_start=new_anchor,
        estimated_prompt_tokens=system_tokens + selected_tokens,
        max_completion_tokens=effective_reserve,
        context_window=effective_window,
        usable_prompt_budget=usable_prompt_budget,
        truncated=True,
        dropped_messages=new_anchor,
    )
