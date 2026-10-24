"""Streaming text chat with the Windows Desktop profile's own llama-server.

Desktop has no Portal agent stack, no LiteLLM and no host agent: this router
talks to the loopback llama-server the native runtime started, with its key
file. History is fitted to the model's real context before every request
(see context_budget.py), and every failure has a fixed code and sentence.
"""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import native_llm
from context_budget import MessageTooLong, fit
from routers.pixel import _ClientDisconnected, _iter_upstream_chunks
from security import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["local-chat"])

ANSWER_TOKENS = 1024
_MAX_SSE_LINE_BYTES = 1024 * 1024
_PROBE_TIMEOUT = httpx.Timeout(5.0)
# Prompt processing on a CPU can be silent for minutes; only a stall between
# bytes ends the stream, never a total deadline.
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)
# This app and the model's task both start at sign-in, and the model's
# launcher checks its files before llama-server even listens. Until then a
# silent model is starting, not broken.
_STARTED_AT = time.monotonic()
_START_GRACE_SECONDS = 180

_MESSAGES = {
    "model_starting": "The model is starting. This can take a few minutes after you sign in.",
    "model_loading": "The model is still loading. Try again in a moment.",
    "model_stopped": "The local model is stopped. Start ODS from the Start menu to chat again.",
    "model_failed": "The local model could not start. Open ODS setup to repair it.",
    "model_unavailable": "The local model is not answering. Restart ODS and try again.",
    "message_too_long": "This message is too long for the model. Shorten it or split it into parts.",
    "upstream_error": "The local model returned an error. Try again; if it repeats, restart ODS.",
}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=32 * 1024)


class LocalChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=400)
    system: Optional[str] = Field(default=None, max_length=16 * 1024)
    # The contextStart the previous answer reported. Sending it back keeps the
    # model's prompt prefix unchanged, so its cache answers in about a second.
    contextStart: Optional[int] = Field(default=None, ge=0)


class LocalChatError(Exception):
    def __init__(self, code: str, status: int, **detail: int) -> None:
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


def _error_body(code: str, **detail: int) -> dict:
    return {"error": {"code": code, "message": _MESSAGES[code], **detail}}


def _error_event(code: str) -> bytes:
    return f"data: {json.dumps(_error_body(code))}\n\n".encode()


def _llm() -> native_llm.NativeLlm:
    llm = native_llm.configured()
    if llm is None:
        raise HTTPException(status_code=404, detail="Local chat is part of the Windows Desktop profile")
    return llm


def _offline_state(llm: native_llm.NativeLlm) -> str:
    intent, ready = native_llm.runtime_records(llm)
    if intent and intent.get("State") == "stopped":
        return "model_stopped"
    if ready and ready.get("Error"):
        return "model_failed"
    if time.monotonic() - _STARTED_AT < _START_GRACE_SECONDS:
        return "model_starting"
    return "model_unavailable"


async def _health(client: httpx.AsyncClient, llm: native_llm.NativeLlm) -> str:
    """'ready', or the error code that explains why the model cannot answer."""
    try:
        response = await client.get(f"{llm.base_url}/health", timeout=_PROBE_TIMEOUT)
    except httpx.TransportError:
        return _offline_state(llm)
    if response.status_code == 200:
        return "ready"
    if response.status_code == 503:
        return "model_loading"
    return "upstream_error"


async def _context_size(client: httpx.AsyncClient, llm: native_llm.NativeLlm) -> int:
    response = await client.get(f"{llm.base_url}/props", headers=native_llm.headers(llm), timeout=_PROBE_TIMEOUT)
    response.raise_for_status()
    n_ctx = response.json().get("default_generation_settings", {}).get("n_ctx")
    if not isinstance(n_ctx, int) or n_ctx <= 0:
        raise LocalChatError("upstream_error", 502)
    return n_ctx


def _measurer(client: httpx.AsyncClient, llm: native_llm.NativeLlm):
    async def measure(messages: list[dict[str, str]]) -> int:
        # Exactly what llama-server will evaluate: its chat template, then its
        # tokenizer with special tokens, as handle_completions_impl does.
        rendered = await client.post(f"{llm.base_url}/apply-template", headers=native_llm.headers(llm),
                                     json={"messages": messages}, timeout=_PROBE_TIMEOUT)
        rendered.raise_for_status()
        counted = await client.post(f"{llm.base_url}/tokenize", headers=native_llm.headers(llm),
                                    json={"content": rendered.json()["prompt"], "add_special": True,
                                          "parse_special": True}, timeout=_PROBE_TIMEOUT)
        counted.raise_for_status()
        return len(counted.json()["tokens"])
    return measure


