#!/usr/bin/env python3
"""
M3: API Privacy Shield - HTTP Proxy (ODS Integration)
FastAPI-based proxy with connection pooling and PII caching.
"""

import logging
import os
import re
import time
import httpx
import secrets
import hashlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, Security, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from functools import lru_cache
from cachetools import TTLCache

from pii_scrubber import PrivacyShield, StreamRestorer
from key_management import resolve_shield_api_key

logger = logging.getLogger("privacy-shield")

# Hop-by-hop headers must not be forwarded between client and upstream
# (RFC 7230 6.1). Content-Length / Content-Encoding are dropped on the
# response side because PII restore changes the body length and we never
# re-compress, so a stale length/encoding header would corrupt the stream.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})
_DROP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length", "content-encoding"}

# Content types we will decode + run PII restore over. Everything else
# (images, audio, gzip/brotli-compressed, anything non-text) is streamed
# through byte-for-byte without a utf-8 decode so it can never raise.
_TEXTUAL_RE = re.compile(
    r"^(?:text/|application/(?:json|.*\+json|xml|.*\+xml|x-ndjson|javascript)"
    r"|application/x-www-form-urlencoded)",
    re.IGNORECASE,
)

# Bodies larger than this are passed through untouched even if textual, so a
# pathological response cannot pin the box buffering a hold-back window.
RESTORE_MAX_BYTES = int(os.getenv("SHIELD_RESTORE_MAX_BYTES", str(8 * 1024 * 1024)))


def _parse_content_type(content_type: str) -> tuple[str, str]:
    """Return (lowercased mime, charset) from a Content-Type header value."""
    mime = content_type.split(";", 1)[0].strip().lower()
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip('"').lower() or "utf-8"
    return mime, charset


def _is_textual(mime: str) -> bool:
    return bool(_TEXTUAL_RE.match(mime))


def _sanitize_error(exc: Exception) -> str:
    """Strip PII tokens / emails from an exception string before logging."""
    error_str = str(exc)
    error_str = re.sub(r"<PII_\w+_\w{12}>", "[REDACTED]", error_str)
    error_str = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", error_str
    )
    return error_str

# Security: API Key Authentication
DEFAULT_KEY_PATH = os.environ.get("SHIELD_API_KEY_PATH", "/data/shield_api_key")
SHIELD_API_KEY = resolve_shield_api_key(os.environ.get("SHIELD_API_KEY"), DEFAULT_KEY_PATH)

# auto_error=False so we return 401 (not FastAPI's default 403) for missing
# credentials. REST convention: 401 = "you must authenticate", 403 =
# "you authenticated but are forbidden". Matches the contract asserted by
# tests/test_proxy_auth.py::test_stats_no_auth_returns_401.
security_scheme = HTTPBearer(auto_error=False)

async def verify_api_key(credentials: HTTPAuthorizationCredentials | None = Security(security_scheme)):
    """Verify API key for protected endpoints."""
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _token_valid(credentials.credentials):
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return credentials.credentials


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await http_client.aclose()


app = FastAPI(title="API Privacy Shield", version="0.2.0", lifespan=lifespan)

# Configuration from environment
TARGET_API_BASE = os.getenv("TARGET_API_URL", "http://llama-server:8080/v1")
TARGET_API_KEY = os.getenv("TARGET_API_KEY", "not-needed")
PORT = int(os.getenv("SHIELD_PORT", "8085"))
CACHE_ENABLED = os.getenv("PII_CACHE_ENABLED", "true").lower() == "true"
CACHE_SIZE = int(os.getenv("PII_CACHE_SIZE", "1000"))
CACHE_TTL = int(os.getenv("PII_CACHE_TTL", "300"))

# Connection pool for better performance
http_client = httpx.AsyncClient(
    limits=httpx.Limits(max_keepalive_connections=100, max_connections=200),
    timeout=httpx.Timeout(60.0, connect=5.0)
)

# Session store (TTL cache with auto-eviction to prevent unbounded growth)
# maxsize=10000 sessions, ttl=3600 seconds (1 hour)
SESSION_MAXSIZE = int(os.getenv("SHIELD_SESSION_MAXSIZE", "10000"))
SESSION_TTL = int(os.getenv("SHIELD_SESSION_TTL", "3600"))
sessions = TTLCache(maxsize=SESSION_MAXSIZE, ttl=SESSION_TTL)


