"""Only the closed diagnostic projection reaches authenticated Portal clients."""

import uuid

import httpx
import pytest

from test_model_routes import _patch_router_client


def observation():
    return {"schemaVersion": 1,
        "boundary": "router-forwarded-tools-not-execution-proof",
        "encoding": "json-sort-keys-ascii-v1", "state": "observed",
        "count": 12, "sha256": "a" * 64}


@pytest.mark.parametrize("unavailable", [False, True])
def test_proxy_preserves_valid_observation_and_correlation(test_client, monkeypatch, unavailable):
    probe, request_id = str(uuid.uuid4()), str(uuid.uuid4())
    tools = observation()
    if unavailable:
        tools.update(state="unavailable", count=None, sha256=None)
    monkeypatch.setenv("ODS_ROUTER_INTERNAL_KEY", "internal-secret")
    _patch_router_client(monkeypatch, httpx.Response(200, json={
        "probeId": probe, "requestId": request_id, "offeredTools": tools,
        "tools": [{"name": "private"}], "messages": ["private"]}))
    response = test_client.get(f"/api/models/routes/{probe}", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json() == {"probeId": probe, "requestId": request_id, "offeredTools": tools}


@pytest.mark.parametrize("change", [
    {"schemaVersion": True}, {"schemaVersion": 2}, {"count": True}, {"count": -1},
    {"count": 257}, {"count": None}, {"sha256": "A" * 64}, {"sha256": "private"},
    {"sha256": None}, {"boundary": "verified-release"}, {"encoding": "future"},
    {"state": "ready"}, {"state": "unavailable"}, {"private": "schema body"},
])
def test_proxy_drops_malformed_or_expanded_observation(change):
    from routers.model_routes import _sanitize_evidence
    probe = str(uuid.uuid4())
    value = _sanitize_evidence({"probeId": probe, "offeredTools": {**observation(), **change}}, probe)
    assert value == {"probeId": probe}


@pytest.mark.parametrize("request_id", [None, True, {}, "private", str(uuid.uuid4()).upper()])
def test_invalid_request_id_is_not_forwarded(request_id):
    from routers.model_routes import _sanitize_evidence
    probe = str(uuid.uuid4())
    assert _sanitize_evidence({"probeId": probe, "requestId": request_id}, probe) == {"probeId": probe}
