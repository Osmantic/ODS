"""Shared context-window policy for dashboard API surfaces."""

HERMES_MIN_CONTEXT = 65536
HERMES_TARGET_CONTEXT = 131072
PIXEL_MIN_CONTEXT = 16384

def clamp_context(
    context_tokens: int,
    min_context: int = PIXEL_MIN_CONTEXT,
    max_context: int = HERMES_TARGET_CONTEXT,
) -> int:
    """Clamp an arbitrary requested context size between policy bounds."""
    if not isinstance(context_tokens, int) or context_tokens < min_context:
        return min_context
    return min(context_tokens, max_context)


def is_context_sufficient(
    available_tokens: int,
    required_tokens: int = HERMES_MIN_CONTEXT,
) -> bool:
    """Return True if available context satisfies the required threshold."""
    return isinstance(available_tokens, int) and available_tokens >= required_tokens
