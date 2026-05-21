"""Small server-side bridge from Dream Talk to the pinned Hermes dashboard.

Dream Talk deliberately does not expose Hermes's browser session token to the
phone. The dashboard-api fetches that token from the internal Hermes HTML page,
opens the JSON-RPC WebSocket on the Docker network, and returns only simplified
chat results to the mobile portal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"')
DEFAULT_HERMES_URL = "http://dream-hermes:9119"
DEFAULT_TIMEOUT_SECONDS = 180

_SESSION_IDS: dict[str, str] = {}
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()


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


async def _session_lock(key: str) -> asyncio.Lock:
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[key] = lock
        return lock


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


async def _rpc_request(
    ws: aiohttp.ClientWebSocketResponse,
    request_id: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    await ws.send_str(json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }))

    while True:
        frame = await _recv_json(ws, timeout)
        if frame.get("id") != request_id:
            continue
        if frame.get("error"):
            err = frame["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise HermesBridgeError(message or f"Hermes request failed: {method}")
        result = frame.get("result")
        return result if isinstance(result, dict) else {}


async def create_session(session_key: str) -> str:
    timeout = aiohttp.ClientTimeout(total=_request_timeout())
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ws = await _connect_ws(session)
        async with ws:
            result = await _rpc_request(ws, "dream-talk-session", "session.create")
    session_id = str(result.get("session_id") or result.get("id") or "").strip()
    if not session_id:
        raise HermesBridgeError("Hermes did not return a session id")
    _SESSION_IDS[session_key] = session_id
    return session_id


async def ensure_session(session_key: str) -> str:
    existing = _SESSION_IDS.get(session_key)
    if existing:
        return existing
    lock = await _session_lock(session_key)
    async with lock:
        existing = _SESSION_IDS.get(session_key)
        if existing:
            return existing
        return await create_session(session_key)


async def submit_prompt(session_key: str, text: str) -> HermesReply:
    session_id = await ensure_session(session_key)
    timeout_seconds = _request_timeout()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds + 20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        ws = await _connect_ws(session)
        async with ws:
            request_id = "dream-talk-prompt"
            await ws.send_str(json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "prompt.submit",
                "params": {"session_id": session_id, "text": text},
            }))

            chunks: list[str] = []
            prompt_result_seen = False
            while True:
                frame = await _recv_json(ws, timeout_seconds)
                if frame.get("id") == request_id:
                    if frame.get("error"):
                        err = frame["error"]
                        message = err.get("message") if isinstance(err, dict) else str(err)
                        # A stale in-memory session after a Hermes restart is
                        # recoverable. Drop it once; the caller can retry.
                        if "session" in (message or "").lower():
                            _SESSION_IDS.pop(session_key, None)
                        raise HermesBridgeError(message or "Hermes prompt failed")
                    prompt_result_seen = True
                    continue

                if frame.get("method") != "event":
                    continue
                event = frame.get("params") or {}
                if not isinstance(event, dict):
                    continue
                if event.get("session_id") and event.get("session_id") != session_id:
                    continue

                payload = event.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = {}

                event_type = event.get("type")
                if event_type == "message.delta":
                    chunk = payload.get("text")
                    if isinstance(chunk, str):
                        chunks.append(chunk)
                elif event_type == "message.complete":
                    final_text = payload.get("text")
                    if not isinstance(final_text, str) or not final_text.strip():
                        final_text = "".join(chunks)
                    return HermesReply(
                        session_id=session_id,
                        text=final_text.strip(),
                        status=str(payload.get("status") or "ok"),
                        warning=payload.get("warning") if isinstance(payload.get("warning"), str) else None,
                    )
                elif event_type == "error":
                    message = payload.get("message") if isinstance(payload.get("message"), str) else "Hermes reported an error"
                    raise HermesBridgeError(message)

                if prompt_result_seen and chunks:
                    # Some Hermes builds can resolve the request without a
                    # complete event in failure paths. Keep waiting for the
                    # preferred complete event until timeout.
                    continue


def clear_session_for_tests(session_key: str | None = None) -> None:
    if session_key is None:
        _SESSION_IDS.clear()
        _SESSION_LOCKS.clear()
    else:
        _SESSION_IDS.pop(session_key, None)
        _SESSION_LOCKS.pop(session_key, None)
