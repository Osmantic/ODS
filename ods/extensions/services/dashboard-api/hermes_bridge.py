"""Small server-side bridge from ODS Talk to the pinned Hermes dashboard.

ODS Talk deliberately does not expose Hermes's browser session token to the
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

Per-cookie connection pool (issue #1322): instead of opening a new WS for
every ODS Talk message, we hold one ``HermesConnection`` per ``session_key``
in a process-wide pool and reuse the same Hermes session across messages from
the same phone. Hermes's per-WS event scoping makes this work — the same WS
is the owner, so events keep flowing. The big win is llama-server's
prompt-cache stays warm across messages, so the second "hey" doesn't pay the
30-60s prefill of the 16k-token agent system prompt. An idle sweeper closes
connections that have been quiet for >5 minutes so a fleet of one-time
visitors doesn't pin Hermes resources forever.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"')
DEFAULT_HERMES_URL = "http://ods-hermes:9119"
DEFAULT_TIMEOUT_SECONDS = 180


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "")
    if raw.isdigit():
        return max(minimum, int(raw))
    return default


# Connection pool tuning. Override per environment if needed.
_IDLE_EXPIRY_SECONDS = _env_int("ODS_TALK_IDLE_EXPIRY", 300)  # 5 min default
_IDLE_SWEEP_INTERVAL = 60  # how often the background sweeper runs


class HermesBridgeError(RuntimeError):
    """Base bridge error surfaced as a 502/503 by the talk router."""


class HermesUnavailable(HermesBridgeError):
    """Hermes is not reachable or did not expose the expected dashboard API."""


class HermesConnectionStale(HermesUnavailable):
    """The pooled WebSocket died before a prompt was submitted.

    This is safe to retry transparently because Hermes never accepted the
    prompt on that transport. Once prompt.submit has been sent, later
    connection drops surface as errors instead of retrying and potentially
    duplicating tool calls or streamed text.
    """


@dataclass
class HermesReply:
    session_id: str
    text: str
    status: str = "ok"
    warning: str | None = None


def _base_url() -> str:
    return (os.environ.get("HERMES_INTERNAL_URL") or DEFAULT_HERMES_URL).rstrip("/")


def _request_timeout() -> int:
    raw = os.environ.get("ODS_TALK_HERMES_TIMEOUT", "")
    if raw.isdigit():
        return max(10, int(raw))
    return DEFAULT_TIMEOUT_SECONDS


def talk_session_key(cookie_value: str) -> str:
    """Stable opaque key for the lifetime of a ods-session cookie."""
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
    request_id = "ods-talk-create"
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


@dataclass
class _HermesConnection:
    """One long-lived WS + Hermes session, scoped to a single phone cookie.

    Holding the WS open across messages lets Hermes reuse its session_id and
    keeps the 16k-token system prompt warm in llama-server's KV cache. Each
    new prompt on the same connection costs ~1k tokens of context (the user
    message + maybe a tool result), not 16k+1k. Big latency win.

    The per-connection ``lock`` serializes two prompts from the same phone:
    Hermes can't multiplex two prompt.submit calls on one session anyway,
    and the SPA's UI already enforces "wait for the previous reply" — this
    is the server-side belt to that suspenders.
    """
    http_session: aiohttp.ClientSession
    ws: aiohttp.ClientWebSocketResponse
    session_id: str
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.ws.close()
        except Exception:  # pragma: no cover — best-effort cleanup
            logger.debug("ws.close raised during pool eviction", exc_info=True)
        try:
            await self.http_session.close()
        except Exception:  # pragma: no cover
            logger.debug("http_session.close raised during pool eviction", exc_info=True)


_CONNECTION_POOL: dict[str, _HermesConnection] = {}
_POOL_GUARD = asyncio.Lock()
# Per-key creation locks: two concurrent first-messages from the same phone
# share one open instead of racing, without holding the global guard.
_OPENING_LOCKS: dict[str, asyncio.Lock] = {}
_SWEEPER_TASK: asyncio.Task | None = None


async def _open_connection(session_key: str) -> _HermesConnection:
    """Open one fresh WS + run session.create.

    Caller holds the per-key opening lock, NOT _POOL_GUARD — this does
    network I/O that can hang for the full client timeout when Hermes is
    slow or down, and must never stall other phones' pool lookups.
    """
    timeout_seconds = _request_timeout()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds + 20)
    http_session = aiohttp.ClientSession(timeout=timeout)
    try:
        ws = await _connect_ws(http_session)
    except Exception:
        await http_session.close()
        raise
    try:
        session_id = await _create_session_on_ws(ws, timeout=30)
    except Exception:
        await ws.close()
        await http_session.close()
        raise
    conn = _HermesConnection(http_session=http_session, ws=ws, session_id=session_id)
    logger.info("hermes-bridge: opened pooled connection for %s (session_id=%s)", session_key[:8], session_id)
    return conn


async def _get_connection(session_key: str) -> _HermesConnection:
    """Look up the pooled connection for this session_key, or create one.

    Caller must use ``async with conn.lock:`` around any send/receive cycle
    to keep concurrent same-key prompts from interleaving on the same WS.

    _POOL_GUARD is held only for dict bookkeeping. Opening a connection
    (token fetch + ws connect + session.create) is network I/O that can
    hang for the full client timeout when Hermes is slow or down; holding
    the global guard across it would head-of-line block every other
    phone's warm-connection lookup behind one cold open. The per-key
    opening lock keeps concurrent same-key calls from opening duplicates.
    """
    async with _POOL_GUARD:
        conn = _CONNECTION_POOL.get(session_key)
        if conn is not None and not conn.closed and not conn.ws.closed:
            return conn
        opening = _OPENING_LOCKS.setdefault(session_key, asyncio.Lock())

    async with opening:
        # Re-check under the guard: another same-key call may have finished
        # opening while this one waited on the opening lock.
        async with _POOL_GUARD:
            conn = _CONNECTION_POOL.get(session_key)
            if conn is not None and not conn.closed and not conn.ws.closed:
                return conn
            # Either no entry, marked closed, or the underlying ws died.
            # Drop whatever is in the slot and open fresh.
            stale = _CONNECTION_POOL.pop(session_key, None)
        if stale is not None:
            await stale.aclose()
        new_conn = await _open_connection(session_key)
        async with _POOL_GUARD:
            _CONNECTION_POOL[session_key] = new_conn
        return new_conn


async def _drop_connection(session_key: str, conn: _HermesConnection) -> None:
    """Evict a connection from the pool (e.g. after a dead-WS error)."""
    async with _POOL_GUARD:
        current = _CONNECTION_POOL.get(session_key)
        if current is conn:
            _CONNECTION_POOL.pop(session_key, None)
    await conn.aclose()


async def _sweep_idle_connections() -> None:
    """Background task: close pool entries idle for > _IDLE_EXPIRY_SECONDS.

    Runs forever; the task is held in ``_SWEEPER_TASK``. Cancelled by
    ``shutdown_pool()`` on app shutdown.
    """
    while True:
        try:
            await asyncio.sleep(_IDLE_SWEEP_INTERVAL)
            now = time.monotonic()
            stale: list[tuple[str, _HermesConnection]] = []
            async with _POOL_GUARD:
                for key, conn in list(_CONNECTION_POOL.items()):
                    if conn.lock.locked():
                        # Active prompt. Do not close the WS while Hermes is
                        # streaming or pre-filling a long response; last_used
                        # is refreshed again when the prompt completes.
                        continue
                    if conn.closed or conn.ws.closed:
                        stale.append((key, conn))
                        _CONNECTION_POOL.pop(key, None)
                        continue
                    if now - conn.last_used > _IDLE_EXPIRY_SECONDS:
                        stale.append((key, conn))
                        _CONNECTION_POOL.pop(key, None)
                # Opening locks are keyed by cookie hash and would otherwise
                # grow forever; drop the ones that are idle and unpooled.
                for key, lock in list(_OPENING_LOCKS.items()):
                    if not lock.locked() and key not in _CONNECTION_POOL:
                        del _OPENING_LOCKS[key]
            for key, conn in stale:
                logger.info("hermes-bridge: evicting idle connection for %s", key[:8])
                await conn.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover — keep sweeper alive on bugs
            logger.exception("hermes-bridge: idle sweeper hit an error; continuing")


def _ensure_sweeper_running() -> None:
    """Lazily start the idle sweeper on first use. Idempotent."""
    global _SWEEPER_TASK
    if _SWEEPER_TASK is not None and not _SWEEPER_TASK.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _SWEEPER_TASK = loop.create_task(_sweep_idle_connections())


async def shutdown_pool() -> None:
    """Close every pooled connection. Call from a FastAPI lifespan handler
    so a graceful shutdown doesn't leak WSes / file descriptors."""
    global _SWEEPER_TASK
    if _SWEEPER_TASK is not None:
        _SWEEPER_TASK.cancel()
        try:
            await _SWEEPER_TASK
        except (asyncio.CancelledError, Exception):
            pass
        _SWEEPER_TASK = None
    async with _POOL_GUARD:
        connections = list(_CONNECTION_POOL.values())
        _CONNECTION_POOL.clear()
        _OPENING_LOCKS.clear()
    for conn in connections:
        await conn.aclose()


