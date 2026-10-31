"""Authenticated ODS Talk transport with optional durable conversation identity.

Default mode retains the existing per-owner-cookie pool. With durable Talk
explicitly enabled, every conversational route resolves an owner-and-tab-bound
opaque handle. Reopening that pool entry uses the persisted native root and
manual resume contract; session.create is never a reconnect fallback. A failed
send has uncertain delivery and is never transparently replayed.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import aiohttp
import hermes_auth
import talk_conversations

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"')
DEFAULT_HERMES_URL = "http://ods-hermes:9119"
DEFAULT_TIMEOUT_SECONDS = 180


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if raw.isdigit():
        return max(minimum, int(raw))
    return default


# Connection pool tuning. Override per environment if needed.
_IDLE_EXPIRY_SECONDS = _env_int("ODS_TALK_IDLE_EXPIRY", 300)  # 5 min default
_IDLE_SWEEP_INTERVAL = 60  # how often the background sweeper runs
_APPROVAL_SEND_TIMEOUT = 5.0
_APPROVAL_DISPLAY_LIMIT = 500


class HermesBridgeError(RuntimeError):
    """Base bridge error surfaced as a 502/503 by the talk router."""


class HermesUnavailable(HermesBridgeError):
    """Hermes is not reachable or did not expose the expected dashboard API."""


class HermesConnectionStale(HermesUnavailable):
    """Compatibility error type; no bridge path automatically retries it."""


@dataclass
class HermesReply:
    session_id: str
    text: str
    status: str = "ok"
    warning: str | None = None


def _base_url() -> str:
    return (os.environ.get("HERMES_INTERNAL_URL") or DEFAULT_HERMES_URL).rstrip("/")


def _request_timeout() -> int:
    return _env_int("ODS_TALK_HERMES_TIMEOUT", DEFAULT_TIMEOUT_SECONDS, minimum=10)


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
    ws_base = _base_url().replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    auth = hermes_auth.settings()
    legacy_protocol = False
    if auth:
        async with session.get(f"{_base_url()}/api/status", allow_redirects=False) as resp:
            if resp.status != 200:
                raise HermesUnavailable("Hermes dashboard protocol identity is unavailable")
            # The previously shipped 0.16 release has password login but no
            # ws-ticket endpoint (its token gate can mask missing routes).
            # Select its known protocol before sending a ticket request;
            # authentication failures never trigger a downgrade.
            legacy_protocol = (await resp.json()).get("version") == "0.16.0"
    authenticated = await login_dashboard(session, auth) if auth else None
    if authenticated is not None and not legacy_protocol:
        async with session.post(f"{_base_url()}/api/auth/ws-ticket", allow_redirects=False) as resp:
            if resp.status != 200:
                raise HermesUnavailable("Hermes did not issue a WebSocket ticket")
            ticket = (await resp.json()).get("ticket")
        if not isinstance(ticket, str) or not ticket:
            raise HermesUnavailable("Hermes WebSocket ticket was missing")
        from urllib.parse import quote
        url = f"{ws_base}/api/ws?ticket={quote(ticket, safe='')}"
    else:
        # Explicit older custom images retain their original token protocol.
        token = await _fetch_hermes_token(session)
        url = f"{ws_base}/api/ws?token={token}"
    try:
        return await session.ws_connect(url)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise HermesUnavailable("Hermes JSON-RPC websocket is not reachable") from exc


async def login_dashboard(session: aiohttp.ClientSession, auth: dict) -> list | None:
    """Supported upstream password login. Never relay the credential to a browser."""
    try:
        async with session.post(f"{_base_url()}/auth/password-login", json={
            "provider": "basic", "username": auth["username"], "password": auth["password"],
        }, allow_redirects=False) as resp:
            if resp.status == 404 and auth.get("managed"):
                # Older explicit image overrides predate password auth. Never
                # downgrade after a credential rejection or transport failure.
                return None
            if resp.status != 200 or (await resp.json()).get("ok") is not True:
                raise HermesUnavailable("Hermes dashboard sign-in failed")
            return list(resp.cookies.values())
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise HermesUnavailable("Hermes dashboard sign-in is unavailable") from exc


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


async def _rpc(ws, method, params):
    request_id = "ods-talk-" + str(time.monotonic_ns())
    await ws.send_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    while True:
        frame = await _recv_json(ws, 30)
        if frame.get("id") == request_id:
            return frame


async def _require_manual_runtime(ws):
    # Probe the actual dispatcher with an absent exact ID. This never creates
    # a session or constructs an agent. Unknown/invalid methods fail closed.
    result = await _rpc(ws, "session.resume.manual", {"session_id": "ods-capability-" + os.urandom(24).hex()})
    if (result.get("error") or {}).get("code") != 4007:
        raise HermesUnavailable("Durable conversation continuity is unavailable on this Hermes runtime.")
    result = await _rpc(ws, "session.create.manual", {"request_key": "invalid-capability-probe"})
    if (result.get("error") or {}).get("code") not in {4000, 4004}:
        raise HermesUnavailable("Durable conversation creation is unavailable on this Hermes runtime.")


async def durable_available():
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),
                                        cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            ws = await _connect_ws(http)
            try:
                await _require_manual_runtime(ws)
            finally:
                await ws.close()
        return True
    except (HermesBridgeError, aiohttp.ClientError, asyncio.TimeoutError):
        return False


async def _resume_conversation(ws, key):
    row = talk_conversations.store().get(key)
    if not row['root']:
        raise HermesUnavailable("Conversation creation was interrupted. Start a new conversation explicitly.")
    frame = await _rpc(ws, "session.resume.manual", {"session_id": row['root']})
    result = frame.get('result') or {}
    if (frame.get('error') or result.get('resume_contract') != 'manual-v1'
            or result.get('automatic_continuation') is not False
            or not isinstance(result.get('messages'), list)
            or not isinstance(result.get('running'), bool)
            or not isinstance(result.get('session_key'), str) or not result['session_key']
            or not isinstance(result.get('session_id'), str) or not result['session_id']):
        raise HermesUnavailable("Full conversation history cannot be resumed safely on this Hermes runtime.")
    talk_conversations.store().resumed(key, result['session_key'], result['session_id'], running=result['running'])
    return result


async def open_conversation(key, *, create=False):
    async with _conversation_admission(key):
        existing = _CONNECTION_POOL.get(key)
        if existing is not None and existing.lock.locked():
            raise HermesUnavailable("This conversation already has an active request.")
        if existing is not None:
            await _drop_connection(key, existing)
        http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_request_timeout()+20),
                                     cookie_jar=aiohttp.CookieJar(unsafe=True))
        ws = None
        try:
            ws = await _connect_ws(http)
            row = talk_conversations.store().get(key)
            if create and row['root'] is None:
                await _require_manual_runtime(ws)
                # Persist the uncertain boundary before transmitting creation.
                talk_conversations.store().delivery(key, 'creating')
                # Stable request identity recovers the same empty native root
                # if the previous creation response was lost.
                frame = await _rpc(ws, 'session.create.manual', {'request_key': key.removeprefix('durable:')})
                result = frame.get('result') or {}
                native, runtime = result.get('conversation_root'), result.get('session_id')
                if (frame.get('error') or result.get('resume_contract') != 'manual-v1'
                        or result.get('automatic_continuation') is not False
                        or not isinstance(native, str) or not native or not isinstance(runtime, str) or not runtime):
                    raise HermesUnavailable('Hermes did not provide a durable conversation identity.')
                talk_conversations.store().created(key, native, runtime)
            snapshot = await _resume_conversation(ws, key)
            conn = _HermesConnection(http_session=http, ws=ws, session_id=snapshot['session_id'])
            conn.pending_approval = snapshot.get('pending_approval')
            _CONNECTION_POOL[key] = conn
            return snapshot
        except BaseException:
            if ws is not None:
                await ws.close()
            await http.close()
            raise


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
    """One long-lived WS + Hermes session, scoped to a resolved conversation key.

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
    # Approval responses must be writable while ``lock`` is held by the
    # long-running prompt receive loop. All WS writes use this narrower lock.
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The browser never owns or echoes this protocol payload. It only submits
    # one of the two choices accepted by ``respond_approval``.
    pending_approval: dict[str, Any] | None = None
    closed: bool = False

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.pending_approval = None
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
# Lifecycle admission covers every lookup/claim, replacement and control write.
# It is released after claiming conn.lock, before receiving a prompt response,
# so approval/cancel never wait for a prompt that needs their response to finish.
_OPENING_LOCKS: dict[str, asyncio.Lock] = {}
_ADMISSION_USERS: dict[str, int] = {}
_SWEEPER_TASK: asyncio.Task | None = None


