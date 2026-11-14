"""Fit a conversation into the model's real context before inference.

Saved history is never shortened; only the copy sent to the model is. The
prompt is measured the way llama-server will see it (its chat template, then
its tokenizer), the answer's tokens are reserved, and whole turns are dropped
from the oldest end. A latest message that cannot fit on its own is refused
before inference, with numbers, instead of failing inside the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

Message = dict[str, str]
Measure = Callable[[list[Message]], Awaitable[int]]


class MessageTooLong(Exception):
    """The system prompt plus the newest message alone exceed the budget."""

    def __init__(self, prompt_tokens: int, budget: int) -> None:
        super().__init__(f"The message needs {prompt_tokens} tokens; this model has room for {budget}.")
        self.prompt_tokens = prompt_tokens
        self.budget = budget


# When history must be cut, cut to this share of the budget. The headroom lets
# the next several messages keep the same first turn, so llama-server reuses
# its cached prompt prefix instead of re-reading thousands of tokens (about
# 28 s per re-read for a 1B model on a laptop CPU).
TRIM_TARGET = 0.75


@dataclass(frozen=True)
class Fitted:
    messages: list[Message]
    prompt_tokens: int
    dropped_messages: int  # also the history index the kept slice starts at


def _with(system: Optional[str], history: list[Message], user: str) -> list[Message]:
    head = [{"role": "system", "content": system}] if system else []
    return head + history + [{"role": "user", "content": user}]


async def fit(
    system: Optional[str],
    history: list[Message],
    user: str,
    *,
    n_ctx: int,
    reserve: int,
    measure: Measure,
    start_hint: Optional[int] = None,
) -> Fitted:
    """Fit the most recent complete turns into n_ctx - reserve.

    start_hint is the cut the previous request used. Reusing it while it still
    fits keeps the prompt prefix identical between messages.
    """
    budget = n_ctx - reserve
    if budget <= 0:
        raise ValueError("The answer reserve leaves no room for a prompt.")
    alone = await measure(_with(system, [], user))
    if alone > budget:
        raise MessageTooLong(alone, budget)
    # A kept slice always starts at a user turn, so no answer is orphaned.
    starts = [i for i, message in enumerate(history) if message["role"] == "user"]
    if start_hint is not None and start_hint in starts:
        candidate = _with(system, history[start_hint:], user)
        tokens = await measure(candidate)
        if tokens <= budget:
            return Fitted(candidate, tokens, start_hint)
    whole = _with(system, history, user)
    if history and starts and starts[0] == 0:
        tokens = await measure(whole)
        if tokens <= budget:
            return Fitted(whole, tokens, 0)
    target = max(int(budget * TRIM_TARGET), alone)
    best = Fitted(_with(system, [], user), alone, len(history))
    low, high = 0, len(starts) - 1
    while low <= high:  # More history is never fewer tokens: binary search.
        middle = (low + high) // 2
        candidate = _with(system, history[starts[middle]:], user)
        tokens = await measure(candidate)
        if tokens <= target:
            best = Fitted(candidate, tokens, starts[middle])
            high = middle - 1
        else:
            low = middle + 1
    return best