async def _submit_on_connection(
    conn: _HermesConnection, text: str, timeout_seconds: int,
) -> AsyncIterator[dict[str, Any]]:
    """Send one prompt on an already-open pooled connection and yield events.

    Caller must hold ``conn.lock`` for the duration of this generator so two
    same-key prompts can't interleave on the same WS. Mutates ``conn.last_used``
    on completion. Raises HermesUnavailable when the WS is dead (caller is
    expected to evict + reopen), HermesBridgeError on protocol-level errors.
    """
    request_id = f"ods-talk-prompt-{int(time.monotonic() * 1000)}"
    try:
        conn.last_used = time.monotonic()
        await conn.ws.send_str(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "prompt.submit",
            "params": {"session_id": conn.session_id, "text": text},
        }))
    except (aiohttp.ClientError, ConnectionResetError, ConnectionError) as exc:
        # Pooled WS was closed under us between the freshness check and this
        # send (Hermes restart, network blip, idle timeout that hadn't been
        # noticed yet). Surface as HermesConnectionStale so stream_prompt
        # evicts the pool entry + retries on a fresh connection. This is the
        # only transparent-retry case because the prompt was not submitted.
        raise HermesConnectionStale(f"Hermes WS closed before prompt submit: {exc}") from exc

    chunks: list[str] = []
    while True:
        frame = await _recv_json(conn.ws, timeout_seconds)

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
        if event.get("session_id") and event.get("session_id") != conn.session_id:
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
        elif event_type == "status.update":
            # Hermes (pinned image) emits status.update events with a pre-
            # formatted human-readable text and a `kind` category. Examples
            # we've observed in the wild:
            #
            #   {"kind": "lifecycle", "text": "⏳ Retrying in 5.1s (attempt 2/3)..."}
            #   {"kind": "lifecycle", "text": "⚠️ Max retries (3) exhausted..."}
            #
            # Upstream is responsible for the human-readable wording (often
            # with emoji prefixes); we just forward the text so the SPA's
            # spinner caption shows what Hermes is actually doing. The
            # "Searching the web…" / "Reading the page…" labels we add on
            # the SSE-layer side are for older `tool.start`-style events
            # that some builds may still emit; this branch handles the
            # newer status.update path.
            status_text = payload.get("text") if isinstance(payload.get("text"), str) else None
            if status_text:
                yield {
                    "type": "status",
                    "label": status_text,
                    "kind": payload.get("kind") if isinstance(payload.get("kind"), str) else None,
                }
        elif event_type == "tool.start":
            # Older-build path: Hermes may also emit explicit tool.start
            # events with name + context. We translate those into status
            # frames via the SSE layer's _label_for_tool() mapping.
            tool_name = payload.get("name") if isinstance(payload.get("name"), str) else None
            if tool_name:
                yield {
                    "type": "tool_start",
                    "tool": tool_name,
                    "detail": payload.get("context") if isinstance(payload.get("context"), str) else None,
                }
        elif event_type == "tool.complete":
            # Older-build path companion: tool finished, clear the caption.
            tool_name = payload.get("name") if isinstance(payload.get("name"), str) else None
            if tool_name:
                yield {
                    "type": "tool_complete",
                    "tool": tool_name,
                    "duration_s": payload.get("duration_s") if isinstance(payload.get("duration_s"), (int, float)) else None,
                    "summary": payload.get("summary") if isinstance(payload.get("summary"), str) else None,
                }
        elif event_type == "message.complete":
            final_text = payload.get("text")
            if not isinstance(final_text, str) or not final_text.strip():
                final_text = "".join(chunks)
            conn.last_used = time.monotonic()
            yield {
                "type": "complete",
                "session_id": conn.session_id,
                "text": final_text.strip(),
                "status": str(payload.get("status") or "ok"),
                "warning": payload.get("warning") if isinstance(payload.get("warning"), str) else None,
            }
            return
        elif event_type == "error":
            message = payload.get("message") if isinstance(payload.get("message"), str) else "Hermes reported an error"
            raise HermesBridgeError(message)


