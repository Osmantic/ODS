"""Front door for a Windows-native install: dashboard-api takes nginx's place.

In Docker the dashboard container's nginx (``dashboard/nginx.conf``) serves the
built SPA and proxies ``/api/`` to dashboard-api, adding the server-held
``DASHBOARD_API_KEY`` only for a request it trusts. A native install
(``ODS_RUNTIME=native``) has no nginx, so this ASGI layer wraps the FastAPI app
and does the same work in-process:

- ``/api/`` requests pass the same sign-in gate. A request is trusted without
  a session only when its TCP peer is loopback, it names a loopback Host and
  carries no proxy forwarding header; anything else needs a valid
  ``ods-dashboard-session`` cookie or gets nginx's 401 sign-in answer. A
  trusted request gets ``Authorization: Bearer <key>`` added here; the key is
  never sent to the browser.
- Sign-in/out and ``/api/talk/`` are public and never receive the key.
- A request that brings its own ``Authorization`` is a direct API client, as
  on dashboard-api's own port in Docker; the app checks its key.
- Per-route body limits, X-Real-IP / X-Forwarded-* and the security headers
  are nginx's. ``tests/test_native_gateway_nginx_parity.py`` parses nginx.conf
  and fails when the two drift.
- Everything else is the built SPA (``ODS_DASHBOARD_DIST``) with nginx's
  ``try_files`` fallback and cache rules. Responses stream unbuffered: this
  layer forwards each ASGI message as the app sends it.

The app's own same-origin (CSRF) check still runs behind this layer.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from starlette.requests import ClientDisconnect, cookie_parser
from starlette.responses import FileResponse, Response

from native_runtime import unavailable_feature

# --- nginx.conf equivalents (kept in step by the parity test) ----------------

LOOPBACK_HOST_RE = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?")
# nginx distrusts any non-empty value of these; this layer distrusts their
# presence, as dashboard/dev-api-access.js does.
FORWARDING_HEADERS = frozenset({
    b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto",
    b"forwarded", b"x-real-ip", b"via",
})

CSP_PREVIEW_PORT_PLACEHOLDER = "__PIXEL_PREVIEW_PORT__"
SECURITY_HEADERS_TEMPLATE: tuple[tuple[str, str], ...] = (
    ("X-Frame-Options", "SAMEORIGIN"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-XSS-Protection", "1; mode=block"),
    ("Content-Security-Policy",
     "default-src 'self'; script-src 'self' https://unpkg.com https://cdn.jsdelivr.net; "
     "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
     "img-src 'self' data: blob: https://huggingface.co https://cdn-avatars.huggingface.co "
     "https://t3.gstatic.com/faviconV2; "
     "connect-src 'self' http://*.localhost:__PIXEL_PREVIEW_PORT__; media-src 'self' blob:; "
     "font-src 'self' https://cdn.jsdelivr.net; "
     "frame-src 'self' http://localhost:__PIXEL_PREVIEW_PORT__ http://127.0.0.1:__PIXEL_PREVIEW_PORT__ "
     "http://*.localhost:__PIXEL_PREVIEW_PORT__;"),
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains"),
)

SIGN_IN_REQUIRED_BODY = (
    '{"detail":"Sign in to the ODS dashboard to continue.","code":"dashboard_sign_in_required"}'
)

KIB, MIB = 1024, 1024 * 1024
DEFAULT_BODY_LIMIT = 1 * MIB  # nginx's client_max_body_size default

SIGN_IN_OUT_RE = re.compile(r"/api/auth/dashboard-session/(login|logout)")
TALK_PREFIX = "/api/talk/"
PIXEL_IMAGES_RE = re.compile(r"/api/pixel/images/[A-Za-z0-9_-]{1,128}(?:/img-[a-f0-9]{32})?")
BODY_LIMITS_EXACT = {
    "/api/pixel/chat/stream": 8 * MIB,
    "/api/pixel/access-mode": 2 * KIB,
    "/api/models/recovery": 2 * KIB,
    "/api/pixel/approval-terminal": 4 * KIB,
}
# Locations whose X-Forwarded-Proto keeps an HTTPS terminator's claim.
COOKIE_SCHEME_EXACT = frozenset({"/api/pixel/approval-terminal"})

STATIC_ASSET_RE = re.compile(r"\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$", re.IGNORECASE)
# Stable-name files nginx revalidates (``expires -1``) despite the asset rule.
STABLE_NAMES = frozenset({"/index.html", "/pixel-mascot.js", "/sw.js", "/theme-init.js", "/sw-register.js"})
CACHE_REVALIDATE = "no-cache"
CACHE_IMMUTABLE = "max-age=31536000, public, immutable"

# Explicit types: Windows' registry-backed mimetypes can serve .js as text/plain,
# which nosniff then blocks.
MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript", ".mjs": "application/javascript",
    ".css": "text/css", ".json": "application/json", ".map": "application/json",
    ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8", ".wasm": "application/wasm", ".mp4": "video/mp4",
    ".webm": "video/webm", ".wav": "audio/wav", ".mp3": "audio/mpeg",
}
BEARER_SAFE_RE = re.compile(r"[A-Za-z0-9._~+/=-]+")  # dashboard/entrypoint.sh's check


def security_headers(preview_port: int) -> tuple[tuple[str, str], ...]:
    if type(preview_port) is not int or not 1 <= preview_port <= 65535:
        raise ValueError("PIXEL_PREVIEW_PORT must be an integer from 1 to 65535")
    return tuple((name, value.replace(CSP_PREVIEW_PORT_PLACEHOLDER, str(preview_port)))
                 for name, value in SECURITY_HEADERS_TEMPLATE)


@dataclass(frozen=True)
class ApiRoute:
    public: bool          # no sign-in gate and never the server key
    body_limit: int
    cookie_scheme: bool   # X-Forwarded-Proto keeps a client's "https"


def api_route(path: str) -> ApiRoute:
    """nginx's location for an /api/ path, as this layer needs it."""
    if SIGN_IN_OUT_RE.fullmatch(path):
        return ApiRoute(public=True, body_limit=4 * KIB, cookie_scheme=True)
    if path.startswith(TALK_PREFIX):
        return ApiRoute(public=True, body_limit=26 * MIB, cookie_scheme=False)
    if path in BODY_LIMITS_EXACT:
        return ApiRoute(False, BODY_LIMITS_EXACT[path], path in COOKIE_SCHEME_EXACT)
    if PIXEL_IMAGES_RE.fullmatch(path):
        return ApiRoute(public=False, body_limit=8 * MIB, cookie_scheme=False)
    return ApiRoute(public=False, body_limit=DEFAULT_BODY_LIMIT, cookie_scheme=False)


