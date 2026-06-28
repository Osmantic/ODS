"""Tests for routers/setup.py — setup wizard, persona selection, and completion."""

import json


# ---------------------------------------------------------------------------
# Auth enforcement — 401 without Bearer token
# ---------------------------------------------------------------------------


def test_setup_persona_requires_auth(test_client):
    resp = test_client.post("/api/setup/persona", json={"persona": "general"})
    assert resp.status_code == 401


def test_setup_complete_requires_auth(test_client):
    resp = test_client.post("/api/setup/complete")
    assert resp.status_code == 401


def test_list_personas_requires_auth(test_client):
    resp = test_client.get("/api/setup/personas")
    assert resp.status_code == 401


def test_get_persona_info_requires_auth(test_client):
    resp = test_client.get("/api/setup/persona/general")
    assert resp.status_code == 401


def test_setup_diagnostics_requires_auth(test_client):
    resp = test_client.post("/api/setup/test")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/setup/status
# ---------------------------------------------------------------------------


def test_setup_status_first_run(test_client, setup_config_dir):
    """When setup-complete.json is absent, first_run is True and step is 0."""
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["first_run"] is True
    assert data["step"] == 0
    assert data["persona"] is None


def test_setup_status_already_complete(test_client, setup_config_dir):
    """When setup-complete.json exists, first_run is False."""
    (setup_config_dir / "setup-complete.json").write_text(
        json.dumps({"completed_at": "2025-01-01T00:00:00+00:00", "version": "1.0.0"})
    )
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["first_run"] is False


def test_setup_status_with_progress_step(test_client, setup_config_dir):
    """Progress step is read from setup-progress.json."""
    (setup_config_dir / "setup-progress.json").write_text(
        json.dumps({"step": 2, "persona_selected": True})
    )
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["step"] == 2


def test_setup_status_with_active_persona(test_client, setup_config_dir):
    """Active persona ID is returned when persona.json exists."""
    (setup_config_dir / "persona.json").write_text(
        json.dumps({"persona": "coding", "name": "Coding Buddy"})
    )
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["persona"] == "coding"


def test_setup_status_includes_personas_available(test_client, setup_config_dir):
    """personas_available lists at least the built-in persona keys."""
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    available = resp.json()["personas_available"]
    assert "general" in available
    assert "coding" in available
    assert "creative" in available


def test_setup_status_tolerates_corrupt_progress_file(test_client, setup_config_dir):
    """Corrupt setup-progress.json falls back to step=0 without crashing."""
    (setup_config_dir / "setup-progress.json").write_text("not-valid-json{{")
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["step"] == 0


# ---------------------------------------------------------------------------
# POST /api/setup/persona
# ---------------------------------------------------------------------------


def test_setup_persona_valid(test_client, setup_config_dir):
    """Valid persona ID returns 200 and writes persona.json."""
    resp = test_client.post(
        "/api/setup/persona",
        json={"persona": "general"},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["persona"] == "general"
    assert "name" in data
    # File was written
    persona_file = setup_config_dir / "persona.json"
    assert persona_file.exists()
    stored = json.loads(persona_file.read_text())
    assert stored["persona"] == "general"


def test_setup_persona_invalid(test_client, setup_config_dir):
    """Unknown persona ID returns 400."""
    resp = test_client.post(
        "/api/setup/persona",
        json={"persona": "nonexistent"},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 400


def test_setup_persona_stores_system_prompt(test_client, setup_config_dir):
    """persona.json written by setup_persona contains the system_prompt field."""
    test_client.post(
        "/api/setup/persona",
        json={"persona": "coding"},
        headers=test_client.auth_headers,
    )
    stored = json.loads((setup_config_dir / "persona.json").read_text())
    assert "system_prompt" in stored
    assert len(stored["system_prompt"]) > 10


def test_setup_persona_updates_progress(test_client, setup_config_dir):
    """Selecting a persona advances the progress step to 2."""
    test_client.post(
        "/api/setup/persona",
        json={"persona": "creative"},
        headers=test_client.auth_headers,
    )
    progress = json.loads((setup_config_dir / "setup-progress.json").read_text())
    assert progress["step"] == 2
    assert progress["persona_selected"] is True


# ---------------------------------------------------------------------------
# POST /api/setup/complete
# ---------------------------------------------------------------------------


def test_setup_complete_creates_marker(test_client, setup_config_dir):
    """POST /api/setup/complete returns 200 and creates setup-complete.json."""
    resp = test_client.post("/api/setup/complete", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert (setup_config_dir / "setup-complete.json").exists()


def test_setup_complete_removes_progress_file(test_client, setup_config_dir):
    """POST /api/setup/complete deletes setup-progress.json if it exists."""
    (setup_config_dir / "setup-progress.json").write_text(json.dumps({"step": 2}))
    test_client.post("/api/setup/complete", headers=test_client.auth_headers)
    assert not (setup_config_dir / "setup-progress.json").exists()


def test_setup_complete_idempotent(test_client, setup_config_dir):
    """Calling complete twice does not error."""
    test_client.post("/api/setup/complete", headers=test_client.auth_headers)
    resp = test_client.post("/api/setup/complete", headers=test_client.auth_headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/setup/personas  and  GET /api/setup/persona/{id}
# ---------------------------------------------------------------------------


def test_list_personas_returns_all(test_client, setup_config_dir):
    """GET /api/setup/personas lists all built-in personas."""
    resp = test_client.get("/api/setup/personas", headers=test_client.auth_headers)
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["personas"]}
    assert ids >= {"general", "coding", "creative"}


def test_list_personas_includes_name_and_icon(test_client, setup_config_dir):
    """Each persona entry has name and icon fields."""
    resp = test_client.get("/api/setup/personas", headers=test_client.auth_headers)
    for persona in resp.json()["personas"]:
        assert "name" in persona
        assert "icon" in persona


def test_get_persona_info_valid(test_client, setup_config_dir):
    """GET /api/setup/persona/general returns the persona details."""
    resp = test_client.get("/api/setup/persona/general", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "general"
    assert "name" in data
    assert "system_prompt" in data


def test_get_persona_info_not_found(test_client, setup_config_dir):
    """GET /api/setup/persona/<unknown> returns 404."""
    resp = test_client.get("/api/setup/persona/unknown-persona", headers=test_client.auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Diagnostics fallback timeout type
# ---------------------------------------------------------------------------


def test_diagnostics_fallback_uses_client_timeout(test_client, monkeypatch):
    """The fallback health-check loop must pass aiohttp.ClientTimeout, not a bare int."""
    import aiohttp
    import routers.setup as setup_router

    # Ensure the diagnostic script does NOT exist so we hit the fallback path
    monkeypatch.setattr(setup_router, "INSTALL_DIR", "/nonexistent-path")

    # Provide at least one service so the health-check loop body executes
    monkeypatch.setattr(setup_router, "SERVICES", {
        "fake-svc": {"name": "Fake", "host": "localhost", "port": 1, "health": "/"}
    })

    captured_timeouts = []

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, **kwargs):
            timeout = kwargs.get("timeout")
            captured_timeouts.append(timeout)
            return FakeResponse()

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    resp = test_client.post("/api/setup/test", headers=test_client.auth_headers)
    assert resp.status_code == 200

    # Verify at least one request was made and all timeouts are ClientTimeout instances
    assert len(captured_timeouts) > 0, "Expected at least one health check request"
    for t in captured_timeouts:
        assert isinstance(t, aiohttp.ClientTimeout), (
            f"Expected aiohttp.ClientTimeout but got {type(t).__name__}: {t}"
        )