@router.get("/api/local-chat/status", dependencies=[Depends(verify_api_key)])
async def local_chat_status():
    llm = _llm()
    async with httpx.AsyncClient(trust_env=False) as client:
        state = await _health(client, llm)
        if state != "ready":
            return {"state": state, "message": _MESSAGES[state]}
        _, ready = native_llm.runtime_records(llm)
        return {"state": "ready", "model": (ready or {}).get("ModelId"),
                "contextSize": await _context_size(client, llm)}


@router.post("/api/local-chat/stream", dependencies=[Depends(verify_api_key)])
async def local_chat_stream(body: LocalChatRequest, request: Request):
    llm = _llm()
    turns = [message.model_dump() for message in body.messages]
    if turns[-1]["role"] != "user":
        raise HTTPException(status_code=422, detail="The last message must be the user's")
    client = httpx.AsyncClient(trust_env=False)
    upstream: Optional[httpx.Response] = None
    try:
        state = await _health(client, llm)
        if state != "ready":
            raise LocalChatError(state, 503)
        n_ctx = await _context_size(client, llm)
        answer_tokens = min(ANSWER_TOKENS, n_ctx // 4)  # A small context still leaves room to ask.
        try:
            fitted = await fit(body.system, turns[:-1], turns[-1]["content"],
                               n_ctx=n_ctx, reserve=answer_tokens, measure=_measurer(client, llm),
                               start_hint=body.contextStart)
        except MessageTooLong as exc:
            raise LocalChatError("message_too_long", 413, promptTokens=exc.prompt_tokens,
                                 budgetTokens=exc.budget) from exc
        payload = {"messages": fitted.messages, "stream": True, "max_tokens": answer_tokens,
                   "stream_options": {"include_usage": True}}
        upstream = await client.send(client.build_request(
            "POST", f"{llm.base_url}/v1/chat/completions", headers=native_llm.headers(llm),
            json=payload, timeout=_STREAM_TIMEOUT), stream=True)
        if upstream.status_code != 200 or not upstream.headers.get("content-type", "").startswith("text/event-stream"):
            raise LocalChatError("upstream_error", 502)
    except LocalChatError as exc:
        await _close(client, upstream)
        return JSONResponse(_error_body(exc.code, **exc.detail), status_code=exc.status)
    except (httpx.HTTPError, KeyError, ValueError):
        await _close(client, upstream)
        logger.warning("Local chat could not reach llama-server", exc_info=True)
        return JSONResponse(_error_body("model_unavailable"), status_code=503)
    except BaseException:
        await _close(client, upstream)
        raise

    async def stream() -> AsyncIterator[bytes]:
        # First event: what was sent, so the owner can see when old turns
        # were left out of the model's view (they stay in the saved chat).
        yield ("event: ods\ndata: " + json.dumps({"droppedMessages": fitted.dropped_messages,
                                                 "contextStart": fitted.dropped_messages,
                                                 "promptTokens": fitted.prompt_tokens}) + "\n\n").encode()
        done_seen = False
        buffered = bytearray()
        try:
            async for chunk in _iter_upstream_chunks(upstream, request, lambda: not buffered):
                buffered.extend(chunk)
                while (newline := buffered.find(b"\n")) >= 0:
                    line = bytes(buffered[: newline + 1])
                    del buffered[: newline + 1]
                    if len(line) > _MAX_SSE_LINE_BYTES:
                        yield _error_event("upstream_error")
                        return
                    done_seen = done_seen or line.rstrip(b"\r\n") == b"data: [DONE]"
                    yield line
                if len(buffered) > _MAX_SSE_LINE_BYTES:
                    yield _error_event("upstream_error")
                    return
            if buffered:
                done_seen = done_seen or buffered.rstrip(b"\r\n") == b"data: [DONE]"
                yield bytes(buffered) + b"\n"
        except _ClientDisconnected:
            return  # Closing the upstream response stops generation.
        except httpx.HTTPError:
            yield _error_event("model_unavailable")
        finally:
            await _close(client, upstream)
        # Outside finally: a generator must not yield while being cancelled.
        if not done_seen:
            yield b"data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})


async def _close(client: httpx.AsyncClient, upstream: Optional[httpx.Response]) -> None:
    if upstream is not None:
        await upstream.aclose()
    await client.aclose()
