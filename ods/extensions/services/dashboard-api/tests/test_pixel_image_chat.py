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


def test_missing_image_does_not_reserve_or_start_retained_attempt(turn, tmp_path, monkeypatch):
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
        assert store.get((owner_namespace("owner"), "chat", "turn")) is None
    finally:
        store.close()
