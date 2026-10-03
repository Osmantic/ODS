"""Admission slots survive cancellation and exceptions at resource boundaries."""

import asyncio

import pytest
from fastapi import HTTPException

import pixel_runtime_state
import pixel_chat_identity
from routers import pixel
from test_pixel import ConnectedRequest, FakeClient, FakeResponse, FakeStreamContext


@pytest.fixture(autouse=True)
def ready_pixel(monkeypatch):
    async def saved_identity(*_args, **_kwargs):
        return {"schemaVersion": 1, "revision": 1, "displayName": "Portal"}
    monkeypatch.setattr(pixel_chat_identity, "async_request_json", saved_identity)
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", "e" * 64)
    monkeypatch.setenv("PIXEL_EDGE_URL", "http://pixel-edge:9595")
    monkeypatch.setenv("ODS_PIXEL_MAX_STREAMS", "1")
    monkeypatch.setattr(pixel_runtime_state, "_active_streams", 0)

    async def ready():
        return None

    async def cancel(*args):
        return None

    monkeypatch.setattr(pixel, "_model_readiness_issue", ready)
    monkeypatch.setattr(pixel, "_cancel_edge_run", cancel)


def body():
    return pixel.ChatStreamRequest(chat_id="slot", messages=[{"role": "user", "content": "hello"}])


def assert_slot_reusable():
    assert not pixel_runtime_state._local_pixel_stream_active()
    assert pixel_runtime_state.try_begin_pixel_stream()
    assert not pixel_runtime_state.try_begin_pixel_stream()
    pixel_runtime_state.end_pixel_stream()


@pytest.mark.asyncio
async def test_cancelled_upstream_handshake_releases_admission(monkeypatch):
    opening = asyncio.Event()
    closed = []

    class Context(FakeStreamContext):
        async def __aenter__(self):
            opening.set()
            await asyncio.Event().wait()

    class Client(FakeClient):
        def stream(self, *args, **kwargs):
            return Context(self.response)

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kwargs: Client(FakeResponse()))
    task = asyncio.create_task(pixel.pixel_chat_stream(ConnectedRequest(), body()))
    await asyncio.wait_for(opening.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]
    assert_slot_reusable()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["client", "stream", "exit", "close"])
async def test_exception_during_setup_or_cleanup_releases_admission(monkeypatch, failure):
    closed = []

    class Context(FakeStreamContext):
        async def __aexit__(self, *args):
            if failure == "exit":
                raise RuntimeError("exit fixture")

    class Client(FakeClient):
        def stream(self, *args, **kwargs):
            if failure == "stream":
                raise RuntimeError("stream fixture")
            return Context(self.response)

        async def aclose(self):
            closed.append(True)
            if failure == "close":
                raise RuntimeError("close fixture")

    def create(**kwargs):
        if failure == "client":
            raise RuntimeError("client fixture")
        return Client(FakeResponse(status=503))

    monkeypatch.setattr(pixel.httpx, "AsyncClient", create)
    with pytest.raises((RuntimeError, HTTPException)):
        await pixel.pixel_chat_stream(ConnectedRequest(), body())
    assert closed == ([] if failure == "client" else [True])
    assert_slot_reusable()


@pytest.mark.asyncio
async def test_header_send_failure_releases_unstarted_stream(monkeypatch):
    closed, cancelled = [], []

    class Client(FakeClient):
        async def aclose(self):
            closed.append(True)

    async def cancel(*args):
        cancelled.append(True)

    async def send(message):
        assert message["type"] == "http.response.start"
        raise OSError("client disconnected before body")

    async def receive():
        await asyncio.Event().wait()

    monkeypatch.setattr(pixel, "_cancel_edge_run", cancel)
    monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **kwargs: Client(FakeResponse(content_type="text/event-stream")))
    response = await pixel.pixel_chat_stream(ConnectedRequest(), body())
    with pytest.raises(Exception):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert closed == [True]
    assert cancelled == [True]
    assert_slot_reusable()
