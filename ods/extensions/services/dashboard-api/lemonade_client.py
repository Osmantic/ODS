"""Small Lemonade API adapter for ODS provider-mode code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import httpx


DEFAULT_BASE_URL = "http://localhost:13305"
DEFAULT_API_BASE_PATH = "/api/v1"


def _clean_path(path: str) -> str:
    path = (path or DEFAULT_API_BASE_PATH).strip()
    if not path:
        return DEFAULT_API_BASE_PATH
    return path if path.startswith("/") else f"/{path}"


def normalize_base_url(base_url: str, api_base_path: str = DEFAULT_API_BASE_PATH) -> str:
    """Return a Lemonade host base URL without an API suffix."""
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    api_path = _clean_path(api_base_path).rstrip("/")
    for suffix in (api_path, "/api/v1", "/v1"):
        if suffix and base.lower().endswith(suffix.lower()):
            return base[: -len(suffix)].rstrip("/") or DEFAULT_BASE_URL
    return base


@dataclass(frozen=True)
class LemonadeSettings:
    """Connection settings for a Lemonade-compatible API surface."""

    base_url: str = DEFAULT_BASE_URL
    api_base_path: str = DEFAULT_API_BASE_PATH
    api_key: str = ""
    timeout: float = 20.0

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "LemonadeSettings":
        env = environ or os.environ
        base_url = (
            env.get("LEMONADE_CONTAINER_BASE_URL")
            or env.get("LEMONADE_BASE_URL")
            or env.get("LLM_API_URL")
            or DEFAULT_BASE_URL
        )
        api_base_path = (
            env.get("LEMONADE_API_BASE_PATH")
            or env.get("LLM_API_BASE_PATH")
            or DEFAULT_API_BASE_PATH
        )
        api_key = (
            env.get("LEMONADE_API_KEY")
            or env.get("LITELLM_LEMONADE_API_KEY")
            or ""
        )
        return cls(
            base_url=normalize_base_url(base_url, api_base_path),
            api_base_path=_clean_path(api_base_path),
            api_key=api_key,
        )

    @property
    def api_root(self) -> str:
        return f"{normalize_base_url(self.base_url, self.api_base_path)}{_clean_path(self.api_base_path)}"


class LemonadeClientError(RuntimeError):
    """Classified Lemonade request failure."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        payload: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.payload = payload or {}


def classify_status(status_code: int) -> str:
    if status_code in (401, 403):
        return "auth_rejected"
    if status_code == 404:
        return "not_found"
    if status_code == 408 or status_code == 504:
        return "timeout"
    if status_code >= 500:
        return "provider_error"
    return "request_rejected"


class LemonadeClient:
    """Async client for the Lemonade API paths ODS depends on."""

    def __init__(
        self,
        settings: Optional[LemonadeSettings] = None,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.settings = settings or LemonadeSettings.from_env()
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "LemonadeClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *_exc_info):
        await self.aclose()

    async def aclose(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def api_url(self, path: str) -> str:
        return f"{self.settings.api_root}/{path.lstrip('/')}"

    def auth_headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            return {}
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.timeout)
        return self._client

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        client = await self._ensure_client()
        # httpx treats an explicit `timeout=None` as "no timeout" rather than
        # "use the client default", so passing None here would silently disable
        # the client's configured timeout on every core request. Fall back to
        # the client default sentinel unless the caller overrides it.
        request_timeout = httpx.USE_CLIENT_DEFAULT if timeout is None else timeout
        try:
            response = await client.request(
                method,
                self.api_url(path),
                json=json,
                headers=self.auth_headers(),
                timeout=request_timeout,
            )
            response.raise_for_status()
            payload = response.json() if response.content else {}
            return payload if isinstance(payload, dict) else {"data": payload}
        except httpx.HTTPStatusError as exc:
            payload = _json_payload(exc.response)
            message = _payload_message(payload) or exc.response.text or str(exc)
            raise LemonadeClientError(
                classify_status(exc.response.status_code),
                message,
                status_code=exc.response.status_code,
                payload=payload,
            ) from exc
        except httpx.TimeoutException as exc:
            raise LemonadeClientError("timeout", str(exc)) from exc
        except httpx.RequestError as exc:
            raise LemonadeClientError("provider_unreachable", str(exc)) from exc
        except ValueError as exc:
            raise LemonadeClientError("invalid_response", str(exc)) from exc

    async def health(self) -> dict[str, Any]:
        return await self._request_json("GET", "health")

    async def stats(self) -> dict[str, Any]:
        return await self._request_json("GET", "stats")

    async def models(self) -> list[dict[str, Any]]:
        payload = await self._request_json("GET", "models")
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    async def model(self, model_id: str) -> dict[str, Any]:
        return await self._request_json("GET", f"models/{model_id}")

    async def chat_completion(
        self,
        model: str,
        messages: Sequence[dict[str, Any]],
        *,
        max_tokens: int = 16,
        stream: bool = False,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if extra_body:
            body.update(extra_body)
        return await self._request_json("POST", "chat/completions", json=body)

    async def embeddings(self, model: str, text: str) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            "embeddings",
            json={"model": model, "input": text},
        )

    async def rerank(self, model: str, query: str, documents: Sequence[str]) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            "reranking",
            json={"model": model, "query": query, "documents": list(documents)},
        )

    async def speech(self, model: str, text: str, *, voice: str = "af_heart") -> bytes:
        client = await self._ensure_client()
        response = await client.post(
            self.api_url("audio/speech"),
            json={"model": model, "input": text, "voice": voice},
            headers=self.auth_headers(),
            timeout=self.settings.timeout,
        )
        response.raise_for_status()
        return response.content

    async def transcribe_wav(self, model: str, wav_bytes: bytes, *, filename: str = "audio.wav") -> dict[str, Any]:
        client = await self._ensure_client()
        try:
            response = await client.post(
                self.api_url("audio/transcriptions"),
                files={"file": (filename, wav_bytes, "audio/wav")},
                data={"model": model},
                headers=self.auth_headers(),
                timeout=self.settings.timeout,
            )
            response.raise_for_status()
            payload = response.json() if response.content else {}
            return payload if isinstance(payload, dict) else {"data": payload}
        except httpx.HTTPStatusError as exc:
            payload = _json_payload(exc.response)
            raise LemonadeClientError(
                classify_status(exc.response.status_code),
                _payload_message(payload) or exc.response.text or str(exc),
                status_code=exc.response.status_code,
                payload=payload,
            ) from exc


def _json_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {"data": payload}


def _payload_message(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or error.get("code") or "")
    if error:
        return str(error)
    message = payload.get("message")
    return str(message) if message else ""