class CachedPrivacyShield(PrivacyShield):
    """PrivacyShield with LRU cache for PII patterns."""

    def __init__(self, backend_client=None):
        super().__init__(backend_client)
        if CACHE_ENABLED:
            self._scrub_cached = lru_cache(maxsize=CACHE_SIZE)(self._scrub_impl)

    def _scrub_impl(self, text: str) -> str:
        """Internal scrub implementation."""
        return self.detector.scrub(text)

    def scrub(self, text: str) -> str:
        """Scrub with optional caching."""
        if CACHE_ENABLED and len(text) < 1000:  # Only cache small texts
            return self._scrub_cached(text)
        return self._scrub_impl(text)


def get_session(request: Request) -> CachedPrivacyShield:
    """Get or create session-specific PrivacyShield."""
    # Use Authorization header or IP as session key
    auth = request.headers.get("Authorization", "")
    # Use SHA256 for deterministic, stable session keying (hash() is not deterministic across restarts)
    if auth:
        session_key = hashlib.sha256(auth.encode()).hexdigest()
    else:
        client_info = str(request.client.host if request.client else "default")
        session_key = hashlib.sha256(client_info.encode()).hexdigest()

    if session_key not in sessions:
        sessions[session_key] = CachedPrivacyShield()

    return sessions[session_key]


def _token_valid(token: str | None) -> bool:
    """Constant-time compare a presented token against SHIELD_API_KEY.

    Compared as UTF-8 bytes so a non-ASCII presented token is a clean
    mismatch rather than a TypeError on the pre-auth path. Same secret +
    secrets.compare_digest as verify_api_key / _is_authenticated.
    """
    if not token:
        return False
    return secrets.compare_digest(
        token.encode("utf-8", "strict"),
        SHIELD_API_KEY.encode("utf-8", "strict"),
    )


def _is_authenticated(request: Request) -> bool:
    """Check Bearer token from request headers directly (avoids FastAPI Security() version issues)."""
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") and _token_valid(auth[7:])


def _extract_ws_token(client_ws: WebSocket) -> tuple[str | None, str | None]:
    """Pull a bearer token off the WS handshake.

    Browser / Control-UI WebSocket clients cannot set an ``Authorization``
    header, so three carriers are accepted (first match wins):

      1. ``Authorization: Bearer <t>`` request header.
      2. ``?token=<t>`` query parameter.
      3. A ``Sec-WebSocket-Protocol`` subprotocol value, either the raw
         token or ``bearer.<token>`` / ``bearer,<token>`` paired form.

    Returns ``(token, selected_subprotocol)`` where the second element is the
    subprotocol the server must echo back on ``accept()`` when the token
    arrived via the subprotocol carrier (required for the browser handshake
    to complete), else ``None``.
    """
    auth = client_ws.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        # A present-but-empty "Authorization: Bearer " header short-circuits
        # here and suppresses the ?token= / subprotocol fallbacks. Intentional
        # / deferred: this can only ever cause a false REJECT (empty token
        # fails _token_valid), never a false accept.
        return auth[7:], None

    token = client_ws.query_params.get("token")
    if token:
        return token, None

    raw = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [p.strip() for p in raw.split(",") if p.strip()]
    for i, proto in enumerate(offered):
        if proto in ("bearer", "Bearer") and i + 1 < len(offered):
            return offered[i + 1], proto
        if proto.startswith(("bearer.", "Bearer.")):
            return proto.split(".", 1)[1], proto
    return None, None


@app.get("/health")
async def health(request: Request):
    """Health check endpoint. Sensitive fields require authentication."""
    if _is_authenticated(request):
        return {
            "status": "ok",
            "service": "api-privacy-shield",
            "version": "0.2.0",
            "target_api": TARGET_API_BASE,
            "cache_enabled": CACHE_ENABLED,
            "active_sessions": len(sessions),
        }
    return {"status": "ok"}


@app.get("/stats", dependencies=[Depends(verify_api_key)])
async def stats():
    """Session statistics."""
    total_pii = sum(
        s.detector.get_stats()['unique_pii_count']
        for s in sessions.values()
    )
    return {
        "cache_enabled": CACHE_ENABLED,
        "cache_size": CACHE_SIZE,
        "active_sessions": len(sessions),
        "total_pii_scrubbed": total_pii,
    }


def _build_upstream_headers(request: Request, scrubbed_len: int | None) -> dict:
    """Forward client headers to upstream, stripping hop-by-hop + host."""
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
        and k.lower() not in ("host", "content-length")
    }
    headers["host"] = TARGET_API_BASE.split("//")[-1].split("/")[0]
    if scrubbed_len is not None:
        headers["content-length"] = str(scrubbed_len)
    if TARGET_API_KEY and TARGET_API_KEY != "not-needed":
        headers["Authorization"] = f"Bearer {TARGET_API_KEY}"
    return headers


