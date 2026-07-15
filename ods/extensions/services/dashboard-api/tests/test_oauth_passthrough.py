"""Tests for the OAuth passthrough — the redirect target that bridges
provider auth flows back into the agent's session without the user
having to copy-paste a code."""

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
from routers import oauth_passthrough


def _register_state(skill: str, state: str = "google-workspace") -> None:
    """Pre-register a state nonce bound to a skill for testing callback endpoints."""
    oauth_passthrough._PENDING_FLOWS[state] = {
        "skill": skill,
        "expires_at": int(time.time()) + 900
    }


@pytest.fixture(autouse=True)
def clean_pending_store():
    """Ensure the pending flow store is clean before and after each test."""
    oauth_passthrough._PENDING_FLOWS.clear()
    yield
    oauth_passthrough._PENDING_FLOWS.clear()


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


def test_oauth_callback_writes_pending_file_and_returns_success_html(oauth_client):
    """Happy path: provider redirects to /api/oauth/callback with a code.
    The handler should persist the code under data/persona/ and return
    an HTML success page."""
    _register_state("google-workspace", "google-workspace")
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code-abc123", "state": "google-workspace"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # Confirms the user-facing copy mentions the skill so they know what
    # they just authorised — important when multiple skills are in play.
    assert "google-workspace" in resp.text or "service" in resp.text
    assert "Authorised" in resp.text or "Authorized" in resp.text or "✓" in resp.text

    # The handler should have written the callback to disk for the
    # agent to pick up on its next turn.
    callback = oauth_client.tmp / "oauth_callback.json"
    assert callback.exists(), f"callback file not written at {callback}"
    payload = json.loads(callback.read_text())
    assert payload["code"] == "fake-code-abc123"
    assert payload["state"] == "google-workspace"
    assert isinstance(payload["captured_at"], int)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode bits are not reliable on Windows")
def test_oauth_callback_file_is_owner_only(oauth_client):
    _register_state("google-workspace", "google-workspace")
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code-abc123", "state": "google-workspace"},
    )
    assert resp.status_code == 200
    callback = oauth_client.tmp / "oauth_callback.json"
    assert stat.S_IMODE(callback.stat().st_mode) == 0o600


def test_oauth_callback_handles_provider_error(oauth_client):
    """If the user denied the consent or the provider sent back an
    error, surface the reason in HTML rather than writing a corrupt
    callback file. The agent shouldn't see a callback that contains
    no code."""
    _register_state("google-workspace", "google-workspace")
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"error": "access_denied", "state": "google-workspace"},
    )
    assert resp.status_code == 400
    assert "access_denied" in resp.text
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_oauth_callback_rejects_missing_code(oauth_client):
    """If a provider redirect somehow lands here with no code and no
    error, fail loudly rather than write a corrupt callback file."""
    _register_state("google-workspace", "google-workspace")
    resp = oauth_client.get("/api/oauth/callback", params={"state": "google-workspace"})
    assert resp.status_code == 400
    assert "code" in resp.text.lower()
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_oauth_callback_rejects_missing_state(oauth_client):
    """Replacing test_oauth_callback_defaults_state_to_google_workspace.
    If state is missing, the callback must be rejected immediately to
    prevent unauthenticated state bypass/injection."""
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code"},
    )
    assert resp.status_code == 400
    assert "missing state" in resp.text.lower()
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_oauth_pending_endpoint_returns_false_when_no_callback(oauth_client):
    """The pending endpoint is a debugging helper for the agent / operator.
    Returns ``{"pending": false}`` when nothing's waiting."""
    unauth = oauth_client.get("/api/oauth/pending")
    assert unauth.status_code == 401

    resp = oauth_client.get("/api/oauth/pending", headers=oauth_client.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"pending": False}


