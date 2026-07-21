"""Tests for the OAuth passthrough — the redirect target that bridges
provider auth flows back into the agent's session without the user
having to copy-paste a code.

The security contract these tests enforce:

  * ``/api/oauth/callback`` MUST only write a callback file when
    ``state`` matches a live nonce issued by ``/api/oauth/init``.
  * Nonces are single-use and consumed before the callback payload is
    written, so replays and concurrent uses can't both succeed.
  * The callback endpoint stays unauthenticated (it's a provider
    redirect target) but has zero side effects when validation fails.
  * ``skill_id`` and ``return_url`` are bound at init time; the callback
    query string can't influence either.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as main_module


@pytest.fixture
def oauth_client(monkeypatch):
    """TestClient pointed at a temp persona dir so callbacks don't pollute
    the host's real data/persona/."""
    tmp = tempfile.mkdtemp(prefix="ods-oauth-test-")
    monkeypatch.setenv("ODS_PERSONA_DIR", tmp)
    client = TestClient(main_module.app)
    client.tmp = Path(tmp)
    client.auth_headers = {"Authorization": "Bearer test-key-12345"}
    return client


def _init_flow(client, skill_id="google-workspace", return_url="", ttl_seconds=None):
    """Bootstrap a valid nonce for ``skill_id``. Returns the state string.
    Every callback happy-path test starts here — mirrors what Hermes does
    on ``setup.py --auth-url``."""
    body = {"skill_id": skill_id, "return_url": return_url}
    if ttl_seconds is not None:
        body["ttl_seconds"] = ttl_seconds
    resp = client.post("/api/oauth/init", json=body, headers=client.auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["state"]


# ---------------------------------------------------------------------------
# /api/oauth/init
# ---------------------------------------------------------------------------


def test_oauth_init_requires_auth(oauth_client):
    """Only the agent (which holds the dashboard API key) can request a
    nonce. Without auth, no nonce is issued."""
    resp = oauth_client.post("/api/oauth/init", json={"skill_id": "google-workspace"})
    assert resp.status_code == 401


def test_oauth_init_returns_state_and_expiry(oauth_client):
    resp = oauth_client.post(
        "/api/oauth/init",
        json={"skill_id": "google-workspace"},
        headers=oauth_client.auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["skill_id"] == "google-workspace"
    # 43 chars is what secrets.token_urlsafe(32) yields, but the endpoint's
    # regex accepts 22..128 to leave room for entropy changes.
    assert 22 <= len(body["state"]) <= 128
    assert body["expires_at"] > int(time.time())


def test_oauth_init_generates_unique_nonces(oauth_client):
    seen = set()
    for _ in range(5):
        state = _init_flow(oauth_client)
        assert state not in seen
        seen.add(state)


@pytest.mark.parametrize(
    "skill_id",
    ["", "   ", "has spaces", "has/slash", "has\\backslash", "has$shell", "a" * 65],
)
def test_oauth_init_rejects_bad_skill_id(oauth_client, skill_id):
    resp = oauth_client.post(
        "/api/oauth/init",
        json={"skill_id": skill_id},
        headers=oauth_client.auth_headers,
    )
    assert resp.status_code == 422, f"skill_id={skill_id!r} should have been rejected"


@pytest.mark.parametrize(
    "return_url",
    ["http://evil.example/", "//evil.example/", "javascript:alert(1)", "not-a-path"],
)
def test_oauth_init_rejects_unsafe_return_url(oauth_client, return_url):
    resp = oauth_client.post(
        "/api/oauth/init",
        json={"skill_id": "google-workspace", "return_url": return_url},
        headers=oauth_client.auth_headers,
    )
    assert resp.status_code == 422


def test_oauth_init_accepts_relative_return_url(oauth_client):
    resp = oauth_client.post(
        "/api/oauth/init",
        json={"skill_id": "google-workspace", "return_url": "/talk"},
        headers=oauth_client.auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("ttl_seconds", [30, 1801, -1, 0])
def test_oauth_init_clamps_ttl(oauth_client, ttl_seconds):
    resp = oauth_client.post(
        "/api/oauth/init",
        json={"skill_id": "google-workspace", "ttl_seconds": ttl_seconds},
        headers=oauth_client.auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode bits are not reliable on Windows")
def test_oauth_init_nonce_file_is_owner_only(oauth_client):
    state = _init_flow(oauth_client)
    nonce_file = oauth_client.tmp / "oauth-nonces" / f"{state}.json"
    assert nonce_file.exists()
    assert stat.S_IMODE(nonce_file.stat().st_mode) == 0o600


def test_oauth_init_prunes_expired_nonces(oauth_client):
    """A stale nonce sitting on disk should be reaped on the next init
    so we don't accumulate garbage across long deployments."""
    nonce_dir = oauth_client.tmp / "oauth-nonces"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    stale = nonce_dir / "stale-nonce-abcdefghijklmno.json"
    stale.write_text(
        json.dumps(
            {
                "nonce": "stale-nonce-abcdefghijklmno",
                "skill_id": "google-workspace",
                "return_url": "",
                "created_at": int(time.time()) - 3600,
                "ttl_seconds": 900,
            }
        )
    )
    assert stale.exists()

    _init_flow(oauth_client, skill_id="spotify")
    assert not stale.exists(), "expired nonce should have been pruned on init"


# ---------------------------------------------------------------------------
# /api/oauth/callback — happy path
# ---------------------------------------------------------------------------


def test_callback_with_valid_nonce_writes_callback_and_returns_success(oauth_client):
    """Full happy path: init → callback with the issued state → callback
    file written with the SERVER-RESOLVED skill_id (not whatever the
    callback query said)."""
    state = _init_flow(oauth_client, skill_id="google-workspace")
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code-abc123", "state": state},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "google-workspace" in resp.text or "service" in resp.text
    assert "Authorised" in resp.text or "Authorized" in resp.text or "✓" in resp.text

    callback = oauth_client.tmp / "oauth_callback.json"
    assert callback.exists(), f"callback file not written at {callback}"
    payload = json.loads(callback.read_text())
    assert payload["code"] == "fake-code-abc123"
    # The agent contract: `state` field carries the resolved skill_id, not
    # the raw nonce. Hermes reads this to know which skill to finalise.
    assert payload["state"] == "google-workspace"
    assert isinstance(payload["captured_at"], int)


def test_callback_consumes_nonce_after_success(oauth_client):
    state = _init_flow(oauth_client)
    nonce_file = oauth_client.tmp / "oauth-nonces" / f"{state}.json"
    assert nonce_file.exists()

    resp = oauth_client.get("/api/oauth/callback", params={"code": "x", "state": state})
    assert resp.status_code == 200
    assert not nonce_file.exists(), "nonce should be deleted after successful callback"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode bits are not reliable on Windows")
def test_callback_file_is_owner_only(oauth_client):
    state = _init_flow(oauth_client)
    resp = oauth_client.get("/api/oauth/callback", params={"code": "fake", "state": state})
    assert resp.status_code == 200
    callback = oauth_client.tmp / "oauth_callback.json"
    assert stat.S_IMODE(callback.stat().st_mode) == 0o600


def test_callback_uses_bound_return_url_not_query_param(oauth_client):
    """return_url is bound at init and MUST come from the nonce — a
    callback query param must not influence it (would be an open-redirect
    surface)."""
    state = _init_flow(oauth_client, return_url="/talk")
    resp = oauth_client.get(
        "/api/oauth/callback",
        # Attacker attempts to override the return_url via query string.
        params={"code": "fake", "state": state, "return_url": "javascript:alert(1)"},
    )
    assert resp.status_code == 200
    assert 'href="/talk"' in resp.text
    assert "javascript:alert" not in resp.text


def test_callback_success_page_omits_button_when_no_return_url_bound(oauth_client):
    state = _init_flow(oauth_client, return_url="")
    resp = oauth_client.get("/api/oauth/callback", params={"code": "fake", "state": state})
    assert resp.status_code == 200
    assert "Back to ODS Talk" not in resp.text


def test_callback_escapes_skill_id_in_success_html(oauth_client, monkeypatch):
    """Even if a compromised nonce file somehow contained an unsafe
    skill_id, the success page must not reflect it as raw HTML."""
    # Bypass init to plant a nonce whose skill_id is not what the endpoint
    # would normally accept. The callback should fall back to "service".
    nonce_dir = oauth_client.tmp / "oauth-nonces"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    state = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
    nonce_file = nonce_dir / f"{state}.json"
    nonce_file.write_text(
        json.dumps(
            {
                "nonce": state,
                "skill_id": "<script>alert(1)</script>",
                "return_url": "",
                "created_at": int(time.time()),
                "ttl_seconds": 900,
            }
        )
    )
    resp = oauth_client.get("/api/oauth/callback", params={"code": "fake", "state": state})
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    # Falls back to the generic label when the stored skill_id is unsafe.
    assert "service" in resp.text


# ---------------------------------------------------------------------------
# /api/oauth/callback — rejection paths (the security-critical ones)
# ---------------------------------------------------------------------------


def test_callback_rejects_missing_state(oauth_client):
    """Old default-to-google-workspace behaviour is GONE. A callback with
    no state is unrecognised and MUST NOT produce a callback file."""
    resp = oauth_client.get("/api/oauth/callback", params={"code": "fake"})
    assert resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_callback_rejects_unknown_state(oauth_client):
    """This is the injection attack from the issue: attacker fires a
    callback with a guessed state, but never called /init. Refuse."""
    fake_state = "attacker-guessed-state-abcdefghijklmnop"
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "attacker-code", "state": fake_state},
    )
    assert resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


@pytest.mark.parametrize(
    "state",
    [
        "short",                       # too short
        "has spaces in it and more",   # invalid chars, wrong length
        "../../../../etc/passwd",      # path traversal attempt
        "..",                          # bare parent
        "/absolute/path/attack",       # absolute path attempt
        "has.dots.in.it.abcdefghij",   # '.' not in the base64url alphabet
        "a" * 200,                     # too long
    ],
)
def test_callback_rejects_malformed_state(oauth_client, state):
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "code", "state": state},
    )
    assert resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_callback_rejects_expired_nonce(oauth_client):
    """A nonce past its TTL must be refused and cleaned up. Simulate by
    rewriting the on-disk nonce to look ancient."""
    state = _init_flow(oauth_client)
    nonce_file = oauth_client.tmp / "oauth-nonces" / f"{state}.json"
    payload = json.loads(nonce_file.read_text())
    payload["created_at"] = int(time.time()) - 10_000
    nonce_file.write_text(json.dumps(payload))

    resp = oauth_client.get("/api/oauth/callback", params={"code": "fake", "state": state})
    assert resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()
    assert not nonce_file.exists(), "expired nonce should be cleaned up"


