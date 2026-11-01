"""Chat for a Windows-native install: llama-server's web UI behind the dashboard gate.

A native install runs llama-server with ``--api-key-file``, so every API route
needs the key, and users must never type it. This ASGI app is a reverse proxy
for that llama-server that adds the key server-side. The supervisor runs it as
a second loopback listener next to dashboard-api::

    python -m uvicorn native_chat:app --host 127.0.0.1 --port <ODS_NATIVE_CHAT_PORT>

Why its own port and not a ``/chat/`` path on the dashboard: the llama.cpp web
UI renders model output and needs inline scripts the dashboard CSP forbids. On
its own origin it cannot read dashboard API responses or drive them (the
dashboard's CORS allowlist and same-origin CSRF check apply to it like to any
other site), so a flaw in that UI does not become dashboard-admin access. The
UI uses relative URLs (``./v1/chat/completions``, ``./props``, ``./slots``,
``./v1/models``), so it is proxied at the root unchanged.

Every request passes the dashboard's gate (``native_gateway``): trusted only
from a loopback peer naming a loopback Host with no forwarding headers, else a
valid ``ods-dashboard-session`` cookie (host-only cookies are shared across
ports of the same host). The same-origin rule applies to every method, and
llama-server's reflected CORS headers are dropped, so another site can neither
run nor read a chat. Responses stream as llama-server sends them.
"""

from __future__ import annotations

import logging
from typing import Callable

import httpx

from native_gateway import is_trusted_local, json_response, session_cookie, sign_in_required
from same_origin import is_cross_site_state_change, origin_matches_host

logger = logging.getLogger(__name__)

MAX_REQUEST_BYTES = 32 * 1024 * 1024  # chat with attached images, base64-encoded
# Request headers the web UI needs; everything else (cookies, the caller's
# Authorization, forwarding headers) stays here.
FORWARDED_REQUEST_HEADERS = frozenset({
    b"accept", b"accept-encoding", b"accept-language", b"cache-control", b"content-type",
    b"if-modified-since", b"if-none-match", b"pragma", b"range", b"user-agent",
})
DROPPED_RESPONSE_HEADERS = frozenset({
    "connection", "keep-alive", "transfer-encoding", "set-cookie", "www-authenticate",
})
# Clickjacking and sniffing protection; the UI's own inline scripts need no CSP here.
CHAT_SECURITY_HEADERS = ((b"x-frame-options", b"SAMEORIGIN"), (b"x-content-type-options", b"nosniff"))
# llama-server's CORS proxy for MCP would let a page fetch arbitrary URLs.
BLOCKED_PATHS = ("/cors-proxy",)


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    values = [value for key, value in headers if key == name]
    return values[0].decode("latin-1") if len(values) == 1 else None


def is_cross_site(method: str, headers: list[tuple[bytes, bytes]]) -> bool:
    """Same-origin rule for every method: llama-server would answer any page."""
    origin, host = _header(headers, b"origin"), _header(headers, b"host")
    site, mode = _header(headers, b"sec-fetch-site"), _header(headers, b"sec-fetch-mode")
    if is_cross_site_state_change(method, origin, host, site):
        return True
    if origin is not None and not origin_matches_host(origin, host):
        return True
    # A cross-site page may still navigate here (harmless); it may not load
    # chat data as a script, image or no-cors fetch.
    return site == "cross-site" and mode != "navigate"


class NativeChatProxy:
    def __init__(self, *, upstream: str, api_key: str, session_is_valid: Callable[[str], bool],
                 session_cookie_name: str, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ValueError("the native chat proxy needs the llama-server API key")
        self._upstream = upstream.rstrip("/")
        self._authorization = f"Bearer {api_key}"
        self._session_is_valid = session_is_valid
        self._session_cookie_name = session_cookie_name
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport, trust_env=False, follow_redirects=False,
                # Generation can run for minutes between tokens on CPU.
                timeout=httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0),
            )
        return self._client

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._lifespan(receive, send)
        if scope["type"] != "http":
            return await send({"type": "websocket.close", "code": 1008})
        headers = list(scope["headers"])
        if not (is_trusted_local(scope.get("client"), headers) or self._session_is_valid(
                session_cookie(headers, self._session_cookie_name))):
            return await sign_in_required()(scope, receive, send)
        if is_cross_site(scope["method"], headers):
            return await json_response(403, {"detail": "Cross-origin chat request rejected."})(scope, receive, send)
        if scope["path"].startswith(BLOCKED_PATHS):
            return await json_response(404, {"detail": "Not available."})(scope, receive, send)
        body = await _read_body(receive)
        if body is None:
            return await json_response(413, {"detail": "Request body is too large."})(scope, receive, send)
        await self._proxy(scope, headers, body, send)

    async def _proxy(self, scope, headers, body: bytes, send) -> None:
        outgoing = [(key.decode("latin-1"), value.decode("latin-1"))
                    for key, value in headers if key in FORWARDED_REQUEST_HEADERS]
        outgoing.append(("authorization", self._authorization))
        url = self._upstream + scope["path"]
        if scope.get("query_string"):
            url += "?" + scope["query_string"].decode("latin-1")
        request = self._http().build_request(scope["method"], url, headers=outgoing, content=body)
        try:
            response = await self._http().send(request, stream=True)
        except httpx.TransportError as exc:
            logger.warning("llama-server unreachable for chat: %s", type(exc).__name__)
            return await json_response(502, {"detail": "llama-server is not reachable."})(scope, None, send)
        try:
            await send({"type": "http.response.start", "status": response.status_code,
                        "headers": _response_headers(response)})
            # Raw bytes: a gzip body stays gzip with its Content-Encoding.
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await response.aclose()

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._client is not None:
                    await self._client.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return


def _response_headers(response: httpx.Response) -> list[tuple[bytes, bytes]]:
    kept = [(key.encode("latin-1"), value.encode("latin-1"))
            for key, value in response.headers.multi_items()
            if key.lower() not in DROPPED_RESPONSE_HEADERS and not key.lower().startswith("access-control-")]
    names = {key for key, _ in kept}
    return kept + [(name, value) for name, value in CHAT_SECURITY_HEADERS if name not in names]


async def _read_body(receive) -> bytes | None:
    """The whole request body, or None past MAX_REQUEST_BYTES."""
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _build_app() -> NativeChatProxy:
    from config import SERVICES
    from native_runtime import NATIVE, llama_server_api_key
    from routers import dashboard_session

    if not NATIVE:
        raise RuntimeError("native_chat runs only with ODS_RUNTIME=native")
    llama = SERVICES["llama-server"]
    return NativeChatProxy(
        upstream=f"http://{llama['host']}:{llama['port']}",
        api_key=llama_server_api_key(),
        session_is_valid=dashboard_session.session_is_valid,
        session_cookie_name=dashboard_session.SESSION_COOKIE_NAME,
    )


def __getattr__(name: str):
    # ``native_chat:app`` is built on first access, so tests can import the
    # module (and NativeChatProxy) without a native environment.
    if name == "app":
        globals()["app"] = _build_app()
        return globals()["app"]
    raise AttributeError(name)
