#!/usr/bin/env python3
"""
Shared helper for Anthropic API authentication.

Provides a unified `create_message()` function using the Anthropic Python SDK.
Used by scanner scripts: generate-type-hints.py, generate-docstrings.py
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class ContentBlock:
    type: str
    text: str = ""


@dataclass
class MessageResponse:
    """Minimal response object matching anthropic.Message interface used by scanner scripts."""

    content: list[ContentBlock] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(0, 0))


def create_message(
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    messages: list[dict[str, Any]],
    thinking: dict[str, Any] | None = None,
) -> MessageResponse:
    """Create a message using the Anthropic API."""
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "::error::No API credentials. Set ANTHROPIC_API_KEY",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, messages=messages)
    if thinking:
        kwargs["thinking"] = thinking
        kwargs["temperature"] = 1
    else:
        kwargs["temperature"] = temperature

    response = client.messages.create(**kwargs)

    content_blocks = [
        ContentBlock(type="text", text=block.text)
        for block in response.content
        if block.type == "text"
    ]

    return MessageResponse(
        content=content_blocks,
        usage=Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        ),
    )
