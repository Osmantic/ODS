"""Bounded dashboard bridge to the internal Pixel edge service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from host_agent_client import AgentClientError, async_request_json as request_agent_json
from pixel_runtime_state import begin_pixel_stream, end_pixel_stream
from security import verify_api_key


logger = logging.getLogger(__name__)


_DEFAULT_EDGE_URL = "http://pixel-edge:9595"
_MODEL = "pixel/default"
_CHAT_STREAM_TIMEOUT_SECONDS = 2040.0
_CLIENT_DISCONNECT_POLL_SECONDS = 0.25
_CLIENT_CANCEL_TIMEOUT_SECONDS = 7.0
_MAX_KEY_LENGTH = 4096
_MAX_STATUS_BYTES = 64 * 1024
_MAX_SSE_LINE_BYTES = 1024 * 1024
_MAX_MESSAGE_CHARS = 16 * 1024
_MAX_TOTAL_MESSAGE_BYTES = 256 * 1024
_SAFE_CHAT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_OPS_JOB_ID = re.compile(r"^ops-[0-9]{13}-[a-f0-9]{12}$")
_OPS_PLAN_HASH = re.compile(r"^[a-f0-9]{64}$")
_OPS_STATUSES = frozenset(
    {
        "awaiting-approval",
        "paused",
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "rejected",
    }
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MODEL_SWITCH_DETAIL = "Model switch in progress; Pixel will be ready when activation completes"
_MODEL_ADAPTIVE_DETAIL = (
    "Pixel is ready and adapts its tool flow for this model. Model capability "
    "affects the quality and persistence of complex work, not access or the "
    "broker-enforced safety boundary."
)


def _validate_edge_url(raw: str) -> str:
    """Accept exactly the fixed internal Pixel edge origin."""
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("PIXEL_EDGE_URL is invalid") from exc
    if parsed.scheme != "http":
        raise ValueError("PIXEL_EDGE_URL scheme must be http")
    if parsed.hostname != "pixel-edge" or port != 9595:
        raise ValueError("PIXEL_EDGE_URL must use pixel-edge:9595")
    if parsed.username or parsed.password or parsed.path or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("PIXEL_EDGE_URL must be an origin without userinfo, path, query, or fragment")
    return _DEFAULT_EDGE_URL


def _pixel_config() -> tuple[str, str] | None:
    """Return validated runtime config, or None when Pixel is not enabled."""
    raw_key = os.environ.get("PIXEL_OPENWEBUI_KEY", "")
    if not raw_key:
        return None
    if raw_key != raw_key.strip() or len(raw_key) < 32 or len(raw_key) > _MAX_KEY_LENGTH:
        raise RuntimeError("PIXEL_OPENWEBUI_KEY is invalid")
    if _CONTROL.search(raw_key):
        raise RuntimeError("PIXEL_OPENWEBUI_KEY is invalid")
    raw_url = os.environ.get("PIXEL_EDGE_URL", _DEFAULT_EDGE_URL)
    return _validate_edge_url(raw_url), raw_key


def _edge_headers(key: str, *, accept: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Accept": accept,
        "Content-Type": "application/json",
    }


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: str
    content: str = Field(max_length=_MAX_MESSAGE_CHARS)

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        if value not in {"system", "user", "assistant"}:
            raise ValueError("unsupported message role")
        return value


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chat_id: str
    messages: list[_Message] = Field(min_length=1, max_length=50)

    @field_validator("chat_id")
    @classmethod
    def _chat_id(cls, value: str) -> str:
        if not _SAFE_CHAT_ID.fullmatch(value):
            raise ValueError("invalid chat_id")
        return value

    @field_validator("messages")
    @classmethod
    def _total_size(cls, messages: list[_Message]) -> list[_Message]:
        total = sum(len(item.content.encode("utf-8")) for item in messages)
        if total > _MAX_TOTAL_MESSAGE_BYTES:
            raise ValueError("aggregate message content is too large")
        return messages


class ChatCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chat_id: str

    @field_validator("chat_id")
    @classmethod
    def _chat_id(cls, value: str) -> str:
        if not _SAFE_CHAT_ID.fullmatch(value):
            raise ValueError("invalid chat_id")
        return value


router = APIRouter(prefix="/api/pixel", tags=["pixel"])


async def _host_model_status() -> dict[str, object] | None:
    try:
        status = await request_agent_json("GET", "/v1/model/status", timeout=2.0)
    except AgentClientError:
        return None
    return status if isinstance(status, dict) else None


def _model_readiness_issue_from_status(status: object) -> tuple[str, str] | None:
    switching = (
        isinstance(status, dict)
        and status.get("activeOperation") == "model_activation"
        and bool(status.get("lifecycleActive") or status.get("activeOperation"))
    )
    if switching:
        return "model_switching", _MODEL_SWITCH_DETAIL
    return None


def _model_support_from_status(status: object) -> dict[str, str] | None:
    """Return fixed advisory metadata without turning model quality into access.

    ODS model qualification is a recommendation signal. Pixel's brokers,
    approvals, and typed capabilities enforce safety independently of model
    intelligence, so an unqualified model remains usable and testable.
    """
    if isinstance(status, dict) and status.get("activeAgentViable") is False:
        return {"tier": "adaptive", "detail": _MODEL_ADAPTIVE_DETAIL}
    return None


async def _model_readiness_issue() -> tuple[str, str] | None:
    """Return a host-proven model transition, if present.

    A failed lifecycle probe does not falsely take down an otherwise healthy
    Pixel edge. Model quality metadata is advisory; the edge readiness check
    remains authoritative.
    """
    return _model_readiness_issue_from_status(await _host_model_status())


def _active_runtime_projection(status: object) -> dict[str, object] | None:
    if not isinstance(status, dict):
        return None
    runtime = status.get("activeRuntime")
    if isinstance(runtime, dict) and runtime.get("source") == "local-switchboard":
        expected = {"source", "model", "contextLength"}
        if (
            set(runtime) == expected
            and isinstance(runtime.get("model"), str)
            and 1 <= len(runtime["model"]) <= 256
            and type(runtime.get("contextLength")) is int
            and 1 <= runtime["contextLength"] <= 10_000_000
        ):
            return {key: runtime[key] for key in expected}
        return None
    expected = {"source", "model", "contextLength", "maxTokens", "reasoning"}
    if (
        not isinstance(runtime, dict)
        or set(runtime) != expected
        or runtime.get("source") != "remote-provider"
        or not isinstance(runtime.get("model"), str)
        or not 1 <= len(runtime["model"]) <= 256
        or type(runtime.get("contextLength")) is not int
        or not 4096 <= runtime["contextLength"] <= 10_000_000
        or type(runtime.get("maxTokens")) is not int
        or not 1 <= runtime["maxTokens"] <= runtime["contextLength"]
        or type(runtime.get("reasoning")) is not bool
    ):
        return None
    return {key: runtime[key] for key in expected}


async def _model_activation_in_progress() -> bool:
    """Compatibility wrapper retained for focused lifecycle callers/tests."""
    issue = await _model_readiness_issue()
    return issue is not None and issue[0] == "model_switching"


async def _bounded_response_bytes(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ValueError("response too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/status", dependencies=[Depends(verify_api_key)])
async def pixel_status() -> dict[str, object]:
    """Return a fixed, nonsecret Pixel availability projection."""
    config = _pixel_config()
    if config is None:
        return {"available": False, "model": None, "detail": "Pixel is not enabled"}
    host_status = await _host_model_status()
    readiness_issue = _model_readiness_issue_from_status(host_status)
    if readiness_issue is not None:
        state, detail = readiness_issue
        return {
            "available": False,
            "model": None,
            "state": state,
            "detail": detail,
        }
    edge_url, key = config
    try:
        timeout = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            async with client.stream(
                "GET",
                f"{edge_url}/v1/models",
                headers=_edge_headers(key, accept="application/json"),
            ) as response:
                if response.status_code != 200:
                    return {"available": False, "model": None, "detail": "Pixel edge is unavailable"}
                if not response.headers.get("content-type", "").lower().startswith("application/json"):
                    return {"available": False, "model": None, "detail": "Pixel edge returned an invalid response"}
                raw = await _bounded_response_bytes(response, _MAX_STATUS_BYTES)
        payload = json.loads(raw)
        models = payload.get("data") if isinstance(payload, dict) else None
        available = isinstance(models, list) and any(
            isinstance(item, dict) and item.get("id") == _MODEL for item in models
        )
        result: dict[str, object] = {
            "available": available,
            "model": _MODEL if available else None,
            "detail": "Owner agent ready" if available else "pixel/default is unavailable",
        }
        runtime = _active_runtime_projection(host_status)
        if available and runtime is not None:
            result["runtime"] = runtime
        model_support = _model_support_from_status(host_status)
        if available and model_support is not None:
            result["modelSupport"] = model_support
        return result
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        # Exception text and request objects can contain upstream credentials.
        # Retain the failure phase/type without logging those sensitive values.
        logger.warning("Pixel edge status request failed (%s)", type(exc).__name__)
        return {"available": False, "model": None, "detail": "Pixel edge is unavailable"}
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        return {"available": False, "model": None, "detail": "Pixel edge returned an invalid response"}


@router.get("/ops/{job_id}", dependencies=[Depends(verify_api_key)])
async def pixel_operations_status(job_id: str, plan_hash: str) -> dict[str, object]:
    """Return only a host-verified, nonsecret Operations status receipt."""
    if _OPS_JOB_ID.fullmatch(job_id) is None or _OPS_PLAN_HASH.fullmatch(plan_hash) is None:
        raise HTTPException(status_code=400, detail="Invalid Pixel Operations receipt")
    try:
        value = await request_agent_json(
            "GET",
            "/v1/pixel/ops-status",
            params={"job_id": job_id, "plan_hash": plan_hash},
            timeout=7.0,
        )
    except AgentClientError as exc:
        raise HTTPException(status_code=503, detail="Pixel Operations status is unavailable") from exc
    expected = {
        "schemaVersion",
        "kind",
        "jobId",
        "planHash",
        "status",
        "riskTier",
        "approvalRequired",
        "updatedAt",
        "approvalCommand",
    }
    command = value.get("approvalCommand")
    if (
        set(value) != expected
        or value.get("schemaVersion") != 1
        or value.get("kind") != "ods-pixel-operations-status"
        or value.get("jobId") != job_id
        or value.get("planHash") != plan_hash
        or value.get("status") not in _OPS_STATUSES
        or not isinstance(value.get("riskTier"), str)
        or re.fullmatch(r"[a-z][a-z-]{0,31}", value["riskTier"]) is None
        or not isinstance(value.get("approvalRequired"), bool)
        or not isinstance(value.get("updatedAt"), str)
        or not 1 <= len(value["updatedAt"]) <= 64
        or (command is not None and (not isinstance(command, str) or not 1 <= len(command) <= 4096))
    ):
        raise HTTPException(status_code=502, detail="Pixel Operations returned an invalid status")
    return {key: value[key] for key in expected}


def _error_event(message: str) -> bytes:
    payload = {"error": {"message": message, "type": "pixel_dashboard_error"}}
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _cancel_edge_run(edge_url: str, key: str, chat_id: str) -> bool:
    """Best-effort cancellation over the fixed authenticated internal edge."""
    timeout = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST",
                f"{edge_url}/v1/chat/cancel",
                json={"user": chat_id},
                headers=_edge_headers(key, accept="application/json"),
            ) as response:
                if response.status_code != 200:
                    return False
                if not response.headers.get("content-type", "").lower().startswith(
                    "application/json"
                ):
                    return False
                raw = await _bounded_response_bytes(response, 1024)
        parsed = json.loads(raw)
        return (
            isinstance(parsed, dict)
            and set(parsed) == {"aborted"}
            and parsed.get("aborted") is True
        )
    except (
        httpx.HTTPError,
        asyncio.TimeoutError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
        TypeError,
    ):
        return False


@router.post("/chat/cancel", dependencies=[Depends(verify_api_key)])
async def pixel_chat_cancel(body: ChatCancelRequest) -> dict[str, bool]:
    """Cancel only the active run for this validated dashboard conversation.

    The explicit endpoint makes the owner's Stop action independent of HTTP
    disconnect propagation. The stream finalizer retains the same cancellation
    call as an idempotent fallback for tab closes and network failures.
    """
    config = _pixel_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Pixel is not enabled")
    edge_url, key = config
    return {"aborted": await _cancel_edge_run(edge_url, key, body.chat_id)}


class _ClientDisconnected(Exception):
    """The dashboard consumer left while Pixel was still producing a turn."""


async def _iter_upstream_chunks(
    upstream: httpx.Response,
    request: Request,
) -> AsyncIterator[bytes]:
    """Yield upstream bytes while promptly observing a silent client exit."""
    iterator = upstream.aiter_bytes().__aiter__()
    pending: asyncio.Task[bytes] | None = None
    try:
        while True:
            pending = asyncio.create_task(anext(iterator))
            while not pending.done():
                done, _ = await asyncio.wait(
                    {pending},
                    timeout=_CLIENT_DISCONNECT_POLL_SECONDS,
                )
                if done:
                    break
                if await request.is_disconnected():
                    raise _ClientDisconnected
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()


@router.post("/chat/stream", dependencies=[Depends(verify_api_key)])
async def pixel_chat_stream(request: Request, body: ChatStreamRequest) -> StreamingResponse:
    """Forward one bounded chat over authenticated, unbuffered SSE."""
    config = _pixel_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Pixel is not enabled")
    readiness_issue = await _model_readiness_issue()
    if readiness_issue is not None:
        _state, detail = readiness_issue
        raise HTTPException(status_code=409, detail=detail)
    edge_url, key = config
    edge_body = {
        "model": _MODEL,
        "stream": True,
        "user": body.chat_id,
        "messages": [message.model_dump() for message in body.messages],
    }

    # Pixel Edge is capped at 33 minutes; retain one bounded minute of outer
    # headroom so this bridge never aborts a valid CPU-only first turn first.
    timeout = httpx.Timeout(
        connect=5.0,
        read=_CHAT_STREAM_TIMEOUT_SECONDS,
        write=30.0,
        pool=5.0,
    )
    client = httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)
    upstream_context = client.stream(
        "POST",
        f"{edge_url}/v1/chat/completions",
        json=edge_body,
        headers=_edge_headers(key, accept="text/event-stream"),
    )
    try:
        upstream = await upstream_context.__aenter__()
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        await client.aclose()
        logger.warning("Pixel edge stream connection failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Pixel stream is unavailable") from exc
    if upstream.status_code != 200:
        await upstream_context.__aexit__(None, None, None)
        await client.aclose()
        raise HTTPException(status_code=502, detail="Pixel request was rejected")
    if not upstream.headers.get("content-type", "").lower().startswith("text/event-stream"):
        await upstream_context.__aexit__(None, None, None)
        await client.aclose()
        raise HTTPException(status_code=502, detail="Pixel returned an invalid stream")

    begin_pixel_stream()

    async def stream() -> AsyncIterator[bytes]:
        done_seen = False
        try:
            async with asyncio.timeout(_CHAT_STREAM_TIMEOUT_SECONDS):
                buffered = bytearray()
                async for chunk in _iter_upstream_chunks(upstream, request):
                    buffered.extend(chunk)
                    while True:
                        newline = buffered.find(b"\n")
                        if newline < 0:
                            break
                        line = bytes(buffered[: newline + 1])
                        del buffered[: newline + 1]
                        if len(line.rstrip(b"\r\n")) > _MAX_SSE_LINE_BYTES:
                            yield _error_event("Pixel stream exceeded its safety limit")
                            yield b"data: [DONE]\n\n"
                            return
                        yield line
                        if line.rstrip(b"\r\n") == b"data: [DONE]":
                            done_seen = True
                    if len(buffered) > _MAX_SSE_LINE_BYTES:
                        yield _error_event("Pixel stream exceeded its safety limit")
                        yield b"data: [DONE]\n\n"
                        return
                if buffered:
                    yield bytes(buffered)
        except _ClientDisconnected:
            return
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except (httpx.HTTPError, asyncio.TimeoutError):
            yield _error_event("Pixel stream is unavailable")
        except Exception:
            yield _error_event("Pixel stream failed")
        finally:
            if not done_seen:
                cancel_task = asyncio.create_task(_cancel_edge_run(edge_url, key, body.chat_id))
                try:
                    await asyncio.wait_for(
                        asyncio.shield(cancel_task),
                        timeout=_CLIENT_CANCEL_TIMEOUT_SECONDS,
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            await upstream_context.__aexit__(None, None, None)
            await client.aclose()
            end_pixel_stream()
        if not done_seen:
            yield b"data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
