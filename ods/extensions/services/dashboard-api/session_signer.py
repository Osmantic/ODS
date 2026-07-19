"""HMAC-signed session cookies for ODS's ods-session.

The cookie value format is:

    <random-id>.<expiry-epoch>.<signature>

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
import secrets
import time
from typing import Tuple

logger = logging.getLogger(__name__)

# Module-level secret. Read once at import; tests can override via the
# `_set_secret_for_tests` hook. Empty/missing secret = signing is
# DISABLED — issue() raises, verify() always returns (False, "no-secret").
# This prevents an unconfigured ODS from silently issuing
# unsignable cookies that look valid because they pass an empty-key
# HMAC check.
_SECRET: bytes = (os.environ.get("ODS_SESSION_SECRET", "")).encode("utf-8")


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
    if not _SECRET:
        return False, "no-secret"
    if not cookie_value or not isinstance(cookie_value, str):
        return False, "malformed"

    parts = cookie_value.split(".")
    if len(parts) != 3:
        return False, "malformed"

    random_id, expiry_str, claimed_sig = parts
    if not random_id or not expiry_str or not claimed_sig:
        return False, "malformed"

    payload = f"{random_id}.{expiry_str}"
    expected_sig = _sign(payload)
    # Constant-time compare to defeat signature timing oracles. Encoded to
    # UTF-8 first because compare_digest raises TypeError on non-ASCII str,
    # and claimed_sig comes straight off an attacker-controlled cookie —
    # verify() must return a reason, never raise.
    if not hmac.compare_digest(expected_sig.encode("utf-8"), claimed_sig.encode("utf-8")):
        return False, "bad-signature"

    # Signature is good — check expiry.
    try:
        expiry = int(expiry_str)
    except (ValueError, TypeError):
        return False, "malformed"

    # `<=` because the expiry timestamp is the moment of invalidation, not
    # the last valid second. A cookie issued with ttl=60 stops being valid
    # AT t+60, not after t+61.
    if expiry <= int(time.time()):
        return False, "expired"

    return True, "ok"
