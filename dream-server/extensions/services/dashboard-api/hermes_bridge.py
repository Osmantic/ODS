"""Small server-side bridge from Dream Talk to the pinned Hermes dashboard.

Dream Talk deliberately does not expose Hermes's browser session token to the
phone. The dashboard-api fetches that token from the internal Hermes HTML page,
opens the JSON-RPC WebSocket on the Docker network, and returns only simplified
chat results to the mobile portal.

Architectural note: Hermes scopes streaming event delivery to the WebSocket
that owns the session. If we open WS-A for ``session.create`` and then open a
fresh WS-B for ``prompt.submit``, Hermes accepts the submit (returns
``{"status":"streaming"}``) but the streaming events fire to WS-A — which we
already closed. The bridge would then wait forever for events that never
arrive and 502 at the request timeout. So a single submit_prompt / stream_prompt
call MUST do both create-session and submit-prompt on the same WS.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"')
DEFAULT_HERMES_URL = "http://dream-hermes:9119"
DEFAULT_TIMEOUT_SECONDS = 180


class HermesBridgeError(RuntimeError):
    """Base bridge error surfaced as a 502/503 by the talk router."""


class HermesUnavailable(HermesBridgeError):
    """Hermes is not reachable or did not expose the expected dashboard API."""


@dataclass
class HermesReply:
    session_id: str
    text: str
    status: str = "ok"
    warning: str | None = None


def _base_url() -> str:
    return (os.environ.get("HERMES_INTERNAL_URL") or DEFAULT_HERMES_URL).rstrip("/")


def _request_timeout() -> int:
    raw = os.environ.get("DREAM_TALK_HERMES_TIMEOUT", "")
    if raw.isdigit():
        return max(10, int(raw))
    return DEFAULT_TIMEOUT_SECONDS


def talk_session_key(cookie_value: str) -> str:
    """Stable opaque key for the lifetime of a dream-session cookie."""
    return hashlib.sha256(cookie_value.encode("utf-8")).hexdigest()


async def _fetch_hermes_token(session: aiohttp.ClientSession) -> str:
    url = _base_url()
    try:
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise HermesUnavailable(f"Hermes dashboard returned HTTP {resp.status}")
            html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise HermesUnavailable("Hermes dashboard is not reachable") from exc

    match = TOKEN_RE.search(html)
    if not match:
        raise HermesUnavailable("Hermes dashboard token was not found")
    return match.group(1)


async def _connect_ws(session: aiohttp.ClientSession) -> aiohttp.ClientWebSocketResponse:
    token = await _fetch_hermes_token(session)
    ws_base = _base_url().replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    url = f"{ws_base}/api/ws?token={token}"
    try:
        return await session.ws_connect(url)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise HermesUnavailable("Hermes JSON-RPC websocket is not reachable") from exc


async def _recv_json(ws: aiohttp.ClientWebSocketResponse, timeout: float) -> dict[str, Any]:
    msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
    if msg.type == aiohttp.WSMsgType.TEXT:
        try:
            return json.loads(msg.data)
        except json.JSONDecodeError as exc:
            raise HermesBridgeError("Hermes sent malformed JSON") from exc
    if msg.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
        raise HermesUnavailable("Hermes websocket closed")
    return {}


async def _create_session_on_ws(ws: aiohttp.ClientWebSocketResponse, *, timeout: float = 30) -> str:
    """Run session.create over an already-open WS and return the session_id."""
    request_id = "dream-talk-create"
    await ws.send_str(json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "session.create",
        "params": {},
    }))
    while True:
        frame = await _recv_json(ws, timeout)
        if frame.get("id") != request_id:
            # Pre-create events (gateway.ready etc.) can arrive before the
            # session.create result lands. Ignore them and keep reading.
            continue
        if frame.get("error"):
            err = frame["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise HermesBridgeError(message or "Hermes session.create failed")
        result = frame.get("result")
        if not isinstance(result, dict):
            raise HermesBridgeError("Hermes session.create returned an unexpected shape")
        session_id = str(result.get("session_id") or result.get("id") or "").strip()
        if not session_id:
            raise HermesBridgeError("Hermes did not return a session id")
        return session_id


async def stream_prompt(session_key: str, text: str) -> AsyncIterator[dict[str, Any]]:
    """Submit a prompt to Hermes and yield delta events as they stream back.

    Yields dicts with:
      {"type": "session",  "session_id": <id>}                      # once at start
      {"type": "delta",    "text": <chunk>}                          # zero or more
      {"type": "complete", "session_id": <id>, "text": <full>, ...}  # once at end

    On error, raises HermesUnavailable / HermesBridgeError; no partial yield.

    The session_key argument is currently advisory — Hermes per-WS event scoping
    means we can't safely persist a session across submit calls (see module
    docstring), so each call creates a fresh Hermes session. Cross-call
    conversational memory is provided by Hermes's own agent memory layer.
    """
    timeout_seconds = _request_timeout()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds + 20)

    async with aiohttp.ClientSession(timeout=timeout) as http_session:
        ws = await _connect_ws(http_session)
        async with ws:
            session_id = await _create_session_on_ws(ws, timeout=30)
            yield {"type": "session", "session_id": session_id}

            request_id = "dream-talk-prompt"
            await ws.send_str(json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "prompt.submit",
                "params": {"session_id": session_id, "text": text},
            }))

            chunks: list[str] = []
            while True:
                frame = await _recv_json(ws, timeout_seconds)

                # Reply to our prompt.submit RPC — informational; events still follow.
                if frame.get("id") == request_id:
                    if frame.get("error"):
                        err = frame["error"]
                        message = err.get("message") if isinstance(err, dict) else str(err)
                        raise HermesBridgeError(message or "Hermes prompt failed")
                    continue

                if frame.get("method") != "event":
                    continue
                event = frame.get("params") or {}
                if not isinstance(event, dict):
                    continue
                if event.get("session_id") and event.get("session_id") != session_id:
                    # Stray event from a sibling session — ignore.
                    continue

                payload = event.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = {}

                event_type = event.get("type")
                if event_type == "message.delta":
                    chunk = payload.get("text")
                    if isinstance(chunk, str) and chunk:
                        chunks.append(chunk)
                        yield {"type": "delta", "text": chunk}
                elif event_type == "message.complete":
                    final_text = payload.get("text")
                    if not isinstance(final_text, str) or not final_text.strip():
                        final_text = "".join(chunks)
                    yield {
                        "type": "complete",
                        "session_id": session_id,
                        "text": final_text.strip(),
                        "status": str(payload.get("status") or "ok"),
                        "warning": payload.get("warning") if isinstance(payload.get("warning"), str) else None,
                    }
                    return
                elif event_type == "error":
                    message = payload.get("message") if isinstance(payload.get("message"), str) else "Hermes reported an error"
                    raise HermesBridgeError(message)


async def submit_prompt(session_key: str, text: str) -> HermesReply:
    """Blocking wrapper that consumes stream_prompt and returns the final reply.

    Kept for callers (and tests) that want the full reply as a single dict
    instead of an event stream. New UI code should use stream_prompt directly
    so the user sees tokens land in real time.
    """
    session_id = ""
    final_text = ""
    status = "ok"
    warning: str | None = None
    async for event in stream_prompt(session_key, text):
        et = event.get("type")
        if et == "session":
            session_id = event.get("session_id", "") or session_id
        elif et == "complete":
            session_id = event.get("session_id", "") or session_id
            final_text = event.get("text", "") or final_text
            status = event.get("status") or "ok"
            warning = event.get("warning")
    if not session_id and not final_text:
        raise HermesBridgeError("Hermes did not finish the response.")
    return HermesReply(session_id=session_id, text=final_text, status=status, warning=warning)


# -------- legacy compat shims (kept so existing tests keep importing OK) --------

_SESSION_IDS: dict[str, str] = {}


async def ensure_session(session_key: str) -> str:
    """Legacy: tests call this to seed _SESSION_IDS before invoking submit_prompt.

    The streaming bridge now creates a fresh Hermes session per call, so the
    stored value is informational only. We still return *something* truthy so
    tests that assert "ensure_session returned a non-empty string" pass.
    """
    existing = _SESSION_IDS.get(session_key)
    if existing:
        return existing
    timeout_seconds = _request_timeout()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as http_session:
        ws = await _connect_ws(http_session)
        async with ws:
            session_id = await _create_session_on_ws(ws, timeout=30)
    _SESSION_IDS[session_key] = session_id
    return session_id


def clear_session_for_tests(session_key: str | None = None) -> None:
    if session_key is None:
        _SESSION_IDS.clear()
    else:
        _SESSION_IDS.pop(session_key, None)
