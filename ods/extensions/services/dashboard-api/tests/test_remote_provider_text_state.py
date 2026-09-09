"""Invalid persisted text remains diagnostic at the HTTP boundary."""

import json
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize("raw", [b"\xff", b"\xc3", b"\xff\xfe{\x00}\x00"])
def test_invalid_route_encoding_is_diagnostic(test_client, monkeypatch, tmp_path, raw):
    from routers import remote_provider_status as rps

    path = tmp_path / "routing-state.json"
    path.write_bytes(raw)
    monkeypatch.setattr(rps, "_state_path", lambda: path)
    monkeypatch.setattr(rps, "_fetch_egress_health", AsyncMock(return_value={"reachable": True, "ready": True}))
    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", AsyncMock(return_value={"ready": False}))
    response = test_client.get("/api/remote-provider/status", headers=test_client.auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert data["routeState"]["exists"] is True
    assert data["routeState"]["valid"] is False
    assert data["routeState"]["provider"] is None
    assert data["availableActions"]["test"] is False
    assert data["availableActions"]["remove"] is True
    assert path.read_bytes() == raw


@pytest.mark.parametrize("raw", [b"\xff", "秘密".encode(), b"token\nvalue"])
def test_invalid_peer_token_is_rejected_before_network(test_client, monkeypatch, tmp_path, raw):
    from routers import remote_provider_status as rps

    state_path = tmp_path / "routing-state.json"
    state_path.write_text(json.dumps({
        "schema": "ods.remote-routing-state.v1",
        "enabled": True,
        "provider": {"transport": "direct"},
        "peer": {"controlBaseUrl": "https://peer.example.test", "transport": "direct"},
    }))
    token_path = tmp_path / "peer-token"
    token_path.write_bytes(raw)
    monkeypatch.setattr(rps, "_state_path", lambda: state_path)
    monkeypatch.setattr(rps, "_peer_token_path", lambda: token_path)
    client = Mock(side_effect=AssertionError("invalid credentials must not reach the network"))
    monkeypatch.setattr(rps.httpx, "AsyncClient", client)

    response = test_client.get("/api/remote-provider/peer/models", headers=test_client.auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "invalid_peer_token"
    client.assert_not_called()
    assert token_path.read_bytes() == raw
