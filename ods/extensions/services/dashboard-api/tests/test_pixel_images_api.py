from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from routers import pixel_images
from security import verify_api_key
import asyncio
import base64
from fastapi import HTTPException


def png():
    output = BytesIO()
    Image.new("RGB", (4, 4), "red").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ODS_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(pixel_images.router, prefix="/api/pixel")
    app.dependency_overrides[verify_api_key] = lambda: "owner-a"
    with TestClient(app) as client:
        yield client


def upload(client, chat="chat"):
    return client.post(f"/api/pixel/images/{chat}", content=png(), headers={"Content-Type": "image/png"})


def test_authenticated_upload_read_and_owner_conversation_isolation(client):
    result = upload(client)
    assert result.status_code == 201
    receipt = result.json()
    assert set(receipt) == {"id", "sha256", "media_type", "width", "height", "bytes"}
    assert receipt["bytes"] == len(png())
    url = f'/api/pixel/images/chat/{receipt["id"]}'
    read = client.get(url)
    assert read.status_code == 200 and read.content == png()
    assert read.headers["cache-control"] == "no-store"
    assert read.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in read.headers["content-security-policy"]
    assert client.get(url.replace("/chat/", "/different/")).status_code == 404
    client.app.dependency_overrides[verify_api_key] = lambda: "owner-b"
    assert client.get(url).status_code == 404
    client.app.dependency_overrides.clear()
    assert client.get(url).status_code in {401, 403}
    assert upload(client).status_code in {401, 403}


def test_chat_resolution_uses_same_private_store_and_never_text_fallback(client):
    receipt = upload(client).json()
    reference = {key: receipt[key] for key in ("id", "sha256")}
    parts = asyncio.run(pixel_images.resolve_message_images("owner-a", "chat", "Read it", [reference]))
    assert parts[0] == {"type": "text", "text": "Read it"}
    assert base64.b64decode(parts[1]["image_url"]["url"].split(",")[1], validate=True) == png()
    for owner, chat, refs in [
        ("owner-b", "chat", [reference]), ("owner-a", "other", [reference]),
        ("owner-a", "chat", [{**reference, "sha256": "0" * 64}]),
    ]:
        with pytest.raises(HTTPException) as failure:
            asyncio.run(pixel_images.resolve_message_images(owner, chat, "Read it", refs))
        assert failure.value.status_code == 409


def test_remove_draft_cannot_delete_an_image_retained_for_chat(client):
    receipt = upload(client).json()
    url = f'/api/pixel/images/chat/{receipt["id"]}'
    assert client.delete(url).json() == {"retained": False, "discarded": True}
    assert client.get(url).status_code == 404
    receipt = upload(client).json()
    url = f'/api/pixel/images/chat/{receipt["id"]}'
    reference = {key: receipt[key] for key in ("id", "sha256")}
    asyncio.run(pixel_images.resolve_message_images("owner-a", "chat", "Read", [reference]))
    assert client.delete(url).json() == {"retained": True, "discarded": False}
    assert client.get(url).content == png()
    client.app.dependency_overrides.clear()
    assert client.delete(url).status_code in {401, 403}


@pytest.mark.parametrize("media,data,status", [
    ("image/svg+xml", b"<svg/>", 415),
    ("image/png", b"not an image", 422),
    ("image/jpeg", png(), 422),
    ("application/json", b'"https://example.com/image"', 415),
])
def test_rejects_invalid_images_and_remote_references(client, media, data, status):
    assert client.post("/api/pixel/images/chat", content=data,
                       headers={"Content-Type": media}).status_code == status


def test_stream_limit_without_content_length(client, monkeypatch):
    monkeypatch.setattr(pixel_images, "MAX_IMAGE_BYTES", 10)
    response = client.post("/api/pixel/images/chat", content=iter([b"1" * 6, b"2" * 6]),
                           headers={"Content-Type": "image/png"})
    assert response.status_code == 413


def test_storage_failure_is_explicit_without_private_details(client, monkeypatch):
    def broken(*args):
        raise OSError("private path and bytes must not leak")
    monkeypatch.setattr(pixel_images, "_storage_call", broken)
    response = upload(client)
    assert response.status_code == 503
    assert response.json() == {"detail": "Portal image storage is unavailable"}


@pytest.mark.parametrize("operation", ["upload", "lifecycle"])
@pytest.mark.parametrize("failure_kind,diagnostic", [
    ("custody", "ImageStoreCustodyError; custody=database_mode"),
    ("unknown-custody", "ImageStoreCustodyError; custody=unknown"),
    ("other", "ValueError"),
])
def test_custody_logs_only_allowlisted_reason_and_keeps_public_response(
    client, monkeypatch, caplog, operation, failure_kind, diagnostic,
):
    from pixel_image_store import ImageStoreCustodyError
    private = "/private/path owner=123 credential=secret image=private-bytes"
    failure = (ValueError(private) if failure_kind == "other" else
               ImageStoreCustodyError("database_mode" if failure_kind == "custody" else private, private))

    def broken(*args):
        raise failure

    monkeypatch.setattr(pixel_images, "_storage_call", broken)
    if operation == "upload":
        response = upload(client)
        detail = "Portal image storage is unavailable"
        log = "Portal image storage operation failed"
    else:
        response = client.delete("/api/pixel/images/chat")
        detail = "Conversation image deletion could not be confirmed. Retry before deleting local history."
        log = "Portal conversation lifecycle unavailable"
    assert response.status_code == 503
    assert response.json() == {"detail": detail}
    assert f"{log} ({diagnostic})" in caplog.messages
    assert private not in caplog.text
    assert private not in response.text


def test_capacity_preserves_previous_image(client, monkeypatch):
    import pixel_image_store
    first = upload(client).json()
    monkeypatch.setattr(pixel_image_store, "MAX_STORE_IMAGES", 1)
    assert upload(client).json() == first
    assert upload(client, "another").status_code == 507
    assert client.get(f'/api/pixel/images/chat/{first["id"]}').content == png()