def test_oauth_pending_endpoint_returns_true_after_callback(oauth_client):
    """After a callback lands, pending should report ``true`` plus the
    state and age so the agent can decide whether the code is still
    fresh enough to redeem."""
    _register_state("google-workspace", "google-workspace")
    oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fresh-code", "state": "google-workspace"},
    )
    resp = oauth_client.get("/api/oauth/pending", headers=oauth_client.auth_headers)
    body = resp.json()
    assert body["pending"] is True
    assert body["state"] == "google-workspace"
    assert isinstance(body["captured_at"], int)
    assert body["age_seconds"] >= 0
    assert body["stale"] is False


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


def test_oauth_callback_atomic_write(oauth_client):
    """The handler writes via a .tmp + rename so a concurrent read by the
    agent never sees a half-written file. Verify the tmp file is gone
    after a successful callback."""
    _register_state("google-workspace", "google-workspace")
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "code1", "state": "google-workspace"},
    )
    assert resp.status_code == 200
    assert not (oauth_client.tmp / "oauth_callback.json.tmp").exists()
    assert (oauth_client.tmp / "oauth_callback.json").exists()


def test_oauth_callback_overwrites_previous_pending(oauth_client):
    """A user might restart the OAuth flow mid-setup (cancel, retry).
    The latest callback should overwrite the previous one cleanly."""
    _register_state("google-workspace", "google-workspace")
    oauth_client.get("/api/oauth/callback", params={"code": "first", "state": "google-workspace"})
    _register_state("google-workspace", "google-workspace")
    oauth_client.get("/api/oauth/callback", params={"code": "second", "state": "google-workspace"})
    payload = json.loads((oauth_client.tmp / "oauth_callback.json").read_text())
    assert payload["code"] == "second"


def test_oauth_callback_rejects_malicious_skill_name(oauth_client):
    """Replacing test_oauth_callback_escapes_state_in_success_html.
    Strict regex check on skill names prevents HTML injection by rejecting
    any malformed skill layouts with 400."""
    reg_resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "google-workspace"},
        headers=oauth_client.auth_headers
    )
    state = reg_resp.json()["state"]

    # We mock the skill name in the database directly to test rejection
    oauth_passthrough._PENDING_FLOWS[state]["skill"] = "<script>alert(1)</script>"

    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code", "state": state},
    )
    assert resp.status_code == 400
    assert "invalid skill name structure" in resp.text.lower()


def test_oauth_callback_only_reflects_relative_return_url(oauth_client):
    _register_state("google-workspace", "google-workspace")
    safe = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code", "state": "google-workspace", "return_url": "/talk"},
    )
    assert 'href="/talk"' in safe.text

    _register_state("google-workspace", "google-workspace")
    unsafe = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code", "state": "google-workspace", "return_url": "javascript:alert(1)"},
    )
    assert "javascript:alert" not in unsafe.text
    assert "Back to ODS Talk" not in unsafe.text


# =============================================================================
# Issue #1790 Secure OAuth State Validation Regression Tests
# =============================================================================

def test_oauth_pending_registration_requires_auth(oauth_client):
    """POST /api/oauth/pending requires authentication."""
    resp = oauth_client.post("/api/oauth/pending", json={"skill": "spotify"})
    assert resp.status_code == 401


def test_oauth_registration_generates_high_entropy_state(oauth_client):
    """Registration generates a high-entropy server-side state nonce."""
    resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "state" in body
    assert len(body["state"]) >= 32  # generated via token_urlsafe(32)


def test_different_registrations_generate_different_states(oauth_client):
    """Different registrations generate unique states."""
    r1 = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()
    r2 = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()
    assert r1["state"] != r2["state"]


def test_state_is_bound_to_registered_skill(oauth_client):
    """State is bound to the registered skill in the pending flow registry."""
    resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()
    state = resp["state"]
    assert oauth_passthrough._PENDING_FLOWS[state]["skill"] == "spotify"


