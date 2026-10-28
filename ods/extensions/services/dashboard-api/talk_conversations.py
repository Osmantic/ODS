"""Conversation bridge: TalkStore bindings over the pooled Hermes transport.

Intermediate repair layer. Not wired into router/UI yet. Every public
operation validates owner/binding/expiry before touching the pool, and
every store call runs via asyncio.to_thread so SQLite never blocks the
event loop.

Cancellation model: every awaitable that must not be abandoned is wrapped
by _settle(), which shields a tracked task and re-raises the saved
cancellation only after the inner task has actually completed. Store
mutations run through _store_change(), which executes the committed()
callback inside the inner task before any cancellation can propagate, so
durable outcomes are recorded even when the outer waiter is cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, TypeVar

import aiohttp

import hermes_bridge
from hermes_bridge import (
    HermesBridgeError,
    HermesUnavailable,
    _CONNECTION_POOL,
    _POOL_GUARD,
    _OPENING_LOCKS,
    _HermesConnection,
    _connect_ws,
    _drop_connection,
    _ensure_sweeper_running,
    _recv_json,
    _submit_on_connection,
    deny_pending_approval,
    interrupt_active_prompt,
    respond_approval,
)
from talk_store import StoreError, TalkStore

_TERMINAL_OK = {"ok", "complete", "error", "interrupted"}
_CAP_VERSION = "1"
_RPC_TIMEOUT = 30.0
_CLEANUP_TIMEOUT = 5.0

T = TypeVar("T")


class ConversationError(HermesBridgeError):
    """Typed bridge error with a safe code and user-facing message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _store_error(exc: StoreError) -> ConversationError:
    return ConversationError(exc.code, exc.message)


def _pool_key(owner: str, cid: str) -> str:
    return "talk:" + owner + ":" + cid


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConversationError("invalid", "invalid " + name)
    return value


async def _settle(awaitable: Awaitable[T]) -> T:
    """Retain ownership until bounded work settles, then propagate cancellation.

    SQLite has its own busy timeout. Network cleanup has explicit deadlines.
    An outer timeout must not abandon a thread that can still commit later.
    """
    task = asyncio.ensure_future(awaitable)
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except BaseException:
            break
    try:
        result = task.result()
    except BaseException:
        if cancelled is not None:
            raise cancelled
        raise
    if cancelled is not None:
        raise cancelled
    return result


async def _store_change(
    fn: Callable[..., Any],
    *args: Any,
    committed: Optional[Callable[[Any], None]] = None,
) -> Any:
    """Run a blocking store call in a thread, recording its outcome.

    The committed() callback runs inside the inner coroutine immediately
    after the store call returns, before _settle can re-raise a saved
    cancellation. This guarantees the caller observes the actual durable
    outcome even if the outer waiter is cancelled.
    """

    async def inner() -> Any:
        try:
            result = await asyncio.to_thread(fn, *args)
        except StoreError as exc:
            raise _store_error(exc) from exc
        if committed is not None:
            committed(result)
        return result

    return await _settle(inner())


async def _rpc(
    conn: _HermesConnection, method: str, params: dict, timeout: float
) -> dict:
    """One JSON-RPC call on an existing connection with a single deadline."""
    request_id = "talk-" + secrets.token_hex(8)
    deadline = time.monotonic() + timeout
    await asyncio.wait_for(
        conn.write_lock.acquire(), max(0.01, deadline - time.monotonic())
    )
    try:
        try:
            await asyncio.wait_for(
                conn.ws.send_str(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": method,
                            "params": params,
                        }
                    )
                ),
                max(0.01, deadline - time.monotonic()),
            )
        except (aiohttp.ClientError, ConnectionResetError, ConnectionError) as exc:
            raise HermesUnavailable("Hermes websocket closed") from exc
    finally:
        conn.write_lock.release()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConversationError("timeout", "Hermes did not respond in time")
        frame = await _recv_json(conn.ws, remaining)
        if not isinstance(frame, dict):
            raise ConversationError(
                "protocol", "Hermes returned an unexpected response"
            )
        if frame.get("id") != request_id:
            continue
        if frame.get("error"):
            raise ConversationError("native_error", "Hermes rejected the request")
        result = frame.get("result")
        if not isinstance(result, dict):
            raise ConversationError(
                "protocol", "Hermes returned an unexpected response"
            )
        return result


async def _open_talk_connection(key: str) -> _HermesConnection:
    """Open HTTP+WS only. Never creates a native session."""
    timeout = aiohttp.ClientTimeout(total=hermes_bridge._request_timeout() + 20)
    http_session = aiohttp.ClientSession(timeout=timeout)
    try:
        ws = await _connect_ws(http_session)
    except BaseException:
        await http_session.close()
        raise
    return _HermesConnection(http_session=http_session, ws=ws, session_id="")


