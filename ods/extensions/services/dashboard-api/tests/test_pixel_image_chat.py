import asyncio
import base64
from io import BytesIO

from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError
import pytest

import pixel_chat_identity
from pixel_chat_results import owner_namespace
from pixel_chat_results import ChatResultStore
from pixel_image_store import ImageStore
from routers import pixel


@pytest.fixture
def turn(tmp_path, monkeypatch):
    monkeypatch.setenv("ODS_DATA_DIR", str(tmp_path))
    async def identity():
        return "Portal"
    monkeypatch.setattr(pixel_chat_identity, "confirmed_display_name", identity)
    async def context(_body):
        return {"model": {"routeFingerprint": "f" * 64, "imageInput": "supported"}}
    monkeypatch.setattr(pixel, "_chat_context_request", context)
    output = BytesIO()
    Image.new("RGB", (3, 2), "blue").save(output, format="PNG")
    store = ImageStore(tmp_path / "pixel-images")
    try:
        receipt = store.put(owner_namespace("owner"), "chat", output.getvalue(), "image/png")
    finally:
        store.close()
    reference = {key: receipt[key] for key in ("id", "sha256")}
    message = {"role": "user", "content": "", "images": [reference]}
    value = {"chat_id": "chat", "request_id": "turn", "messages": [message],
             "image_route": {"routeFingerprint": "f" * 64, "unknownConsent": False},
             "history_snapshot": {"schemaVersion": 2, "messages": [message]}}
    return value, output.getvalue()


def test_image_only_chat_preparation_preserves_archive_and_resolves_bytes(turn):
    value, image = turn
    body = pixel.ChatStreamRequest.model_validate(value)
    before = body.model_dump()
    messages = asyncio.run(pixel._prepare_chat_messages(body, "owner"))
    assert messages[0]["role"] == "system"
    assert messages[-1]["images"] == value["messages"][-1]["images"]
    assert base64.b64decode(messages[-1]["content"][0]["image_url"]["url"].split(",")[1]) == image
    assert body.model_dump() == before
    outgoing = pixel._edge_chat_body(body, messages)
    assert outgoing["history_snapshot"] == value["history_snapshot"]


def test_wrong_owner_does_not_prepare_text_only_request(turn):
    value, _ = turn
    body = pixel.ChatStreamRequest.model_validate(value)
    with pytest.raises(HTTPException) as error:
        asyncio.run(pixel._prepare_chat_messages(body, "other"))
    assert error.value.status_code == 409


@pytest.mark.parametrize("capability,fingerprint,consent,allowed", [
    ("supported", "f" * 64, False, True), ("unsupported", "f" * 64, True, False),
    ("unknown", "f" * 64, False, False), ("unknown", "f" * 64, True, True),
    (None, "f" * 64, True, False), ("supported", "e" * 64, True, False),
])
def test_image_capability_and_consent_are_bound_to_current_route(turn, monkeypatch, capability, fingerprint, consent, allowed):
    value, _ = turn
    value["image_route"]["unknownConsent"] = consent
    async def context(_body):
        return {"model": {"routeFingerprint": fingerprint, "imageInput": capability}}
    monkeypatch.setattr(pixel, "_chat_context_request", context)
    body = pixel.ChatStreamRequest.model_validate(value)
    if allowed:
        assert asyncio.run(pixel._prepare_chat_messages(body, "owner"))[-1]["content"]
    else:
        with pytest.raises(HTTPException) as error:
            asyncio.run(pixel._prepare_chat_messages(body, "owner"))
        assert error.value.status_code == 409


@pytest.mark.parametrize("missing", ["request_id", "history_snapshot", "image_route"])
def test_images_require_durable_request_and_history(turn, missing):
    value, _ = turn
    value.pop(missing)
    with pytest.raises(ValidationError):
        pixel.ChatStreamRequest.model_validate(value)


def test_image_in_older_live_message_must_use_history(turn):
    value, _ = turn
    value["messages"].append({"role": "user", "content": "Another turn"})
    with pytest.raises(ValidationError):
        pixel.ChatStreamRequest.model_validate(value)


def test_legacy_text_serialization_keeps_exact_shape():
    value = {"role": "user", "content": "hello"}
    body = pixel.ChatStreamRequest(chat_id="chat", messages=[value])
    assert body.messages[0].model_dump() == value


def test_missing_image_retains_terminal_rejection_without_starting_agent(turn, tmp_path, monkeypatch):
    value, _ = turn
    value["messages"][0]["images"][0]["id"] = "img-" + "0" * 32
    body = pixel.ChatStreamRequest.model_validate(value)
    store = ChatResultStore(tmp_path / "receipts")
    monkeypatch.setattr(pixel, "_result_store", store)
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", "x" * 64)
    async def ready():
        return None
    monkeypatch.setattr(pixel, "_model_readiness_issue", ready)
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(pixel._retained_chat_stream(None, body, "owner"))
        assert error.value.status_code == 409
        key = (owner_namespace("owner"), "chat", "turn")
        assert store.get(key)["state"] == "interrupted"
        assert not store.has_pending(key[:2])
        assert key not in pixel._result_tasks
        assert key not in pixel._result_preflights
        data = b"".join(row["data"] for row in store.chunks(key))
        assert b"Portal did not start this attempt" in data
        assert b"data: [DONE]" in data
    finally:
        store.close()


def test_retained_image_turn_prepares_bytes_inside_reserved_preflight(turn, tmp_path, monkeypatch):
    value, image = turn
    body = pixel.ChatStreamRequest.model_validate(value)
    store = ChatResultStore(tmp_path / "receipts")
    key = (owner_namespace("owner"), "chat", "turn")
    monkeypatch.setattr(pixel, "_result_store", store)
    monkeypatch.setattr(pixel, "_result_tasks", {})
    monkeypatch.setattr(pixel, "_result_preflights", set())
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", "x" * 64)
    original_prepare = pixel._prepare_chat_messages
    captured = []
    async def ready():
        return None
    async def prepare(body, owner):
        assert store.get(key)["state"] == "active"
        assert key in pixel._result_preflights
        return await original_prepare(body, owner)
    async def producer(store, identity, body, config, messages, *, owner):
        captured.extend(messages)
        store.finish(identity, "complete")
    monkeypatch.setattr(pixel, "_model_readiness_issue", ready)
    monkeypatch.setattr(pixel, "_prepare_chat_messages", prepare)
    monkeypatch.setattr(pixel, "_produce_retained_result", producer)
    async def run():
        await pixel._retained_chat_stream(None, body, "owner")
        await pixel._result_tasks[key]
    try:
        asyncio.run(run())
        block = captured[-1]["content"][0]
        assert base64.b64decode(block["image_url"]["url"].split(",")[1]) == image
        assert store.get(key)["state"] == "complete"
        assert key not in pixel._result_preflights
    finally:
        store.close()


def test_text_followup_preserves_images_only_in_history(turn):
    value, _ = turn
    original = value['messages'][0]
    following = {'role': 'user', 'content': 'Continue with text'}
    value['messages'] = [{'role': 'user', 'content': original['content']}, following]
    value['history_snapshot']['messages'] = [original, following]
    body = pixel.ChatStreamRequest.model_validate(value)
    assert body.messages[0].images is None
    assert body.history_snapshot.messages[0].images[0].id == original['images'][0]['id']
    assert body.messages[-1].model_dump() == following