@app.post("/{path:path}", dependencies=[Depends(verify_api_key)])
@app.get("/{path:path}", dependencies=[Depends(verify_api_key)])
async def proxy(request: Request, path: str):
    """Streaming proxy: scrub PII outbound, restore inbound chunk-by-chunk.

    The request body is buffered (it is the prompt — PII must be fully
    scrubbed before any byte leaves the box; partial scrubbing would leak).
    A non-UTF-8 / binary request body is forwarded verbatim instead of
    failing, since the scrubber only operates on text. The *response* is
    streamed: textual bodies are PII-restored incrementally (SSE-safe,
    boundary-safe), everything else passes through byte-for-byte.
    """
    start_time = time.time()
    shield = get_session(request)

    raw_body = await request.body()
    if raw_body:
        try:
            body_str = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            # Binary / non-UTF-8 request: cannot scrub text; forward as-is
            # rather than 500. (LLM JSON prompts are always UTF-8 text.)
            body_str = None
    else:
        body_str = ""

    if body_str is None:
        outbound = raw_body
        metadata = {"pii_count": 0}
    else:
        scrubbed_body, metadata = shield.process_request(body_str)
        outbound = scrubbed_body.encode("utf-8")

    target_url = f"{TARGET_API_BASE}/{path}"
    method = request.method
    upstream_headers = _build_upstream_headers(
        request, len(outbound) if method == "POST" else None
    )

    req_kwargs: dict = {"headers": upstream_headers}
    if method == "POST":
        req_kwargs["content"] = outbound

    try:
        cm = http_client.stream(method, target_url, **req_kwargs)
        upstream = await cm.__aenter__()
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504, content={"error": "Gateway timeout", "shield": "active"}
        )
    except Exception as exc:  # noqa: BLE001 - sanitized below
        logger.error("Privacy shield connect error: %s", _sanitize_error(exc))
        return JSONResponse(
            status_code=500,
            content={"error": "Privacy check failed", "shield": "active"},
        )

    content_type = upstream.headers.get("Content-Type", "application/json")
    mime, charset = _parse_content_type(content_type)
    content_encoding = upstream.headers.get("Content-Encoding", "")
    try:
        declared_len = int(upstream.headers.get("Content-Length", "") or -1)
    except ValueError:
        declared_len = -1

    # Restore only when the body is textual, not transport-compressed, and
    # not over the size cap. Otherwise stream raw bytes (binary-safe).
    do_restore = (
        body_str is not None
        and _is_textual(mime)
        and not content_encoding
        and not (0 <= declared_len > RESTORE_MAX_BYTES)
    )

    overhead_ms = (time.time() - start_time) * 1000
    # On the restore path the body length changes, so Content-Length and
    # Content-Encoding must be dropped. On the passthrough path we forward
    # the raw (still-encoded) bytes verbatim, so those headers stay valid
    # and must be preserved or the client cannot decode the body.
    drop = _DROP_RESPONSE_HEADERS if do_restore else _HOP_BY_HOP
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in drop
    }
    response_headers.update(
        {
            "X-Privacy-Shield": "active",
            "X-PII-Scrubbed": str(metadata.get("pii_count", 0)),
            "X-Processing-Time-Ms": f"{overhead_ms:.2f}",
            "Content-Type": content_type,
        }
    )

    async def raw_chunks():
        """Yield undecoded upstream bytes, transport encoding preserved.

        ``aiter_raw()`` is correct for real upstreams and streaming mocks. A
        non-streaming/already-materialised response (e.g. some test mocks)
        raises ``StreamConsumed``; fall back to the buffered *raw* content
        so passthrough stays byte-for-byte and never auto-decompresses.
        """
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        except httpx.StreamConsumed:
            raw = getattr(upstream, "_raw_content", None)
            if raw is None:
                raw = upstream.read()
            if raw:
                yield raw

    async def body_iter():
        # One iterator for the whole response. httpx response streams are
        # single-consumption, so the oversized-text cutover must keep draining
        # *this same* generator (switching mode to raw passthrough and
        # re-emitting the chunk that crossed the cap) rather than calling
        # raw_chunks() a second time — re-iterating the httpx response would
        # drop the remainder of a large text body.
        chunks = raw_chunks()
        try:
            if do_restore:
                # do_restore is only true when the body is uncompressed text,
                # so raw bytes == decoded bytes here and stay byte-exact.
                restorer = StreamRestorer(shield.detector, charset)
                seen = 0
                async for chunk in chunks:
                    seen += len(chunk)
                    if seen > RESTORE_MAX_BYTES:
                        # Exceeded cap mid-stream: stop restoring, flush what
                        # we held, then pass the rest through untouched.
                        # Continue draining the SAME iterator — do NOT
                        # re-iterate the upstream response.
                        tail = restorer.finalize()
                        if tail:
                            yield tail.encode(charset, "replace")
                        yield chunk
                        async for rest in chunks:
                            yield rest
                        return
                    out = restorer.feed(chunk)
                    if out:
                        yield out.encode(charset, "replace")
                tail = restorer.finalize()
                if tail:
                    yield tail.encode(charset, "replace")
            else:
                # Transparent byte-for-byte passthrough (compressed/binary):
                # raw bytes preserve the original transport encoding.
                async for chunk in chunks:
                    yield chunk
        except httpx.TimeoutException:
            logger.warning("Privacy shield upstream timeout mid-stream")
        except Exception as exc:  # noqa: BLE001 - sanitized below
            logger.error("Privacy shield stream error: %s", _sanitize_error(exc))
        finally:
            await chunks.aclose()
            await cm.__aexit__(None, None, None)

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=content_type,
    )


