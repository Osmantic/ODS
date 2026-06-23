"""Dream Talk mobile portal API.

These endpoints are intentionally cookie-authenticated only. The dashboard
nginx injects the admin API key for same-origin /api requests, but Dream Talk
is a consumer surface opened from an owner QR. Holding the admin API key alone
must not grant access here.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

import hermes_bridge
import session_signer
from config import SERVICES
from helpers import check_service_health

logger = logging.getLogger(__name__)

router = APIRouter(tags=["talk"])

SESSION_COOKIE_NAME = "dream-session"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_MESSAGE_CHARS = 8000

# Attachment limits — chat surface, not document-ingestion. Pick conservative
# ceilings that still let a phone photo + a short doc through.
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 10 MB raw — base64 inflates to ~13 MB on the wire
MAX_DOC_BYTES = 5 * 1024 * 1024      # 5 MB extracted text limit, generous
MAX_DOC_CHARS = 80_000               # post-extraction character cap so a megabyte
                                     # log doesn't blow past the model's context

# MIME types we accept on the attach surface. Anything else → 415.
_IMAGE_MIME_PREFIXES = ("image/",)
_IMAGE_EXTENSION_MIMES = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_TEXT_LIKE_MIMES = {
    "text/plain", "text/markdown", "text/csv", "text/x-markdown",
    "application/json", "application/xml", "text/xml",
    "application/x-yaml", "text/yaml", "text/x-yaml",
}
_TEXT_LIKE_EXTENSIONS = {  # browsers sometimes upload these as application/octet-stream
    ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml",
    ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh",
}


def _vision_model_name() -> str:
    """Lemonade name of the vision-capable model. Defaults match the strix
    user.* registration we ship; operators can override per-host via env."""
    return os.environ.get("DREAM_TALK_VISION_MODEL", "user.Qwen3.6-35B-A3B-Vision")


def _vision_backend_base_url() -> str:
    """OpenAI-compatible base URL for multimodal requests.

    Defaults to Lemonade / llama-server direct (``http://llama-server:8080/v1``)
    — NOT litellm — because litellm's
    ``model_name: '*'`` wildcard normalises our ``user.*`` model id down to
    whatever llama-server has currently loaded, which silently downgrades
    image queries to the text-only model. Lemonade routes by exact model
    id and auto-swaps to the vision variant on first multimodal call.

    ``DREAM_TALK_VISION_URL`` accepts either a host root
    (``http://host:8080``) or a full OpenAI-compatible base
    (``http://host:8080/v1`` / ``http://host:8080/api/v1``). Normalising here
    keeps Linux container, Windows host, llama-server, and Lemonade paths from
    accidentally becoming ``/v1/v1`` or ``/api/v1/v1``.
    """
    raw = (
        os.environ.get("DREAM_TALK_VISION_URL")
        or os.environ.get("LLM_API_URL")
        or "http://llama-server:8080"
    ).rstrip("/")
    if raw.endswith("/v1") or raw.endswith("/api/v1"):
        return raw

    base_path = (os.environ.get("LLM_API_BASE_PATH") or "/v1").strip()
    if not base_path:
        base_path = "/v1"
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"
    return f"{raw}{base_path.rstrip('/')}"


def _vision_chat_completions_url() -> str:
    return f"{_vision_backend_base_url()}/chat/completions"


def _vision_backend_key() -> str:
    """Bearer token for the vision backend. Empty when hitting Lemonade
    direct on the internal docker network (no auth needed there); set when
    a host routes through litellm or another authenticated proxy."""
    return os.environ.get("DREAM_TALK_VISION_KEY") or ""


async def _stream_vision_chat(image_bytes: bytes, content_type: str, prompt_text: str) -> AsyncIterator[bytes]:
    """Send a single multimodal turn directly to litellm and translate the
    streaming response into the same SSE frame shape Dream Talk already uses
    (session / delta / complete / done / error). Bypasses Hermes for image
    queries because Hermes's prompt.submit only takes text — the multimodal
    content array is a litellm/llama-server-level concept.

    Trade-off: image queries don't get Hermes's tool layer (no web_search,
    memory, etc.) — they're a one-shot "describe this image" exchange. For
    follow-up turns, users continue typing normally and Hermes resumes.
    """
    image_url = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    payload = {
        "model": _vision_model_name(),
        "stream": True,
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]},
        ],
    }
    yield _sse_event("session", {"session_id": "vision-oneshot"})

    accumulated: list[str] = []
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    key = _vision_backend_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                _vision_chat_completions_url(),
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    detail = body.decode("utf-8", errors="replace")[:300]
                    yield _sse_event("error", {"status_code": resp.status_code, "detail": detail})
                    yield _sse_event("done", {})
                    return
                async for raw in resp.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    payload_str = raw[5:].strip()
                    if payload_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload_str)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        accumulated.append(text)
                        yield _sse_event("delta", {"text": text})
    except (httpx.ReadTimeout, httpx.ConnectError, httpx.HTTPError) as exc:
        yield _sse_event("error", {"status_code": 502, "detail": f"Vision model unavailable: {exc}"})
        yield _sse_event("done", {})
        return

    final_text = "".join(accumulated).strip() or "(no response)"
    yield _sse_event("complete", {
        "session_id": "vision-oneshot",
        "text": final_text,
        "status": "ok",
        "warning": None,
    })
    yield _sse_event("done", {})


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
    return os.environ.get("AUDIO_TTS_VOICE") or os.environ.get("TTS_VOICE") or "af_heart"


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


async def _stream_speech(text: str) -> AsyncIterator[bytes]:
    """Stream MP3 bytes from Kokoro as they arrive instead of buffering the
    whole reply before sending anything back to the SPA.

    Kokoro already supports streaming (``stream: true`` is its default; we
    just used to throw it away by reading ``resp.content``). For a typical
    multi-sentence Hermes reply Kokoro emits its first audio chunk in
    ~500ms-1s while the full mux still takes 5-15s — so streaming cuts
    time-to-first-audio from "wait for whole mp3" to "wait for one
    sentence." That's the difference between a 7-second silent pause and
    nearly-instant playback for the operator on Dream Talk.

    On a mid-stream Kokoro error we log + end the response cleanly. The
    browser then hears truncated audio (half a sentence) rather than
    silence — strictly better UX than the previous buffer-then-503
    failure mode, which the SPA had to silently swallow.
    """
    payload = {
        "model": _tts_model(),
        "voice": _tts_voice(),
        "input": text,
        "response_format": "mp3",
        # Explicitly request streaming. Kokoro's default is true but the
        # request schema lets clients override; pinning makes the
        # contract obvious to anyone tracing the call.
        "stream": True,
    }
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{_tts_url()}/v1/audio/speech",
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    logger.warning(
                        "kokoro returned %s for /v1/audio/speech: %s",
                        resp.status_code, body.decode("utf-8", errors="replace")[:200],
                    )
                    return
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk
    except (httpx.HTTPError, httpx.StreamError) as exc:
        # Mid-stream errors: log + return. The browser sees the response
        # close early and plays whatever audio it already buffered.
        logger.warning("kokoro stream ended early: %s", exc)
        return


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


# Hermes tool name → human-readable spinner caption. We map by exact name
# first, then by a few well-known prefixes. The fallback is a literal "Using
# `<name>`…" so an unrecognised tool still produces something honest rather
# than a blank.
_TOOL_LABELS: dict[str, str] = {
    "web_search": "Searching the web…",
    "web_extract": "Reading the page…",
    "execute_code": "Running code…",
    "read_file": "Looking at a file…",
    "write_file": "Saving a file…",
    "patch": "Editing a file…",
    "search_files": "Searching files…",
    "memory": "Checking memory…",
    "text_to_speech": "Generating voice…",
    "session_search": "Searching past conversations…",
    "todo": "Updating todos…",
    "cronjob": "Scheduling…",
    "delegate_task": "Delegating to a subagent…",
    "image_generate": "Drawing an image…",
    "vision_analyze": "Looking at an image…",
    "clarify": "Asking for clarification…",
    "send_message": "Sending a message…",
}


def _label_for_tool(tool_name: str) -> str:
    """Map a Hermes tool name to a friendly spinner caption."""
    if tool_name in _TOOL_LABELS:
        return _TOOL_LABELS[tool_name]
    # Common prefixes — browser_* / memory_* / skill_* etc.
    if tool_name.startswith("browser_"):
        return "Browsing…"
    if tool_name.startswith("memory_") or tool_name.startswith("memory"):
        return "Checking memory…"
    if tool_name.startswith("skill_") or tool_name == "skills_list":
        return "Loading a skill…"
    if tool_name.startswith("github_"):
        return "Talking to GitHub…"
    # Honest fallback — show the literal tool name so the operator can map it
    # in _TOOL_LABELS next pass.
    return f"Using `{tool_name}`…"


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
            elif et == "status":
                # Hermes pre-formatted status text (e.g. "⏳ Retrying in 5.1s…",
                # "🔎 Searching the web…"). Forward verbatim — upstream is
                # responsible for the human-readable wording.
                yield _sse_event("status", {
                    "label": event.get("label"),
                    "kind": event.get("kind"),
                    "tool": None,
                    "detail": None,
                })
            elif et == "tool_start":
                # Translate raw bridge event into a friendly "status" frame
                # the SPA renders as the spinner caption. The SPA replaces
                # the caption on each new status, then drops it once
                # message.delta frames start arriving.
                tool_name = event.get("tool", "")
                yield _sse_event("status", {
                    "label": _label_for_tool(tool_name),
                    "tool": tool_name,
                    "detail": event.get("detail") or None,
                })
            elif et == "tool_complete":
                # Tool finished — flip back to the default "Thinking…" caption
                # until the next tool starts or message.delta arrives. SPA
                # treats label=None as "clear and show default."
                yield _sse_event("status", {
                    "label": None,
                    "tool": None,
                    "detail": None,
                })
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


def _classify_attachment(file: UploadFile) -> str:
    """Return one of: ``image``, ``text``, or raise 415.

    Looks at the MIME type the browser supplied first; falls back to filename
    extension because some browsers / iOS upload everything as
    application/octet-stream. We deliberately keep the accept-set narrow on
    this v1 surface — chat, not document ingestion.
    """
    ct = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""

    if any(ct.startswith(p) for p in _IMAGE_MIME_PREFIXES):
        return "image"
    if ext in _IMAGE_EXTENSION_MIMES:
        return "image"
    if ct in _TEXT_LIKE_MIMES:
        return "text"
    if ext in _TEXT_LIKE_EXTENSIONS:
        return "text"
    raise HTTPException(
        status_code=415,
        detail=f"This file type isn't supported on chat yet (got {ct or 'unknown'}, {ext or 'no extension'}). Try an image, .txt, .md, .csv, .json, or code file.",
    )


def _image_content_type(file: UploadFile) -> str:
    """Return a browser-safe image MIME, falling back from filename extension."""
    ct = (file.content_type or "").lower()
    if any(ct.startswith(p) for p in _IMAGE_MIME_PREFIXES):
        return ct

    name = (file.filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return _IMAGE_EXTENSION_MIMES.get(ext, "image/png")


@router.post("/api/talk/attachment")
async def talk_attachment(
    request: Request,
    file: UploadFile = File(...),
    text: str = Form(""),
) -> StreamingResponse:
    """Multipart attachment endpoint. Returns the same SSE event shape as
    ``/api/talk/message/stream`` so the SPA can use a single rendering path.

    Two routing paths inside:

    1. **Images** → multimodal one-shot to litellm against the vision-capable
       model (e.g. ``user.Qwen3.6-35B-A3B-Vision`` on Lemonade hosts). Hermes's
       prompt.submit API only accepts plain text, so vision queries bypass
       the agent loop. Acceptable trade-off for v1: image queries don't get
       Hermes's tool layer, but they do get a real model-vision answer.
    2. **Text-like files** (.txt/.md/.csv/.json/code) → extract content,
       prepend to the user's caption, route through the existing Hermes
       bridge so the agent retains tools and memory.

    PDF/docx aren't in scope for v1 (no parser dependency yet).
    """
    session_key, _expires_at = _require_session(request)
    kind = _classify_attachment(file)

    caption = (text or "").strip()
    if len(caption) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="Caption is too long.")

    if kind == "image":
        data = await file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail=f"Image is too large (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB).")
        prompt_text = caption or "Describe what you see in this image."
        return StreamingResponse(
            _stream_vision_chat(data, _image_content_type(file), prompt_text),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # text-like
    data = await file.read(MAX_DOC_BYTES + 1)
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(status_code=413, detail=f"File is too large (max {MAX_DOC_BYTES // (1024 * 1024)} MB).")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("utf-8", errors="replace")
    if len(content) > MAX_DOC_CHARS:
        content = content[:MAX_DOC_CHARS] + f"\n\n[Truncated — file was {len(data):,} bytes, showing first {MAX_DOC_CHARS:,} chars]"

    filename = file.filename or "attachment.txt"
    prompt = (
        f"[Attached file: {filename}]\n"
        f"```\n{content}\n```\n\n"
        f"{caption or 'Take a look at this and let me know what you think.'}"
    )
    return StreamingResponse(
        _stream_hermes_sse(session_key, prompt, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
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
async def talk_speak(request: Request, text: str = Form(...)) -> StreamingResponse:
    """Stream MP3 audio for ``text`` as Kokoro produces it.

    The SPA's preferred consumption path is the browser's ``MediaSource`` API
    fed from ``fetch().response.body``, which plays audio chunks as they
    arrive (first audible token within ~500ms-1s). Browsers without
    ``MediaSource`` fall back to collecting the full body into a Blob
    before playback — same wall-clock as today, but no regression.
    """
    _session_key, _expires_at = _require_session(request)
    clean = text.strip()
    if not clean:
        raise HTTPException(status_code=422, detail="Text is required.")
    if len(clean) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="Text is too long.")
    return StreamingResponse(
        _stream_speech(clean),
        media_type="audio/mpeg",
        # X-Accel-Buffering: no tells nginx (and similar reverse proxies)
        # NOT to buffer the audio stream — otherwise our streaming work
        # gets re-buffered into the same multi-second delay we just removed.
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )
