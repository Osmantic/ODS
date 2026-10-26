"""Connected-apps status route: owner-only, content-free, and strict about its shape."""
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable
from routers import pixel_connections as api

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
from pixel_connections import catalog, status  # noqa: E402

AUTH = {"Authorization": "Bearer test-key-12345"}
NOW = datetime(2026, 10, 25, 20, 30, tzinfo=timezone.utc)


def host_reply(tmp_path, *, connected=True):
    record = tmp_path / "portal-connections.json"
    token = tmp_path / "google-token.json"
    broker = tmp_path / "status.json"
    if connected:
        record.write_text(json.dumps({
            "schemaVersion": 1, "kind": "ods-portal-connections", "updatedAt": "2026-10-25T20:00:00Z",
            "ownerTimeZone": "America/Chicago",
            "connectors": {"google": {
                "status": "connected", "account": "owner@example.com", "calendarId": "primary",
                "gmailWindowDays": 180, "cloudModels": True, "connectedAt": "2026-10-25T20:00:00Z",
                "capabilities": {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
                                 "gmail.read": True, "gmail.drafts": False}}}}))
        record.chmod(0o640)
        token.write_text("{}")
        source = {"enabled": True, "state": "ok", "errorClass": None, "httpStatus": None, "errorType": None,
                  "lastAttemptAt": "2026-10-25T20:29:00Z", "lastSuccessAt": "2026-10-25T20:29:00Z",
                  "failingSince": None, "recordCount": 3, "complete": True}
        broker.write_text(json.dumps({
            "schemaVersion": 1, "kind": "pixel-source-broker-status", "generatedAt": "2026-10-25T20:29:30Z",
            "startedAt": "2026-10-25T20:29:00Z", "grantedScopes": [catalog.CALENDAR_READ_SCOPE, catalog.GMAIL_READ_SCOPE],
            "sources": {"email": source, "calendar": source}, "boundary": {"contentFree": True, "credentialsIncluded": False}}))
    tmp_path.chmod(0o755)
    owners = (0, os.getuid()) if hasattr(os, "getuid") else (0,)
    value = status.report(surface="linux-systemd", record_path=record, token_path=token, status_path=broker,
                          lifecycle_path=tmp_path / "connection.json", now=NOW, owners=owners)
    return dict(status.validate_public(value), catalog=catalog.public_catalog())


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    import security
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-key-12345")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_request():
    with patch.object(api, "request_agent_json", new_callable=AsyncMock) as mocked:
        yield mocked


def test_status_requires_the_owner(client, mock_request):
    assert client.get("/api/pixel/connections").status_code == 401
    mock_request.assert_not_called()


def test_main_registers_the_connections_route(test_client):
    assert test_client.get("/api/pixel/connections").status_code == 401