@contextlib.asynccontextmanager
async def _conversation_admission(key):
    async with _POOL_GUARD:
        lock = _OPENING_LOCKS.setdefault(key, asyncio.Lock())
        _ADMISSION_USERS[key] = _ADMISSION_USERS.get(key, 0) + 1
    try:
        async with lock:
            yield
    finally:
        async with _POOL_GUARD:
            users = _ADMISSION_USERS[key] - 1
            if users:
                _ADMISSION_USERS[key] = users
            else:
                del _ADMISSION_USERS[key]
                if key not in _CONNECTION_POOL:
                    # Count waiters too: removing an unlocked lock while a
                    # waiter owns its reference would split admission in two.
                    _OPENING_LOCKS.pop(key, None)


async def _open_connection(session_key: str) -> _HermesConnection:
    """Open one fresh WS + run session.create.

    Caller holds the per-key opening lock, NOT _POOL_GUARD — this does
    network I/O that can hang for the full client timeout when Hermes is
    slow or down, and must never stall other phones' pool lookups.
    """
    timeout_seconds = _request_timeout()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds + 20)
    http_session = aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.CookieJar(unsafe=True))
    try:
        ws = await _connect_ws(http_session)
    except BaseException:
        await http_session.close()
        raise
    try:
        if session_key.startswith('durable:'):
            snapshot = await _resume_conversation(ws, session_key)
            session_id = snapshot['session_id']
        else:
            session_id = await _create_session_on_ws(ws, timeout=30)
    except BaseException:
        await ws.close()
        await http_session.close()
        raise
    conn = _HermesConnection(http_session=http_session, ws=ws, session_id=session_id)
    if session_key.startswith('durable:'):
        conn.pending_approval = snapshot.get('pending_approval')
    logger.info("hermes-bridge: opened pooled connection for %s (session_id=%s)", session_key[:8], session_id)
    return conn


