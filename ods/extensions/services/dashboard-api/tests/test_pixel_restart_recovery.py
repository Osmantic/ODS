"""A turn cut off by a restart heals by itself once nothing can still run.

Seen on the RTX Spark: an update recreated dashboard-api and pixel-edge and
restarted the gateway while "Can you do things on cron timers?" streamed. The
retained receipt stayed unresolved, the restarted edge had no record of the
chat, and every later message was refused while the page polled forever.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any, cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-test-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pixel_chat_results as receipts
from routers import pixel
import pixel_chat_identity
from test_pixel import EDGE_KEY, ConnectedRequest, FakeClient, FakeResponse

OWNER = "dashboard-test-key"
CHAT = "spark-chat"
CUT_OFF = (receipts.owner_namespace(OWNER), CHAT, "cron-question")
PARTIAL = b'data: {"choices":[{"delta":{"content":"Yes. Portal can run"}}]}\n\n'


@pytest.fixture
def stores(tmp_path, monkeypatch):
    async def saved_identity(*_args, **_kwargs):
        return {"schemaVersion": 1, "revision": 1, "displayName": "Portal"}
    monkeypatch.setattr(pixel_chat_identity, "async_request_json", saved_identity)
    monkeypatch.setattr(pixel, "_result_tasks", {})
    monkeypatch.setattr(pixel, "_result_preflights", set())
    monkeypatch.setattr(pixel, "_result_stops", set())
    monkeypatch.setattr(pixel, "_result_abort_ack", set())
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", EDGE_KEY)
    async def ready():
        return None
    monkeypatch.setattr(pixel, "_model_readiness_issue", ready)
    # The API process that was streaming the turn when the update recreated it.
    before = receipts.ChatResultStore(tmp_path / "receipts")
    before.reserve(CUT_OFF, "cron-question-input")
    before.append(CUT_OFF, PARTIAL)
    # The recreated API process: the receipt is unresolved, nobody owns it.
    after = receipts.ChatResultStore(tmp_path / "receipts")
    monkeypatch.setattr(pixel, "_result_store", after)
    yield after
    before.close()
    after.close()


@pytest.fixture
def edge(monkeypatch):
    """The restarted edge has no record of the chat; the ingress proves it idle."""
    state: dict[str, Any] = {"activity": "unknown", "settle": "settled", "lookups": [], "settles": []}

    async def activity(edge_url, key, chat_id):
        state["lookups"].append(chat_id)
        return state["activity"]

    async def settle(edge_url, key, chat_id):
        state["settles"].append(chat_id)
        assert (receipts.owner_namespace(OWNER), chat_id) in pixel._result_stops
        return state["settle"]

    async def never_cancel(*_args):
        raise AssertionError("Healing a restart-cut turn must never stop a run")

    monkeypatch.setattr(pixel, "_edge_chat_activity", activity)
    monkeypatch.setattr(pixel, "_settle_edge_chat", settle)
    monkeypatch.setattr(pixel, "_cancel_edge_run", never_cancel)
    return state


def connected() -> Request:
    # The handlers only ask whether the client is still connected.
    return cast(Request, ConnectedRequest())


def stream(request_id, text="Checking in"):
    return pixel.ChatStreamRequest.model_validate(
        {"chat_id": CHAT, "request_id": request_id, "messages": [{"role": "user", "content": text}]})


def test_restart_cut_turn_settles_by_itself_and_the_chat_takes_its_next_message(stores, edge):
    assert stores.get(CUT_OFF)["state"] == "unresolved"
    lookup = pixel.ChatResultRequest(chat_id=CHAT, request_id=CUT_OFF[2])
    result = asyncio.run(pixel.pixel_chat_result(lookup, OWNER))
    # No terminal frame: the producer died with the old process. The saved
    # partial answer stays exactly as it was received.
    assert result == {"state": "interrupted", "events": PARTIAL.decode()}
    assert edge["settles"] == [CHAT]
    assert not stores.has_pending(CUT_OFF[:2])
    assert not pixel._result_stops
    assert stores.reserve((*CUT_OFF[:2], "checking-in"), "checking-in-input") is True


def test_a_refused_follow_up_cannot_strand_the_chat_behind_the_cut_off_turn(stores, edge):
    # "Checking in" was refused while the cut-off turn held the chat, so its
    # request ID has no receipt. Its activity lookup still heals the chat.
    with pytest.raises(HTTPException) as refused:
        asyncio.run(pixel.pixel_chat_stream(connected(), stream("checking-in"), OWNER))
    assert refused.value.status_code == 423
    missing = pixel.ChatResultRequest(chat_id=CHAT, request_id="checking-in")
    assert asyncio.run(pixel.pixel_chat_result(missing, OWNER)) == {"state": "unknown", "events": ""}
    assert asyncio.run(pixel.pixel_chat_activity(pixel.ChatCancelRequest(chat_id=CHAT), OWNER)) == {"state": "terminal"}
    assert stores.get(CUT_OFF)["state"] == "interrupted"
    assert b"".join(row["data"] for row in stores.chunks(CUT_OFF)) == PARTIAL
    assert not stores.has_pending(CUT_OFF[:2])


@pytest.mark.parametrize("activity,settle,expected", [
    ("active", "settled", "active"),     # a stream of this chat still passes through the edge
    ("unknown", "active", "active"),     # the native session is busy: a run may still be alive
    ("unknown", None, "unknown"),        # Portal still restarting, or an older edge or ingress
    (None, "settled", "unknown"),        # the edge could not be read at all
])
def test_work_that_may_still_run_keeps_the_chat_held(stores, edge, activity, settle, expected):
    edge.update(activity=activity, settle=settle)
    result = asyncio.run(pixel.pixel_chat_activity(pixel.ChatCancelRequest(chat_id=CHAT), OWNER))
    assert result == {"state": expected}
    assert edge["settles"] == ([CHAT] if activity == "unknown" else [])
    assert stores.get(CUT_OFF)["state"] == "unresolved"
    assert stores.has_pending(CUT_OFF[:2])
    with pytest.raises(HTTPException) as refused:
        asyncio.run(pixel.pixel_chat_stream(connected(), stream("second-turn"), OWNER))
    assert refused.value.status_code == 423


def test_a_turn_this_process_still_streams_is_active_without_asking_anyone(stores, edge):
    async def run():
        live = asyncio.create_task(asyncio.sleep(3600))
        pixel._result_tasks[(*CUT_OFF[:2], "live-turn")] = live
        try:
            return await pixel.pixel_chat_activity(pixel.ChatCancelRequest(chat_id=CHAT), OWNER)
        finally:
            live.cancel()
            await asyncio.gather(live, return_exceptions=True)
    assert asyncio.run(run()) == {"state": "active"}
    assert edge["lookups"] == [] and edge["settles"] == []
    assert stores.get(CUT_OFF)["state"] == "unresolved"


def test_settle_fences_a_new_attempt_until_it_answers(stores, edge, monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_settle(edge_url, key, chat_id):
            entered.set()
            await release.wait()
            return "settled"

        monkeypatch.setattr(pixel, "_settle_edge_chat", slow_settle)
        healing = asyncio.create_task(pixel.pixel_chat_activity(pixel.ChatCancelRequest(chat_id=CHAT), OWNER))
        await entered.wait()
        stores.finish(CUT_OFF, "interrupted")  # even with nothing pending, the fence holds
        with pytest.raises(HTTPException) as fenced:
            await pixel.pixel_chat_stream(connected(), stream("during-settle"), OWNER)
        assert fenced.value.status_code == 423
        release.set()
        assert await healing == {"state": "terminal"}
        assert not pixel._result_stops
    asyncio.run(run())


def test_continue_in_this_chat_resolves_the_cut_off_turn_by_its_exact_id(stores, monkeypatch):
    calls = []

    async def cancel(edge_url, key, chat_id):
        calls.append(chat_id)
        assert CUT_OFF[:2] in pixel._result_stops
        return True

    monkeypatch.setattr(pixel, "_cancel_edge_run", cancel)
    # The page only knows the refused follow-up's ID, so it asks for the chat.
    result = asyncio.run(pixel.pixel_chat_cancel(pixel.ChatCancelRequest(chat_id=CHAT), OWNER))
    assert result == {"aborted": True}
    assert calls == [CHAT]
    assert stores.get(CUT_OFF)["state"] == "cancelled"
    assert b"".join(row["data"] for row in stores.chunks(CUT_OFF)) == PARTIAL
    assert not stores.has_pending(CUT_OFF[:2])


def test_continue_never_stops_a_turn_this_process_is_streaming(stores, monkeypatch):
    async def forbidden(*_args):
        raise AssertionError("A chat-level request must not stop a live turn")

    monkeypatch.setattr(pixel, "_cancel_edge_run", forbidden)

    async def run():
        stores.finish(CUT_OFF, "interrupted")
        live = (*CUT_OFF[:2], "live-turn")
        stores.reserve(live, "live-input")
        task = asyncio.create_task(asyncio.sleep(3600))
        pixel._result_tasks[live] = task
        try:
            return await pixel.pixel_chat_cancel(pixel.ChatCancelRequest(chat_id=CHAT), OWNER)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert asyncio.run(run()) == {"aborted": False}


@pytest.mark.parametrize("chunks,expected", [
    ([b'{"state":"settled"}'], "settled"),
    ([b'{"state":"active"}'], "active"),
    ([b'{"state":"terminal"}'], None),
    ([b'{"state":"settled","secret":"hidden"}'], None),
    ([b'{"aborted":true}'], None),
    ([b"x" * 1025], None),
])
def test_settle_request_is_exact_and_only_trusts_a_closed_receipt(chunks, expected, monkeypatch):
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", EDGE_KEY)
    capture: dict[str, Any] = {}
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(FakeResponse(chunks=chunks), capture)) as client:
        assert asyncio.run(pixel._settle_edge_chat("http://pixel-edge:9595", EDGE_KEY, CHAT)) == expected
    assert capture["method"] == "POST"
    assert capture["url"] == "http://pixel-edge:9595/v1/chat/settle"
    assert capture["json"] == {"user": CHAT}
    assert capture["headers"]["Authorization"] == f"Bearer {EDGE_KEY}"
    assert client.call_args.kwargs["trust_env"] is False
    assert client.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize("response", [FakeResponse(status=404), FakeResponse(status=502), FakeResponse(content_type="text/plain")])
def test_an_older_or_failing_edge_never_settles(response, monkeypatch):
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(response)):
        assert asyncio.run(pixel._settle_edge_chat("http://pixel-edge:9595", EDGE_KEY, CHAT)) is None
    with patch.object(pixel.httpx, "AsyncClient", side_effect=pixel.httpx.ConnectError("private credential")):
        assert asyncio.run(pixel._settle_edge_chat("http://pixel-edge:9595", EDGE_KEY, CHAT)) is None
    assert EDGE_KEY not in json.dumps(response.headers)
