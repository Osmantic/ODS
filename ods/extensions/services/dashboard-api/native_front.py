"""Native dashboard front-end gate and static file server for Windows Desktop.

Replaces the Docker nginx container in the ``windows-desktop`` runtime profile:
- Enforces the strict local-loopback / session-cookie boundary before injecting
  the server-held ``DASHBOARD_API_KEY`` on admin API routes.
- Defends against DNS rebinding, cross-site requests (CSRF / Sec-Fetch-Site /
  Origin), and spoofed proxy-forwarding headers.
- Serves the built Vite dashboard ``dist/`` bundle with SPA client-route
  fallback, asset caching, and nginx-equivalent security headers.
"""

from __future__ import annotations

import json
import mimetypes
import os
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import security
from routers import dashboard_session

_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "[::1]"})
_FORBIDDEN_PROXY_HEADERS = frozenset({"forwarded", "via", "x-real-ip"})
_SINGLE_VALUE_HEADERS = frozenset({"host", "origin", "sec-fetch-site"})
_ALLOWED_SEC_FETCH_SITE = frozenset({"same-origin", "none"})
_PROTECTED_ROOT_PATHS = frozenset({
    "/status",
    "/gpu",
    "/services",
    "/disk",
    "/model",
    "/bootstrap",
})
_UNAUTHENTICATED_SESSION_PATHS = frozenset({
    "/api/auth/dashboard-session/login",
    "/api/auth/dashboard-session/logout",
    "/api/auth/dashboard-session/verify",
})

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "media-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'"
)

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP_POLICY.encode("ascii")),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


def resolve_dashboard_dist(environment: dict[str, str] | None = None) -> Path | None:
    """Resolve and validate ``ODS_DASHBOARD_DIST`` if configured."""
    env = environment if environment is not None else os.environ
    raw = str(env.get("ODS_DASHBOARD_DIST", "") or "").strip()
    if not raw:
        return None
    dist_path = Path(raw)
    if not dist_path.is_absolute():
        raise RuntimeError("ODS_DASHBOARD_DIST must be an absolute path when set")
    if not dist_path.is_dir() or not (dist_path / "index.html").is_file():
        raise RuntimeError(
            f"ODS_DASHBOARD_DIST must point to a built dashboard directory containing index.html: {dist_path}"
        )
    return dist_path.resolve()


def _decode_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for k_bytes, v_bytes in raw_headers:
        key = k_bytes.decode("latin-1").strip().lower()
        val = v_bytes.decode("latin-1").strip()
        grouped.setdefault(key, []).append(val)
    return grouped


def _expected_host_headers(listener_port: int | None) -> set[str]:
    if listener_port is None or listener_port in (80, 443):
        allowed = set(_LOOPBACK_HOSTNAMES)
        if listener_port:
            allowed.update({f"{h}:{listener_port}" for h in _LOOPBACK_HOSTNAMES})
        return allowed
    return {f"{h}:{listener_port}" for h in _LOOPBACK_HOSTNAMES}


def is_trusted_local_request(scope: dict[str, Any]) -> bool:
    """Return True only when the request arrived directly from a local loopback browser/client."""
    client = scope.get("client")
    if not client or not isinstance(client, (tuple, list)) or len(client) < 1:
        return False
    peer_ip = str(client[0] or "").strip().lower()
    if peer_ip not in _LOOPBACK_PEERS:
        return False

    server = scope.get("server")
    listener_port = int(server[1]) if (server and len(server) >= 2 and server[1]) else None

    headers = _decode_headers(list(scope.get("headers") or []))

    # Reject duplicate security-critical headers or comma-joined multiple values
    for single_key in _SINGLE_VALUE_HEADERS:
        values = headers.get(single_key, [])
        if len(values) > 1:
            return False
        if len(values) == 1 and "," in values[0]:
            return False

    # Reject any proxy/forwarding headers (e.g., reverse proxy or tunnel in front of localhost)
    for key in headers:
        if key in _FORBIDDEN_PROXY_HEADERS or key.startswith("x-forwarded-"):
            return False

    host_values = headers.get("host", [])
    if len(host_values) != 1:
        return False
    host_header = host_values[0].lower()
    if host_header not in _expected_host_headers(listener_port):
        return False

    # Validate Origin if present
    origin_values = headers.get("origin", [])
    if origin_values:
        origin = origin_values[0].lower()
        if origin == "null":
            return False
        try:
            parsed_origin = urlparse(origin)
        except ValueError:
            return False
        if parsed_origin.scheme not in ("http", "https") or parsed_origin.netloc.lower() != host_header:
            return False

    # Validate Sec-Fetch-Site if present
    sfs_values = headers.get("sec-fetch-site", [])
    if sfs_values:
        if sfs_values[0].lower() not in _ALLOWED_SEC_FETCH_SITE:
            return False

    return True


