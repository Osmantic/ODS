from host_agent_client import AgentUnavailable
import copy
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pixel_provider_public import normalize_public
from routers import pixel_providers as api

DEFAULT_CONFIG = {
    "schemaVersion": 1,
    "revision": 0,
    "enabled": False,
    "providers": [],
    "roles": {"leader": None, "backups": [], "advisor": None, "handoff": None},
    "policy": {"allowCloud": False, "maxAttempts": 3, "deadlineSeconds": 120}
}

@pytest.mark.parametrize("reason", ["provider-inspection-changed", "model-lifecycle-busy", "provider-recovery-conflict"])
def test_runtime_conflict_preserves_only_allowlisted_host_code(client, mock_request, reason):
    from host_agent_client import AgentHTTPError
    mock_request.side_effect = AgentHTTPError(409, "private-sentinel", json.dumps({
        "error": "private-sentinel", "code": reason,
    }))
    response = client.post("/api/pixel/providers/runtime", json={
        "operation": "deactivate", "revision": "a" * 64, "providerRevision": 3,
    }, headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == reason
    assert "private-sentinel" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert mock_request.call_count == 1


@pytest.mark.parametrize("raw", [
    '{"code":"private-sentinel"}', '{"code":"provider-inspection-changed","code":"private-sentinel"}',
    '{"code":"provider-inspection-changed","extra":"private-sentinel"}',
    '{"code":"provider-inspection-changed","error":"' + 'x' * 2048 + '"}',
    '{', '["provider-inspection-changed"]',
])
def test_runtime_conflict_unknown_or_malformed_body_stays_generic(client, mock_request, raw):
    from host_agent_client import AgentHTTPError
    mock_request.side_effect = AgentHTTPError(409, "private-sentinel", raw)
    response = client.post("/api/pixel/providers/runtime", json={
        "operation": "deactivate", "revision": "a" * 64, "providerRevision": 3,
    }, headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)
    assert "private-sentinel" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert mock_request.call_count == 1

@pytest.mark.parametrize("field,value", [
    ("schemaVersion", True), ("revision", "0"),
    ("roles", {"leader": None, "backups": [], "advisor": None}),
    ("roles", {"leader": None, "backups": [], "advisor": None, "handoff": None, "secret": "private-sentinel"}),
    ("policy", {"allowCloud": False, "maxAttempts": 3, "deadlineSeconds": 120, "credentialRef": "private-sentinel"}),
])
def test_public_response_rejects_nested_leaks(client, mock_request, field, value):
    normalize_public(DEFAULT_CONFIG)
    bad = copy.deepcopy(DEFAULT_CONFIG)
    bad[field] = value
    mock_request.return_value = bad
    response = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 502
    assert "private-sentinel" not in response.text

@pytest.mark.parametrize("raw", [
    b'{"expectedRevision":true,"document":{}}', b'"\xff"',
    b'{"expectedRevision":0,"document":{"x":' + b'[' * 50 + b'0' + b']' * 50 + b'}}',
])
def test_malformed_post_never_calls_host(client, mock_request, raw):
    response = client.post("/api/pixel/providers/save", content=raw,
                           headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 400
    mock_request.assert_not_called()

@pytest.mark.parametrize("status,expected", [(409, 409), (401, 502), (500, 502)])
def test_post_error_is_not_retried_or_leaked(client, mock_request, status, expected):
    from host_agent_client import AgentHTTPError
    mock_request.side_effect = AgentHTTPError(status, "private-sentinel")
    response = client.post("/api/pixel/providers/save",
        json={"expectedRevision": 0, "document": DEFAULT_CONFIG},
        headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == expected
    assert "private-sentinel" not in response.text
    assert mock_request.call_count == 1

def test_main_registers_provider_router(test_client):
    # Full app fixture, not just the isolated router app: unauthenticated route
    # must exist and fail authentication instead of returning 404.
    assert test_client.get("/api/pixel/providers").status_code == 401

def create_app():
    app = FastAPI()
    app.include_router(api.router)
    return app

@pytest.fixture
def client(monkeypatch):
    app = create_app()
    import security
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-key-12345")
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture
def mock_request():
    with patch.object(api, 'request_agent_json', new_callable=AsyncMock) as m:
        yield m

def test_get_unauthorized(client, mock_request):
    resp = client.get("/api/pixel/providers")
    assert resp.status_code == 401
    mock_request.assert_not_called()

def test_post_unauthorized(client, mock_request):
    resp = client.post("/api/pixel/providers/save", json={})
    assert resp.status_code == 401
    mock_request.assert_not_called()

def test_get_success(client, mock_request):
    mock_request.return_value = DEFAULT_CONFIG
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime"] == {"status": "not-inspected", "reason": "runtime-status-separate"}
    assert resp.headers['cache-control'] == 'no-store'
    assert data["configuration"] == DEFAULT_CONFIG

def test_post_success(client, mock_request):
    mock_request.return_value = DEFAULT_CONFIG
    payload = {"expectedRevision": 0, "document": DEFAULT_CONFIG}
    resp = client.post("/api/pixel/providers/save", json=payload, headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 200
    mock_request.assert_called_once_with("POST", "/v1/pixel/providers/save", payload=payload, timeout=10)

def test_explicit_credential_changes_forward_once_without_response_leak(client, mock_request):
    mock_request.return_value = DEFAULT_CONFIG
    payload = {"expectedRevision": 0, "document": DEFAULT_CONFIG,
               "credentialChanges": {"tower": {"action": "set", "value": "synthetic-private-key"}}}
    response = client.post("/api/pixel/providers/save", json=payload,
                           headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 200
    assert "synthetic-private-key" not in response.text
    mock_request.assert_called_once_with("POST", "/v1/pixel/providers/save", payload=payload, timeout=10)

@pytest.mark.parametrize("changes", [None, [], "bad", {str(i): {} for i in range(33)}])
def test_bad_credential_envelope_never_calls_host(client, mock_request, changes):
    response = client.post("/api/pixel/providers/save",
        json={"expectedRevision": 0, "document": DEFAULT_CONFIG, "credentialChanges": changes},
        headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 400
    mock_request.assert_not_called()

def test_post_bad_json(client):
    resp = client.post("/api/pixel/providers/save", content=b"not json", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 400

def test_post_duplicate_keys(client):
    # JSON parser with object_pairs_hook detects duplicates
    resp = client.post("/api/pixel/providers/save", content=b'{"expectedRevision":0,"expectedRevision":1}', headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 400

def test_post_nan(client):
    resp = client.post("/api/pixel/providers/save", content=b'{"expectedRevision": NaN, "document": {}}', headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 400

def test_post_large_number(client):
    resp = client.post("/api/pixel/providers/save", content=b'{"expectedRevision": 0, "document": {"x":1e999}}', headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 400

def test_post_missing_key(client):
    resp = client.post("/api/pixel/providers/save", json={"expectedRevision": 0}, headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 400

def test_post_unknown_key(client):
    resp = client.post("/api/pixel/providers/save", json={"expectedRevision": 0, "document": {}, "extra": 1}, headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 400

def test_post_oversize(client):
    # 256KB limit
    big_doc = {"expectedRevision": 0, "document": {"x": "a" * 300000}}
    resp = client.post("/api/pixel/providers/save", json=big_doc, headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 413

def test_upstream_400(client, mock_request):
    from host_agent_client import AgentHTTPError
    mock_request.side_effect = AgentHTTPError(400, "Bad")
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 400
    assert "Bad" not in resp.text

def test_upstream_503(client, mock_request):
    from host_agent_client import AgentUnavailable
    mock_request.side_effect = AgentUnavailable()
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 503

def test_upstream_502_protocol(client, mock_request):
    from host_agent_client import AgentProtocolError
    mock_request.side_effect = AgentProtocolError()
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 502

def test_bad_public_config_unknown_key(client, mock_request):
    bad_config = {**DEFAULT_CONFIG, "unknown": 1}
    mock_request.return_value = bad_config
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 502

def test_bad_public_config_bad_id(client, mock_request):
    bad_config = {**DEFAULT_CONFIG, "providers": [{"id": "Bad ID", "label": "L", "kind": "local", "baseUrl": "http://localhost/v1", "model": "m", "contextTokens": 1000, "maxOutputTokens": 100, "supportsTools": False, "supportsVision": False, "reasoning": False, "enabled": True, "hasCredential": False}]}
    mock_request.return_value = bad_config
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 502

def test_bad_public_config_cloud_no_auth(client, mock_request):
    provider = {"id": "p1", "label": "L", "kind": "cloud", "baseUrl": "https://example.test/v1", "model": "m", "contextTokens": 1000, "maxOutputTokens": 100, "supportsTools": False, "supportsVision": False, "reasoning": False, "enabled": True, "hasCredential": True}
    bad_config = {**DEFAULT_CONFIG, "enabled": True, "providers": [provider], "roles": {"leader": "p1", "backups": [], "advisor": None, "handoff": None}, "policy": {"allowCloud": False, "maxAttempts": 3, "deadlineSeconds": 120}}
    mock_request.return_value = bad_config
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 502

def test_valid_inactive_cloud(client, mock_request):
    provider = {"id": "p1", "label": "L", "kind": "cloud", "baseUrl": "https://example.test/v1", "model": "m", "contextTokens": 1000, "maxOutputTokens": 100, "supportsTools": False, "supportsVision": False, "reasoning": False, "enabled": True, "hasCredential": True}
    config = {**DEFAULT_CONFIG, "enabled": False, "providers": [provider], "roles": {"leader": "p1", "backups": [], "advisor": None, "handoff": None}, "policy": {"allowCloud": False, "maxAttempts": 3, "deadlineSeconds": 120}}
    mock_request.return_value = config
    resp = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 200

def test_normalize_public_valid():
    provider = {"id": "p1", "label": "L", "kind": "local", "baseUrl": "http://localhost/v1", "model": "m", "contextTokens": 1000, "maxOutputTokens": 100, "supportsTools": False, "supportsVision": False, "reasoning": False, "enabled": True, "hasCredential": False}
    config = {**DEFAULT_CONFIG, "providers": [provider], "roles": {"leader": "p1", "backups": [], "advisor": None, "handoff": None}}
    result = normalize_public(config)
    assert result["providers"][0]["id"] == "p1"

def test_normalize_public_strict_fail():
    with pytest.raises(ValueError):
        normalize_public({"schemaVersion": 1, "revision": 0, "enabled": False, "providers": [], "roles": {"leader": None, "backups": [], "advisor": None, "handoff": None}, "policy": {"allowCloud": False, "maxAttempts": 3, "deadlineSeconds": 120}, "extra": 1})

def test_host_credential_url_never_reflected(client, mock_request):
    provider = {"id": "p1", "label": "L", "kind": "local",
        "baseUrl": "https://example.test/v1", "model": "m", "contextTokens": 1000,
        "maxOutputTokens": 100, "supportsTools": False, "supportsVision": False,
        "reasoning": False, "enabled": True, "hasCredential": False}
    valid = {**DEFAULT_CONFIG, "providers": [provider]}
    normalize_public(valid)
    bad = copy.deepcopy(valid)
    bad["providers"][0]["baseUrl"] = "https://owner:private-sentinel@example.test/v1"
    mock_request.return_value = bad
    response = client.get("/api/pixel/providers", headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 502
    assert "private-sentinel" not in response.text


def test_active_provider_health_online(client, mock_request):
    mock_request.return_value = {"status": "online", "models": 2}
    resp = client.get("/api/pixel/providers/health", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "online", "models": 2}
    mock_request.assert_called_once_with("GET", "/v1/pixel/providers/health", timeout=12)

def test_active_provider_health_offline(client, mock_request):
    mock_request.side_effect = AgentUnavailable()
    resp = client.get("/api/pixel/providers/health", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "unavailable"}


@pytest.mark.parametrize("value", [
    {}, None, {"status": "online", "models": True}, {"status": "online", "models": -1},
    {"status": "online", "models": 2, "credential": "private-sentinel"},
    {"status": "online", "models": 0}, {"status": "offline", "error": "private-sentinel"},
])
def test_health_never_trusts_malformed_or_secret_host_payload(client, mock_request, value):
    mock_request.return_value = value
    response = client.get("/api/pixel/providers/health", headers={"Authorization": "Bearer test-key-12345"})
    assert response.json() == {"status": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "private-sentinel" not in response.text


def test_health_requires_owner_auth_before_probe(client, mock_request):
    assert client.get("/api/pixel/providers/health").status_code == 401
    mock_request.assert_not_called()


@pytest.mark.parametrize("status", ["offline", "inactive", "unavailable"])
def test_health_preserves_truthful_host_status(client, mock_request, status):
    mock_request.return_value = {"status": status}
    response = client.get("/api/pixel/providers/health", headers={"Authorization": "Bearer test-key-12345"})
    assert response.json() == {"status": status}