@app.websocket("/{path:path}")
async def proxy_websocket(client_ws: WebSocket, path: str):
    """Transparent WebSocket passthrough for upstreams that upgrade.

    Frames are bridged verbatim in both directions (no scrub/restore): a
    WebSocket stream is not the JSON request/response the PII pipeline is
    built for, and tampering with frames would corrupt the protocol. The
    upstream WS client (``websockets``) is imported lazily so the HTTP path
    is unaffected when it is absent.

    The handshake is authenticated *before* ``accept()`` and before any
    upstream socket is opened: an unauthenticated client must never reach the
    backend model (which carries ``TARGET_API_KEY``). This mirrors the HTTP
    lane's ``Depends(verify_api_key)`` using the same SHIELD_API_KEY secret.
    """
    token, selected_subprotocol = _extract_ws_token(client_ws)
    if not _token_valid(token):
        # 1008 = policy violation. Closed pre-accept so no upstream socket is
        # opened and TARGET_API_KEY is never attached for an unauthed client.
        await client_ws.close(code=1008)
        return

    if selected_subprotocol is not None:
        # Token arrived via Sec-WebSocket-Protocol; the server must echo a
        # selected subprotocol or the browser handshake fails.
        await client_ws.accept(subprotocol=selected_subprotocol)
    else:
        await client_ws.accept()
    try:
        import websockets
    except ModuleNotFoundError:
        logger.error("WebSocket upgrade requested but 'websockets' is not installed")
        await client_ws.close(code=1011, reason="WebSocket passthrough unavailable")
        return

    base = TARGET_API_BASE.split("//", 1)[-1]
    scheme = "wss" if TARGET_API_BASE.startswith("https") else "ws"
    upstream_url = f"{scheme}://{base}/{path}"
    if client_ws.url.query:
        upstream_url += f"?{client_ws.url.query}"

    extra_headers = []
    if TARGET_API_KEY and TARGET_API_KEY != "not-needed":
        extra_headers.append(("Authorization", f"Bearer {TARGET_API_KEY}"))

    import anyio

    try:
        async with websockets.connect(
            upstream_url, additional_headers=extra_headers, open_timeout=5
        ) as upstream_ws:

            async def client_to_upstream():
                try:
                    while True:
                        msg = await client_ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if (data := msg.get("bytes")) is not None:
                            await upstream_ws.send(data)
                        elif (text := msg.get("text")) is not None:
                            await upstream_ws.send(text)
                finally:
                    await upstream_ws.close()

            async def upstream_to_client():
                try:
                    async for message in upstream_ws:
                        if isinstance(message, bytes):
                            await client_ws.send_bytes(message)
                        else:
                            await client_ws.send_text(message)
                finally:
                    await client_ws.close()

            async with anyio.create_task_group() as tg:
                tg.start_soon(client_to_upstream)
                tg.start_soon(upstream_to_client)
    except Exception as exc:  # noqa: BLE001 - sanitized
        logger.error("WebSocket passthrough error: %s", _sanitize_error(exc))
        try:
            await client_ws.close(code=1011)
        except RuntimeError:
            pass


if __name__ == "__main__":
    import uvicorn  # imported here so the app/tests don't require the server

    print(f"🔒 API Privacy Shield starting on port {PORT}")
    print(f"📡 Proxying to: {TARGET_API_BASE}")
    print(f"💾 Cache: {'enabled' if CACHE_ENABLED else 'disabled'} (size={CACHE_SIZE}, ttl={CACHE_TTL}s)")
    print(f"🧪 Test with: curl http://localhost:{PORT}/health")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