async def _get_connection(session_key: str) -> _HermesConnection:
    """Unclaimed lookup; prompt callers must use _claim_prompt_connection.

    Even a warm lookup waits for replacement to finish. All network I/O is
    under per-conversation admission, never the global bookkeeping guard.
    """
    async with _conversation_admission(session_key):
        return await _get_connection_admitted(session_key)


async def _get_connection_admitted(session_key):
    """Caller holds conversation admission; returned connection is unclaimed."""
    async with _POOL_GUARD:
        conn = _CONNECTION_POOL.get(session_key)
        if conn is not None and not conn.closed and not conn.ws.closed:
            return conn
        stale = _CONNECTION_POOL.pop(session_key, None)
    if stale is not None:
        await stale.aclose()
    new_conn = await _open_connection(session_key)
    async with _POOL_GUARD:
        _CONNECTION_POOL[session_key] = new_conn
    return new_conn


@contextlib.asynccontextmanager
async def _claim_prompt_connection(key):
    while True:
        async with _conversation_admission(key):
            conn = _CONNECTION_POOL.get(key)
            if conn is None or not conn.lock.locked():
                conn = await _get_connection_admitted(key)
                # An unlocked asyncio.Lock is acquired without suspension.
                # Refresh/eviction cannot intervene between lookup and claim.
                await conn.lock.acquire()
                break
        # Never hold admission while waiting for an active prompt: its approval
        # or cancel route needs admission. Recheck identity after waking because
        # the old connection may have been evicted in the meantime.
        async with conn.lock:
            pass
    try:
        yield conn
    finally:
        conn.lock.release()


