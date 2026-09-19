"""Closing a Talk response must release the upstream bridge immediately."""

import asyncio

import pytest
from starlette.requests import Request


@pytest.mark.asyncio
@pytest.mark.parametrize("close_mode", ["close", "cancel"])
async def test_talk_response_closes_bridge_after_yield(monkeypatch, close_mode):
    import session_signer
    from routers import talk

    session_signer._set_secret_for_tests("stream-cleanup-test")
    cookie = session_signer.issue(ttl_seconds=3600)
    request = Request({"type": "http", "headers": [(b"cookie", f"ods-session={cookie}".encode())]})
    closed = asyncio.Event()

    async def compatible():
        return {}

    async def bridge(session_key, text):
        try:
            yield {"type": "delta", "text": "first token"}
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(talk, "_require_hermes_talk_compatible", compatible)
    monkeypatch.setattr(talk.hermes_bridge, "stream_prompt", bridge)
    response = await talk.talk_message_stream({"text": "hello"}, request)
    iterator = response.body_iterator
    assert b"first token" in await anext(iterator)
    if close_mode == "close":
        await iterator.aclose()
    else:
        with pytest.raises(asyncio.CancelledError):
            await iterator.athrow(asyncio.CancelledError())
    assert closed.is_set()
