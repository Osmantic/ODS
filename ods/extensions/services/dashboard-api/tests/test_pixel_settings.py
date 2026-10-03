"""Tests for pixel settings dashboard proxy."""
import copy
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import pixel_settings as api
from host_agent_client import AgentHTTPError, AgentUnavailable, AgentProtocolError

VALID_GET = {
    "configuration": {
        "schemaVersion": 1,
        "revision": 5,
        "preferences": {},
    },
    "runtime": {"status": "not-applied", "reason": "settings-runtime-not-integrated"},
}

VALID_POST_RESP = {
    "configuration": {
        "schemaVersion": 1,
        "revision": 1,
        "preferences": {"verbosity": "on"},
    },
    "runtime": {"status": "not-applied", "reason": "settings-runtime-not-integrated"},
}

def create_app():
    app = FastAPI()
    app.include_router(api.router)
    return app


@pytest.fixture
def client(monkeypatch):
    app = create_app()
    import security
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-key-12345")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_request_agent():
    with patch.object(api, "request_agent_json", new_callable=AsyncMock) as m:
        yield m


def test_get_unauthorized(client, mock_request_agent):
    resp = client.get("/api/pixel/settings")
    assert resp.status_code == 401
    mock_request_agent.assert_not_called()


def test_post_unauthorized(client, mock_request_agent):
    resp = client.post("/api/pixel/settings/save", json={})
    assert resp.status_code == 401
    mock_request_agent.assert_not_called()


