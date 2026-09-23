"""Route availability must not masquerade as verified runtime readiness."""
import asyncio
import json
from unittest.mock import patch

import pytest

from test_pixel import FakeClient, FakeResponse
from test_pixel_runtime_identity import observed
from routers import pixel


ACCESS = dict(available=True, surface="darwin", configured_mode="sandboxed", effective_mode="sandboxed",
              runtime_verified=True, revision="a" * 64, busy=False, pending=False, reason=None, scope="owner-host")
FAILED = dict(ACCESS, available=False, configured_mode="unknown", effective_mode="unknown", runtime_verified=False,
              revision=None, reason="inspection-failed")


async def status_for(monkeypatch, access=ACCESS, identity=None):
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", "e" * 64)
    monkeypatch.setenv("PIXEL_EDGE_URL", "http://pixel-edge:9595")
    calls = []

    async def host(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/v1/model/status":
            return {"status": "idle", "activeRuntime": {"source": "local-switchboard", "model": "fixture-model", "contextLength": 65536}}
        assert path == "/v1/pixel/access-mode" and method == "GET"
        if isinstance(access, Exception):
            raise access
        if callable(access):
            return await access()
        return access

    monkeypatch.setattr(pixel, "request_agent_json", host)
    monkeypatch.setattr(pixel, "read_live_env_value", lambda _key: "")
    value = observed() if identity is None else identity

    class Client(FakeClient):
        def stream(self, method, url, **kwargs):
            self.response = FakeResponse(chunks=[json.dumps({"data": [{"id": "portal/default"}]} if url.endswith("/v1/models") else value).encode()])
            return super().stream(method, url, **kwargs)

    with patch.object(pixel.httpx, "AsyncClient", side_effect=lambda **_kwargs: Client(None)):
        result = await pixel.pixel_status()
    assert result["available"] is True
    assert result["runtime"] == {"source": "local-switchboard", "model": "fixture-model", "contextLength": 65536}
    assert result["runtimeMatchesRelease"] is (False if value.get("state") == "mismatch" else None)
    assert all(method == "GET" for method, _path, _kwargs in calls)
    return result


@pytest.mark.asyncio
async def test_exact_mac_route_available_access_inspection_failed_is_not_ready(monkeypatch):
    result = await status_for(monkeypatch, {**FAILED, "token": "private-upstream-secret", "details": {"path": "/private/owner"}})
    assert result["readiness"] == {
        "schemaVersion": 1, "state": "attention", "routeAvailable": True,
        "accessState": "failed", "effectiveMode": "unknown", "releaseState": "unverified",
        "reasonCode": "access-inspection-failed", "observedAt": result["readiness"]["observedAt"],
    }
    serialized = json.dumps(result)
    assert "host access verification failed" in result["detail"]
    assert "private-upstream-secret" not in serialized and "/private/owner" not in serialized
    assert "held" not in serialized and "admission" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [False, True])
async def test_verified_access_does_not_promote_partial_release_identity(monkeypatch, busy):
    result = await status_for(monkeypatch, {**ACCESS, "busy": busy})
    assert result["readiness"]["state"] == "unverified"
    assert result["readiness"]["accessState"] == "verified"
    assert result["readiness"]["reasonCode"] == "release-binding-unavailable"


@pytest.mark.asyncio
async def test_pending_access_and_runtime_disk_mismatch_are_conspicuous(monkeypatch):
    result = await status_for(monkeypatch, {**ACCESS, "pending": True, "runtime_verified": False,
                                          "effective_mode": "unknown", "reason": "transition-recovery-required"})
    assert result["readiness"]["state"] == "attention"
    assert result["readiness"]["accessState"] == "transitioning"
    identity = {**observed(), "state": "mismatch", "diskComparison": "mismatch",
                "runtimeMatchesRelease": False, "reasonCode": "runtime-files-changed"}
    result = await status_for(monkeypatch, identity=identity)
    assert result["readiness"]["state"] == "attention"
    assert result["readiness"]["releaseState"] == "mismatch"
    assert result["readiness"]["reasonCode"] == "runtime-files-changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("access,reason", [
    (pixel.AgentHTTPError(404, "private legacy upstream"), "access-probe-unavailable"),
    ({"available": True}, "access-probe-invalid"),
    ({**ACCESS, "available": "true"}, "access-probe-invalid"),
    ({**ACCESS, "effective_mode": "unknown"}, "access-probe-invalid"),
    ({**ACCESS, "pending": True}, "access-probe-invalid"),
    ({**ACCESS, "runtime_verified": False, "effective_mode": "unknown", "reason": "runtime-proof-required"}, "access-proof-unverified"),
])
async def test_old_invalid_or_unverified_access_stays_unknown_without_breaking_route(monkeypatch, access, reason):
    result = await status_for(monkeypatch, access)
    assert result["readiness"]["state"] == "unverified"
    assert result["readiness"]["accessState"] == "unverified"
    assert result["readiness"]["effectiveMode"] == "unknown"
    assert result["readiness"]["reasonCode"] == reason
    assert "private legacy upstream" not in json.dumps(result)


@pytest.mark.asyncio
async def test_access_probe_has_whole_coroutine_deadline_and_clears_previous_proof(monkeypatch):
    first = await status_for(monkeypatch)
    assert first["readiness"]["accessState"] == "verified"
    cancelled = asyncio.Event()

    async def stalled_transport():
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(pixel, "_READINESS_PROBE_SECONDS", 0.01)
    result = await asyncio.wait_for(status_for(monkeypatch, stalled_transport), timeout=0.5)
    assert cancelled.is_set()
    assert result["readiness"]["accessState"] == "unverified"
    assert result["readiness"]["reasonCode"] == "access-probe-timeout"
    assert result["readiness"]["effectiveMode"] == "unknown"


@pytest.mark.asyncio
async def test_forged_green_or_future_identity_still_cannot_create_ready(monkeypatch):
    for identity in ({**observed(), "runtimeMatchesRelease": True}, {**observed(), "schemaVersion": 2}):
        result = await status_for(monkeypatch, identity=identity)
        assert result["readiness"]["state"] == "unverified"
        assert result["readiness"]["releaseState"] == "unverified"
        assert result["runtimeMatchesRelease"] is None
