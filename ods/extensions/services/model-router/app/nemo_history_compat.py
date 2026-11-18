"""Request-only projection for Nemo's alternating plain conversation roles."""
from typing import Any


def _text_parts(message: dict) -> list[dict] | None:
    if set(message) != {"role", "content"}:
        return None
    content = message["content"]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if (isinstance(content, list) and content
            and all(isinstance(part, dict) and set(part) == {"type", "text"}
                    and part["type"] == "text" and isinstance(part["text"], str)
                    for part in content)):
        return content
    return None


def coalesce_plain_user_messages(messages: Any) -> tuple[Any, bool]:
    """Preserve ordered text blocks without editing the transcript or input.

    The pinned llama.cpp parser flattens typed text for Nemo's string template.
    Extra message/block metadata and non-text content are not ours to interpret;
    leave the whole history alone if any user record cannot be safely projected.
    Tools and assistant turns remain separate, including tool-call IDs.
    """
    if not isinstance(messages, list) or not messages:
        return messages, False
    if any(not isinstance(message, dict) or (
            message.get("role") == "user" and _text_parts(message) is None)
            for message in messages):
        return messages, False
    result: list[dict] = []
    changed = False
    for message in messages:
        if (message.get("role") == "user" and result
                and result[-1].get("role") == "user"):
            result[-1] = {"role": "user", "content": [
                *_text_parts(result[-1]), *_text_parts(message),
            ]}
            changed = True
        else:
            result.append(message)
    return (result, True) if changed else (messages, False)