def test_get_success(client, mock_request_agent):
    mock_request_agent.return_value = VALID_GET
    resp = client.get("/api/pixel/settings", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime"]["status"] == "not-applied"
    assert resp.headers.get("cache-control") == "no-store"


def test_post_success(client, mock_request_agent):
    mock_request_agent.return_value = VALID_POST_RESP
    payload = {"expectedRevision": 0, "changes": {"verbosity": "on"}}
    resp = client.post(
        "/api/pixel/settings/save",
        json=payload,
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 200
    mock_request_agent.assert_called_once_with("POST", "/v1/pixel/settings/save", payload=payload, timeout=10)
    assert resp.headers.get("cache-control") == "no-store"


def test_post_null_reset(client, mock_request_agent):
    mock_request_agent.return_value = copy.deepcopy(VALID_POST_RESP)
    mock_request_agent.return_value["configuration"]["preferences"]["verbosity"] = None
    payload = {"expectedRevision": 0, "changes": {"verbosity": None}}
    resp = client.post(
        "/api/pixel/settings/save",
        json=payload,
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 200


def test_post_wrong_revision(client, mock_request_agent):
    mock_request_agent.return_value = {
        "configuration": {"schemaVersion": 1, "revision": 99, "preferences": {}},
        "runtime": {"status": "not-applied", "reason": "settings-runtime-not-integrated"},
    }
    resp = client.post(
        "/api/pixel/settings/save",
        json={"expectedRevision": 0, "changes": {}},
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 502


def test_invalid_nested_private(client, mock_request_agent):
    bad = copy.deepcopy(VALID_POST_RESP)
    bad["configuration"]["preferences"]["__private"] = "leak"
    mock_request_agent.return_value = bad
    resp = client.post(
        "/api/pixel/settings/save",
        json={"expectedRevision": 0, "changes": {}},
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 502
    assert "leak" not in resp.text


def test_malformed_json(client, mock_request_agent):
    resp = client.post(
        "/api/pixel/settings/save",
        content=b"not json",
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 400
    mock_request_agent.assert_not_called()


def test_duplicate_keys(client, mock_request_agent):
    resp = client.post(
        "/api/pixel/settings/save",
        content=b'{"expectedRevision":0,"expectedRevision":1}',
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 400
    mock_request_agent.assert_not_called()


def test_bool_revision(client, mock_request_agent):
    resp = client.post(
        "/api/pixel/settings/save",
        json={"expectedRevision": True, "changes": {}},
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 400
    mock_request_agent.assert_not_called()


def test_unknown_control(client, mock_request_agent):
    resp = client.post(
        "/api/pixel/settings/save",
        json={"expectedRevision": 0, "changes": {"unknownControl": 1}},
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 400
    mock_request_agent.assert_not_called()


def test_nan_value(client, mock_request_agent):
    resp = client.post(
        "/api/pixel/settings/save",
        content=b'{"expectedRevision":0,"changes":{"temperature":NaN}}',
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 400
    mock_request_agent.assert_not_called()


def test_oversize(client, mock_request_agent):
    resp = client.post(
        "/api/pixel/settings/save",
        json={"expectedRevision": 0, "changes": {"verbosity": "a" * 300000}},
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 413
    mock_request_agent.assert_not_called()


def test_upstream_400_no_echo(client, mock_request_agent):
    mock_request_agent.side_effect = AgentHTTPError(400, "private-error")
    resp = client.post(
        "/api/pixel/settings/save",
        json={"expectedRevision": 0, "changes": {}},
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 400
    assert "private-error" not in resp.text
    assert mock_request_agent.call_count == 1


def test_upstream_503(client, mock_request_agent):
    mock_request_agent.side_effect = AgentUnavailable()
    resp = client.get("/api/pixel/settings", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 503


def test_upstream_protocol(client, mock_request_agent):
    mock_request_agent.side_effect = AgentProtocolError()
    resp = client.get("/api/pixel/settings", headers={"Authorization": "Bearer test-key-12345"})
    assert resp.status_code == 502


def test_main_registered(test_client):
    assert test_client.get("/api/pixel/settings").status_code == 401


def test_public_controls_match_host_contract_without_importing_host_at_runtime():
    import importlib.util
    from pathlib import Path
    from pixel_settings_public import CONTROLS, normalize_preferences
    path = Path(__file__).resolve().parents[4] / "bin/pixel_settings/contract.py"
    spec = importlib.util.spec_from_file_location("_pixel_settings_contract_parity", path)
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    assert CONTROLS == host.CONTROLS
    for name, definition in CONTROLS.items():
        values = [None, True, False, "invalid", [], {}]
        if definition[0] in ("integer", "number"):
            values += [definition[1], definition[2], definition[1] - 1, definition[2] + 1, float("nan"), 10**1000]
        elif definition[0] == "choice":
            values += list(definition[1:])
        for value in values:
            try:
                expected = host.validate_preferences({name: value})
            except ValueError:
                with pytest.raises(ValueError):
                    normalize_preferences({name: value})
            else:
                assert normalize_preferences({name: value}) == expected


def test_maximum_saved_revision_and_fresh_projection():
    from pixel_settings_public import normalize_response, normalize_edit
    value = copy.deepcopy(VALID_GET)
    value["configuration"].update(revision=2**53-1, preferences={"temperature": 1, "topP": 1})
    result = normalize_response(value)
    assert result == value
    result["configuration"]["preferences"]["temperature"] = 0
    assert value["configuration"]["preferences"]["temperature"] == 1
    with pytest.raises(ValueError):
        normalize_edit({"expectedRevision": 2**53-1, "changes": {}})


@pytest.mark.parametrize("changes", [{"verbosity": None}, {"temperature": 1}, {"compactionNotify": True}])
def test_post_missing_or_mismatching_changes_is_not_success(client, mock_request_agent, changes):
    mock_request_agent.return_value = copy.deepcopy(VALID_POST_RESP)
    response = client.post("/api/pixel/settings/save", json={"expectedRevision": 0, "changes": changes},
                           headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 502
    assert mock_request_agent.call_count == 1


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(private="do-not-echo"),
    lambda value: value["runtime"].update(private="do-not-echo"),
    lambda value: value["configuration"].update(private="do-not-echo"),
    lambda value: value["configuration"].update(schemaVersion=True),
    lambda value: value["configuration"].update(revision=True),
    lambda value: value["runtime"].update(status="applied"),
])
def test_invalid_host_envelope_is_never_exposed(client, mock_request_agent, mutation):
    value = copy.deepcopy(VALID_GET)
    mutation(value)
    mock_request_agent.return_value = value
    response = client.get("/api/pixel/settings", headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 502
    assert "do-not-echo" not in response.text

def test_export_workspace_snapshot(client, mock_request_agent):
    def mock_agent_response(method, path, **kwargs):
        if path == "/v1/pixel/settings":
            return copy.deepcopy(VALID_GET)
        if path == "/v1/pixel/providers":
            return {
                "schemaVersion": 1,
                "revision": 2,
                "enabled": True,
                "providers": [
                    {
                        "id": "my-local-ai",
                        "label": "Local AI",
                        "kind": "local",
                        "baseUrl": "http://127.0.0.1:8080/v1",
                        "model": "llama3",
                        "contextTokens": 4096,
                        "maxOutputTokens": 2048,
                        "supportsTools": False,
                        "supportsVision": False,
                        "reasoning": False,
                        "enabled": True,
                        "hasCredential": False
                    }
                ],
                "roles": {
                    "leader": "my-local-ai",
                    "backups": [],
                    "advisor": None,
                    "handoff": None
                },
                "policy": {
                    "deadlineSeconds": 60,
                    "allowCloud": True,
                    "maxAttempts": 3
                }
            }
        return {}
    mock_request_agent.side_effect = mock_agent_response

    resp = client.get(
        "/api/pixel/settings/export",
        headers={"Authorization": "Bearer test-key-12345"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("Content-Disposition") == 'attachment; filename="ods-workspace-snapshot.json"'
    data = resp.json()
    assert data["schemaVersion"] == 1
    assert data["type"] == "ods-workspace-snapshot"
    assert data["scope"] == "pixel-configuration"
    assert data["excludes"] == ["credentials", "conversations", "workspace-files"]
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "settings" in data
    assert "providers" in data
    assert data["providers"]["providers"][0]["id"] == "my-local-ai"


def test_export_requires_owner_auth(client, mock_request_agent):
    assert client.get("/api/pixel/settings/export").status_code == 401
    mock_request_agent.assert_not_called()


@pytest.mark.parametrize("provider", [
    {"credentialRef": "private-sentinel"},
    {"schemaVersion": 1, "apiKey": "private-sentinel"},
    None,
])
def test_export_rejects_unvalidated_provider_documents(client, mock_request_agent, provider):
    mock_request_agent.side_effect = [copy.deepcopy(VALID_GET), provider]
    response = client.get("/api/pixel/settings/export", headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 502
    assert "private-sentinel" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_export_does_not_publish_partial_snapshot_when_host_fails(client, mock_request_agent):
    mock_request_agent.side_effect = [copy.deepcopy(VALID_GET), AgentUnavailable()]
    response = client.get("/api/pixel/settings/export", headers={"Authorization": "Bearer test-key-12345"})
    assert response.status_code == 503
    assert "settings" not in response.json()
