"""Abstract Base Class for LLM Providers.

All provider implementations must inherit from LLMProvider and implement
the required abstract methods for request/response handling and cost calculation.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import httpx


class LLMProvider(ABC):
    """Abstract base for LLM API providers.

    Providers handle the provider-specific logic for:
    - Request analysis (extracting metrics from incoming requests)
    - Request rewriting (transforming requests for provider compatibility)
    - Response parsing (extracting usage from responses)
    - Stream parsing (extracting usage from SSE streams)
    - Cost calculation (pricing per model)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize provider with optional configuration.

        Args:
            config: Provider-specific configuration (base_url overrides, etc.)
        """
        self.config = config or {}
        self._client: Optional[httpx.AsyncClient] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (anthropic, openai, google, etc.)"""
        pass

    @property
    @abstractmethod
    def default_base_url(self) -> str:
        """Default API base URL for this provider."""
        pass

    @property
    def base_url(self) -> str:
        """API base URL, allowing config override."""
        return self.config.get("base_url", self.default_base_url)

    @property
    @abstractmethod
    def api_endpoint(self) -> str:
        """Primary API endpoint path (e.g., /v1/messages or /v1/chat/completions)."""
        pass

    @abstractmethod
    def get_model_pricing(self, model: str) -> Dict[str, float]:
        """Return pricing per 1M tokens for a model.

        Returns:
            Dict with keys: input, output, cache_read, cache_write
            Values are USD per 1M tokens, 0.0 if unknown.
        """
        pass

    @abstractmethod
    def analyze_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Extract metrics from request body.

        Returns dict with:
            - system_prompt_total_chars: Total system prompt size
            - base_prompt_chars: Base (static) prompt size
            - workspace_*_chars: Optional breakdown by workspace file
            - message_count: Total messages
            - user_message_count: User messages
            - assistant_message_count: Assistant messages
            - conversation_history_chars: Total serialized message chars
            - tool_count: Number of tools defined
        """
        pass

    @abstractmethod
    def rewrite_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Rewrite request for provider compatibility.

        E.g., convert 'developer' role to 'system' for Moonshot.
        Returns the potentially modified body (may modify in place).
        """
        pass

    @abstractmethod
    def extract_usage_from_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Extract token usage from non-streaming response.

        Returns dict with:
            - input_tokens: Input/prompt tokens
            - output_tokens: Output/completion tokens
            - cache_read_tokens: Tokens read from cache (0 if not supported)
            - cache_write_tokens: Tokens written to cache (0 if not supported)
            - stop_reason: Why generation stopped (optional)
        """
        pass

    @abstractmethod
    def extract_usage_from_stream(
        self, line: str, event_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Extract usage from a single SSE stream line.

        Args:
            line: Raw SSE line (may include "data:" prefix)
            event_type: For Anthropic-style SSE, the current event type

        Returns:
            Partial usage dict if this line contains usage info, None otherwise.
            Can return partial updates that get merged with existing usage.
        """
        pass

    def get_auth_headers(self, request_headers: Dict[str, str]) -> Dict[str, str]:
        """Extract and return authentication headers to forward.

        Override in subclasses for provider-specific auth handling.
        Default implementation returns empty dict (no auth forwarding).

        Args:
            request_headers: Incoming request headers (lowercase keys)

        Returns:
            Headers to include in upstream request
        """
        return {}

    def get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with provider-specific config.

        Creates a new client if none exists or the existing one is closed.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def close(self):
        """Close the HTTP client if open."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def calculate_cost(self, usage: Dict[str, Any], model: str) -> float:
        """Calculate cost in USD from usage and model.

        Args:
            usage: Dict with *_tokens keys
            model: Model name for pricing lookup

        Returns:
            Estimated cost in USD
        """
        rates = self.get_model_pricing(model)
        return (
            usage.get("input_tokens", 0) * rates.get("input", 0) / 1_000_000 +
            usage.get("output_tokens", 0) * rates.get("output", 0) / 1_000_000 +
            usage.get("cache_read_tokens", 0) * rates.get("cache_read", 0) / 1_000_000 +
            usage.get("cache_write_tokens", 0) * rates.get("cache_write", 0) / 1_000_000
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} base_url={self.base_url}>"