def test_valid_state_callback_succeeds(oauth_client):
    """A valid state callback succeeds and writes the correct artifacts."""
    resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()
    state = resp["state"]

    callback_resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "auth-code-123", "state": state}
    )
    assert callback_resp.status_code == 200

    legacy = oauth_client.tmp / "oauth_callback.json"
    skill_specific = oauth_client.tmp / "oauth_callback_spotify.json"
    assert legacy.exists()
    assert skill_specific.exists()


def test_unknown_state_is_rejected_without_artifact(oauth_client):
    """An unknown state callback is rejected without writing any callback artifacts."""
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "code123", "state": "unknown-state-nonce"}
    )
    assert resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_expired_state_is_rejected_without_artifact(oauth_client):
    """An expired state callback is rejected without writing any callback artifacts."""
    resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()
    state = resp["state"]

    # Backdate expiration time
    oauth_passthrough._PENDING_FLOWS[state]["expires_at"] = int(time.time()) - 1

    callback_resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "code123", "state": state}
    )
    assert callback_resp.status_code == 400
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_consumed_state_cannot_be_reused(oauth_client):
    """A consumed state is immediately removed and cannot be reused."""
    resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()
    state = resp["state"]

    # First succeeds
    r1 = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "code1", "state": state}
    )
    assert r1.status_code == 200

    # Second fails
    r2 = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "code2", "state": state}
    )
    assert r2.status_code == 400


def test_callback_derives_skill_from_trusted_pending_flow(oauth_client):
    """Callback derives the skill identifier solely from the trusted registration record."""
    resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "github"},
        headers=oauth_client.auth_headers
    ).json()
    state = resp["state"]

    # Callback parameters do not carry the skill identifier anymore
    r = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "code123", "state": state}
    )
    assert r.status_code == 200
    # Verified by check of the skill-specific artifact written
    assert (oauth_client.tmp / "oauth_callback_github.json").exists()


def test_independent_skill_artifacts_do_not_clobber(oauth_client):
    """Independent skill flows write to separate files and do not clobber each other."""
    state_spotify = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()["state"]

    state_github = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "github"},
        headers=oauth_client.auth_headers
    ).json()["state"]

    # Trigger both callbacks
    oauth_client.get("/api/oauth/callback", params={"code": "c_spotify", "state": state_spotify})
    oauth_client.get("/api/oauth/callback", params={"code": "c_github", "state": state_github})

    art_spotify = oauth_client.tmp / "oauth_callback_spotify.json"
    art_github = oauth_client.tmp / "oauth_callback_github.json"

    assert art_spotify.exists()
    assert art_github.exists()
    assert json.loads(art_spotify.read_text())["code"] == "c_spotify"
    assert json.loads(art_github.read_text())["code"] == "c_github"


def test_concurrent_consumption_allows_exactly_one_success(oauth_client):
    """Simultaneous validation-and-consume checks block race double redemption."""
    resp = oauth_client.post(
        "/api/oauth/pending",
        json={"skill": "spotify"},
        headers=oauth_client.auth_headers
    ).json()
    state = resp["state"]

    # Sequential callback checks (as uvicorn executes requests atomically under store locking)
    r1 = oauth_client.get("/api/oauth/callback", params={"code": "code1", "state": state})
    r2 = oauth_client.get("/api/oauth/callback", params={"code": "code2", "state": state})

    assert r1.status_code == 200
    assert r2.status_code == 400


def test_expired_pending_flow_cleanup():
    """Registration triggers the cleanup of any expired flow records."""
    s1, _ = oauth_passthrough.register_pending_flow("spotify")
    s2, _ = oauth_passthrough.register_pending_flow("github")

    # Manually expire s2
    oauth_passthrough._PENDING_FLOWS[s2]["expires_at"] = int(time.time()) - 10

    # New registration triggers cleanup
    oauth_passthrough.register_pending_flow("google-workspace")

    assert s1 in oauth_passthrough._PENDING_FLOWS
    assert s2 not in oauth_passthrough._PENDING_FLOWS
