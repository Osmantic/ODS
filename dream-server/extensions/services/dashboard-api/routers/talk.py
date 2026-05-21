"""Dream Talk mobile portal API.

These endpoints are intentionally cookie-authenticated only. The dashboard
nginx injects the admin API key for same-origin /api requests, but Dream Talk
is a consumer surface opened from an owner QR. Holding the admin API key alone
must not grant access here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

import hermes_bridge
import session_signer
from config import SERVICES
from helpers import check_service_health

logger = logging.getLogger(__name__)

router = APIRouter(tags=["talk"])

SESSION_COOKIE_NAME = "dream-session"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_MESSAGE_CHARS = 8000


def _require_session(request: Request) -> tuple[str, int]:
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    ok, reason = session_signer.verify(cookie_value)
    if not ok:
        logger.info("dream-talk session denied: reason=%s", reason)
        raise HTTPException(status_code=401, detail="Scan the owner card again to start a Dream Talk session.")
    try:
        _, expiry_str, _ = cookie_value.split(".")
        expires_at = int(expiry_str)
    except (ValueError, TypeError):
        expires_at = 0
    return hermes_bridge.talk_session_key(cookie_value), expires_at


async def _service_state(service_id: str) -> dict[str, Any]:
    cfg = SERVICES.get(service_id)
    if not cfg:
        return {"configured": False, "status": "not_configured"}
    try:
        result = await check_service_health(service_id, cfg)
        return {"configured": True, "status": result.status}
    except Exception:
        logger.warning("Dream Talk health check failed for %s", service_id, exc_info=True)
        return {"configured": True, "status": "unavailable"}


def _whisper_url() -> str:
    return (os.environ.get("WHISPER_URL") or "http://whisper:8000").rstrip("/")


def _tts_url() -> str:
    return (os.environ.get("KOKORO_URL") or os.environ.get("TTS_URL") or "http://tts:8880").rstrip("/")


def _stt_model() -> str:
    return os.environ.get("AUDIO_STT_MODEL") or "Systran/faster-whisper-base"


def _tts_model() -> str:
    return os.environ.get("AUDIO_TTS_MODEL") or "kokoro"


def _tts_voice() -> str:
    return os.environ.get("AUDIO_TTS_VOICE") or "af_heart"


async def _transcribe_bytes(data: bytes, filename: str, content_type: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{_whisper_url()}/v1/audio/transcriptions",
                data={"model": _stt_model()},
                files={"file": (filename, data, content_type or "application/octet-stream")},
            )
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Speech transcription is not available right now.") from exc

    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="No speech was detected in that audio.")
    return text.strip()


async def _speak_text(text: str) -> tuple[bytes, str]:
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{_tts_url()}/v1/audio/speech",
                json={
                    "model": _tts_model(),
                    "voice": _tts_voice(),
                    "input": text,
                    "response_format": "mp3",
                },
            )
            resp.raise_for_status()
            media_type = resp.headers.get("content-type") or "audio/mpeg"
            return resp.content, media_type
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Text-to-speech is not available right now.") from exc


async def _send_to_hermes(session_key: str, text: str) -> dict[str, Any]:
    try:
        reply = await hermes_bridge.submit_prompt(session_key, text)
    except hermes_bridge.HermesUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (hermes_bridge.HermesBridgeError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc) or "Hermes did not finish the response.") from exc

    return {
        "session_id": reply.session_id,
        "text": reply.text,
        "status": reply.status,
        "warning": reply.warning,
    }


def _sse_event(event_type: str, data: dict[str, Any]) -> bytes:
    """Encode one Server-Sent Events frame."""
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")


# SSE comment frame — clients ignore lines starting with ``:``. Used as a
# keepalive so iOS Safari and intermediate proxies don't close the connection
# while llama-server is doing 30-60s of prompt processing with no real frames.
_SSE_KEEPALIVE = b": keepalive\n\n"

# Emit a keepalive frame this often during silent gaps (in seconds).
_KEEPALIVE_INTERVAL = 5.0


async def _stream_hermes_sse(session_key: str, text: str, request: Request):
    """SSE generator wrapping the bridge's stream_prompt.

    Yields one ``data:`` line per bridge event, terminated by ``\\n\\n``. A
    final ``done`` frame is emitted on normal completion or bridge errors, so
    the client knows the stream closed cleanly. If the HTTP client disconnects
    or the ASGI task is cancelled, the upstream bridge is cancelled without
    trying to write another frame to the dead response.

    Two ongoing-availability mechanisms:

    1. **Keepalive** — emit a ``: keepalive`` SSE comment every
       ``_KEEPALIVE_INTERVAL`` seconds while the bridge is silent (e.g.
       during the 30-60s cold prompt processing of the system prompt). Without
       this, iOS Safari and some intermediate proxies close idle streams,
       leaving the SPA stuck on a stalled "thinking" spinner.
    2. **Disconnect cancellation** — if the client's HTTP connection drops
       mid-request (phone screen locked, tab closed, retry), stop pulling
       from the bridge so we don't keep an upstream llama-server slot busy
       for a response nobody will ever read.
    """
    bridge_iter = hermes_bridge.stream_prompt(session_key, text).__aiter__()
    pending: asyncio.Task | None = None
    emit_done = True

    async def cancel_pending() -> None:
        nonlocal pending
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        pending = None

    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(bridge_iter.__anext__())
            try:
                done_set, _ = await asyncio.wait({pending}, timeout=_KEEPALIVE_INTERVAL)
            except asyncio.CancelledError:
                emit_done = False
                await cancel_pending()
                raise
            if not done_set:
                # No bridge event in the keepalive window; check disconnect
                # before sending more bytes, then emit a keepalive comment.
                if await request.is_disconnected():
                    emit_done = False
                    await cancel_pending()
                    return
                yield _SSE_KEEPALIVE
                continue
            # The bridge yielded something — pending is in done_set.
            try:
                event = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            except hermes_bridge.HermesUnavailable as exc:
                yield _sse_event("error", {"status_code": 503, "detail": str(exc)})
                pending = None
                break
            except (hermes_bridge.HermesBridgeError, asyncio.TimeoutError) as exc:
                yield _sse_event("error", {"status_code": 502, "detail": str(exc) or "Hermes did not finish the response."})
                pending = None
                break
            pending = None  # ready for next iteration

            et = event.get("type")
            if et == "session":
                yield _sse_event("session", {"session_id": event.get("session_id", "")})
            elif et == "delta":
                yield _sse_event("delta", {"text": event.get("text", "")})
            elif et == "complete":
                yield _sse_event("complete", {
                    "session_id": event.get("session_id", ""),
                    "text": event.get("text", ""),
                    "status": event.get("status") or "ok",
                    "warning": event.get("warning"),
                })
    finally:
        await cancel_pending()
        if emit_done:
            yield _sse_event("done", {})


@router.get("/api/talk/status")
async def talk_status(request: Request) -> dict[str, Any]:
    _session_key, expires_at = _require_session(request)
    hermes, whisper, tts = await asyncio.gather(
        _service_state("hermes"),
        _service_state("whisper"),
        _service_state("tts"),
    )
    voice_ready = whisper.get("status") == "healthy" and tts.get("status") == "healthy"
    return {
        "ok": True,
        "session": {"expires_at": expires_at},
        "services": {
            "hermes": hermes,
            "whisper": whisper,
            "tts": tts,
        },
        "capabilities": {
            "text_chat": hermes.get("status") == "healthy",
            "tts": tts.get("status") == "healthy",
            "audio_message": voice_ready,
            "live_mic_requires_secure_context": True,
        },
    }


@router.post("/api/talk/session")
async def talk_session(request: Request) -> dict[str, Any]:
    session_key, expires_at = _require_session(request)
    try:
        session_id = await hermes_bridge.ensure_session(session_key)
    except hermes_bridge.HermesUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (hermes_bridge.HermesBridgeError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc) or "Hermes session could not be started.") from exc
    return {"session_id": session_id, "expires_at": expires_at}


def _extract_message_text(payload: Any) -> str:
    """Pull and validate the ``text`` field from a /api/talk/message body."""
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="Message text is required.")
    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="Message is too long.")
    return text


@router.post("/api/talk/message")
async def talk_message(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Synchronous chat send. Waits for the full Hermes reply, returns JSON.

    Kept for non-browser callers and tests. New UI code should use the SSE
    endpoint /api/talk/message/stream so the user sees tokens land as Hermes
    generates them — on a cold first message (16k-token system prompt) the
    blocking version can hold the request open for 60+ seconds before any
    visible feedback, which strands the UI on a "thinking" spinner.
    """
    session_key, _expires_at = _require_session(request)
    text = _extract_message_text(payload)
    return await _send_to_hermes(session_key, text)


