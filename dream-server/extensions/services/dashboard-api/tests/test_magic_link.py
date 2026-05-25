"""Tests for routers/magic_link.py — magic-link auth (generate / redeem / list / revoke).

Covers:
  * Auth enforcement on admin endpoints
  * Generate happy path (returns plaintext token + URL)
  * Generate validation (bad username, expiry bounds, scoped redirect targets)
  * Redeem happy path (single-use, sets session cookie, 302 to chat)
  * Redeem failure modes (invalid, expired, already-redeemed, revoked) → all
    return the same opaque 404
  * Reusable-token semantics (can redeem twice, still tracked in audit)
  * Rate-limit on repeated failures
  * List + revoke flows
"""

import importlib
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Fixtures — isolate per-test storage + rate-limit state
# ---------------------------------------------------------------------------


@pytest.fixture()
def magic_link_module(tmp_path, monkeypatch):
    """Reload routers.magic_link with an isolated DATA_DIR and clean state."""
    monkeypatch.setenv("DREAM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DREAM_SESSION_SECRET", "test-secret-for-magic-link-tests")
    monkeypatch.delenv("DREAM_PUBLIC_URL", raising=False)
    monkeypatch.delenv("WEBUI_URL", raising=False)
    monkeypatch.delenv("DREAM_TRUST_FORWARDED", raising=False)
    monkeypatch.delenv("DREAM_COOKIE_DOMAIN", raising=False)

    # session_signer reads DREAM_SESSION_SECRET at import time, so any
    # already-loaded copy keeps the old value. Force-set it for the test.
    import session_signer
    session_signer._set_secret_for_tests("test-secret-for-magic-link-tests")

    # Reimport so module-level constants pick up the new DATA_DIR.
    from routers import magic_link as ml
    importlib.reload(ml)

    # Reset in-memory rate-limit table between tests.
    ml._RATE_LIMIT_BUCKETS.clear()
    monkeypatch.setattr(ml, "_dream_proxy_lan_ready", lambda: (True, ""))

    # The main app already imported the router at module load — re-include the
    # reloaded one so the TestClient routes to fresh module state.
    from main import app
    # FastAPI's APIRouter is by-reference; reloading the module replaces
    # ml.router with a new instance. Re-mount it.
    app.include_router(ml.router)
    return ml


@pytest.fixture()
def magic_link_client(test_client, magic_link_module):
    """TestClient wired to the freshly-reloaded magic_link router."""
    return test_client


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_generate_requires_auth(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
    )
    assert resp.status_code == 401


def test_list_requires_auth(magic_link_client):
    resp = magic_link_client.get("/api/auth/magic-link/list")
    assert resp.status_code == 401


def test_revoke_requires_auth(magic_link_client):
    resp = magic_link_client.delete("/api/auth/magic-link/abcd1234")
    assert resp.status_code == 401


def test_qr_requires_auth(magic_link_client):
    resp = magic_link_client.get("/api/auth/magic-link/qr?url=http://example/")
    assert resp.status_code == 401


def test_owner_card_status_requires_auth(magic_link_client):
    resp = magic_link_client.get("/api/auth/magic-link/owner-card/status")
    assert resp.status_code == 401


def test_redeem_is_public(magic_link_client):
    """Redemption endpoint must be reachable without an API key (it's the
    whole point — the holder of the link is who's getting access)."""
    resp = magic_link_client.get(
        "/auth/magic-link/totally-bogus-token",
        follow_redirects=False,
    )
    # Bogus token → 404 (constant-shape failure), not 401.
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Generate happy path
# ---------------------------------------------------------------------------