async def _drop_connection(session_key: str, conn: _HermesConnection) -> None:
    """Evict a connection from the pool (e.g. after a dead-WS error)."""
    async with _POOL_GUARD:
        current = _CONNECTION_POOL.get(session_key)
        if current is conn:
            _CONNECTION_POOL.pop(session_key, None)
        if session_key not in _CONNECTION_POOL and not _ADMISSION_USERS.get(session_key):
            _OPENING_LOCKS.pop(session_key, None)
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
            async with _POOL_GUARD:
                keys = list(_CONNECTION_POOL)
            for key in keys:
                async with _conversation_admission(key):
                    conn = _CONNECTION_POOL.get(key)
                    if conn is None:
                        continue
                    if conn.lock.locked():
                        # Active prompt. Do not close the WS while Hermes is
                        # streaming or pre-filling a long response; last_used
                        # is refreshed again when the prompt completes.
                        continue
                    if conn.closed or conn.ws.closed or now - conn.last_used > _IDLE_EXPIRY_SECONDS:
                        logger.info("hermes-bridge: evicting idle connection for %s", key[:8])
                        await _drop_connection(key, conn)
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
        for key in list(_OPENING_LOCKS):
            if key not in _ADMISSION_USERS:
                del _OPENING_LOCKS[key]
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
        async with conn.write_lock:
            if conn.closed or conn.ws.closed:
                raise HermesUnavailable("The conversation connection closed before submission. Reconnect before sending again.")
            await conn.ws.send_str(json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "prompt.submit",
                "params": {"session_id": conn.session_id, "text": text},
            }))
    except (aiohttp.ClientError, ConnectionResetError, ConnectionError) as exc:
        # A transport error during send cannot establish that zero bytes
        # reached Hermes. Never replay a potentially accepted tool-bearing turn.
        raise HermesUnavailable("Message delivery is uncertain. Reconnect to inspect history before sending again.") from exc

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
        elif event_type == "approval.request":
            # Keep the authoritative request on the live server-side
            # connection. The phone receives bounded display text only and
            # can answer with a fixed choice; it cannot alter the command.
            conn.pending_approval = dict(payload)
            command = payload.get("command") if isinstance(payload.get("command"), str) else ""
            description = payload.get("description") if isinstance(payload.get("description"), str) else ""
            yield {
                "type": "approval",
                "command": command[:_APPROVAL_DISPLAY_LIMIT],
                "description": description[:_APPROVAL_DISPLAY_LIMIT],
                "choices": ["once", "deny"],
            }
        elif event_type == "message.complete":
            final_text = payload.get("text")
            if not isinstance(final_text, str) or not final_text.strip():
                final_text = "".join(chunks)
            conn.last_used = time.monotonic()
            conn.pending_approval = None
            yield {
                "type": "complete",
                "session_id": conn.session_id,
                "text": final_text.strip(),
                "status": str(payload.get("status") or "ok"),
                "warning": payload.get("warning") if isinstance(payload.get("warning"), str) else None,
            }
            return
        elif event_type == "error":
            conn.pending_approval = None
            message = payload.get("message") if isinstance(payload.get("message"), str) else "Hermes reported an error"
            raise HermesBridgeError(message)


