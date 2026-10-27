"""Tests for native_front.DashboardGate and DashboardFiles (nginx-equivalent security boundary)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

import dashboard_password
import native_front
import security
from routers import dashboard_session as ds


@pytest.fixture
def dist_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    assets = d / "assets"
    assets.mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>ODS Desktop</title>", encoding="utf-8")
    (assets / "index-abc123.js").write_text("console.log('ods');", encoding="utf-8")
    return d


@pytest.fixture
def gated_app(dist_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-desktop-secret-key")
    monkeypatch.setattr(dashboard_password, "PASSWORD_FILE", tmp_path / "dashboard-password.json")
    ds._FAILURES.clear()
    ds._LOGIN_LINKS.clear()

    app = FastAPI()
    app.include_router(ds.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def api_status(api_key: str = Depends(security.verify_api_key)):
        return {"runtimeProfile": "windows-desktop", "authenticated": True}

    @app.post("/api/settings/test")
    async def api_settings_post(api_key: str = Depends(security.verify_api_key)):
        return {"saved": True}

    @app.get("/api/talk/status")
    async def api_talk_status(request: Request):
        return {"talk": "ok", "has_auth": "authorization" in request.headers}

    app.add_middleware(native_front.DashboardGate)
    app.mount("/", native_front.DashboardFiles(dist_dir), name="dashboard-static")
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    host: str = "127.0.0.1:3101",
    client: tuple[str, int] = ("127.0.0.1", 54321),
    server: tuple[str, int] = ("127.0.0.1", 3101),
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    raw_headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        raw_headers.append((b"host", host.encode("latin-1")))
    if headers:
        raw_headers.extend(headers)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path.split("?", 1)[0],
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "server": server,
    }

    status_code = 500
    resp_headers: dict[str, str] = {}
    body_chunks: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = message["status"]
            for k, v in message.get("headers", []):
                resp_headers[k.decode("latin-1").lower()] = v.decode("latin-1")
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return status_code, resp_headers, b"".join(body_chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("host_val", ["127.0.0.1:3101", "localhost:3101", "[::1]:3101"])
async def test_local_loopback_admitted_and_bearer_injected(gated_app: FastAPI, host_val: str):
    status, headers, body = await _request(gated_app, "GET", "/api/status", host=host_val)
    assert status == 200
    assert b"windows-desktop" in body
    assert "content-security-policy" in headers
    assert headers.get("x-frame-options") == "SAMEORIGIN"
    assert headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_kwargs",
    [
        {"host": "evil.example:3101"},  # DNS rebinding
        {"host": "evil.example"},       # DNS rebinding default port
        {"host": "localhost:9999"},     # Wrong listener port
        {"headers": [(b"x-forwarded-for", b"203.0.113.5")]},
        {"headers": [(b"forwarded", b"for=203.0.113.5")]},
        {"headers": [(b"via", b"1.1 proxy")]},
        {"headers": [(b"x-real-ip", b"203.0.113.5")]},
        {"headers": [(b"origin", b"https://evil.example")]},
        {"headers": [(b"origin", b"null")]},
        {"headers": [(b"sec-fetch-site", b"cross-site")]},
        {"headers": [(b"sec-fetch-site", b"same-site")]},
        {"headers": [(b"host", b"evil.example")]},  # Duplicate Host header
        {"client": ("192.168.1.50", 54321)},        # Non-loopback peer
        {"client": ("192.168.1.50", 54321), "headers": [(b"cookie", b"ods-dashboard-session=forged.cookie.val")]},
        {"client": ("192.168.1.50", 54321), "headers": [(b"authorization", b"Bearer test-desktop-secret-key")]},
    ],
)
async def test_untrusted_requests_refused_with_401_sign_in(gated_app: FastAPI, case_kwargs: dict):
    status, headers, body = await _request(gated_app, "GET", "/api/status", **case_kwargs)
    assert status == 401
    assert headers.get("x-ods-sign-in") == "required"
    assert b"dashboard_auth_required" in body
    assert "content-security-policy" in headers


@pytest.mark.asyncio
async def test_valid_session_cookie_admits_network_peer(gated_app: FastAPI):
    valid_cookie = ds.issue_session()
    status, headers, body = await _request(
        gated_app,
        "GET",
        "/api/status",
        host="192.168.1.10:3101",
        client=("192.168.1.50", 54321),
        headers=[(b"cookie", f"{ds.SESSION_COOKIE_NAME}={valid_cookie}".encode("ascii"))],
    )
    assert status == 200
    assert b"windows-desktop" in body


@pytest.mark.asyncio
async def test_static_files_spa_fallback_and_unknown_api_404(gated_app: FastAPI):
    # 1. Root serves index.html with no-cache
    status, headers, body = await _request(gated_app, "GET", "/")
    assert status == 200
    assert b"ODS Desktop" in body
    assert headers.get("cache-control") == "no-cache"

    # 2. SPA client route /settings serves index.html
    status, headers, body = await _request(gated_app, "GET", "/settings")
    assert status == 200
    assert b"ODS Desktop" in body

    # 3. Hashed asset under /assets/ gets immutable 1-year cache
    status, headers, body = await _request(gated_app, "GET", "/assets/index-abc123.js")
    assert status == 200
    assert b"console.log" in body
    assert headers.get("cache-control") == "public, max-age=31536000, immutable"

    # 4. Unknown /api/* path returns JSON 404, never index.html
    status, headers, body = await _request(gated_app, "GET", "/api/nonexistent-endpoint")
    assert status == 404
    assert b"ODS Desktop" not in body
    assert headers.get("content-type") == "application/json"

    # 5. /pixel-preview returns 404
    status, _, _ = await _request(gated_app, "GET", "/pixel-preview/index.html")
    assert status == 404

    # 6. Path traversal attempts return 404
    for traversal in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        status, _, _ = await _request(gated_app, "GET", traversal)
        assert status == 404, traversal
