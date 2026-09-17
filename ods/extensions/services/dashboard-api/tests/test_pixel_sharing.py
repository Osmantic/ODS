import copy
import json
import security
import pytest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import pixel_sharing as api
from pixel_sharing_public import normalize_sharing_response


# --- Fixtures ---

@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(api.router)
    return app

@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setattr(security, 'DASHBOARD_API_KEY', 'test-key-12345')
    with TestClient(app) as c:
        yield c

@pytest.fixture
def mock_request():
    with patch.object(api, 'request_agent_json', new_callable=AsyncMock) as m:
        yield m

@pytest.fixture
def base_host_response():
    return {
        "configuration": {
            "schemaVersion": 1,
            "revision": 0,
            "enabled": False,
            "devices": []
        },
        "activeRoute": None,
        "transport": {
            "mode": "loopback-only",
            "defaultPort": 4005,
            "port": 4005
        },
        "runtime": {
            "status": "not-probed"
        }
    }

@pytest.fixture
def device_fixture():
    return {
        "id": "device-" + "a" * 16,
        "label": "Laptop",
        "catalogId": "glm",
        "runtimeModelId": "GLM",
        "createdAt": 100,
        "expiresAt": 3700,
        "revoked": False,
        "maxConcurrent": 1,
        "maxOutputTokens": 64,
        "deadlineSeconds": 60,
        "requestsPerMinute": 20
    }

# --- Tests ---

