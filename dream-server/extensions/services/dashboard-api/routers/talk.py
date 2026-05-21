"""Dream Talk mobile portal API.

These endpoints are intentionally cookie-authenticated only. The dashboard
nginx injects the admin API key for same-origin /api requests, but Dream Talk
is a consumer surface opened from an owner QR. Holding the admin API key alone
must not grant access here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

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


@router.post("/api/talk/message")
async def talk_message(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    session_key, _expires_at = _require_session(request)
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="Message text is required.")
    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="Message is too long.")
    return await _send_to_hermes(session_key, text)


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