async def stream_prompt(session_key: str, text: str) -> AsyncIterator[dict[str, Any]]:
    """Submit a prompt to Hermes and yield delta events as they stream back.

    Yields dicts with:
      {"type": "session",        "session_id": <id>}                          # once at start
      {"type": "tool_start",     "tool": <name>, "detail": <context|None>}    # zero or more
      {"type": "tool_complete",  "tool": <name>, "duration_s": <float|None>,
                                 "summary": <str|None>}                       # zero or more
      {"type": "delta",          "text": <chunk>}                             # zero or more
      {"type": "complete",       "session_id": <id>, "text": <full>, ...}     # once at end

    On error, raises HermesUnavailable / HermesBridgeError; no partial yield.

    Uses the per-cookie connection pool so subsequent messages from the same
    phone reuse the same Hermes session_id (and therefore keep llama-server's
    prompt cache warm for the 16k-token agent system prompt). On the first
    call for a cookie, opens a fresh WS and runs session.create; on later
    calls, reuses the pooled connection and just sends prompt.submit. The
    pool's idle sweeper closes inactive connections after
    ODS_TALK_IDLE_EXPIRY seconds (default 300s = 5 min) so a fleet of
    one-time visitors doesn't pin resources forever.

    Two same-key prompts can't overlap (per-connection ``lock`` serializes
    them); two different-key prompts run in parallel as long as
    llama-server's slots can absorb the load.
    """
    _ensure_sweeper_running()
    timeout_seconds = _request_timeout()

    # Attempt twice only for the pre-submit stale-WS case: the first try uses
    # the pooled connection (warm cache, fast path); if send_str fails before
    # Hermes accepts prompt.submit, evict it and try once more with a fresh
    # connection. Once prompt.submit has been sent, later WS failures surface
    # as errors instead of retrying and duplicating tool calls or text.
    session_frame_emitted = False
    for attempt in (1, 2):
        conn = await _get_connection(session_key)
        async with conn.lock:
            if not session_frame_emitted:
                # Yield the session frame once, from the *first* attempt's
                # connection. If the connection dies before any deltas, the
                # retry's session_id will be different but the SPA only cares
                # about the most recent — replacing the frame is fine.
                yield {"type": "session", "session_id": conn.session_id}
                session_frame_emitted = True
            try:
                async for event in _submit_on_connection(conn, text, timeout_seconds):
                    yield event
                return
            except HermesConnectionStale as exc:
                await _drop_connection(session_key, conn)
                if attempt == 2:
                    # Already retried once; give up and surface the failure.
                    raise
                logger.info("hermes-bridge: retrying once after stale WS (%s)", exc)
                # Drop the lock + loop to attempt 2 with a fresh connection.
                continue
            except HermesUnavailable:
                await _drop_connection(session_key, conn)
                raise


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
