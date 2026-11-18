import os
import pathlib
import sys
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-test-key")
os.environ.setdefault("ODS_INSTALL_DIR", "/tmp/ods-dashboard-access-test")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import pixel

app = FastAPI()
app.include_router(pixel.router)
client = TestClient(app)
headers = {"Authorization": "Bearer " + os.environ["DASHBOARD_API_KEY"]}
safe = dict(available=True, surface="linux-systemd", configured_mode="sandboxed", effective_mode="unknown",
            runtime_verified=False, revision="a" * 64, busy=False, pending=False, reason="runtime-proof-required", scope="owner-host")


def test_auth_before_any_host_call():
    with patch.object(pixel, "request_agent_json", new_callable=AsyncMock) as upstream:
        assert client.get("/api/pixel/access-mode").status_code == 401
        assert client.post("/api/pixel/access-mode", json={}).status_code == 401
        upstream.assert_not_called()


def test_confirmation_and_strict_revision_before_host_mutation():
    with patch.object(pixel, "request_agent_json", new_callable=AsyncMock) as upstream:
        assert client.post("/api/pixel/access-mode", headers=headers, json={"mode": "full-access", "confirmed": False, "revision": "a" * 64}).status_code == 400
        assert client.post("/api/pixel/access-mode", headers=headers, json={"mode": "full-access", "confirmed": "true", "revision": "a" * 64}).status_code == 422
        assert client.post("/api/pixel/access-mode", headers=headers, json={"mode": "full-access", "confirmed": True, "revision": "bad"}).status_code == 422
        upstream.assert_not_called()


def test_allowlisted_projection_and_exact_host_forwarding():
    with patch.object(pixel, "request_agent_json", new_callable=AsyncMock, return_value={**safe, "token": "secret", "config": {"apiKey": "secret"}}) as upstream:
        response = client.get("/api/pixel/access-mode", headers=headers)
        assert response.status_code == 200
        assert response.json() == safe
        request = {"mode": "full-access", "confirmed": True, "revision": safe["revision"]}
        response = client.post("/api/pixel/access-mode", headers=headers, json=request)
        assert response.status_code == 200
        upstream.assert_awaited_with("POST", "/v1/pixel/access-mode", payload=request, timeout=330.0)


def test_config_cannot_be_presented_as_effective_without_runtime_proof():
    for status in ({**safe, "effective_mode": "full-access"},
                   {**safe, "effective_mode": "sandboxed", "runtime_verified": True, "pending": True}):
        with patch.object(pixel, "request_agent_json", new_callable=AsyncMock, return_value=status):
            assert client.get("/api/pixel/access-mode", headers=headers).status_code == 502


def test_busy_refusal_is_not_reported_as_an_unverified_change():
    from host_agent_client import AgentHTTPError

    request = {"mode": "full-access", "confirmed": True, "revision": safe["revision"]}
    busy = AgentHTTPError(409, "busy", '{"error": "model-lifecycle-busy"}')
    with patch.object(pixel, "request_agent_json", new_callable=AsyncMock, side_effect=busy):
        response = client.post("/api/pixel/access-mode", headers=headers, json=request)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "model_lifecycle_busy"
    assert "Nothing was changed" in response.json()["detail"]["message"]

    failed = AgentHTTPError(503, "unavailable", '{"error": "access-service-unavailable"}')
    with patch.object(pixel, "request_agent_json", new_callable=AsyncMock, side_effect=failed):
        response = client.post("/api/pixel/access-mode", headers=headers, json=request)
    assert response.status_code == 409
    assert "was not verified" in response.json()["detail"]


def test_sandbox_recheck_is_offered_only_while_portal_is_idle():
    # Nothing can re-check the sandbox while Portal is busy: the host's
    # re-proof and Verify Sandbox both refuse a busy runtime rather than wait
    # (laptop, 2026-10-31: the banner sat through a whole run with nothing
    # able to clear it).
    assert pixel._access_recovery(safe) == "verify-sandbox"
    assert pixel._access_recovery({**safe, "busy": True}) is None
    # A held chat keeps its own recovery either way.
    held = {**safe, "reason": "chat-recovery-required", "busy": True}
    assert pixel._access_recovery(held) == "verify-sandbox"
