"""Auth-related dashboard-api endpoints.

Two endpoints today:

  * ``GET /api/auth/verify-session`` — validates the HMAC-signed
    ``dream-session`` cookie. Used by Caddy reverse proxies (e.g., the
    Hermes auth-proxy) via ``forward_auth`` to gate access on a valid
    session without each proxy needing to know the signing secret.

  * ``POST /api/auth/admin-session`` — mints a signed ``dream-session``
    cookie for the install owner (gated by ``DASHBOARD_API_KEY``). Lets
    the admin reach cookie-gated services (Hermes, etc.) without
    redeeming their own magic link. Without this, the install owner
    would be locked out of their own services until they minted +
    redeemed an invite to themselves, which is absurd UX.

Security:
  * ``verify-session`` is intentionally NOT gated by the API key — it's
    reachable from any reverse proxy on the bridge network. The cookie
    ITSELF is the credential.
  * ``admin-session`` IS gated by the API key. Only callers that already
    hold the admin secret (the dashboard, dream-cli, the host agent)
    can mint a session. The minted cookie is identical in shape to one
    issued by magic-link redemption; downstream consumers can't tell
    them apart.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import session_signer
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

SESSION_COOKIE_NAME = "dream-session"
# Same TTL the magic-link router uses for redeemed sessions; keeping
# them aligned means an admin and a guest share the same expiry model
# and the dashboard's "session expires in N hours" surface is uniform.
SESSION_TTL_SECONDS = 12 * 3600


def _cookie_domain() -> Optional[str]:
    """Cookie ``Domain`` attribute. Empty/None = host-only cookie.

    DREAM_COOKIE_DOMAIN is set by the installer to ``<DREAM_DEVICE_NAME>.local``
    when dream-proxy + mDNS are wired so a single redemption authenticates
    across chat.<name>.local, hermes.<name>.local, and the rest. With it
    empty, the cookie is host-only — functional for single-host setups
    but breaks subdomain SSO. Same logic as routers/magic_link.py;
    duplicated here intentionally so the two cookie-issuing paths stay
    coupled without one depending on the other.
    """
    raw = (os.environ.get("DREAM_COOKIE_DOMAIN") or "").strip()
    return raw or None


@router.get("/api/auth/verify-session")
def verify_session(request: Request) -> dict:
    """Validate the dream-session cookie. Returns 200 if valid, 401 if not.

    Caddy reverse proxies use this via ``forward_auth``:

        forward_auth dashboard-api:3002 {
            uri /api/auth/verify-session
            copy_headers Cookie
        }

    Caddy forwards the original request's Cookie header here; we read
    the dream-session cookie, hand it to session_signer.verify(), and
    return 200/401 based on the result. The proxy honors the status
    code: 2xx → forward the original request to the upstream; non-2xx
    → return the forward_auth response to the client.

    Response body on success is intentionally minimal — proxies just
    care about the status code. We do return the cookie's expiry so
    callers that ALSO want to read it (e.g., the dashboard UI showing
    "session expires in N minutes") can do so without re-implementing
    the parser.
    """
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    ok, reason = session_signer.verify(cookie_value)
    if not ok:
        logger.info("verify-session denied: reason=%s", reason)
        # We don't echo the reason back to the caller — that would help
        # an attacker probe (is it expired? bad signature? no secret?).
        # Caddy only needs the status code.
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # On success, return the expiry so the dashboard can surface "session
    # ends at X" without each consumer re-parsing the cookie. The format is
    # `<id>.<expiry>.<sig>`; we already validated the signature in verify().
    try:
        _, expiry_str, _ = cookie_value.split(".")
        expiry = int(expiry_str)
    except (ValueError, TypeError):
        # Validated above, but be defensive.
        expiry = 0

    return {"valid": True, "expires_at": expiry}


@router.post("/api/auth/admin-session", dependencies=[Depends(verify_api_key)])
def admin_session(response: Response, request: Request) -> dict:
    """Mint a signed ``dream-session`` cookie for the install owner.

    The install owner already holds ``DASHBOARD_API_KEY`` (the admin
    credential). Requiring them to ALSO redeem a magic link to access
    cookie-gated services (Hermes, future ones) is bad UX — they own
    the box. This endpoint trades the admin API key in for a signed
    ``dream-session`` cookie identical to one a magic-link redemption
    would issue.

    Used by:
      * The dashboard UI on load — when ``verify-session`` returns 401
        and the caller has the API key, the dashboard POSTs here to
        mint a session quietly. From the user's POV, the sidebar
        "Hermes" link just works.
      * The first-boot wizard — after Finish, before showing the
        success screen, so a fresh install lands with a session.
      * The host agent / dream-cli — when running setup flows that
        need to leave a cookie in a browser session for follow-up.

    The cookie is identical in shape to a magic-link-issued one:
    HMAC-SHA256 signed against ``DREAM_SESSION_SECRET``, ``HttpOnly``,
    ``SameSite=Lax``, ``Secure`` when reached over HTTPS, with the
    operator's ``DREAM_COOKIE_DOMAIN`` (if set) so it travels across
    proxy subdomains.

    Returns 503 if ``DREAM_SESSION_SECRET`` is not configured — same
    behaviour as the redemption path. Issue must fail loudly, not
    silently mint unverifiable cookies.
    """
    if not session_signer.is_configured():
        logger.error(
            "admin-session refused: DREAM_SESSION_SECRET is not configured. "
            "Set it in .env (32+ random bytes) and restart dashboard-api."
        )
        raise HTTPException(
            status_code=503,
            detail="Session signing is not configured on this server.",
        )

    session_token = session_signer.issue(ttl_seconds=SESSION_TTL_SECONDS)
    secure_cookie = request.url.scheme == "https"
    cookie_domain = _cookie_domain()

    cookie_kwargs: dict = dict(
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )
    if cookie_domain:
        cookie_kwargs["domain"] = cookie_domain

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        **cookie_kwargs,
    )

    # Pull the expiry out for the dashboard's "session ends at X" surface.
    # We know the format because we just minted it; defensive parse anyway.
    try:
        _, expiry_str, _ = session_token.split(".")
        expiry = int(expiry_str)
    except (ValueError, TypeError):
        expiry = 0

    logger.info("admin-session minted; expires_at=%d", expiry)
    return {"ok": True, "expires_at": expiry}
