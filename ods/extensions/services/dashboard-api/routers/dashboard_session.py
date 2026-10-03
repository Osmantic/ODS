"""Sign-in for dashboard access from another device.

The dashboard's nginx proxy adds the server-held ``DASHBOARD_API_KEY`` to its
admin API requests. That is only safe for a browser on the ODS machine itself,
so nginx trusts a request without a session only when it arrived on the
loopback-published listener, names a loopback Host and carries no proxy
forwarding headers. Every other request (LAN mode, ODS proxy, a reverse proxy
or Tailscale Serve in front of localhost, a DNS-rebinding page) must first
present the signed ``ods-dashboard-session`` cookie issued here.

Sign-in accepts a user-chosen password or a one-time recovery link minted by
``ods dashboard-login`` (``POST /api/auth/dashboard-session/link``, which needs
the key). Sessions are stateless and signed with a key derived from
``DASHBOARD_API_KEY`` and the password revision: replacing the password or
rotating that key signs every device out. Direct API-key login remains for
internal compatibility; the browser never asks users to copy the server key.

This cookie is deliberately separate from ``ods-session`` (magic links, ODS
Talk, the optional Hermes gate). It is host-only and ``SameSite=Strict``, so it
never travels to chat/hermes/talk subdomains, and an ``ods-session`` value can
never be replayed as a dashboard session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from typing import Annotated, Any

import dashboard_password
import security
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard-session"])

SESSION_COOKIE_NAME = "ods-dashboard-session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
LOGIN_LINK_TTL_SECONDS = 10 * 60
_FORMAT = "v1"
_MAX_CREDENTIAL_LENGTH = 512

# Failed sign-ins per client within the window before answering 429. The key
# and link tokens are 32 random bytes, so this only slows noisy clients.
_FAILURE_LIMIT = 10
_FAILURE_WINDOW_SECONDS = 300
_FAILURES: dict[str, list[float]] = {}
_LOGIN_LINKS: dict[str, float] = {}  # sha256(token) -> expiry; single process
_LOCK = threading.Lock()


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _signing_key() -> bytes:
    return hmac.new(
        security.DASHBOARD_API_KEY.encode("utf-8"),
        b"ods-dashboard-session/v1:" + dashboard_password.revision().encode("ascii"), hashlib.sha256
    ).digest()


def _sign(payload: str) -> str:
    return _b64u(hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest())


def issue_session(now: float | None = None) -> str:
    expiry = int(now if now is not None else time.time()) + SESSION_TTL_SECONDS
    payload = f"{_FORMAT}.{expiry}.{secrets.token_urlsafe(18)}"
    return f"{payload}.{_sign(payload)}"


def session_is_valid(value: str, now: float | None = None) -> bool:
    if not value or len(value) > 256:
        return False
    parts = value.split(".")
    if len(parts) != 4 or parts[0] != _FORMAT or not parts[1].isdigit() or not parts[2]:
        return False
    expected = _sign(".".join(parts[:3]))
    if not hmac.compare_digest(expected.encode("utf-8"), parts[3].encode("utf-8")):
        return False
    return int(parts[1]) > int(now if now is not None else time.time())


def _client_key(request: Request) -> str:
    # nginx sets X-Real-IP; direct callers fall back to the socket peer.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _recent_failures(client: str, now: float) -> list[float]:
    recent = [at for at in _FAILURES.get(client, []) if now - at < _FAILURE_WINDOW_SECONDS]
    if recent:
        _FAILURES[client] = recent
    else:
        _FAILURES.pop(client, None)
    return recent


def _consume_login_link(token: str, now: float) -> bool:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for stale in [key for key, expiry in _LOGIN_LINKS.items() if expiry <= now]:
        del _LOGIN_LINKS[stale]
    return _LOGIN_LINKS.pop(digest, 0) > now


def _credential(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict) or len(payload) != 1:
        raise HTTPException(status_code=422, detail="Send a password or a sign-in link token.")
    kind, value = next(iter(payload.items()))
    if kind not in ("password", "key", "token") or not isinstance(value, str) or not 0 < len(value) <= _MAX_CREDENTIAL_LENGTH:
        raise HTTPException(status_code=422, detail="Send a password or a sign-in link token.")
    return kind, value


@router.get("/api/auth/dashboard-session/verify")
def verify_dashboard_session(request: Request) -> Response:
    """nginx auth_request target: 204 for a valid dashboard session, else 401."""
    if session_is_valid(request.cookies.get(SESSION_COOKIE_NAME, "")):
        return Response(status_code=204)
    return Response(status_code=401)


@router.get("/api/auth/dashboard-session", dependencies=[Depends(verify_api_key)])
def dashboard_session_status(request: Request) -> dict:
    """Reached only once nginx allowed the request. ``session`` is False for
    the no-sign-in local browser, so the UI can hide Sign out there."""
    return {
        "signedIn": True,
        "session": session_is_valid(request.cookies.get(SESSION_COOKIE_NAME, "")),
        "passwordConfigured": dashboard_password.record() is not None,
    }


@router.post("/api/auth/dashboard-session/login")
def dashboard_login(request: Request, payload: Annotated[Any, Body()] = None) -> JSONResponse:
    kind, value = _credential(payload)
    client = _client_key(request)
    now = time.time()
    with _LOCK:
        if len(_recent_failures(client, now)) >= _FAILURE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait a few minutes and try again.")
        if kind == "password":
            ok = dashboard_password.verify(value)
        elif kind == "key":
            ok = hmac.compare_digest(value.encode("utf-8"), security.DASHBOARD_API_KEY.encode("utf-8"))
        else:
            ok = _consume_login_link(value, now)
        if not ok:
            _FAILURES.setdefault(client, []).append(now)
        if not ok:
            logger.info("dashboard sign-in rejected method=%s client=%s", kind, client)
            detail = ("That sign-in link has expired or was already used. Run `ods dashboard-login` for a new one."
                      if kind == "token" else "That password is not correct.")
            raise HTTPException(status_code=401, detail=detail)

        logger.info("dashboard sign-in accepted method=%s client=%s", kind, client)
        response = JSONResponse({"signedIn": True,
                                 "passwordSetup": kind == "token" or dashboard_password.record() is None},
                                headers={"Cache-Control": "no-store"})
        _set_session_cookie(response, request, now)
        return response


def _set_session_cookie(response: Response, request: Request, now: float) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=issue_session(now),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        # nginx overwrites this header with the effective transport scheme.
        # A direct caller claiming HTTPS can only request a stricter cookie.
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https",
        path="/",
    )


@router.post("/api/auth/dashboard-session/password", dependencies=[Depends(verify_api_key)])
def dashboard_set_password(request: Request, payload: Annotated[Any, Body()] = None) -> JSONResponse:
    """Owner-only setup/recovery behind nginx's admin gate (or local CLI key).

    A password change revokes all existing dashboard sessions and unused links.
    The current browser receives a fresh session after the atomic write.
    """
    if not isinstance(payload, dict) or set(payload) != {"password"}:
        raise HTTPException(422, "Send the new dashboard password.")
    with _LOCK:
        dashboard_password.save(payload["password"])
        _LOGIN_LINKS.clear()
        _FAILURES.clear()
        response = JSONResponse({"signedIn": True, "passwordConfigured": True},
                                headers={"Cache-Control": "no-store"})
        _set_session_cookie(response, request, time.time())
    return response


@router.post("/api/auth/dashboard-session/logout")
def dashboard_logout() -> JSONResponse:
    response = JSONResponse({"signedIn": False}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


@router.post("/api/auth/dashboard-session/link", dependencies=[Depends(verify_api_key)])
def dashboard_login_link(response: Response) -> dict:
    """One-time sign-in token for ``ods dashboard-login``. It travels in the
    URL fragment (``#ods-login=``), so it never reaches server or proxy logs."""
    response.headers["Cache-Control"] = "no-store"
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        for stale in [key for key, expiry in _LOGIN_LINKS.items() if expiry <= now]:
            del _LOGIN_LINKS[stale]
        _LOGIN_LINKS[hashlib.sha256(token.encode("utf-8")).hexdigest()] = now + LOGIN_LINK_TTL_SECONDS
    return {"token": token, "fragment": f"#ods-login={token}", "expiresIn": LOGIN_LINK_TTL_SECONDS}
