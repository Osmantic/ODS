"""HMAC-signed, scope-aware session cookies for ODS's ods-session.

The legacy cookie value format is:

    <random-id>.<expiry-epoch>.<signature>

New cookies use a versioned, HMAC-covered scope:

    v2.<scope>.<random-id>.<expiry-epoch>.<signature>

Supported scopes are ``owner``, ``guest``, and ``admin``. Legacy cookies
continue to validate as scope ``legacy`` for compatibility, but neither
legacy nor API-key-minted admin cookies can authorize owner approval.

Where:
  * random-id is `secrets.token_urlsafe(24)` — opaque per-redemption ID
    (used for audit/logging; the signature is what gates validity)
  * expiry-epoch is the integer Unix timestamp the cookie should stop
    being honored (server-side expiry; the browser may keep the cookie
    longer but we reject it)
  * signature is `HMAC-SHA256(ODS_SESSION_SECRET, "<random-id>.<expiry>")`
    base64-url-encoded (no padding)

Why this shape:
  * Stateless validation — the verifier only needs ODS_SESSION_SECRET,
    no DB. This is why the Hermes auth-proxy can validate without a per-
    request session-store lookup.
  * Tamper-evident — a leaked cookie can't have its expiry extended;
    that would invalidate the signature.
  * Revocation is bounded by expiry — if a cookie leaks, the operator
    rotates ODS_SESSION_SECRET (which invalidates every issued cookie)
    or waits for natural expiry. Adding a per-cookie revocation list is
    a follow-up if needed; the cookie format reserves room (the random-
    id field is what a revocation list would key on).
  * No identity in the cookie — the random-id is opaque. The magic-link
    redemption records the target user separately (via the
    `ods-target-user` cookie or server-side audit log). Putting the
    username in the signed cookie would leak it via JavaScript on any
    same-origin page — keeping it out is more conservative.

Usage::

    from session_signer import issue, verify

    cookie_value = issue(ttl_seconds=12 * 3600)
    # → "abc123.1715000000.def456=="

    ok, reason = verify(cookie_value)
    # → (True, "ok") or (False, "expired"|"bad-signature"|"malformed")

The verifier uses :func:`hmac.compare_digest` for constant-time comparison
to defeat timing attacks on the signature byte.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger(__name__)

# Module-level secret. Read once at import; tests can override via the
# `_set_secret_for_tests` hook. Empty/missing secret = signing is
# DISABLED — issue() raises, verify() always returns (False, "no-secret").
# This prevents an unconfigured ODS from silently issuing
# unsignable cookies that look valid because they pass an empty-key
# HMAC check.
_SECRET: bytes = (os.environ.get("ODS_SESSION_SECRET", "")).encode("utf-8")
SCOPED_VERSION = "v2"
SCOPES = frozenset({"owner", "guest", "admin"})
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_COOKIE_CHARS = 1024


@dataclass(frozen=True)
class SessionClaims:
    """Verified server-side claims; never serialize ``session_id`` to clients."""

    scope: str
    session_id: str
    expires_at: int
    version: str


def is_configured() -> bool:
    """Return True iff ODS_SESSION_SECRET was provided.

    Callers use this as a pre-flight check before committing irreversible
    state (e.g., marking a single-use magic-link as redeemed) so the
    operation fails BEFORE the side effect lands, not after.
    """
    return bool(_SECRET)


def _set_secret_for_tests(value: str) -> None:
    """Test-only override. Module-level secret is read once at import
    time; tests need to inject a value AFTER module load."""
    global _SECRET
    _SECRET = value.encode("utf-8")


def _b64u(data: bytes) -> str:
    """URL-safe base64 with no padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    """Inverse of _b64u. Re-pads as needed; raises on invalid input."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: str) -> str:
    """HMAC-SHA256 of ``payload`` with ``_SECRET``. Returns base64-url."""
    mac = hmac.new(_SECRET, payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64u(mac)


def issue(ttl_seconds: int = 12 * 3600) -> str:
    """Mint a new signed cookie value valid for ``ttl_seconds`` seconds.

    Raises ``RuntimeError`` if ODS_SESSION_SECRET is not configured —
    we refuse to issue cookies that can't be verified.
    """
    if not _SECRET:
        raise RuntimeError(
            "ODS_SESSION_SECRET is not configured; refusing to issue an "
            "unsignable session cookie. Set it in .env (32+ random bytes) "
            "and restart dashboard-api."
        )
    if ttl_seconds < 1:
        raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")

    random_id = secrets.token_urlsafe(24)
    expiry = int(time.time()) + ttl_seconds
    payload = f"{random_id}.{expiry}"
    signature = _sign(payload)
    return f"{payload}.{signature}"


def issue_scoped(scope: str, ttl_seconds: int = 12 * 3600) -> str:
    """Mint a versioned cookie whose exact scope is covered by the HMAC."""
    if not _SECRET:
        raise RuntimeError(
            "ODS_SESSION_SECRET is not configured; refusing to issue an "
            "unsignable session cookie. Set it in .env (32+ random bytes) "
            "and restart dashboard-api."
        )
    if scope not in SCOPES:
        raise ValueError(f"unsupported session scope: {scope!r}")
    if ttl_seconds < 1:
        raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")

    random_id = secrets.token_urlsafe(24)
    expiry = int(time.time()) + ttl_seconds
    payload = f"{SCOPED_VERSION}.{scope}.{random_id}.{expiry}"
    return f"{payload}.{_sign(payload)}"


def verify(cookie_value: str) -> Tuple[bool, str]:
    """Validate a signed cookie. Returns (ok, reason).

    Reasons (when ok is False):
      * ``"no-secret"`` — ODS_SESSION_SECRET not configured server-side
      * ``"malformed"`` — cookie isn't 3 dot-separated pieces
      * ``"expired"`` — signature is valid but the expiry timestamp passed
      * ``"bad-signature"`` — payload/signature mismatch (tampered or
        signed with a different secret)

    Always returns (True, "ok") when validation succeeds; never raises.
    """
    ok, reason, _ = verify_scoped(cookie_value)
    return ok, reason


def verify_scoped(
    cookie_value: str,
) -> tuple[bool, str, SessionClaims | None]:
    """Validate either cookie format and return verified scope claims."""
    if not _SECRET:
        return False, "no-secret", None
    if (
        not cookie_value
        or not isinstance(cookie_value, str)
        or len(cookie_value) > MAX_COOKIE_CHARS
    ):
        return False, "malformed", None

    parts = cookie_value.split(".")
    if len(parts) == 3:
        random_id, expiry_str, claimed_sig = parts
        scope, version = "legacy", "v1"
        payload = f"{random_id}.{expiry_str}"
    elif len(parts) == 5:
        version, scope, random_id, expiry_str, claimed_sig = parts
        if version != SCOPED_VERSION or scope not in SCOPES:
            return False, "malformed", None
        payload = f"{version}.{scope}.{random_id}.{expiry_str}"
    else:
        return False, "malformed", None

    if (
        not random_id
        or not expiry_str
        or not claimed_sig
        or len(random_id) > 128
        or len(claimed_sig) > 128
        or (version == SCOPED_VERSION and not SESSION_ID_RE.fullmatch(random_id))
    ):
        return False, "malformed", None
    try:
        expiry = int(expiry_str)
    except (ValueError, TypeError):
        return False, "malformed", None
    if expiry < 0 or len(expiry_str) > 12:
        return False, "malformed", None

    expected_sig = _sign(payload)
    if not hmac.compare_digest(
        expected_sig.encode("utf-8"), claimed_sig.encode("utf-8")
    ):
        return False, "bad-signature", None
    if expiry <= int(time.time()):
        return False, "expired", None

    return True, "ok", SessionClaims(
        scope=scope,
        session_id=random_id,
        expires_at=expiry,
        version=version,
    )


def owner_approval_identity(cookie_value: str) -> str | None:
    """Return a non-secret audit identity only for a valid owner cookie."""
    ok, _, claims = verify_scoped(cookie_value)
    if not ok or claims is None or claims.scope != "owner":
        return None
    digest = hashlib.sha256(claims.session_id.encode("ascii")).hexdigest()[:16]
    return f"owner-{digest}"