async def _acquire_connection(key: str) -> _HermesConnection:
    """Return a live pooled connection, opening one if needed.

    Network I/O happens outside _POOL_GUARD; the per-key opening lock
    serializes concurrent opens for the same conversation.
    """
    async with _POOL_GUARD:
        conn = _CONNECTION_POOL.get(key)
        if conn is not None and not conn.closed and not conn.ws.closed:
            return conn
        opening = _OPENING_LOCKS.setdefault(key, asyncio.Lock())
    async with opening:
        async with _POOL_GUARD:
            conn = _CONNECTION_POOL.get(key)
            if conn is not None and not conn.closed and not conn.ws.closed:
                return conn
            stale = _CONNECTION_POOL.pop(key, None)
        if stale is not None:
            await stale.aclose()
        new_conn = await _open_talk_connection(key)
        try:
            async with _POOL_GUARD:
                _CONNECTION_POOL[key] = new_conn
        except BaseException:
            await new_conn.aclose()
            raise
        return new_conn


async def _require_capabilities(conn: _HermesConnection) -> None:
    result = await _rpc(conn, "ods.talk.capabilities", {}, _RPC_TIMEOUT)
    if result.get("manual_resume") is not True or result.get("version") != _CAP_VERSION:
        raise ConversationError("unsupported", "Hermes does not support manual resume")


def _validate_resume(result: dict, stored_id: str) -> None:
    if not any(result.get(name) == stored_id for name in ("session_key", "stored_session_id")):
        raise ConversationError("protocol", "Hermes could not verify this conversation")
    identities = [
        result[name]
        for name in ("session_key", "stored_session_id", "resumed")
        if name in result
    ]
    if not identities or any(value != stored_id for value in identities):
        raise ConversationError("protocol", "Hermes could not verify this conversation")
    for field in ("session_key", "stored_session_id", "resumed"):
        value = result.get(field)
        if value is not None and value != stored_id:
            raise ConversationError("protocol", "Hermes resumed the wrong session")
    runtime_id = result.get("session_id")
    if not isinstance(runtime_id, str) or not runtime_id.strip():
        raise ConversationError("protocol", "Hermes did not return a runtime session")
    history = result.get("messages")
    if not isinstance(history, list):
        raise ConversationError("protocol", "Hermes returned malformed history")
    if result.get("ods_recovery_pending") is not False:
        raise ConversationError("recovery", "Hermes session needs recovery")
    if result.get("status") != "idle":
        raise ConversationError("busy", "Hermes session is not idle")
    for flag in (
        "running",
        "inflight",
        "queued",
        "pending_approval",
        "open_requests",
        "pending_connection",
        "hydrating",
        "auto_continue",
    ):
        if result.get(flag):
            raise ConversationError("busy", "Hermes session is not idle")


@dataclass
class _TurnState:
    token: Optional[str] = None
    submitted: bool = False
    finished: bool = False
    uncertain: bool = False