def _has_valid_dashboard_session(scope: dict[str, Any]) -> bool:
    """Check whether the request carries a valid signed ``ods-dashboard-session`` cookie."""
    headers = _decode_headers(list(scope.get("headers") or []))
    # Even with a valid session cookie, reject cross-site Sec-Fetch-Site or mismatched Origin
    for single_key in _SINGLE_VALUE_HEADERS:
        values = headers.get(single_key, [])
        if len(values) > 1 or (len(values) == 1 and "," in values[0]):
            return False

    sfs_values = headers.get("sec-fetch-site", [])
    if sfs_values and sfs_values[0].lower() not in _ALLOWED_SEC_FETCH_SITE:
        return False

    host_values = headers.get("host", [])
    origin_values = headers.get("origin", [])
    if origin_values:
        if not host_values:
            return False
        origin = origin_values[0].lower()
        if origin == "null":
            return False
        try:
            parsed_origin = urlparse(origin)
        except ValueError:
            return False
        if parsed_origin.scheme not in ("http", "https") or parsed_origin.netloc.lower() != host_values[0].lower():
            return False

    for raw_cookie in headers.get("cookie", []):
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            continue
        morsel = cookie.get(dashboard_session.SESSION_COOKIE_NAME)
        if morsel and dashboard_session.session_is_valid(morsel.value):
            return True
    return False


def _is_protected_api_path(path: str) -> bool:
    if path in _PROTECTED_ROOT_PATHS:
        return True
    if path == "/api" or path.startswith("/api/"):
        if path in _UNAUTHENTICATED_SESSION_PATHS:
            return False
        if path == "/api/talk" or path.startswith("/api/talk/"):
            return False
        return True
    return False


