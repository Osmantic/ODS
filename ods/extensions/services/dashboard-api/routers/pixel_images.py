"""Authenticated image storage for Portal; never a public preview surface."""

import asyncio
import logging
import os
from pathlib import Path
import re
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from pixel_chat_results import owner_namespace
from pixel_image_input import ImageInputError, MAX_IMAGE_BYTES
from pixel_image_store import ImageStore, ImageStoreCapacity, ConversationDeleted
from pixel_image_transport import ImageResolutionError, resolve_image_parts
from pixel_image_admission import ImageWorkBudget
from security import verify_api_key


router = APIRouter(prefix="/images", tags=["pixel"])
logger = logging.getLogger(__name__)
_CHAT = re.compile(r"[A-Za-z0-9_-]{1,128}")
_IMAGE = re.compile(r"img-[a-f0-9]{32}")
_storage_lock = threading.Lock()
_image_work = ImageWorkBudget(1)
_UPLOAD_TIMEOUT_SECONDS = 30
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'none'; sandbox"}


def _check_scope(chat_id, image_id=None):
    if not _CHAT.fullmatch(chat_id) or image_id is not None and not _IMAGE.fullmatch(image_id):
        raise HTTPException(status_code=404, detail="Portal image not found")


def _storage_call(method, *args):
    # Open/use/close SQLite on one worker thread. Serialize initialization and
    # transactions without blocking the API event loop or sharing a connection.
    with _storage_lock:
        store = ImageStore(Path(os.environ.get("ODS_DATA_DIR", "/data")) / "pixel-images")
        try:
            return method(store, *args) if callable(method) else getattr(store, method)(*args)
        finally:
            store.close()


def _reserve():
    lease = _image_work.acquire()
    if lease is None:
        raise HTTPException(status_code=429, detail="Portal image processing is busy. Try again shortly.",
                            headers={"Retry-After": "1"})
    return lease


async def _call(method, *args, reservation=None):
    lease = reservation if reservation is not None else _reserve()
    try:
        return await lease.run(_storage_call, method, *args)
    except ImageInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ImageResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImageStoreCapacity as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except ConversationDeleted as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except Exception as exc:
        # Do not expose paths, image data, credentials, or decoder diagnostics.
        logger.warning("Portal image storage operation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Portal image storage is unavailable") from exc
    finally:
        if reservation is None:
            lease.release()


async def resolve_message_images(owner: str, chat_id: str, text: str, references: list):
    """Resolve every immutable reference before admitting a new chat attempt."""
    _check_scope(chat_id)
    return await _call(resolve_image_parts, owner_namespace(owner), chat_id, text, references)


async def conversation_storage(method, owner, chat_id):
    """Small lifecycle transactions do not consume the image decoder lease."""
    try:
        return await asyncio.to_thread(_storage_call, method, owner_namespace(owner), chat_id)
    except ConversationDeleted as exc:
        raise HTTPException(410, str(exc)) from None
    except ImageStoreCapacity as exc:
        raise HTTPException(507, str(exc)) from None
    except Exception as exc:
        logger.warning("Portal conversation lifecycle unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, "Conversation image deletion could not be confirmed. Retry before deleting local history.") from None


@router.delete("/{chat_id}")
async def delete_conversation_images(chat_id: str, response: Response, owner: str = Depends(verify_api_key)):
    from routers import pixel
    _check_scope(chat_id)
    state = await conversation_storage("deletion", owner, chat_id)
    if state and state["completed"]:
        response.headers.update(_PRIVATE_HEADERS)
        return {"schemaVersion": 1, "deleted": True}
    if pixel._chat_results().has_pending((owner_namespace(owner), chat_id)):
        raise HTTPException(409, "Finish, stop or recover this conversation before deleting its images.")
    if state is None:
        native = await pixel._chat_context_request(pixel.ChatCancelRequest(chat_id=chat_id))
        if native.get("status") not in {"ready", "missing"} or native.get("history", {}).get("status") != "ready" or native.get("compaction", {}).get("status") in {"running", "unknown"}:
            raise HTTPException(409, "Finish, stop or recover this conversation before deleting its images.")
        # This intent blocks uploads and new API admissions across processes.
        # Lost replies leave it pending; retry always asks native custody again.
        await conversation_storage("begin_delete", owner, chat_id)
    await pixel._delete_native_conversation_images(chat_id)
    await conversation_storage("delete_conversation", owner, chat_id)
    response.headers.update(_PRIVATE_HEADERS)
    return {"schemaVersion": 1, "deleted": True}


@router.post("/{chat_id}", status_code=201)
async def upload_image(chat_id: str, request: Request, response: Response,
                       owner: str = Depends(verify_api_key)):
    _check_scope(chat_id)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=415, detail="Choose a PNG, JPEG or WebP image")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise HTTPException(status_code=400, detail="Invalid image content length")
        if len(length) > 12 or int(length) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds the 8 MiB upload limit")
    # Admission precedes collecting bytes, not merely scheduling the decoder.
    # There is no unbounded queue of buffered uploads or executor jobs.
    reservation = _reserve()
    try:
        data = bytearray()
        try:
            async with asyncio.timeout(_UPLOAD_TIMEOUT_SECONDS):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                        raise HTTPException(status_code=413, detail="Image exceeds the 8 MiB upload limit")
                    data.extend(chunk)
        except TimeoutError as exc:
            raise HTTPException(status_code=408, detail="Image upload timed out. Try again.") from exc
        raw = bytes(data)
        data.clear()
        receipt = await _call("put", owner_namespace(owner), chat_id, raw, media_type,
                              reservation=reservation)
        response.headers.update(_PRIVATE_HEADERS)
        return {**receipt, "bytes": len(raw)}
    finally:
        reservation.release()


@router.get("/{chat_id}/{image_id}")
async def read_image(chat_id: str, image_id: str, owner: str = Depends(verify_api_key)):
    _check_scope(chat_id, image_id)
    image = await _call("get", owner_namespace(owner), chat_id, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Portal image not found")
    return Response(image["data"], media_type=image["media_type"], headers={
        **_PRIVATE_HEADERS, "Content-Disposition": f'inline; filename="{image_id}"',
    })


@router.delete("/{chat_id}/{image_id}")
async def discard_image(chat_id: str, image_id: str, response: Response,
                        owner: str = Depends(verify_api_key)):
    _check_scope(chat_id, image_id)
    result = await _call("discard_draft", owner_namespace(owner), chat_id, image_id)
    response.headers.update(_PRIVATE_HEADERS)
    return result