class ConversationBridge:
    def __init__(self, store: TalkStore):
        self.store = store

    async def _get(self, owner: str, cid: str) -> dict:
        return await _store_change(self.store.get, owner, cid)

    async def allocate(self, owner: str, expires_at: int) -> str:
        _require_str(owner, "owner")
        return await _store_change(self.store.allocate, owner, expires_at)

    async def stream(
        self, owner: str, cid: str, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        _require_str(owner, "owner")
        _require_str(cid, "conversation")
        _require_str(text, "text")
        _ensure_sweeper_running()
        key = _pool_key(owner, cid)

        snapshot = await self._get(owner, cid)
        if snapshot["state"] not in {"new", "ready"}:
            raise ConversationError(
                "recovery",
                "The previous operation has an unresolved outcome. It will not be repeated.",
            )
        conn = await _acquire_connection(key)
        async with conn.lock:
            state = _TurnState()
            try:
                await _require_capabilities(conn)
                snapshot = await self._get(owner, cid)
                if snapshot["state"] == "new":
                    stored_id = await self._create_session(conn, owner, cid)
                elif snapshot["state"] == "ready":
                    stored_id = _require_str(
                        snapshot["stored_id"], "stored conversation"
                    )
                else:
                    raise ConversationError(
                        "recovery",
                        "The previous operation has an unresolved outcome. It will not be repeated.",
                    )
                await self._claim_turn(owner, cid, state)
                try:
                    resume = await _rpc(
                        conn,
                        "session.resume",
                        {
                            "session_id": stored_id,
                            "ods_talk_manual_resume": True,
                        },
                        _RPC_TIMEOUT,
                    )
                    _validate_resume(resume, stored_id)
                except ConversationError as exc:
                    if exc.code in ("busy", "recovery"):
                        await self._mark_uncertain(owner, cid, state)
                    raise
                conn.session_id = resume["session_id"]
                yield {
                    "type": "session",
                    "session_id": conn.session_id,
                    "conversation_id": cid,
                }

                await self._submit(owner, cid, state)
                async for event in _submit_on_connection(
                    conn, text, hermes_bridge._request_timeout()
                ):
                    if event.get("type") == "complete":
                        if event.get("status") not in _TERMINAL_OK:
                            raise ConversationError(
                                "recovery",
                                "The response ended with an unknown outcome.",
                            )
                        await self._finish(owner, cid, state)
                        yield event
                        return
                    yield event
                raise ConversationError(
                    "recovery",
                    "The response ended before its outcome could be confirmed.",
                )
            finally:
                await self._cleanup(key, conn, owner, cid, state)

    async def _cleanup(
        self,
        key: str,
        conn: _HermesConnection,
        owner: str,
        cid: str,
        state: _TurnState,
    ) -> None:
        """Settle durable state and best-effort remote cleanup.

        Runs under _settle so repeated outer cancellations cannot abandon
        the commit. Each network cleanup is bounded by _CLEANUP_TIMEOUT.
        """

        async def inner() -> None:
            token = state.token
            if token is not None and not state.finished and not state.uncertain:
                if state.submitted:
                    await self._mark_uncertain(owner, cid, state)
                else:
                    await self._release(owner, cid, token)
            if not state.finished and state.submitted:
                # Known-completed turns are never interrupted.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        deny_pending_approval(key), _CLEANUP_TIMEOUT
                    )
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        # This turn owns the durable claim and conn.lock. The
                        # prompt transport may already be closed, but Hermes
                        # can keep running after EOF. Use its separate control
                        # channel to stop this runtime even in that case.
                        hermes_bridge._interrupt_session_with_ack(conn.session_id),
                        _CLEANUP_TIMEOUT,
                    )
            if not state.finished:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        _drop_connection(key, conn), _CLEANUP_TIMEOUT
                    )

        await _settle(inner())

    async def _create_session(
        self, conn: _HermesConnection, owner: str, cid: str
    ) -> str:
        # Caller holds conn.lock and has already verified manual-resume support.
        state = _TurnState()
        try:
            await self._claim_create(owner, cid, state)
            result = await _rpc(conn, "session.create", {}, _RPC_TIMEOUT)
            runtime_id = _require_str(result.get("session_id"), "runtime session")
            stored = _require_str(result.get("stored_session_id"), "stored session")
            if runtime_id == stored:
                raise ConversationError(
                    "protocol", "Hermes returned an invalid session"
                )
            await self._finish_create(owner, cid, state, stored)
            conn.session_id = runtime_id
            return stored
        except BaseException:
            if state.token is not None and not state.finished:
                await self._mark_uncertain(owner, cid, state)
            raise

    async def _claim_create(self, owner: str, cid: str, state: _TurnState) -> None:
        def committed(token: str) -> None:
            state.token = token

        await _store_change(
            self.store.claim_create, owner, cid, committed=committed
        )

    async def _claim_turn(self, owner: str, cid: str, state: _TurnState) -> None:
        def committed(token: str) -> None:
            state.token = token

        await _store_change(
            self.store.claim_turn, owner, cid, committed=committed
        )

    async def _submit(self, owner: str, cid: str, state: _TurnState) -> None:
        def committed(_result: Any) -> None:
            state.submitted = True

        await _store_change(
            self.store.submit_turn,
            owner,
            cid,
            state.token,
            committed=committed,
        )

    async def _finish(self, owner: str, cid: str, state: _TurnState) -> None:
        def committed(_result: Any) -> None:
            state.finished = True

        await _store_change(
            self.store.finish_turn,
            owner,
            cid,
            state.token,
            committed=committed,
        )

    async def _finish_create(
        self, owner: str, cid: str, state: _TurnState, stored: str
    ) -> None:
        def committed(_result: Any) -> None:
            state.finished = True

        await _store_change(
            self.store.finish_create,
            owner,
            cid,
            state.token,
            stored,
            committed=committed,
        )

    async def _release(self, owner: str, cid: str, token: str) -> None:
        try:
            await _store_change(
                self.store.release_prepared_turn, owner, cid, token
            )
        except ConversationError:
            pass

    async def _mark_uncertain(
        self, owner: str, cid: str, state: _TurnState
    ) -> None:
        token = state.token
        if token is None:
            return

        def committed(_result: Any) -> None:
            state.uncertain = True

        try:
            await _store_change(
                self.store.mark_uncertain,
                owner,
                cid,
                token,
                committed=committed,
            )
        except ConversationError:
            pass

    async def respond_approval(self, owner: str, cid: str, choice: str) -> bool:
        _require_str(owner, "owner")
        _require_str(cid, "conversation")
        if choice not in {"once", "deny"}:
            raise ConversationError("invalid", "invalid choice")
        await self._get(owner, cid)
        return await respond_approval(_pool_key(owner, cid), choice)

    async def deny_pending_approval(self, owner: str, cid: str) -> bool:
        _require_str(owner, "owner")
        _require_str(cid, "conversation")
        await self._get(owner, cid)
        return await deny_pending_approval(_pool_key(owner, cid))

    async def interrupt_active_prompt(self, owner: str, cid: str) -> bool:
        _require_str(owner, "owner")
        _require_str(cid, "conversation")
        await self._get(owner, cid)
        return await interrupt_active_prompt(_pool_key(owner, cid))