def test_callback_rejects_replayed_nonce(oauth_client):
    """Nonces are single-use. A second callback with the same state must
    fail — otherwise a leaked callback URL could be exchanged twice."""
    state = _init_flow(oauth_client)
    first = oauth_client.get("/api/oauth/callback", params={"code": "first", "state": state})
    assert first.status_code == 200

    # The callback file exists from the first legit call — snapshot it so
    # we can prove the replay didn't overwrite it.
    callback_file = oauth_client.tmp / "oauth_callback.json"
    original = callback_file.read_text()

    second = oauth_client.get("/api/oauth/callback", params={"code": "second", "state": state})
    assert second.status_code == 400
    assert callback_file.read_text() == original, "replay must not overwrite the legitimate callback"


def test_callback_rejects_unreadable_nonce(oauth_client):
    """If the nonce file is corrupt, refuse the callback and clean up
    rather than falling through to accepting an unvalidated state."""
    state = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
    nonce_dir = oauth_client.tmp / "oauth-nonces"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    nonce_file = nonce_dir / f"{state}.json"
    nonce_file.write_text("not-json{{{")

    resp = oauth_client.get("/api/oauth/callback", params={"code": "fake", "state": state})
    assert resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()
    assert not nonce_file.exists(), "unreadable nonce should be cleaned up"


