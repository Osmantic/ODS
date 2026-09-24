"""Shared context-window policy for dashboard API surfaces."""

HERMES_MIN_CONTEXT = 65536
HERMES_TARGET_CONTEXT = 131072
PIXEL_MIN_CONTEXT = 16384


def _require_positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def clamp_context(
    context_tokens: int,
    min_context: int = PIXEL_MIN_CONTEXT,
    max_context: int = HERMES_TARGET_CONTEXT,
) -> int:
    """Clamp a context size into the supported [min_context, max_context] range."""
    _require_positive_int(context_tokens, "context_tokens")
    _require_positive_int(min_context, "min_context")
    _require_positive_int(max_context, "max_context")
    if min_context > max_context:
        raise ValueError(f"min_context ({min_context}) exceeds max_context ({max_context})")
    return min(max(context_tokens, min_context), max_context)


def is_context_sufficient(
    available_tokens: int,
    required_tokens: int = HERMES_MIN_CONTEXT,
) -> bool:
    """True when *available_tokens* meets the *required_tokens* floor.

    A missing or non-positive available context is simply insufficient —
    callers pass ``effective_context or 0`` for optional values.
    """
    _require_positive_int(required_tokens, "required_tokens")
    if isinstance(available_tokens, bool) or not isinstance(available_tokens, int):
        raise TypeError(
            f"available_tokens must be an int, got {type(available_tokens).__name__}"
        )
    return available_tokens >= required_tokens