async def stream_prompt(session_key: str, text: str) -> AsyncIterator[dict[str, Any]]:
    """Submit once on the resolved conversation; surface uncertain delivery.

    Durable mode restores full native history before each explicit submission.
    Per-connection locking serializes requests, and the store refuses multiple
    API workers. It does not claim distributed turn-generation fencing.
    """
    _ensure_sweeper_running()
    timeout_seconds = _request_timeout()

    async with _claim_prompt_connection(session_key) as conn:
        durable = session_key.startswith('durable:')
        if durable:
            snapshot = await _resume_conversation(conn.ws, session_key)
            conn.session_id = snapshot['session_id']
            conn.pending_approval = snapshot.get('pending_approval')
            if snapshot['running']:
                raise HermesUnavailable("This conversation already has an active request.")
        yield {"type": "session", "session_id": conn.session_id}
        if durable:
            talk_conversations.store().delivery(session_key, 'sending')
        completed = False
        try:
            async for event in _submit_on_connection(conn, text, timeout_seconds):
                if event.get('type') == 'complete':
                    completed = True
                    if durable:
                        talk_conversations.store().delivery(session_key, 'idle')
                yield event
        except BaseException:
            # A consumer may close the generator immediately after the terminal
            # event. That is a completed turn, not uncertain delivery.
            if not completed:
                if durable:
                    talk_conversations.store().delivery(session_key, 'uncertain')
                await _drop_connection(session_key, conn)
            raise


async def respond_approval(session_key: str, choice: str) -> bool:
    """Answer the current approval on an existing pooled Talk connection.

    This deliberately never opens a connection and never acquires the prompt
    ``lock``: that lock is held while Hermes waits for this response. The
    pending request is claimed under ``write_lock`` before sending, so at most
    one racing browser request can succeed. After the send begins, failures are
    treated as ambiguous and the request is not restored or retried.
    """
    if choice not in {"once", "deny"}:
        raise ValueError("choice must be 'once' or 'deny'")

    async with _conversation_admission(session_key):
        return await _respond_approval_admitted(session_key, choice)


async def _respond_approval_admitted(session_key, choice):
    async with _POOL_GUARD:
        conn = _CONNECTION_POOL.get(session_key)
        if conn is None or conn.closed or conn.ws.closed:
            return False

    try:
        await asyncio.wait_for(conn.write_lock.acquire(), timeout=_APPROVAL_SEND_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise HermesBridgeError("Hermes approval channel is busy") from exc

    try:
        if conn.closed or conn.ws.closed or conn.pending_approval is None:
            return False

        rpc = json.dumps({
            "jsonrpc": "2.0",
            "id": f"ods-talk-approval-{time.monotonic_ns()}",
            "method": "approval.respond",
            "params": {
                "session_id": conn.session_id,
                "choice": choice,
                "all": False,
            },
        })
        conn.pending_approval = None
        try:
            await asyncio.wait_for(
                conn.ws.send_str(rpc),
                timeout=_APPROVAL_SEND_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise HermesBridgeError(
                "Hermes approval response timed out; response state is unknown"
            ) from exc
        except (aiohttp.ClientError, ConnectionResetError, ConnectionError) as exc:
            raise HermesBridgeError("Hermes approval response could not be sent") from exc
        conn.last_used = time.monotonic()
        return True
    finally:
        conn.write_lock.release()


async def deny_pending_approval(session_key: str) -> bool:
    """Best-effort safety denial used before abandoning an SSE stream."""
    try:
        return await respond_approval(session_key, "deny")
    except (HermesBridgeError, asyncio.TimeoutError):
        logger.warning(
            "hermes-bridge: could not deny abandoned approval for %s",
            session_key[:8],
            exc_info=True,
        )
        return False


async def _interrupt_session_with_ack(session_id: str) -> None:
    """Interrupt a Hermes session on a short-lived control WebSocket.

    The prompt's pooled WebSocket already has a reader blocked in
    ``_submit_on_connection``.  Reading an interrupt reply on that socket
    would race that reader, while sending and immediately closing gives the
    Hermes gateway no delivery acknowledgement.  A separate authenticated
    control connection can address the process-wide session id, receive the
    JSON-RPC result, and prove Hermes processed the interrupt before the
    prompt-owning socket is evicted.
    """
    timeout = aiohttp.ClientTimeout(total=_APPROVAL_SEND_TIMEOUT * 2)
    control_session = aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.CookieJar(unsafe=True))
    control_ws: aiohttp.ClientWebSocketResponse | None = None
    request_id = f"ods-talk-interrupt-{time.monotonic_ns()}"
    deadline = time.monotonic() + _APPROVAL_SEND_TIMEOUT
    try:
        control_ws = await _connect_ws(control_session)
        await asyncio.wait_for(
            control_ws.send_str(json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "session.interrupt",
                "params": {"session_id": session_id},
            })),
            timeout=max(0.01, deadline - time.monotonic()),
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            frame = await _recv_json(control_ws, remaining)
            if frame.get("id") != request_id:
                # gateway.ready and unrelated process-level events may arrive
                # before the RPC result on a newly opened control socket.
                continue
            if frame.get("error"):
                err = frame["error"]
                message = err.get("message") if isinstance(err, dict) else str(err)
                raise HermesBridgeError(message or "Hermes session interrupt failed")
            result = frame.get("result")
            if not isinstance(result, dict) or result.get("status") != "interrupted":
                raise HermesBridgeError("Hermes did not acknowledge the session interrupt")
            return
    finally:
        if control_ws is not None:
            with contextlib.suppress(Exception):
                await control_ws.close()
        with contextlib.suppress(Exception):
            await control_session.close()