@router.post("/api/talk/message/stream")
async def talk_message_stream(payload: dict[str, Any], request: Request) -> StreamingResponse:
    """Server-Sent Events chat send. Streams delta + complete events.

    Frame shape (one JSON object per ``data:`` line, ``\\n\\n`` terminator):

      {"type": "session",  "session_id": "<id>"}
      {"type": "delta",    "text": "<chunk>"}                    # repeats
      {"type": "complete", "session_id": "<id>", "text": "...",
                           "status": "ok", "warning": null}
      {"type": "error",    "status_code": 502|503, "detail": "..."}  # on failure
      {"type": "done"}                                                # always last

    The endpoint sets ``X-Accel-Buffering: no`` and ``Cache-Control: no-cache``
    so the dashboard nginx proxy passes frames through immediately rather
    than batching them.
    """
    session_key, _expires_at = _require_session(request)
    text = _extract_message_text(payload)
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        _stream_hermes_sse(session_key, text, request),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/api/talk/audio-message")
async def talk_audio_message(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    session_key, _expires_at = _require_session(request)
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio message is too large.")
    transcript = await _transcribe_bytes(
        data,
        file.filename or "dream-talk-audio.webm",
        file.content_type or "application/octet-stream",
    )
    reply = await _send_to_hermes(session_key, transcript)
    reply["transcript"] = transcript
    return reply


@router.post("/api/talk/speak")
async def talk_speak(request: Request, text: str = Form(...)) -> Response:
    _session_key, _expires_at = _require_session(request)
    clean = text.strip()
    if not clean:
        raise HTTPException(status_code=422, detail="Text is required.")
    if len(clean) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="Text is too long.")
    audio, media_type = await _speak_text(clean)
    return Response(content=audio, media_type=media_type)
