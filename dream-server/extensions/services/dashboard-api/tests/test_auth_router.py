"""Tests for routers/auth.py — /api/auth/verify-session.

The endpoint is consumed by Caddy reverse proxies via ``forward_auth``.
It validates the dream-session cookie via session_signer and returns
200/401 based on signature + expiry.
"""

import pytest

import session_signer


@pytest.fixture(autouse=True)
def _set_secret():
    """Install a known signing secret for each test."""
    session_signer._set_secret_for_tests("test-secret-for-verify-endpoint")
    yield
    session_signer._set_secret_for_tests("")


def test_no_cookie_returns_401(test_client):
    """Caddy forward_auth sends the request with no dream-session at all."""
    resp = test_client.get("/api/auth/verify-session")
    assert resp.status_code == 401


def test_empty_cookie_returns_401(test_client):
    """Empty string is malformed, not a valid signature."""
    test_client.cookies.set("dream-session", "")
    resp = test_client.get("/api/auth/verify-session")
    assert resp.status_code == 401
    test_client.cookies.clear()


def test_valid_cookie_returns_200(test_client):
    cookie = session_signer.issue(ttl_seconds=60)
    test_client.cookies.set("dream-session", cookie)
    try:
        resp = test_client.get("/api/auth/verify-session")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert isinstance(body["expires_at"], int)
        assert body["expires_at"] > 0
    finally:
        test_client.cookies.clear()


def test_tampered_signature_returns_401(test_client):
    cookie = session_signer.issue(ttl_seconds=60)
    random_id, expiry, _ = cookie.split(".")
    tampered = f"{random_id}.{expiry}.fakesignature"
    test_client.cookies.set("dream-session", tampered)
    try:
        resp = test_client.get("/api/auth/verify-session")
        assert resp.status_code == 401
    finally:
        test_client.cookies.clear()


def test_expired_cookie_returns_401(test_client):
    """Sign a cookie with a past expiry. Caddy will get a 401 and refuse
    to forward to the upstream."""
    import time
    random_id = "abc"
    past_expiry = int(time.time()) - 60
    payload = f"{random_id}.{past_expiry}"
    sig = session_signer._sign(payload)
    cookie = f"{payload}.{sig}"
    test_client.cookies.set("dream-session", cookie)
    try:
        resp = test_client.get("/api/auth/verify-session")
        assert resp.status_code == 401
    finally:
        test_client.cookies.clear()


def test_endpoint_does_not_require_dashboard_api_key(test_client):
    """The endpoint is reachable from any reverse proxy on the bridge
    network without the dashboard's Bearer API key — Caddy can't easily
    inject that header through forward_auth, and the cookie ITSELF is
    the credential being validated. Confirms no auth dependency was
    accidentally added."""
    cookie = session_signer.issue(ttl_seconds=60)
    test_client.cookies.set("dream-session", cookie)
    try:
        # No auth_headers — bare request, only the cookie.
        resp = test_client.get("/api/auth/verify-session")
        assert resp.status_code == 200
    finally:
        test_client.cookies.clear()


def test_error_response_is_the_same_regardless_of_reason(test_client):
    """The 401 response body must be identical for every failure mode —
    if an attacker can distinguish "bad signature" from "expired" from
    "malformed", they can probe to learn something useful (e.g. whether
    a specific cookie format is server-issued vs. random). Generic
    response shape across all failures defeats that.
    """
    import time

    # Pre-build cookies for each rejection reason.
    bad_sig = "abc.99999999999.tampered"
    past_expiry = int(time.time()) - 60
    payload = f"abc.{past_expiry}"
    sig = session_signer._sign(payload)
    expired_cookie = f"{payload}.{sig}"
    malformed = "only-one-piece"

    bodies = []
    for cookie_value in [bad_sig, expired_cookie, malformed, ""]:
        if cookie_value:
            test_client.cookies.set("dream-session", cookie_value)
        else:
            test_client.cookies.clear()
        resp = test_client.get("/api/auth/verify-session")
        assert resp.status_code == 401
        bodies.append(resp.json())
        test_client.cookies.clear()

    # All four 401 responses must be byte-identical so an attacker can't
    # tell which failure path they hit.
    assert all(b == bodies[0] for b in bodies), (
        f"401 bodies differ across rejection reasons: {bodies!r}"
    )
    # Response should also not contain any internal session_signer reason
    # strings like "bad-signature", "no-secret", "malformed" — those are
    # implementation details. (We use "Invalid or expired session" which
    # is intentionally vague.)
    body_str = str(bodies[0])
    for leak in ("bad-signature", "no-secret", "malformed"):
        assert leak not in body_str, f"reason leaked: {leak!r} in {body_str!r}"


# ---------------------------------------------------------------------------
# admin-session — install owner mints their own cookie via the admin API key.
# ---------------------------------------------------------------------------


class TestAdminSession:

    def test_requires_api_key(self, test_client):
        """No auth header → 401. The endpoint is admin-only."""
        resp = test_client.post("/api/auth/admin-session")
        assert resp.status_code == 401

    def test_with_api_key_returns_200_and_sets_cookie(self, test_client):
        resp = test_client.post(
            "/api/auth/admin-session",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["expires_at"] > 0
        set_cookies = [h for h in resp.headers.raw if h[0].lower() == b"set-cookie"]
        cookie_blob = b" ".join(c[1] for c in set_cookies).lower()
        assert b"dream-session=" in cookie_blob
        assert b"httponly" in cookie_blob
        assert b"samesite=lax" in cookie_blob

    def test_minted_cookie_verifies(self, test_client):
        """The cookie this endpoint mints must round-trip through
        verify-session — proving it's the same signed shape that
        forward_auth consumes from magic-link redemptions."""
        resp = test_client.post(
            "/api/auth/admin-session",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        cookie = resp.cookies.get("dream-session")
        assert cookie

        # Roundtrip the cookie through verify-session directly.
        ok, reason = session_signer.verify(cookie)
        assert ok is True, f"admin-session cookie did not verify: {reason}"

    def test_503_when_signing_unconfigured(self, test_client):
        """If DREAM_SESSION_SECRET is empty, the endpoint refuses to mint."""
        session_signer._set_secret_for_tests("")
        try:
            resp = test_client.post(
                "/api/auth/admin-session",
                headers=test_client.auth_headers,
            )
            assert resp.status_code == 503
            assert "not configured" in resp.json()["detail"].lower()
        finally:
            session_signer._set_secret_for_tests("test-secret-for-verify-endpoint")

    def test_respects_cookie_domain_env(self, test_client, monkeypatch):
        """DREAM_COOKIE_DOMAIN flows through to the Cookie's Domain attribute
        so subdomain SSO works the same as for magic-link cookies."""
        monkeypatch.setenv("DREAM_COOKIE_DOMAIN", "kitchen.local")
        resp = test_client.post(
            "/api/auth/admin-session",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        cookie_blob = b" ".join(
            v for k, v in resp.headers.raw if k.lower() == b"set-cookie"
        ).lower()
        assert b"domain=kitchen.local" in cookie_blob