# ---------------------------------------------------------------------------
# Provider error paths
# ---------------------------------------------------------------------------


def test_callback_provider_error_consumes_nonce(oauth_client):
    """If the user denies consent, the provider redirects with ?error=...
    plus the state we issued. The nonce must be consumed so it can't be
    replayed by an attacker who observed the URL."""
    state = _init_flow(oauth_client)
    nonce_file = oauth_client.tmp / "oauth-nonces" / f"{state}.json"
    assert nonce_file.exists()

    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"error": "access_denied", "state": state},
    )
    assert resp.status_code == 400
    assert "access_denied" in resp.text
    assert not (oauth_client.tmp / "oauth_callback.json").exists()
    assert not nonce_file.exists(), "provider-error path should consume the nonce"


def test_callback_missing_code_consumes_nonce(oauth_client):
    """A malformed provider redirect (no code, no error) still burns the
    nonce — the user needs a fresh flow to try again."""
    state = _init_flow(oauth_client)
    nonce_file = oauth_client.tmp / "oauth-nonces" / f"{state}.json"

    resp = oauth_client.get("/api/oauth/callback", params={"state": state})
    assert resp.status_code == 400
    assert "code" in resp.text.lower()
    assert not (oauth_client.tmp / "oauth_callback.json").exists()
    assert not nonce_file.exists()