class TestPixelSharingRouter:

    def test_get_unauthenticated(self, client):
        resp = client.get("/api/pixel/inference-sharing")
        assert resp.status_code == 401

    def test_get_unknown_action(self, client):
        headers = {"Authorization": "Bearer test-key-12345"}
        resp = client.post("/api/pixel/inference-sharing/unknown", headers=headers)
        assert resp.status_code == 404

    def test_get_success(self, client, mock_request, base_host_response):
        mock_request.return_value = base_host_response
        headers = {"Authorization": "Bearer test-key-12345"}
        resp = client.get("/api/pixel/inference-sharing", headers=headers)

        assert resp.status_code == 200
        assert resp.headers.get("Cache-Control") == "no-store"
        assert resp.json() == base_host_response

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args.args[0] == 'GET'
        assert call_args.args[1] == '/v1/pixel/inference-sharing'
        assert call_args.kwargs['timeout'] == 10

    def test_post_issue_success(self, client, mock_request, base_host_response, device_fixture):
        base_host_response['configuration'].update(revision=1, devices=[device_fixture])
        base_host_response.update(model='ods/shared', credential={'id':device_fixture['id'], 'key':'ods_infer_'+'b'*64})
        mock_request.return_value = base_host_response
        headers = {"Authorization": "Bearer test-key-12345"}
        payload = {
            "expectedRevision": 0,
            "settings": {"key": "value"}
        }
        resp = client.post("/api/pixel/inference-sharing/issue", json=payload, headers=headers)

        assert resp.status_code == 200
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args.args[0] == 'POST'
        assert call_args.args[1] == '/v1/pixel/inference-sharing/issue'
        assert call_args.kwargs['payload'] == payload

    def test_post_enable_success(self, client, mock_request, base_host_response):
        mock_request.return_value = base_host_response
        headers = {"Authorization": "Bearer test-key-12345"}
        payload = {
            "expectedRevision": 0,
            "enabled": True
        }
        resp = client.post("/api/pixel/inference-sharing/enable", json=payload, headers=headers)

        assert resp.status_code == 200
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args.args[1] == '/v1/pixel/inference-sharing/enable'
        assert call_args.kwargs['payload'] == payload

    def test_post_revoke_success(self, client, mock_request, base_host_response):
        mock_request.return_value = base_host_response
        headers = {"Authorization": "Bearer test-key-12345"}
        payload = {
            "expectedRevision": 0,
            "deviceId": "device-123"
        }
        resp = client.post("/api/pixel/inference-sharing/revoke", json=payload, headers=headers)

        assert resp.status_code == 200
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args.args[1] == '/v1/pixel/inference-sharing/revoke'
        assert call_args.kwargs['payload'] == payload

    @pytest.mark.parametrize("status, expected_code", [
        (400, 400),
        (409, 409),
        (413, 413),
        (503, 503),
        (500, 502),
        (404, 502),
    ])
    def test_host_errors_mapping(self, client, mock_request, status, expected_code):
        from routers.pixel_sharing import AgentHTTPError
        mock_request.side_effect = AgentHTTPError(status, 'private-sentinel')
        headers = {"Authorization": "Bearer test-key-12345"}

        resp = client.get("/api/pixel/inference-sharing", headers=headers)
        assert resp.status_code == expected_code
        # Ensure no raw text from sentinel is leaked
        assert 'private-sentinel' not in resp.text

    def test_post_missing_auth(self, client):
        resp = client.post("/api/pixel/inference-sharing/issue", json={})
        assert resp.status_code == 401

    @pytest.mark.parametrize("payload", [
        {"expectedRevision": 0, "settings": {"a": 1}, "extra": "field"}, # Unknown field
        {"expectedRevision": "string", "settings": {}}, # Bool/Str revision instead of int
        {"expectedRevision": 0, "settings": {"a": float('nan')}}, # NaN
    ])
    def test_post_validation_failures(self, client, mock_request, payload):
        headers = {"Authorization": "Bearer test-key-12345"}
        resp = client.post("/api/pixel/inference-sharing/issue", content=json.dumps(payload), headers=headers)
        assert resp.status_code == 400
        mock_request.assert_not_called()

    def test_post_large_payload(self, client, mock_request):
        headers = {"Authorization": "Bearer test-key-12345"}
        # > 256KiB
        large_data = {"expectedRevision": 0, "settings": {"data": "x" * (256 * 1024 + 1)}}
        resp = client.post("/api/pixel/inference-sharing/issue", json=large_data, headers=headers)
        assert resp.status_code == 413
        mock_request.assert_not_called()

    def test_get_response_with_key_rejected(self, client, mock_request, base_host_response):
        # Response containing key/credential/tokenHash should be rejected 502
        resp_data = copy.deepcopy(base_host_response)
        resp_data['configuration']['devices'] = [{"id": "d1", "credential": {"key": "secret"}}]
        mock_request.return_value = resp_data

        headers = {"Authorization": "Bearer test-key-12345"}
        resp = client.get("/api/pixel/inference-sharing", headers=headers)

        assert resp.status_code == 502
        assert 'private-sentinel' not in resp.text

    def test_issue_response_key_only_in_credential(self, client, mock_request, base_host_response, device_fixture):
        # Issue adds credential with key. Key must only be in credential.
        resp_data = copy.deepcopy(base_host_response)
        device = copy.deepcopy(device_fixture)
        resp_data['credential'] = {"id": device['id'], "key": "ods_infer_" + "b" * 64}
        resp_data['model'] = 'ods/shared'
        resp_data['configuration']['devices'] = [device]
        resp_data['configuration']['revision'] = 1

        mock_request.return_value = resp_data
        headers = {"Authorization": "Bearer test-key-12345"}
        payload = {"expectedRevision": 0, "settings": {}}

        resp = client.post("/api/pixel/inference-sharing/issue", json=payload, headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        # Verify key is present in credential
        assert data['credential']['key'].startswith("ods_infer_")
        # Verify key is not elsewhere (simple check on root keys)
        assert 'key' not in data

    def test_normalize_sharing_response_deepcopy(self, base_host_response):
        original = copy.deepcopy(base_host_response)
        result = normalize_sharing_response(base_host_response)

        # Modify original, result should be independent
        base_host_response['configuration']['revision'] = 999
        assert result['configuration']['revision'] == 0
        assert result == original

    def test_normalize_sharing_response_invalid_bounds(self, base_host_response):
        # Wrong integer bounds
        invalid_resp = copy.deepcopy(base_host_response)
        invalid_resp['configuration']['schemaVersion'] = 0 # Assuming 1 is required
        with pytest.raises(ValueError):
            normalize_sharing_response(invalid_resp)

    def test_normalize_sharing_response_unexpected_fields(self, base_host_response):
        # Unexpected nested fields
        invalid_resp = copy.deepcopy(base_host_response)
        invalid_resp['configuration']['unexpected'] = "field"
        with pytest.raises(ValueError):
            normalize_sharing_response(invalid_resp)

    def test_normalize_sharing_response_exact_keys(self, base_host_response):
        # Exact root keys check
        result = normalize_sharing_response(base_host_response)
        expected_keys = {"configuration", "activeRoute", "transport", "runtime"}
        assert set(result.keys()) == expected_keys

    def test_full_app_registration_unauthenticated_get(self, test_client):
        # Using existing fixture test_client as requested
        resp = test_client.get("/api/pixel/inference-sharing")
        assert resp.status_code == 401
        assert resp.status_code != 404

    @pytest.mark.parametrize('action', ['start', 'stop'])
    def test_lifecycle_returns_202_and_only_forwards_revision(self, client, mock_request, base_host_response, action):
        base_host_response['runtime']['status'] = 'starting'
        base_host_response['configuration']['revision'] = 1
        mock_request.return_value = base_host_response
        response = client.post('/api/pixel/inference-sharing/' + action,
            json={'expectedRevision':0}, headers={'Authorization':'Bearer test-key-12345'})
        assert response.status_code == 202
        assert response.headers['cache-control'] == 'no-store'
        mock_request.assert_awaited_once_with('POST', '/v1/pixel/inference-sharing/' + action,
            payload={'expectedRevision':0}, timeout=10)

    @pytest.mark.parametrize('action', ['start', 'stop'])
    def test_lifecycle_rejects_injected_command(self, client, mock_request, action):
        response = client.post('/api/pixel/inference-sharing/' + action,
            json={'expectedRevision':0,'command':'docker restart anything'},
            headers={'Authorization':'Bearer test-key-12345'})
        assert response.status_code == 400
        mock_request.assert_not_called()
