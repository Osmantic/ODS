"""Metadata-only LiteLLM usage callback for Token Spy."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("ods-litellm-token-spy")

_LOCAL_HOSTS = {
    "127.0.0.1",
    "host.docker.internal",
    "llama-server",
    "localhost",
    "model-router",
    "ollama",
}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        result = value.model_dump()
        return result if isinstance(result, dict) else {}
    if hasattr(value, "dict"):
        result = value.dict()
        return result if isinstance(result, dict) else {}
    return {}


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return min(max(0, int(value)), 2_000_000_000)
    except (TypeError, ValueError, OverflowError):
        return 0


def _duration_ms(start_time: Any, end_time: Any) -> int:
    if isinstance(start_time, datetime) and isinstance(end_time, datetime):
        seconds = (end_time - start_time).total_seconds()
    else:
        try:
            seconds = float(end_time) - float(start_time)
        except (TypeError, ValueError):
            seconds = 0
    return min(max(int(seconds * 1000), 0), 86_400_000)


def _provider_name(kwargs: dict[str, Any]) -> str:
    litellm_params = _as_dict(kwargs.get("litellm_params"))
    api_base = str(
        litellm_params.get("api_base")
        or kwargs.get("api_base")
        or ""
    )
    try:
        hostname = (urlparse(api_base).hostname or "").lower()
    except ValueError:
        hostname = ""
    if hostname in _LOCAL_HOSTS or hostname.endswith(".local"):
        return "local"
    provider = str(
        litellm_params.get("custom_llm_provider")
        or kwargs.get("custom_llm_provider")
        or "unknown"
    )
    return provider[:128]


def _endpoint_path(kwargs: dict[str, Any]) -> str:
    call_type = str(kwargs.get("call_type") or "").lower()
    if "responses" in call_type:
        return "/v1/responses"
    if call_type in {"text_completion", "completion"}:
        return "/v1/completions"
    return "/v1/chat/completions"


def build_event(
    kwargs: dict[str, Any],
    response_obj: Any,
    start_time: Any,
    end_time: Any,
) -> dict[str, Any]:
    response = _as_dict(response_obj)
    usage = _as_dict(response.get("usage"))
    prompt_details = _as_dict(
        usage.get("prompt_tokens_details")
        or usage.get("input_tokens_details")
    )
    choices = response.get("choices")
    choices = choices if isinstance(choices, list) else []
    first_choice = _as_dict(choices[0]) if choices else {}
    messages = kwargs.get("messages")
    messages = messages if isinstance(messages, list) else []
    roles = [
        item.get("role")
        for item in messages
        if isinstance(item, dict)
    ]
    optional_params = _as_dict(kwargs.get("optional_params"))
    tools = kwargs.get("tools", optional_params.get("tools"))
    tools = tools if isinstance(tools, list) else []
    model = str(
        response.get("model")
        or kwargs.get("model")
        or "unknown"
    )
    return {
        "agent": "litellm",
        "model": model[:512],
        "provider_name": _provider_name(kwargs),
        "path": _endpoint_path(kwargs),
        # LiteLLM does not expose the original encoded body to callbacks.
        "request_body_bytes": 0,
        "message_count": min(len(messages), 100_000),
        "user_message_count": min(roles.count("user"), 100_000),
        "assistant_message_count": min(roles.count("assistant"), 100_000),
        "tool_count": min(len(tools), 100_000),
        "input_tokens": _count(
            usage.get("prompt_tokens", usage.get("input_tokens", 0))
        ),
        "output_tokens": _count(
            usage.get("completion_tokens", usage.get("output_tokens", 0))
        ),
        "cache_read_tokens": _count(
            prompt_details.get(
                "cached_tokens", usage.get("cache_read_tokens", 0)
            )
        ),
        "cache_write_tokens": _count(usage.get("cache_write_tokens", 0)),
        "duration_ms": _duration_ms(start_time, end_time),
        "stop_reason": str(
            first_choice.get("finish_reason")
            or response.get("stop_reason")
            or response.get("status")
            or ""
        )[:128],
    }


class ODSTokenSpyCallback(CustomLogger):
    """Send successful LiteLLM usage without delaying inference."""

    def __init__(self) -> None:
        super().__init__(turn_off_message_logging=True)
        self.url = os.environ.get("TOKEN_SPY_URL", "").rstrip("/")
        self.api_key = os.environ.get("TOKEN_SPY_API_KEY", "")
        self.enabled = bool(self.url and self.api_key)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max(
                1, int(os.environ.get("ODS_LITELLM_TELEMETRY_QUEUE_DEPTH", "1024"))
            )
        )
        self.worker: asyncio.Task[None] | None = None
        self.last_warning = 0.0

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        # With the switchboard enabled, this same request is recorded by
        # model-router. Skipping here preserves exactly-once accounting.
        if (
            not self.enabled
            or os.environ.get("ODS_MODEL_SWITCHBOARD", "observe") == "enabled"
        ):
            return
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(
                self._run(), name="litellm-token-spy"
            )
        try:
            self.queue.put_nowait(
                build_event(kwargs, response_obj, start_time, end_time)
            )
        except asyncio.QueueFull:
            self._warn("Token Spy callback queue is full; dropping event")

    async def _run(self) -> None:
        timeout = max(
            0.1, float(os.environ.get("ODS_LITELLM_TELEMETRY_TIMEOUT", "3"))
        )
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=timeout
        ) as client:
            while True:
                event = await self.queue.get()
                try:
                    response = await client.post(
                        f"{self.url}/api/ingest/routed",
                        json=event,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    if response.status_code != 202:
                        self._warn(
                            "Token Spy rejected LiteLLM telemetry "
                            f"with HTTP {response.status_code}"
                        )
                except (httpx.HTTPError, RuntimeError) as exc:
                    self._warn(f"Token Spy telemetry unavailable: {exc}")
                finally:
                    self.queue.task_done()

    def _warn(self, message: str) -> None:
        now = time.monotonic()
        if now - self.last_warning >= 60:
            log.warning(message)
            self.last_warning = now


ods_token_spy_callback = ODSTokenSpyCallback()
