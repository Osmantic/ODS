"""Authentication and signed browser-session endpoints.

``verify-session`` is consumed by reverse proxies through ``forward_auth``.
``admin-session`` exchanges the dashboard API key for a signed browser cookie.
``logout`` persists a per-cookie revocation before clearing that cookie.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import session_signer
from config import DATA_DIR
from security import verify_api_key
from session_revocations import RevocationStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

SESSION_COOKIE_NAME = "ods-session"
SESSION_TTL_SECONDS = 12 * 3600
_REVOCATIONS = RevocationStore(Path(DATA_DIR) / "auth" / "session-revocations.json")


def _cookie_domain() -> Optional[str]:
    """Return the shared cookie domain, or None for a host-only cookie."""
    raw = (os.environ.get("ODS_COOKIE_DOMAIN") or "").strip()
    return raw or None


def _validated_session(cookie_value: str) -> tuple[str, int]:
    """Return signed session claims, rejecting revoked cookies identically."""
    ok, reason = session_signer.verify(cookie_value)
    if ok:
        session_id, expiry_str, _signature = cookie_value.split(".")
        expiry = int(expiry_str)
        try:
            revoked = _REVOCATIONS.is_revoked(session_id)
        except (OSError, ValueError) as exc:
            logger.error("session revocation store unavailable: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Session verification is temporarily unavailable",
            ) from exc
        if revoked:
            ok, reason = False, "revoked"
    if not ok:
        logger.info("session denied: reason=%s", reason)
        # All credential failures intentionally share one response so callers
        # cannot distinguish expiry, revocation, or signature failures.
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return session_id, expiry


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    cookie_kwargs: dict = {
        "max_age": SESSION_TTL_SECONDS,
        "httponly": True,
        "samesite": "lax",
        "secure": request.url.scheme == "https",
        "path": "/",
    }
    cookie_domain = _cookie_domain()
    if cookie_domain:
        cookie_kwargs["domain"] = cookie_domain
    response.set_cookie(key=SESSION_COOKIE_NAME, value=token, **cookie_kwargs)


@router.get("/api/auth/verify-session")
def verify_session(request: Request) -> dict:
    """Validate the signed, non-revoked ``ods-session`` cookie."""
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    _session_id, expiry = _validated_session(cookie_value)
    return {"valid": True, "expires_at": expiry}


@router.post("/api/auth/logout")
def logout(response: Response, request: Request) -> dict:
    """Revoke the current signed session and expire its browser cookie."""
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    session_id, expiry = _validated_session(cookie_value)
    try:
        _REVOCATIONS.revoke(session_id, expiry)
    except (OSError, ValueError) as exc:
        logger.error("session revocation write failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Session revocation is temporarily unavailable",
        ) from exc

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        domain=_cookie_domain(),
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    logger.info("session revoked; expires_at=%d", expiry)
    return {"revoked": True, "expires_at": expiry}


@router.post("/api/auth/admin-session", dependencies=[Depends(verify_api_key)])
def admin_session(response: Response, request: Request) -> dict:
    """Mint a signed browser session for a caller holding the admin API key."""
    if not session_signer.is_configured():
        logger.error(
            "admin-session refused: ODS_SESSION_SECRET is not configured. "
            "Set it in .env (32+ random bytes) and restart dashboard-api."
        )
        raise HTTPException(
            status_code=503,
            detail="Session signing is not configured on this server.",
        )

    session_token = session_signer.issue(ttl_seconds=SESSION_TTL_SECONDS)
    _set_session_cookie(response, request, session_token)
    _session_id, expiry_str, _signature = session_token.split(".")
    expiry = int(expiry_str)

    logger.info("admin-session minted; expires_at=%d", expiry)
    return {"ok": True, "expires_at": expiry}
