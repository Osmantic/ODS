"""OpenAI-Compatible Provider — OpenAI, Moonshot, vLLM, and other compatible APIs.

Handles OpenAI-style request/response formats including:
- Chat completions API
- Developer/system role rewriting
- Standard SSE streaming format
"""

import json
from typing import Any, Dict, Optional

from .base import LLMProvider
from .registry import register_provider


@register_provider("openai")
class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible API provider.

    Supports:
    - OpenAI native API
    - Moonshot/Kimi API
    - Local vLLM/Ollama with OpenAI-compatible endpoints
    - Any other OpenAI-compatible service
    """

    # Pricing per 1M tokens: {input, output, cache_read, cache_write}
    # cache_read/write are 0 for providers that don't support caching
    COST_TABLE = {
        # Moonshot Kimi models
        "kimi-k2-0711": {"input": 0.60, "output": 3.0, "cache_read": 0.10, "cache_write": 0.60},
        "kimi-k2-0905": {"input": 0.60, "output": 2.50, "cache_read": 0.15, "cache_write": 0.60},
        "kimi-k2-thinking": {"input": 0.60, "output": 2.50, "cache_read": 0.15, "cache_write": 0.60},
        "kimi-k2.5": {"input": 0.60, "output": 2.50, "cache_read": 0.15, "cache_write": 0.60},
        "kimi-k2": {"input": 0.60, "output": 2.50, "cache_read": 0.15, "cache_write": 0.60},
        # OpenAI models
        "gpt-4o": {"input": 2.50, "output": 10.0, "cache_read": 1.25, "cache_write": 0.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0.0},
        "gpt-4-turbo": {"input": 10.0, "output": 30.0, "cache_read": 0.0, "cache_write": 0.0},
        "gpt-4": {"input": 30.0, "output": 60.0, "cache_read": 0.0, "cache_write": 0.0},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50, "cache_read": 0.0, "cache_write": 0.0},
        "o1": {"input": 15.0, "output": 60.0, "cache_read": 7.50, "cache_write": 0.0},
        "o1-mini": {"input": 3.0, "output": 12.0, "cache_read": 1.50, "cache_write": 0.0},
        "o1-pro": {"input": 150.0, "output": 600.0, "cache_read": 0.0, "cache_write": 0.0},
        # DeepSeek models (OpenAI-compatible)
        "deepseek-chat": {"input": 0.27, "output": 1.10, "cache_read": 0.07, "cache_write": 0.27},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_read": 0.14, "cache_write": 0.55},
        # Local models (free) — Strix Halo llama-server models
        "qwen3-coder-next": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "qwen3:30b-a3b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "qwen3-8b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "qwen3-14b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "qwen3-30b-a3b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "qwen3.5:27b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "gemma-4-e2b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "gemma-4-e4b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "gemma-4-26b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "gemma-4-31b": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "qwen": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "llama": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        "mistral": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    }

    @property
    def name(self) -> str:
        return "openai"

    @property
    def default_base_url(self) -> str:
        return "https://api.openai.com"

    @property
    def api_endpoint(self) -> str:
        return "/v1/chat/completions"

    def get_model_pricing(self, model: str) -> Dict[str, float]:
        """Match model name to pricing table."""
        model_lower = model.lower()

        # Try exact prefix matches (longer prefixes first for specificity)
        for prefix in sorted(self.COST_TABLE.keys(), key=len, reverse=True):
            if prefix in model_lower:
                return self.COST_TABLE[prefix]

        # Default to zero for unknown models (likely local)
        return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}

    def analyze_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze OpenAI-format request for metrics."""
        messages = body.get("messages", [])

        user_count = 0
        assistant_count = 0
        system_chars = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                user_count += 1
            elif role == "assistant":
                assistant_count += 1
            elif role in ("system", "developer"):
                # System prompt - could be string or array
                if isinstance(content, str):
                    system_chars += len(content)
                elif isinstance(content, list):
                    # Array of content blocks
                    for block in content:
                        if isinstance(block, dict):
                            text = block.get("text", "")
                            if isinstance(text, str):
                                system_chars += len(text)
                        elif isinstance(block, str):
                            system_chars += len(block)
                else:
                    system_chars += len(json.dumps(content, separators=(",", ":")))

        # Serialize messages for history char count
        try:
            history_str = json.dumps(messages, separators=(",", ":"))
            history_chars = len(history_str)
        except (TypeError, ValueError):
            history_chars = 0

        return {
            "system_prompt_total_chars": system_chars,
            "base_prompt_chars": system_chars,  # No workspace breakdown for OpenAI
            "message_count": len(messages),
            "user_message_count": user_count,
            "assistant_message_count": assistant_count,
            "conversation_history_chars": history_chars,
            "tool_count": len(body.get("tools", body.get("functions", []))),
        }

    def rewrite_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Rewrite request for OpenAI compatibility.

        Main transformation: convert 'developer' role to 'system' for
        providers that don't support the developer role (e.g., Moonshot).
        """
        messages = body.get("messages", [])
        rewritten = False

        for msg in messages:
            if msg.get("role") == "developer":
                msg["role"] = "system"
                rewritten = True

        if rewritten:
            body["messages"] = messages

        return body

    def extract_usage_from_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Extract usage from non-streaming response."""
        usage = response.get("usage", {})

        # Get stop reason from choices
        choices = response.get("choices", [])
        stop_reason = None
        if choices:
            stop_reason = choices[0].get("finish_reason")

        return {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_read_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            "cache_write_tokens": 0,  # OpenAI doesn't expose cache write stats
            "stop_reason": stop_reason,
        }

    def extract_usage_from_stream(
        self, line: str, event_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Extract usage from OpenAI SSE stream.

        OpenAI streaming:
        - Usage comes in the final chunk with empty choices
        - Stop reason comes in the last content chunk
        """
        stripped = line.strip()

        # Only process data lines
        if not stripped.startswith("data:"):
            return None

        data_str = stripped[5:].strip()
        if data_str == "[DONE]":
            return None

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        result = {}

        # Check for usage in final chunk
        usage = data.get("usage", {})
        if usage:
            result["input_tokens"] = usage.get("prompt_tokens", 0)
            result["output_tokens"] = usage.get("completion_tokens", 0)

            # OpenAI may include cache stats in prompt_tokens_details
            details = usage.get("prompt_tokens_details", {})
            if details:
                result["cache_read_tokens"] = details.get("cached_tokens", 0)

        # Check for stop reason in choices
        choices = data.get("choices", [])
        if choices:
            finish_reason = choices[0].get("finish_reason")
            if finish_reason:
                result["stop_reason"] = finish_reason

        return result if result else None

    def get_auth_headers(self, request_headers: Dict[str, str]) -> Dict[str, str]:
        """Extract Authorization header for OpenAI-compatible APIs."""
        headers = {}

        auth = request_headers.get("authorization")
        if auth:
            headers["Authorization"] = auth

        # Some providers use x-api-key instead
        api_key = request_headers.get("x-api-key")
        if api_key:
            headers["x-api-key"] = api_key

        return headers


# Convenience alias for Moonshot-specific usage
@register_provider("moonshot")
class MoonshotProvider(OpenAICompatibleProvider):
    """Moonshot/Kimi API provider.

    Moonshot is OpenAI-compatible with some quirks handled here.
    """

    @property
    def name(self) -> str:
        return "moonshot"

    @property
    def default_base_url(self) -> str:
        return "https://api.moonshot.ai"


# Local vLLM provider (no cost tracking)
@register_provider("local")
class LocalProvider(OpenAICompatibleProvider):
    """Local inference provider (vLLM, Ollama, etc.).

    Same as OpenAI-compatible but defaults to localhost and zero costs.
    """

    @property
    def name(self) -> str:
        return "local"

    @property
    def default_base_url(self) -> str:
        return self.config.get("base_url", "http://localhost:8000")

    def get_model_pricing(self, model: str) -> Dict[str, float]:
        """Local models are free (electricity cost not tracked)."""
        return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
