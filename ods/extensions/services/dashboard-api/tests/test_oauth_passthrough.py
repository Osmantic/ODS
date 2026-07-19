"""Tests for the OAuth passthrough — the redirect target that bridges
provider auth flows back into the agent's session without the user
having to copy-paste a code."""

from __future__ import annotations

import json
import os
import stat
import tempfile
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


def test_oauth_callback_writes_pending_file_and_returns_success_html(oauth_client):
    """Happy path: provider redirects to /api/oauth/callback with a code.
    The handler should persist the code under data/persona/ and return
    an HTML success page."""
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
    resp = oauth_client.get("/api/oauth/callback", params={"state": "google-workspace"})
    assert resp.status_code == 400
    assert "code" in resp.text.lower()
    assert not (oauth_client.tmp / "oauth_callback.json").exists()


def test_oauth_callback_defaults_state_to_google_workspace(oauth_client):
    """If state is missing (some providers don't echo it back cleanly),
    default to google-workspace since that's the most common ODS
    Server install flow."""
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code"},
    )
    assert resp.status_code == 200
    payload = json.loads((oauth_client.tmp / "oauth_callback.json").read_text())
    assert payload["state"] == "google-workspace"


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
    oauth_client.get("/api/oauth/callback", params={"code": "first", "state": "google-workspace"})
    oauth_client.get("/api/oauth/callback", params={"code": "second", "state": "google-workspace"})
    payload = json.loads((oauth_client.tmp / "oauth_callback.json").read_text())
    assert payload["code"] == "second"


def test_oauth_callback_escapes_state_in_success_html(oauth_client):
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code", "state": "<script>alert(1)</script>"},
    )
    assert resp.status_code == 200
    assert "<script>" not in resp.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text


def test_oauth_callback_only_reflects_relative_return_url(oauth_client):
    safe = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code", "return_url": "/talk"},
    )
    assert 'href="/talk"' in safe.text

    unsafe = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code", "return_url": "javascript:alert(1)"},
    )
    assert "javascript:alert" not in unsafe.text
    assert "Back to ODS Talk" not in unsafe.text


@pytest.mark.parametrize("return_url", ["//evil.com", "/\\evil.com", "/\\/evil.com"])
def test_oauth_callback_rejects_protocol_relative_return_url(oauth_client, return_url):
    """Browsers fold backslashes to forward slashes, so "/\\evil.com" becomes
    the protocol-relative "//evil.com" — an off-origin redirect. The success
    page must not render a Back button for any of these."""
    resp = oauth_client.get(
        "/api/oauth/callback",
        params={"code": "fake-code", "return_url": return_url},
    )
    assert "evil.com" not in resp.text
    assert "Back to ODS Talk" not in resp.text