def test_a_connected_account_is_reported_exactly(client, mock_request, tmp_path):
    reply = host_reply(tmp_path)
    mock_request.return_value = copy.deepcopy(reply)
    response = client.get("/api/pixel/connections", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == reply
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["connectors"]["google"]["state"] == "connected"
    mock_request.assert_awaited_once_with("GET", "/v1/pixel/connections", timeout=25)


def test_nothing_connected_is_reported(client, mock_request, tmp_path):
    mock_request.return_value = host_reply(tmp_path, connected=False)
    response = client.get("/api/pixel/connections", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["connectors"]["google"]["state"] == "not-connected"


@pytest.mark.parametrize("failure,expected", [
    (AgentUnavailable("private-sentinel"), 503),
    (AgentHTTPError(503, "private-sentinel", '{"error":"private-sentinel"}'), 503),
    (AgentHTTPError(500, "private-sentinel"), 502),
    (AgentProtocolError("private-sentinel"), 502),
])
def test_an_unavailable_runtime_gives_a_calm_answer(client, mock_request, failure, expected):
    mock_request.side_effect = failure
    response = client.get("/api/pixel/connections", headers=AUTH)
    assert response.status_code == expected
    assert "private-sentinel" not in response.text
    if expected == 503:
        assert "Try again in a moment" in response.json()["detail"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(secret="private-sentinel"),
    lambda value: value["connectors"]["google"]["sources"]["email"].update(summary="private-sentinel"),
    lambda value: value["connectors"]["google"].update(account=None),
    lambda value: value["connectors"]["google"].update(state="private-sentinel"),
    lambda value: value["connectors"]["google"].update(grantedScopes=["private-sentinel"]),
    lambda value: value["catalog"][0].update(token="private-sentinel"),
    lambda value: value.pop("catalog"),
])
def test_any_other_shape_is_refused_without_echoing_it(client, mock_request, tmp_path, change):
    reply = host_reply(tmp_path)
    change(reply)
    mock_request.return_value = reply
    response = client.get("/api/pixel/connections", headers=AUTH)
    assert response.status_code == 502
    assert "private-sentinel" not in response.text


# ---------------------------------------------------------------- owner actions

FLOW = "a" * 32
READ_BOTH = {"calendar.read": True, "calendar.write": False, "calendar.direct": False,
             "gmail.read": True, "gmail.drafts": False}
GOOGLE_CLIENT = {"installed": {
    "client_id": "123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com",
    "client_secret": "test-secret-test", "project_id": "ods-owner-calendar",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://localhost"]}}
CONNECT = {"client": GOOGLE_CLIENT, "capabilities": READ_BOTH, "cloudModels": False,
           "ownerTimeZone": "America/Chicago", "loginHint": None}
CONNECTED = {"outcome": "connected", "errorClass": None, "account": "owner@example.com",
             "capabilities": READ_BOTH, "missing": []}
ACTIONS = [
    ("connect", CONNECT, "begin", {"flowId": FLOW, "expiresAt": "2026-10-25T20:45:00Z", "redirect": "loopback",
                                   "authUrl": "https://accounts.google.com/o/oauth2/auth?client_id=x&state=y"}, 90),
    ("complete", {"flowId": FLOW, "address": "http://127.0.0.1:53682/?state=s&code=c"}, "complete", CONNECTED, 180),
    ("cancel", {"flowId": FLOW}, "cancel", {"outcome": "cancelled"}, 45),
    ("disconnect", {}, "disconnect", {"outcome": "disconnected", "revoked": True}, 180),
    ("sync", {}, "sync", {"outcome": "started"}, 45),
    ("finish", {}, "finish", {"outcome": "applied"}, 330),
]


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(api, "read_live_env_value", lambda key: "true" if key == "PORTAL_CONNECTED_APPS_ENABLED" else "")


def test_main_registers_the_action_routes(test_client):
    assert test_client.post("/api/pixel/connections/google/sync", json={}).status_code == 401


@pytest.mark.parametrize("action,body,host,reply,timeout", ACTIONS)
def test_actions_stay_hidden_until_connected_apps_are_turned_on(client, mock_request, monkeypatch,
                                                                action, body, host, reply, timeout):
    monkeypatch.setattr(api, "read_live_env_value", lambda key: "")
    response = client.post(f"/api/pixel/connections/google/{action}", json=body, headers=AUTH)
    assert response.status_code == 404
    mock_request.assert_not_called()


@pytest.mark.parametrize("action,body,host,reply,timeout", ACTIONS)
def test_each_action_reaches_its_host_route_with_exactly_the_owners_choices(client, mock_request, enabled,
                                                                            action, body, host, reply, timeout):
    assert client.post(f"/api/pixel/connections/google/{action}", json=body).status_code == 401
    mock_request.return_value = copy.deepcopy(reply)
    response = client.post(f"/api/pixel/connections/google/{action}", json=body, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == reply
    assert response.headers["cache-control"] == "no-store"
    mock_request.assert_awaited_once_with("POST", f"/v1/pixel/connections/google/{host}", payload=body,
                                          timeout=timeout)


@pytest.mark.parametrize("failure,status_code,code,needs_help", [
    (AgentHTTPError(409, "runtime-busy"), 409, "runtime-busy", False),
    (AgentHTTPError(409, "sign-in-flow-expired"), 409, "sign-in-flow-expired", False),
    (AgentHTTPError(400, "client-not-desktop"), 400, "client-not-desktop", False),
    (AgentHTTPError(409, "gateway-change-rolled-back"), 409, "gateway-change-rolled-back", True),
    (AgentHTTPError(503, "connections-unavailable"), 503, "connections-unavailable", True),
    (AgentHTTPError(409, "a-code-this-release-does-not-know"), 409, "a-code-this-release-does-not-know", True),
    (AgentHTTPError(500, "private sentinel text"), 502, "unknown", True),
    (AgentUnavailable("private-sentinel"), 503, "connections-unavailable", True),
    (AgentProtocolError("private-sentinel"), 502, "connection-change-unconfirmed", True),
])
def test_refusals_say_what_happened_and_what_to_do(client, mock_request, enabled, failure, status_code, code,
                                                   needs_help):
    mock_request.side_effect = failure
    response = client.post("/api/pixel/connections/google/finish", json={}, headers=AUTH)
    assert response.status_code == status_code
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"] and "private" not in response.text
    assert detail["help"] == (api.ODS_HELP_DISCORD_URL if needs_help else None)
    assert detail["message"] == api.MESSAGES.get(code, api.FALLBACK)[0]


@pytest.mark.parametrize("action,body", [
    ("connect", dict(CONNECT, extra=True)),
    ("connect", dict(CONNECT, capabilities={"calendar.read": True})),
    ("connect", dict(CONNECT, ownerTimeZone="not a zone")),
    ("connect", dict(CONNECT, loginHint="not-an-address")),
    ("connect", dict(CONNECT, client={"installed": {"client_secret": "x" * 5000}})),
    ("complete", {"flowId": "not-a-flow", "address": "http://127.0.0.1:53682/?code=c"}),
    ("complete", {"flowId": FLOW}),
    ("cancel", {"flowId": FLOW, "extra": True}),
    ("disconnect", {"connector": "google"}),
    ("finish", {"force": True}),
])
def test_requests_outside_the_contract_never_reach_the_host(client, mock_request, enabled, action, body):
    response = client.post(f"/api/pixel/connections/google/{action}", json=body, headers=AUTH)
    assert response.status_code == 422
    mock_request.assert_not_called()


@pytest.mark.parametrize("action,body,reply", [
    ("connect", CONNECT, {"flowId": FLOW, "expiresAt": "2026-10-25T20:45:00Z", "redirect": "loopback",
                          "authUrl": "https://example.com/o/oauth2/auth?private-sentinel"}),
    ("complete", {"flowId": FLOW, "address": "http://127.0.0.1:53682/?state=s&code=c"},
     dict(CONNECTED, account=None)),
    ("complete", {"flowId": FLOW, "address": "http://127.0.0.1:53682/?state=s&code=c"},
     dict(CONNECTED, token="private-sentinel")),
    ("disconnect", {}, {"outcome": "disconnected"}),
    ("sync", {}, {"outcome": "started", "revoked": False}),
])
def test_a_reply_of_any_other_shape_is_refused_without_echoing_it(client, mock_request, enabled, action, body, reply):
    mock_request.return_value = reply
    response = client.post(f"/api/pixel/connections/google/{action}", json=body, headers=AUTH)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "connection-change-unconfirmed"
    assert "private-sentinel" not in response.text