async def interrupt_active_prompt(session_key: str) -> bool:
    """Interrupt and evict the active Hermes prompt for one Talk session.

    Closing the WebSocket alone is not cancellation: Hermes deliberately
    detaches an in-flight session and lets its agent continue after a client
    disconnect. On a single-slot local model that can block every later Talk,
    Pixel, and capability request for minutes. Send Hermes's session-scoped
    abort RPC before closing the pooled connection so the abandoned turn frees
    its upstream model slot without affecting any sibling session.

    Admission prevents replacement while the acknowledgement is outstanding.
    The write lock orders interruption after a started submit and prevents an
    already-admitted submit from sending after cancellation has closed its WS.
    Neither control operation waits for the prompt receive lock.
    """
    async with _conversation_admission(session_key):
        conn = _CONNECTION_POOL.get(session_key)
        if conn is None:
            return await _interrupt_active_prompt_admitted(session_key)
        try:
            await asyncio.wait_for(conn.write_lock.acquire(), timeout=_APPROVAL_SEND_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise HermesBridgeError("Hermes control channel is busy") from exc
        try:
            return await _interrupt_active_prompt_admitted(session_key)
        finally:
            conn.write_lock.release()


async def _interrupt_active_prompt_admitted(session_key):
    if session_key.startswith('durable:'):
        row = talk_conversations.store().get(session_key)
        if not row['runtime']:
            return False
        await _interrupt_session_with_ack(row['runtime'])
        conn = _CONNECTION_POOL.get(session_key)
        if conn is not None:
            await _drop_connection(session_key, conn)
        talk_conversations.store().delivery(session_key, 'idle')
        return True
    async with _POOL_GUARD:
        conn = _CONNECTION_POOL.get(session_key)
        if (
            conn is None
            or conn.closed
            or conn.ws.closed
            or not conn.lock.locked()
        ):
            return False
        _CONNECTION_POOL.pop(session_key, None)

    acknowledged = False
    try:
        await _interrupt_session_with_ack(conn.session_id)
        conn.pending_approval = None
        conn.last_used = time.monotonic()
        acknowledged = True
        logger.info(
            "hermes-bridge: Hermes acknowledged interrupt for abandoned prompt %s",
            session_key[:8],
        )
    except (
        HermesBridgeError,
        asyncio.TimeoutError,
        aiohttp.ClientError,
        ConnectionResetError,
        ConnectionError,
    ):
        logger.warning(
            "hermes-bridge: could not interrupt abandoned prompt for %s",
            session_key[:8],
            exc_info=True,
        )
    finally:
        try:
            await asyncio.wait_for(
                conn.aclose(),
                timeout=_APPROVAL_SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "hermes-bridge: timed out closing interrupted connection for %s",
                session_key[:8],
            )
    return acknowledged


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
