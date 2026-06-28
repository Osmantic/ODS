"""Auth tests for /health and /stats endpoints."""

import os
import sys
from pathlib import Path

import pytest

# Ensure the service directory is importable when running pytest from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set a deterministic key before importing the proxy module so SHIELD_API_KEY
# is resolved from the environment rather than generated/persisted to disk.
TEST_KEY = "test-shield-key-abcdef0123456789"
os.environ["SHIELD_API_KEY"] = TEST_KEY

from fastapi.testclient import TestClient  # noqa: E402

import proxy  # noqa: E402


@pytest.fixture
def client():
    return TestClient(proxy.app)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_KEY}"}


# --- /stats ---

class TestStatsAuth:
    def test_stats_no_auth_returns_401(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate", "").lower().startswith("bearer")

    def test_stats_with_valid_bearer_returns_full_payload(self, client, auth_headers):
        resp = client.get("/stats", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "cache_enabled",
            "cache_size",
            "active_sessions",
            "total_pii_scrubbed",
        }

    def test_stats_with_invalid_bearer_returns_403(self, client):
        resp = client.get("/stats", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 403


# --- /health ---

class TestHealthAuth:
    def test_health_unauth_returns_minimal(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_with_valid_bearer_returns_full_payload(self, client, auth_headers):
        resp = client.get("/health", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "status",
            "service",
            "version",
            "target_api",
            "cache_enabled",
            "active_sessions",
        }
        assert body["service"] == "api-privacy-shield"
        assert body["version"] == "0.2.0"