async def _send_json_response(
    send: Callable,
    status: int,
    payload: dict[str, Any],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        *SECURITY_HEADERS,
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class DashboardGate:
    """ASGI middleware implementing the nginx dashboard security gate natively."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "/")

        # 1. Strip any client-supplied Authorization header so a browser page can never spoof it
        cleaned_headers = [
            (k, v)
            for k, v in (scope.get("headers") or [])
            if k.decode("latin-1").strip().lower() != "authorization"
        ]

        # 2. Wrap send so every response carries nginx's security headers
        async def _send_with_security_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                existing = list(message.get("headers") or [])
                existing_names = {k.decode("latin-1").lower() for k, _ in existing}
                for sec_k, sec_v in SECURITY_HEADERS:
                    if sec_k.decode("latin-1") not in existing_names:
                        existing.append((sec_k, sec_v))
                message = {**message, "headers": existing}
            await send(message)

        # 3. Block /pixel-preview in Windows Desktop profile
        if path == "/pixel-preview" or path.startswith("/pixel-preview/"):
            await _send_json_response(
                send,
                404,
                {"detail": "Not Found"},
                extra_headers=[(b"cache-control", b"no-store")],
            )
            return

        # 4. Enforce gate and inject server-side Bearer key on protected API routes
        if _is_protected_api_path(path):
            if is_trusted_local_request(scope) or _has_valid_dashboard_session(scope):
                api_key = security.DASHBOARD_API_KEY
                cleaned_headers.append(
                    (b"authorization", f"Bearer {api_key}".encode("utf-8"))
                )
            else:
                await _send_json_response(
                    send,
                    401,
                    {
                        "detail": "Dashboard sign-in required.",
                        "code": "dashboard_auth_required",
                    },
                    extra_headers=[
                        (b"x-ods-sign-in", b"required"),
                        (b"cache-control", b"no-store"),
                    ],
                )
                return

        updated_scope = {**scope, "headers": cleaned_headers}
        await self.app(updated_scope, receive, _send_with_security_headers)


class DashboardFiles:
    """ASGI static file and SPA fallback handler for the built dashboard dist/ folder."""

    def __init__(self, dist_dir: Path) -> None:
        self.dist_dir = dist_dir.resolve()
        self.index_file = self.dist_dir / "index.html"

    def _resolve_static_file(self, raw_path: str) -> Path | None:
        decoded = unquote(raw_path)
        if "\x00" in decoded or "\\" in decoded:
            return None
        parts = [p for p in decoded.split("/") if p]
        if any(p in (".", "..") for p in parts):
            return None
        if not parts:
            return self.index_file if self.index_file.is_file() else None
        candidate = (self.dist_dir.joinpath(*parts)).resolve()
        if not candidate.is_relative_to(self.dist_dir):
            return None
        if candidate.is_file():
            return candidate
        return None

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            return

        path = str(scope.get("path") or "/")
        raw_path = scope.get("raw_path")
        raw_str = raw_path.decode("latin-1") if isinstance(raw_path, (bytes, bytearray)) else path

        # Unknown /api/* paths must return JSON 404, never index.html
        if path == "/api" or path.startswith("/api/"):
            await _send_json_response(
                send,
                404,
                {"detail": "Not Found"},
                extra_headers=[(b"cache-control", b"no-store")],
            )
            return

        # Reject path traversal attempts in raw_path before normalising
        unquoted_raw = unquote(raw_str)
        if ".." in unquoted_raw.split("/") or "\\" in unquoted_raw or "\x00" in unquoted_raw:
            await _send_json_response(
                send,
                404,
                {"detail": "Not Found"},
                extra_headers=[(b"cache-control", b"no-store")],
            )
            return

        method = str(scope.get("method") or "GET").upper()
        if method not in ("GET", "HEAD"):
            await _send_json_response(
                send,
                405,
                {"detail": "Method Not Allowed"},
                extra_headers=[(b"cache-control", b"no-store")],
            )
            return

        target_file = self._resolve_static_file(path)
        is_immutable_asset = False
        if target_file is not None:
            rel_parts = target_file.relative_to(self.dist_dir).parts
            is_immutable_asset = len(rel_parts) >= 2 and rel_parts[0] == "assets"
        else:
            # If the request looks like a missing asset under /assets/, return 404 instead of SPA index.html
            if path.startswith("/assets/"):
                await _send_json_response(
                    send,
                    404,
                    {"detail": "Asset Not Found"},
                    extra_headers=[(b"cache-control", b"no-store")],
                )
                return
            target_file = self.index_file if self.index_file.is_file() else None

        if target_file is None:
            await _send_json_response(
                send,
                404,
                {"detail": "Not Found"},
                extra_headers=[(b"cache-control", b"no-store")],
            )
            return

        content = target_file.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(target_file))
        if not mime_type:
            mime_type = "application/octet-stream"
        if mime_type.startswith("text/") or mime_type in ("application/javascript", "application/json"):
            content_type = f"{mime_type}; charset=utf-8".encode("ascii")
        else:
            content_type = mime_type.encode("ascii")

        cache_control = (
            b"public, max-age=31536000, immutable"
            if is_immutable_asset
            else b"no-cache"
        )
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", content_type),
            (b"content-length", str(len(content)).encode("ascii")),
            (b"cache-control", cache_control),
            *SECURITY_HEADERS,
        ]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({
            "type": "http.response.body",
            "body": b"" if method == "HEAD" else content,
        })
