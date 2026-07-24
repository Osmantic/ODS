"""Validation and storage mapping for metadata-only routed LLM telemetry."""

from __future__ import annotations

from typing import Any


class TelemetryValidationError(ValueError):
    """Raised when a routed telemetry event violates the ingest contract."""


_STRING_LIMITS = {
    "agent": 128,
    "model": 512,
    "provider_name": 128,
    "path": 64,
    "stop_reason": 128,
}
_INTEGER_LIMITS = {
    "request_body_bytes": 2 * 1024 * 1024,
    "message_count": 100_000,
    "user_message_count": 100_000,
    "assistant_message_count": 100_000,
    "tool_count": 100_000,
    "input_tokens": 2_000_000_000,
    "output_tokens": 2_000_000_000,
    "cache_read_tokens": 2_000_000_000,
    "cache_write_tokens": 2_000_000_000,
    "duration_ms": 86_400_000,
}
_REQUIRED_FIELDS = {"agent", "model", "provider_name", "path"}
_ALLOWED_PATHS = {
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
}
_LOCAL_PROVIDERS = {
    "hipfire",
    "local",
    "lemonade",
    "llama",
    "llama.cpp",
    "llama-server",
    "mlx",
    "ollama",
    "vllm",
}


def validate_routed_event(payload: Any) -> dict[str, Any]:
    """Return a normalized telemetry event or reject it without coercion."""
    if not isinstance(payload, dict):
        raise TelemetryValidationError("body must be a JSON object")

    unknown = sorted(set(payload) - set(_STRING_LIMITS) - set(_INTEGER_LIMITS))
    if unknown:
        raise TelemetryValidationError(
            f"unknown fields are not accepted: {', '.join(unknown)}"
        )

    missing = sorted(_REQUIRED_FIELDS - set(payload))
    if missing:
        raise TelemetryValidationError(
            f"missing required fields: {', '.join(missing)}"
        )

    normalized: dict[str, Any] = {}
    for field, limit in _STRING_LIMITS.items():
        value = payload.get(field, "")
        if not isinstance(value, str):
            raise TelemetryValidationError(f"{field} must be a string")
        value = value.strip()
        if field in _REQUIRED_FIELDS and not value:
            raise TelemetryValidationError(f"{field} must not be empty")
        if len(value) > limit:
            raise TelemetryValidationError(
                f"{field} exceeds the {limit}-character limit"
            )
        if any(ord(char) < 32 for char in value):
            raise TelemetryValidationError(
                f"{field} must not contain control characters"
            )
        normalized[field] = value

    if normalized["path"] not in _ALLOWED_PATHS:
        raise TelemetryValidationError("path is not a routed LLM endpoint")

    for field, limit in _INTEGER_LIMITS.items():
        value = payload.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TelemetryValidationError(f"{field} must be an integer")
        if value < 0 or value > limit:
            raise TelemetryValidationError(
                f"{field} must be between 0 and {limit}"
            )
        normalized[field] = value

    return normalized


def routed_event_to_usage(event: dict[str, Any]) -> dict[str, Any]:
    """Map validated metadata into the existing Token Spy usage schema."""
    provider = event["provider_name"].lower()
    is_local = provider in _LOCAL_PROVIDERS
    model = event["model"]
    # Lemonade can expose the same registered GGUF as either ``name`` or
    # ``extra.name`` depending on the response path. The prefix is a runtime
    # namespace, not part of the physical model identity.
    if is_local and model.startswith("extra."):
        model = model.removeprefix("extra.")
    return {
        "agent": event["agent"],
        "model": model,
        "provider_name": "local" if is_local else provider,
        "cost_source": "local_zero_cost" if is_local else "untracked",
        "request_body_bytes": event["request_body_bytes"],
        "message_count": event["message_count"],
        "user_message_count": event["user_message_count"],
        "assistant_message_count": event["assistant_message_count"],
        "tool_count": event["tool_count"],
        "system_prompt_total_chars": 0,
        "workspace_agents_chars": 0,
        "workspace_soul_chars": 0,
        "workspace_tools_chars": 0,
        "workspace_identity_chars": 0,
        "workspace_user_chars": 0,
        "workspace_heartbeat_chars": 0,
        "workspace_bootstrap_chars": 0,
        "skill_injection_chars": 0,
        "base_prompt_chars": 0,
        "conversation_history_chars": 0,
        "input_tokens": event["input_tokens"],
        "output_tokens": event["output_tokens"],
        "cache_read_tokens": event["cache_read_tokens"],
        "cache_write_tokens": event["cache_write_tokens"],
        "estimated_cost_usd": 0,
        "duration_ms": event["duration_ms"],
        "stop_reason": event["stop_reason"] or None,
        "filter_chars_saved": 0,
        "filter_tokens_saved": 0,
        "filter_tools_removed": 0,
    }