# --- request classification (pure) ---------------------------------------------

def _is_loopback_peer(host: str | None) -> bool:
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _values(headers: list[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    return [value for key, value in headers if key == name]


def is_trusted_local(client: tuple | None, headers: list[tuple[bytes, bytes]]) -> bool:
    """nginx's ``$ods_trusted_local``: a browser on this machine, not proxied."""
    if not _is_loopback_peer(client[0] if client else None):
        return False
    hosts = _values(headers, b"host")
    if len(hosts) != 1 or not LOOPBACK_HOST_RE.fullmatch(hosts[0].decode("latin-1")):
        return False
    return not any(key in FORWARDING_HEADERS or key.startswith(b"x-forwarded-") for key, _ in headers)


def session_cookie(headers: list[tuple[bytes, bytes]], name: str) -> str:
    raw = "; ".join(value.decode("latin-1") for value in _values(headers, b"cookie"))
    return cookie_parser(raw).get(name, "") if raw else ""


def _proxy_headers(headers, client, scheme: str, cookie_scheme: bool, authorization: bytes | None):
    """Headers as nginx's proxy_set_header lines send them to dashboard-api."""
    peer = (client[0] if client else "").encode("latin-1")
    forwarded_for = b", ".join([*_values(headers, b"x-forwarded-for"), peer]) if peer else b""
    proto = scheme.encode("latin-1")
    if cookie_scheme and any(value.lower() == b"https" for value in _values(headers, b"x-forwarded-proto")):
        proto = b"https"
    replaced = {b"authorization", b"x-real-ip", b"x-forwarded-for", b"x-forwarded-proto"}
    result = [(key, value) for key, value in headers if key not in replaced]
    result += [(b"x-real-ip", peer), (b"x-forwarded-for", forwarded_for), (b"x-forwarded-proto", proto)]
    if authorization is not None:
        result.append((b"authorization", authorization))
    return result


def json_response(status: int, payload: dict | str, headers: dict | None = None) -> Response:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return Response(body, status_code=status, media_type="application/json", headers=headers)


def sign_in_required() -> Response:
    return json_response(401, SIGN_IN_REQUIRED_BODY,
                          {"X-ODS-Sign-In": "required", "Cache-Control": "no-store"})


def _unavailable(feature: str) -> Response:
    return json_response(404, {
        "detail": f"'{feature}' needs the Docker runtime; it is not part of this native install.",
        "code": "native_unavailable",
        "feature": feature,
    })


def _has_dot_segment(path: str) -> bool:
    return any(segment in (".", "..") for segment in path.split("/"))


# --- the ASGI layer --------------------------------------------------------------

class NativeGateway:
    """ASGI middleware: nginx's dashboard role in front of the FastAPI app."""

    def __init__(self, app, *, dist_dir: str | os.PathLike, api_key: str,
                 session_is_valid: Callable[[str], bool], session_cookie_name: str,
                 preview_port: int = 9437):
        self.app = app
        self._dist = Path(dist_dir).resolve(strict=True)
        self._index = self._dist / "index.html"
        if not self._index.is_file():
            raise FileNotFoundError(f"the dashboard build has no index.html: {self._index}")
        if not BEARER_SAFE_RE.fullmatch(api_key or ""):
            raise ValueError("DASHBOARD_API_KEY must be a bearer-token-safe value")
        self._authorization = f"Bearer {api_key}".encode("ascii")
        self._session_is_valid = session_is_valid
        self._session_cookie_name = session_cookie_name
        self._security = [(name.lower().encode("latin-1"), value.encode("latin-1"))
                          for name, value in security_headers(preview_port)]

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            # No dashboard route upgrades to a WebSocket.
            return await send({"type": "websocket.close", "code": 1008})
        send = self._with_security_headers(send)
        path = scope["path"]
        if _has_dot_segment(path):
            return await json_response(400, {"detail": "Bad request path."})(scope, receive, send)
        if path == "/health":
            return await self.app(scope, receive, send)  # the supervisor's liveness probe
        feature = unavailable_feature(scope["method"], path)
        if feature:
            return await _unavailable(feature)(scope, receive, send)
        if path.startswith("/api/"):
            return await self._api(scope, receive, send)
        return await self._static(scope, receive, send)

    def _with_security_headers(self, send):
        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {key.lower() for key, _ in headers}
                for name, value in self._security:
                    # nginx's add_header appends; a policy the app set stays
                    # and the CSP is added alongside it (both are enforced).
                    if name not in present or name == b"content-security-policy":
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)
        return wrapped

    async def _api(self, scope, receive, send):
        path, headers, client = scope["path"], list(scope["headers"]), scope.get("client")
        route = api_route(path)
        if route.public:
            proxied = _proxy_headers(headers, client, scope["scheme"], route.cookie_scheme, None)
            return await self._forward(scope, receive, send, proxied, route.body_limit)
        if any(value for value in _values(headers, b"authorization")):
            # A direct API client (CLI, supervisor) presents its own key.
            return await self.app(scope, receive, send)
        allowed = is_trusted_local(client, headers) or self._session_is_valid(
            session_cookie(headers, self._session_cookie_name))
        if not allowed:
            return await sign_in_required()(scope, receive, send)
        proxied = _proxy_headers(headers, client, scope["scheme"], route.cookie_scheme, self._authorization)
        return await self._forward(scope, receive, send, proxied, route.body_limit)

    async def _forward(self, scope, receive, send, headers, body_limit: int):
        lengths = _values(headers, b"content-length")
        if lengths:
            if len(set(lengths)) != 1 or not lengths[0].isdigit():
                return await json_response(400, {"detail": "Invalid Content-Length."})(scope, receive, send)
            if int(lengths[0]) > body_limit:
                return await _too_large()(scope, receive, send)
        state = {"started": False, "received": 0, "rejected": False}

        async def limited_receive():
            if state["rejected"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > body_limit:
                    state["rejected"] = True
                    if not state["started"]:
                        state["started"] = True
                        await _too_large()(scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        async def tracked_send(message):
            if state["rejected"]:
                return  # the 413 already answered this request
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app({**scope, "headers": headers}, limited_receive, tracked_send)
        except ClientDisconnect:
            if not state["rejected"]:
                raise
            # The app saw the cut-off body as a disconnect; the 413 is sent.

    async def _static(self, scope, receive, send):
        if scope["method"] not in ("GET", "HEAD"):
            return await Response(status_code=405, headers={"Allow": "GET, HEAD"})(scope, receive, send)
        path = scope["path"]
        file, cache = self._resolve(path)
        if file is None:
            return await Response("Not Found", status_code=404, media_type="text/plain")(scope, receive, send)
        media_type = MEDIA_TYPES.get(file.suffix.lower(), "application/octet-stream")
        response = FileResponse(file, media_type=media_type, headers={"Cache-Control": cache})
        return await response(scope, receive, send)

    def _resolve(self, path: str) -> tuple[Path | None, str]:
        """nginx's static locations: exact names, the asset rule, then try_files."""
        candidate = self._file(path)
        if path in STABLE_NAMES:
            return candidate, CACHE_REVALIDATE
        if STATIC_ASSET_RE.search(path):
            return candidate, CACHE_IMMUTABLE  # no SPA fallback for a missing asset
        if candidate is None:
            candidate = self._file(path.rstrip("/") + "/index.html")
        return candidate or self._index, CACHE_REVALIDATE

    def _file(self, path: str) -> Path | None:
        relative = path.lstrip("/")
        # Windows path syntax (drive letters, ADS, backslashes) is never a URL path.
        if not relative or any(char in relative for char in "\\:\x00"):
            return None
        resolved = (self._dist / relative).resolve()
        if os.path.commonpath([self._dist, resolved]) != str(self._dist) or not resolved.is_file():
            return None
        return resolved


def _too_large() -> Response:
    return json_response(413, {"detail": "Request body is too large."})