def test_generate_returns_token_and_url(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "scope": "chat"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["target_username"] == "alice"
    assert data["scope"] == "chat"
    assert data["token_type"] == "guest"
    assert data["url_mode"] == "auto"
    assert data["reusable"] is False
    assert len(data["token"]) >= 32
    # New URL shape: http://auth.<device>.local/magic-link/<token>.
    # The /auth/ path prefix is gone — the auth subdomain implies it.
    assert data["url"].endswith(f"/magic-link/{data['token']}")


def test_generate_with_note_and_reusable(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={
            "target_username": "family",
            "scope": "chat",
            "reusable": True,
            "expires_in": 3600,
            "note": "household share poster",
        },
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reusable"] is True


def test_generate_respects_public_url_env(magic_link_client, monkeypatch, magic_link_module):
    """DREAM_PUBLIC_URL override (for Tailscale tunnels / custom domains)
    keeps the /auth/ prefix because the URL builder assumes that override
    is being fronted by a proxy that routes /auth/* to dashboard-api
    (matching the old shape)."""
    monkeypatch.setenv("DREAM_PUBLIC_URL", "http://dream.local:3002")
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "bob"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("http://dream.local:3002/auth/magic-link/")


def test_generate_url_defaults_to_auth_subdomain(magic_link_client):
    """With no DREAM_PUBLIC_URL set, the URL builds off the auth subdomain
    (http://auth.<DREAM_DEVICE_NAME>.local) — that's where dream-proxy
    routes dashboard-api in the host-based layout. The URL must NOT point
    at localhost:3000 (Open WebUI direct) or :3002 (dashboard-api direct)."""
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith("http://auth.dream.local/magic-link/"), url
    assert "localhost:3000" not in url
    assert ":3002" not in url


def test_generate_url_uses_configured_device_name(magic_link_client, monkeypatch, magic_link_module):
    """DREAM_DEVICE_NAME feeds into the auth subdomain."""
    monkeypatch.setenv("DREAM_DEVICE_NAME", "kitchen")
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith("http://auth.kitchen.local/magic-link/"), url


def test_generate_strips_trailing_slash_from_public_url(magic_link_client, monkeypatch, magic_link_module):
    """An operator who sets DREAM_PUBLIC_URL=http://x/ must not produce a
    double-slash in the magic-link URL."""
    monkeypatch.setenv("DREAM_PUBLIC_URL", "http://dream.local/")
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "carol"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert "//auth/" not in url, f"double-slash leak in {url}"
    assert url.startswith("http://dream.local/auth/magic-link/")


# ---------------------------------------------------------------------------
# Generate validation
# ---------------------------------------------------------------------------


def test_generate_rejects_empty_username(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": ""},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 422


def test_generate_rejects_invalid_username_chars(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice; drop table users"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("scope", ["root", "dashboard", "all"])
def test_generate_rejects_invalid_scope(magic_link_client, scope):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "scope": scope},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 422


def test_generate_accepts_hermes_scope(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "scope": "hermes"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "hermes"


def test_generate_rejects_short_expiry(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "expires_in": 10},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 422


def test_generate_rejects_long_expiry(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "expires_in": 999_999},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 422


def test_generate_owner_token_defaults_to_revoke_only_hermes_lan(magic_link_client, monkeypatch):
    monkeypatch.setenv("DREAM_PUBLIC_URL", "https://dream.example")
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner", "note": "factory card"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["token_type"] == "owner"
    assert data["scope"] == "hermes"
    assert data["url_mode"] == "lan"
    assert data["reusable"] is True
    assert data["expires_at"] is None
    assert data["url"].startswith("http://auth.dream.local/magic-link/")


def test_generate_owner_lan_requires_dream_proxy(magic_link_client, magic_link_module, monkeypatch):
    monkeypatch.setattr(
        magic_link_module,
        "_dream_proxy_lan_ready",
        lambda: (False, "Dream Talk owner cards require dream-proxy"),
    )

    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner"},
        headers=magic_link_client.auth_headers,
    )

    assert resp.status_code == 409
    assert "dream-proxy" in resp.json()["detail"]


def test_generate_owner_rejects_expiry(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner", "expires_in": 3600},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 422


def test_owner_public_url_mode_uses_public_url_when_requested(magic_link_client, monkeypatch):
    monkeypatch.setenv("DREAM_PUBLIC_URL", "https://dream.example")
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner", "url_mode": "public"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["url_mode"] == "public"
    assert data["url"].startswith("https://dream.example/auth/magic-link/")


def test_owner_public_url_mode_does_not_require_dream_proxy(
    magic_link_client, magic_link_module, monkeypatch,
):
    monkeypatch.setenv("DREAM_PUBLIC_URL", "https://dream.example")
    monkeypatch.setattr(
        magic_link_module,
        "_dream_proxy_lan_ready",
        lambda: (False, "Dream Talk owner cards require dream-proxy"),
    )

    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner", "url_mode": "public"},
        headers=magic_link_client.auth_headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["url"].startswith("https://dream.example/auth/magic-link/")


def test_owner_card_status_reports_proxy_state(
    magic_link_client, magic_link_module, monkeypatch,
):
    monkeypatch.setattr(
        magic_link_module,
        "_dream_proxy_lan_ready",
        lambda: (False, "dream-proxy is not enabled"),
    )

    resp = magic_link_client.get(
        "/api/auth/magic-link/owner-card/status",
        headers=magic_link_client.auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "ready": False,
        "requires": "dream-proxy",
        "reason": "dream-proxy is not enabled",
    }


def test_public_url_mode_requires_public_url(magic_link_client):
    resp = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner", "url_mode": "public"},
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 400
    assert "DREAM_PUBLIC_URL" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Redeem happy path
# ---------------------------------------------------------------------------


def test_redeem_sets_cookie_and_redirects(magic_link_client, magic_link_module):
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "scope": "chat"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    resp = magic_link_client.get(
        f"/auth/magic-link/{token}",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Session cookie must be HttpOnly.
    set_cookies = [h for h in resp.headers.raw if h[0].lower() == b"set-cookie"]
    cookie_blob = b" ".join(c[1] for c in set_cookies).lower()
    assert b"dream-session=" in cookie_blob
    assert b"httponly" in cookie_blob
    assert b"samesite=lax" in cookie_blob
    # Username hint is readable by the chat UI's JS (not HttpOnly).
    assert b"dream-target-user=alice" in cookie_blob


def test_redeem_issues_signed_cookie_that_verifies(magic_link_client, magic_link_module):
    """The dream-session cookie set by redemption must round-trip through
    session_signer.verify() — that's what dream-proxy's forward_auth will
    call on every protected request."""
    import session_signer

    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    resp = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 302
    from http.cookies import SimpleCookie

    cookies = SimpleCookie()
    for key, value in resp.headers.raw:
        if key.lower() == b"set-cookie":
            cookies.load(value.decode("latin1"))
    cookie = cookies["dream-session"].value if "dream-session" in cookies else None
    assert cookie, "redemption did not set dream-session cookie"

    ok, reason = session_signer.verify(cookie)
    assert ok is True, f"signed cookie did not verify: {reason}"


def test_redeem_redirects_to_chat_subdomain(magic_link_client, magic_link_module, monkeypatch):
    """Successful redemption 302s to chat.<device>.local — the dream-proxy
    routes Host: chat.<device>.local to Open WebUI. The cookie set just
    above uses Domain=<device>.local so it travels to the chat subdomain."""
    monkeypatch.setenv("DREAM_DEVICE_NAME", "kitchen")
    monkeypatch.setenv("DREAM_COOKIE_DOMAIN", "kitchen.local")

    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    resp = magic_link_client.get(f"/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://chat.kitchen.local"


def test_redeem_hermes_scope_redirects_to_hermes_subdomain(magic_link_client, monkeypatch):
    monkeypatch.setenv("DREAM_DEVICE_NAME", "kitchen")
    monkeypatch.setenv("DREAM_COOKIE_DOMAIN", "kitchen.local")

    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "scope": "hermes"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    resp = magic_link_client.get(f"/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://hermes.kitchen.local"


def test_owner_hermes_scope_redirects_to_dream_talk(magic_link_client, monkeypatch):
    monkeypatch.setenv("DREAM_DEVICE_NAME", "kitchen")
    monkeypatch.setenv("DREAM_COOKIE_DOMAIN", "kitchen.local")

    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner", "scope": "hermes"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    resp = magic_link_client.get(f"/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://talk.kitchen.local/talk"


def test_owner_token_can_be_redeemed_repeatedly_and_revoked(
    magic_link_client, magic_link_module, monkeypatch
):
    monkeypatch.setenv("DREAM_DEVICE_NAME", "studio")

    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "owner", "token_type": "owner"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    first = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    second = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert first.status_code == 302
    assert second.status_code == 302
    assert first.headers["location"] == "http://talk.studio.local/talk"

    store = magic_link_module._ensure_store()
    assert store["tokens"][0]["expires_at"] is None
    assert len(store["tokens"][0]["redemptions"]) == 2

    prefix = store["tokens"][0]["token_hash"][:8]
    rev = magic_link_client.delete(
        f"/api/auth/magic-link/{prefix}",
        headers=magic_link_client.auth_headers,
    )
    assert rev.status_code == 200

    after_revoke = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert after_revoke.status_code == 404


def test_redeem_sets_cookie_with_configured_domain(magic_link_client, magic_link_module, monkeypatch):
    """When DREAM_COOKIE_DOMAIN is set, the cookie carries that Domain
    attribute so the browser shares it across subdomains (SSO).
    Without it, the cookie stays host-only."""
    monkeypatch.setenv("DREAM_DEVICE_NAME", "kitchen")
    monkeypatch.setenv("DREAM_COOKIE_DOMAIN", "kitchen.local")

    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]
    resp = magic_link_client.get(f"/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 302
    cookie_blob = b" ".join(
        v for k, v in resp.headers.raw if k.lower() == b"set-cookie"
    ).lower()
    assert b"domain=kitchen.local" in cookie_blob


def test_redeem_defaults_cookie_domain_to_device_domain(magic_link_client, magic_link_module, monkeypatch):
    """With DREAM_COOKIE_DOMAIN unset in the default host-based layout, derive
    <DREAM_DEVICE_NAME>.local so redemption on auth.<device>.local carries to
    chat.<device>.local."""
    monkeypatch.setenv("DREAM_DEVICE_NAME", "kitchen")
    monkeypatch.delenv("DREAM_COOKIE_DOMAIN", raising=False)
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]
    resp = magic_link_client.get(f"/magic-link/{token}", follow_redirects=False)
    cookie_blob = b" ".join(
        v for k, v in resp.headers.raw if k.lower() == b"set-cookie"
    ).lower()
    assert b"domain=kitchen.local" in cookie_blob, cookie_blob


def test_redeem_omits_cookie_domain_with_public_url_override(magic_link_client, magic_link_module, monkeypatch):
    """DREAM_PUBLIC_URL means the operator chose a single custom origin/path
    layout, so omit Domain and let the cookie stay host-only."""
    monkeypatch.setenv("DREAM_PUBLIC_URL", "http://dream.example")
    monkeypatch.delenv("DREAM_COOKIE_DOMAIN", raising=False)
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]
    resp = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    cookie_blob = b" ".join(
        v for k, v in resp.headers.raw if k.lower() == b"set-cookie"
    ).lower()
    assert b"domain=" not in cookie_blob, cookie_blob


def test_redeem_refuses_when_signing_unconfigured(magic_link_client, magic_link_module):
    """If DREAM_SESSION_SECRET isn't configured, redemption returns 503
    BEFORE the single-use token is marked used. Otherwise a misconfigured
    install burns invites on every attempt — the user can't retry and
    has to ask the admin for a new link. The pre-check protects the
    invite."""
    import session_signer
    # Generate an invite while the secret is set (fixture state).
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    # Now clear the secret to simulate misconfiguration.
    session_signer._set_secret_for_tests("")

    resp = magic_link_client.get(f"/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()

    # The invite must NOT be marked used — restore the secret and confirm
    # redemption still works on a retry.
    session_signer._set_secret_for_tests("test-secret-for-magic-link-tests")
    retry = magic_link_client.get(f"/magic-link/{token}", follow_redirects=False)
    assert retry.status_code == 302, retry.text


def test_redeem_back_compat_auth_prefix_route_still_works(magic_link_client, magic_link_module):
    """The /auth/magic-link/<token> path is kept for back-compat with
    DREAM_PUBLIC_URL overrides and any in-flight QR codes from before
    the URL shape change. Both routes call the same handler."""
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    resp = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 302


def test_redeem_marks_token_used(magic_link_client, magic_link_module):
    """Second redemption of a single-use token must 404."""
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    first = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert first.status_code == 302

    second = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert second.status_code == 404
    assert second.json()["detail"] == "Invalid or expired magic link"


# ---------------------------------------------------------------------------
# Redeem failure modes — all must return the same opaque 404
# ---------------------------------------------------------------------------


def test_redeem_invalid_token(magic_link_client):
    resp = magic_link_client.get(
        "/auth/magic-link/not-a-real-token",
        follow_redirects=False,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Invalid or expired magic link"


def test_redeem_expired_token(magic_link_client, magic_link_module):
    """An expired token returns the same 404 as a bogus one."""
    # Generate with the minimum (60s) expiry, then forcibly age the record.
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "expires_in": 60},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    # Reach into storage and rewind expires_at to the past.
    store = magic_link_module._ensure_store()
    assert store["tokens"], "token store must contain the generated record"
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    store["tokens"][0]["expires_at"] = past
    magic_link_module._write_store(store)

    resp = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Invalid or expired magic link"


def test_redeem_revoked_token(magic_link_client, magic_link_module):
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice"},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    store = magic_link_module._ensure_store()
    prefix = store["tokens"][0]["token_hash"][:8]

    rev = magic_link_client.delete(
        f"/api/auth/magic-link/{prefix}",
        headers=magic_link_client.auth_headers,
    )
    assert rev.status_code == 200
    assert rev.json()["revoked"] is True

    resp = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Reusable-token semantics
# ---------------------------------------------------------------------------


def test_reusable_token_can_be_redeemed_multiple_times(
    magic_link_client, magic_link_module
):
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "family", "reusable": True},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    first = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    second = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    third = magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)

    assert first.status_code == 302
    assert second.status_code == 302
    assert third.status_code == 302

    # Audit trail records every redemption.
    store = magic_link_module._ensure_store()
    redemptions = store["tokens"][0]["redemptions"]
    assert len(redemptions) == 3


def test_owner_token_survives_pruning(magic_link_module):
    store = {
        "tokens": [
            {
                "token_hash": "a" * 64,
                "target_username": "owner",
                "scope": "hermes",
                "reusable": True,
                "token_type": "owner",
                "url_mode": "lan",
                "created_at": (datetime.now(timezone.utc) - timedelta(days=365)).isoformat(),
                "expires_at": None,
                "created_by_ip": "127.0.0.1",
                "redemptions": [],
                "revoked_at": None,
                "note": "factory card",
            }
        ]
    }
    pruned = magic_link_module._prune(store)
    assert len(pruned["tokens"]) == 1
    assert pruned["tokens"][0]["token_type"] == "owner"


# ---------------------------------------------------------------------------
# Rate-limit
# ---------------------------------------------------------------------------


def test_rate_limit_kicks_in_after_repeated_failures(
    magic_link_client, magic_link_module
):
    # 5 failures is the configured ceiling; the 6th must return 429.
    for _ in range(magic_link_module._RATE_LIMIT_MAX_FAILURES):
        bad = magic_link_client.get(
            "/auth/magic-link/no-such-token",
            follow_redirects=False,
        )
        assert bad.status_code == 404

    blocked = magic_link_client.get(
        "/auth/magic-link/no-such-token",
        follow_redirects=False,
    )
    assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# List + revoke
# ---------------------------------------------------------------------------


def test_list_includes_generated_token(magic_link_client):
    magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "note": "for laptop"},
        headers=magic_link_client.auth_headers,
    )
    resp = magic_link_client.get(
        "/api/auth/magic-link/list",
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200
    tokens = resp.json()["tokens"]
    assert len(tokens) == 1
    assert tokens[0]["target_username"] == "alice"
    assert tokens[0]["token_type"] == "guest"
    assert tokens[0]["url_mode"] == "auto"
    assert tokens[0]["note"] == "for laptop"
    assert len(tokens[0]["token_hash_prefix"]) == 8
    assert tokens[0]["redemption_count"] == 0
    assert tokens[0]["revoked_at"] is None


def test_list_normalizes_legacy_records(magic_link_client, magic_link_module):
    magic_link_module._write_store({
        "tokens": [
            {
                "token_hash": "b" * 64,
                "target_username": "legacy",
                "scope": "chat",
                "reusable": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "created_by_ip": "127.0.0.1",
                "redemptions": [],
                "revoked_at": None,
                "note": None,
            }
        ]
    })
    resp = magic_link_client.get(
        "/api/auth/magic-link/list",
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 200
    token = resp.json()["tokens"][0]
    assert token["token_type"] == "guest"
    assert token["url_mode"] == "auto"
    assert token["scope"] == "chat"


def test_list_reflects_redemption_count(magic_link_client):
    gen = magic_link_client.post(
        "/api/auth/magic-link/generate",
        json={"target_username": "alice", "reusable": True},
        headers=magic_link_client.auth_headers,
    )
    token = gen.json()["token"]

    magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)
    magic_link_client.get(f"/auth/magic-link/{token}", follow_redirects=False)

    resp = magic_link_client.get(
        "/api/auth/magic-link/list",
        headers=magic_link_client.auth_headers,
    )
    tokens = resp.json()["tokens"]
    assert tokens[0]["redemption_count"] == 2
    assert tokens[0]["last_redeemed_at"] is not None


def test_revoke_short_prefix_rejected(magic_link_client):
    resp = magic_link_client.delete(
        "/api/auth/magic-link/abc",
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 400


def test_revoke_unknown_prefix_returns_404(magic_link_client):
    resp = magic_link_client.delete(
        "/api/auth/magic-link/deadbeef",
        headers=magic_link_client.auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# QR endpoint
# ---------------------------------------------------------------------------


def test_qr_endpoint_returns_data_url_when_qrcode_installed(magic_link_client):
    """QR endpoint returns a base64 data URL if the qrcode library is available;
    otherwise 503 with a clear hint. Both shapes are acceptable — the test
    asserts the contract on whichever path is taken."""
    resp = magic_link_client.get(
        "/api/auth/magic-link/qr?url=http://dream.local:3002/auth/magic-link/abc",
        headers=magic_link_client.auth_headers,
    )
    if resp.status_code == 200:
        data = resp.json()
        assert data["data_url"].startswith("data:image/png;base64,")
    else:
        assert resp.status_code == 503
        assert "qrcode" in resp.json()["detail"].lower()