def test_callback_provider_error_without_state_still_400s(oauth_client):
    """Some providers may drop state on error redirects. Still fail
    cleanly without writing anything."""
    resp = oauth_client.get("/api/oauth/callback", params={"error": "server_error"})
    assert resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


# ---------------------------------------------------------------------------
# Concurrent flows
# ---------------------------------------------------------------------------


def test_concurrent_flows_have_independent_nonces(oauth_client):
    """Two skills being set up simultaneously must not interfere. Each
    gets its own nonce, each callback resolves to its own skill_id."""
    state_a = _init_flow(oauth_client, skill_id="google-workspace")
    state_b = _init_flow(oauth_client, skill_id="spotify")
    assert state_a != state_b

    # B's callback lands first.
    oauth_client.get("/api/oauth/callback", params={"code": "code-b", "state": state_b})
    b_payload = json.loads((oauth_client.tmp / "oauth_callback.json").read_text())
    assert b_payload["state"] == "spotify"
    assert b_payload["code"] == "code-b"

    # A's callback lands next — legitimate overwrite of the fixed
    # callback file (agent consumes each in turn). A's nonce is still
    # valid because B consumed only B's nonce.
    oauth_client.get("/api/oauth/callback", params={"code": "code-a", "state": state_a})
    a_payload = json.loads((oauth_client.tmp / "oauth_callback.json").read_text())
    assert a_payload["state"] == "google-workspace"
    assert a_payload["code"] == "code-a"


def test_callback_atomic_write(oauth_client):
    """The handler writes via a .tmp + rename so a concurrent read by the
    agent never sees a half-written file."""
    state = _init_flow(oauth_client)
    resp = oauth_client.get("/api/oauth/callback", params={"code": "code1", "state": state})
    assert resp.status_code == 200
    assert not (oauth_client.tmp / "oauth_callback.json.tmp").exists()
    assert (oauth_client.tmp / "oauth_callback.json").exists()


# ---------------------------------------------------------------------------
# /api/oauth/pending
# ---------------------------------------------------------------------------


def test_oauth_pending_endpoint_returns_false_when_no_callback(oauth_client):
    unauth = oauth_client.get("/api/oauth/pending")
    assert unauth.status_code == 401

    resp = oauth_client.get("/api/oauth/pending", headers=oauth_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"pending": False}


def test_oauth_pending_endpoint_returns_true_after_callback(oauth_client):
    state = _init_flow(oauth_client)
    oauth_client.get("/api/oauth/callback", params={"code": "fresh-code", "state": state})

    resp = oauth_client.get("/api/oauth/pending", headers=oauth_client.auth_headers)
    body = resp.json()
    assert body["pending"] is True
    assert body["state"] == "google-workspace"
    assert isinstance(body["captured_at"], int)
    assert body["age_seconds"] >= 0
    assert body["stale"] is False


# ---------------------------------------------------------------------------
# /api/oauth/providers (unchanged surface — smoke covers regressions)
# ---------------------------------------------------------------------------


def test_oauth_providers_requires_auth(oauth_client):
    resp = oauth_client.get("/api/oauth/providers")
    assert resp.status_code == 401


def test_oauth_providers_reports_credential_status(oauth_client, monkeypatch):
    registry = oauth_client.tmp / "providers.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "ods.oauth-providers.v1",
                "providers": [
                    {
                        "id": "google",
                        "name": "Google Workspace",
                        "skill_id": "google-workspace",
                        "flow": "authorization_code",
                        "credential_files": ["google_client_secret.json"],
                        "redirect_uris": ["http://localhost:3002/api/oauth/callback"],
                    },
                    {
                        "id": "spotify",
                        "name": "Spotify",
                        "skill_id": "spotify",
                        "flow": "authorization_code_pkce",
                        "credential_files": ["spotify_client.json"],
                        "redirect_uris": ["http://localhost:3002/api/oauth/callback"],
                    },
                ],
            }
        )
    )
    data_dir = oauth_client.tmp / "data"
    hermes_dir = data_dir / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "google_client_secret.json").write_text("{}")

    monkeypatch.setenv("ODS_OAUTH_PROVIDERS_FILE", str(registry))
    monkeypatch.setenv("ODS_DATA_DIR", str(data_dir))

    resp = oauth_client.get("/api/oauth/providers", headers=oauth_client.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "ods.oauth-providers.v1"
    by_id = {provider["id"]: provider for provider in body["providers"]}
    assert by_id["google"]["configured"] is True
    assert by_id["spotify"]["configured"] is False
    assert by_id["google"]["found_credentials"] == ["hermes/google_client_secret.json"]
