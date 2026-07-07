"""Anthropic Provider — Claude Messages API support.

Handles Anthropic-specific request/response formats including:
- System prompt with cache_control blocks
- Workspace file breakdown (AGENTS.md, SOUL.md, etc.)
- SSE streaming with event types (message_start, message_delta, message_stop)
"""

import json
from typing import Any, Dict, List, Optional

from .base import LLMProvider
from .registry import register_provider


@register_provider("anthropic")
class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider (Claude models)."""

    # Pricing per 1M tokens: {input, output, cache_read, cache_write}
    COST_TABLE = {
        "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "claude-opus-4-1": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
        "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
        "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
        "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
        "claude-haiku-3-5": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
        "claude-haiku": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    }

    # Map workspace file markers to metric keys
    WORKSPACE_FILE_MAP = {
        "AGENTS.md": "workspace_agents_chars",
        "SOUL.md": "workspace_soul_chars",
        "TOOLS.md": "workspace_tools_chars",
        "IDENTITY.md": "workspace_identity_chars",
        "USER.md": "workspace_user_chars",
        "HEARTBEAT.md": "workspace_heartbeat_chars",
        "BOOTSTRAP.md": "workspace_bootstrap_chars",
        "MEMORY.md": "workspace_memory_chars",
    }

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def default_base_url(self) -> str:
        return "https://api.anthropic.com"

    @property
    def api_endpoint(self) -> str:
        return "/v1/messages"

    def get_model_pricing(self, model: str) -> Dict[str, float]:
        """Match model name to pricing table."""
        model_lower = model.lower()

        # Try exact prefix matches (longer prefixes first for specificity)
        for prefix in sorted(self.COST_TABLE.keys(), key=len, reverse=True):
            if prefix in model_lower:
                return self.COST_TABLE[prefix]

        # Default to zero if unknown model
        return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}

    def analyze_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze Anthropic request for metrics.

        Extracts:
        - System prompt breakdown with workspace file detection
        - Message counts and conversation history size
        - Tool count
        """
        result = {
            "system_prompt_total_chars": 0,
            "base_prompt_chars": 0,
            "message_count": 0,
            "user_message_count": 0,
            "assistant_message_count": 0,
            "conversation_history_chars": 0,
            "tool_count": 0,
        }

        # Initialize workspace file metrics
        for key in self.WORKSPACE_FILE_MAP.values():
            result[key] = 0

        # Analyze system prompt
        system = body.get("system", [])
        if system:
            sys_analysis = self._analyze_system_prompt(system)
            result.update(sys_analysis)

        # Analyze messages
        messages = body.get("messages", [])
        msg_analysis = self._analyze_messages(messages)
        result.update(msg_analysis)

        # Tool count
        result["tool_count"] = len(body.get("tools", []))

        return result

    def _analyze_system_prompt(self, system: Any) -> Dict[str, Any]:
        """Parse system prompt structure for workspace file breakdown.

        Anthropic system prompt can be:
        - A string (simple)
        - A list of blocks with text and cache_control
        """
        result = {
            "system_prompt_total_chars": 0,
            "base_prompt_chars": 0,
        }
        for key in self.WORKSPACE_FILE_MAP.values():
            result[key] = 0

        # Convert string to block format
        if isinstance(system, str):
            blocks = [{"type": "text", "text": system}]
        elif isinstance(system, list):
            blocks = system
        else:
            return result

        total_chars = 0
        base_chars = 0
        workspace_chars = {k: 0 for k in self.WORKSPACE_FILE_MAP.values()}

        for block in blocks:
            if not isinstance(block, dict):
                continue

            text = block.get("text", "")
            if not isinstance(text, str):
                text = str(text)

            block_len = len(text)
            total_chars += block_len

            # Check for workspace file markers
            matched_workspace = False
            for filename, metric_key in self.WORKSPACE_FILE_MAP.items():
                # Look for ## FILENAME patterns
                if f"## {filename}" in text or f"# {filename}" in text:
                    workspace_chars[metric_key] += block_len
                    matched_workspace = True
                    break

            if not matched_workspace:
                base_chars += block_len

        result["system_prompt_total_chars"] = total_chars
        result["base_prompt_chars"] = base_chars
        for key, chars in workspace_chars.items():
            result[key] = chars

        return result

    def _analyze_messages(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze message array for counts and sizes."""
        user_count = 0
        assistant_count = 0

        for msg in messages:
            role = msg.get("role", "")
            if role == "user":
                user_count += 1
            elif role == "assistant":
                assistant_count += 1

        # Serialize messages for history char count
        try:
            history_str = json.dumps(messages, separators=(",", ":"))
            history_chars = len(history_str)
        except (TypeError, ValueError):
            history_chars = 0

        return {
            "message_count": len(messages),
            "user_message_count": user_count,
            "assistant_message_count": assistant_count,
            "conversation_history_chars": history_chars,
        }

    def rewrite_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Anthropic is the reference format — no rewriting needed."""
        return body

    def extract_usage_from_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Extract usage from non-streaming response."""
        usage = response.get("usage", {})
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "stop_reason": response.get("stop_reason"),
        }

    def extract_usage_from_stream(
        self, line: str, event_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Extract usage from Anthropic SSE stream.

        Anthropic uses event types:
        - message_start: Contains input tokens, cache stats
        - message_delta: Contains output tokens, stop_reason
        - message_stop: End of stream (no usage)
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

        if event_type == "message_start":
            # Initial message with input usage
            msg_usage = data.get("message", {}).get("usage", {})
            if msg_usage:
                result["input_tokens"] = msg_usage.get("input_tokens", 0)
                result["cache_read_tokens"] = msg_usage.get("cache_read_input_tokens", 0)
                result["cache_write_tokens"] = msg_usage.get("cache_creation_input_tokens", 0)

        elif event_type == "message_delta":
            # Delta with output tokens and/or stop reason
            delta_usage = data.get("usage", {})
            delta = data.get("delta", {})

            if delta_usage.get("output_tokens") is not None:
                result["output_tokens"] = delta_usage["output_tokens"]

            if delta.get("stop_reason"):
                result["stop_reason"] = delta["stop_reason"]

        return result if result else None

    def get_auth_headers(self, request_headers: Dict[str, str]) -> Dict[str, str]:
        """Extract Anthropic-specific headers to forward."""
        headers = {}

        # Required auth header
        for key in ("x-api-key",):
            val = request_headers.get(key.lower())
            if val:
                headers[key] = val

        # Optional Anthropic headers
        for key in (
            "anthropic-version",
            "anthropic-beta",
            "anthropic-dangerous-direct-browser-access",
        ):
            val = request_headers.get(key.lower())
            if val:
                headers[key] = val

        return headers
