import asyncio
from io import BytesIO
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

import pixel_image_store
from pixel_image_store import ImageStore, ConversationDeleted, ImageStoreCapacity
from pixel_chat_results import owner_namespace
from routers import pixel, pixel_images
from security import verify_api_key


def png():
    out = BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ODS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pixel, "_result_store", None)
    monkeypatch.setattr(pixel, "_chat_context_request", AsyncMock(return_value={"status": "ready", "history": {"status": "ready"}, "compaction": {"status": "idle"}}))
    monkeypatch.setattr(pixel, "_delete_native_conversation_images", AsyncMock())
    app = FastAPI()
    app.include_router(pixel.router)
    app.dependency_overrides[verify_api_key] = lambda: "owner"
    with TestClient(app) as value:
        yield value
        if pixel._result_store is not None:
            value.portal.call(pixel._result_store.close)


def upload(client, chat="chat"):
    result = client.post(f"/api/pixel/images/{chat}", content=png(), headers={"Content-Type": "image/png"})
    assert result.status_code == 201
    return result.json()


def test_delete_retained_images_native_first_then_scoped_durable_tombstone(client):
    receipt, other = upload(client), upload(client, "other")
    pixel_images._storage_call("retain", owner_namespace("owner"), "chat", [{"id": receipt["id"], "sha256": receipt["sha256"]}])
    result = client.delete("/api/pixel/images/chat")
    assert result.status_code == 200 and result.json() == {"schemaVersion": 1, "deleted": True}
    assert client.get(f'/api/pixel/images/chat/{receipt["id"]}').status_code == 410
    assert client.get(f'/api/pixel/images/other/{other["id"]}').status_code == 200
    assert client.post("/api/pixel/images/chat", content=png(), headers={"Content-Type": "image/png"}).status_code == 410
    assert client.delete("/api/pixel/images/chat").status_code == 200
    pixel._delete_native_conversation_images.assert_awaited_once_with("chat")
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(pixel._retained_chat_stream(None, pixel.ChatStreamRequest(chat_id="chat", request_id="retry", messages=[{"role": "user", "content": "stale"}]), "owner"))
    assert rejected.value.status_code == 410


def test_native_failure_preserves_bytes_and_pending_tombstone_then_retry_completes(client):
    upload(client)
    pixel._delete_native_conversation_images.side_effect = HTTPException(503, "pending retry")
    assert client.delete("/api/pixel/images/chat").status_code == 503
    assert pixel_images._storage_call("deletion", owner_namespace("owner"), "chat") == {"completed": False}
    assert pixel_images._storage_call(lambda store: store.db.execute("SELECT COUNT(*) FROM images").fetchone()[0]) == 1
    assert client.post("/api/pixel/images/chat", content=png(), headers={"Content-Type": "image/png"}).status_code == 410
    pixel._delete_native_conversation_images.side_effect = None
    # Retrying a pending deletion does not require context that is now fenced.
    pixel._chat_context_request.side_effect = AssertionError("must not reopen deleted native context")
    assert client.delete("/api/pixel/images/chat").status_code == 200
    assert pixel_images._storage_call(lambda store: store.db.execute("SELECT COUNT(*) FROM images").fetchone()[0]) == 0


@pytest.mark.parametrize("native", [
    {"status": "busy", "history": {"status": "ready"}, "compaction": {"status": "idle"}},
    {"status": "ready", "history": {"status": "unknown"}, "compaction": {"status": "idle"}},
    {"status": "ready", "history": {"status": "ready"}, "compaction": {"status": "running"}},
])
def test_active_interrupted_compacting_refuses_without_deletion_intent(client, native):
    receipt = upload(client)
    pixel._chat_context_request.return_value = native
    assert client.delete("/api/pixel/images/chat").status_code == 409
    assert pixel_images._storage_call("deletion", owner_namespace("owner"), "chat") is None
    assert client.get(f'/api/pixel/images/chat/{receipt["id"]}').status_code == 200
    pixel._delete_native_conversation_images.assert_not_awaited()


def test_retained_api_attempt_blocks_even_when_native_reports_idle(client):
    upload(client)
    client.portal.call(lambda: pixel._chat_results().reserve((owner_namespace("owner"), "chat", "active"), "a" * 64))
    assert client.delete("/api/pixel/images/chat").status_code == 409
    pixel._chat_context_request.assert_not_awaited()


def test_independent_connections_cannot_reupload_retain_or_reopen_a_tombstoned_scope(tmp_path, monkeypatch):
    first, second = ImageStore(tmp_path / "store"), ImageStore(tmp_path / "store")
    owner = "a" * 64
    image = first.put(owner, "chat", png(), "image/png")
    second.begin_delete(owner, "chat")
    with pytest.raises(ConversationDeleted):
        first.put(owner, "chat", png(), "image/png")
    with pytest.raises(ConversationDeleted):
        first.retain(owner, "chat", [image])
    first.delete_conversation(owner, "chat")
    assert second.deletion(owner, "chat") == {"completed": True}
    monkeypatch.setattr(pixel_image_store, "MAX_DELETED_CONVERSATIONS", 1)
    with pytest.raises(ImageStoreCapacity):
        second.begin_delete(owner, "new-chat")
    assert second.deletion(owner, "new-chat") is None
    assert second.begin_delete(owner, "chat") == {"completed": True}
    first.close()
    second.close()
