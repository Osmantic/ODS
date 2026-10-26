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
                          lifecycle_path=tmp_path / "connection.json", now=NOW, owners=owners,
                          broker_state=tmp_path / "broker")
    return dict(status.validate_public(value), catalog=catalog.public_catalog())


def flag(monkeypatch, value):
    """The owner's PORTAL_CONNECTED_APPS_ENABLED; None leaves it unset."""
    monkeypatch.setattr(api, "read_live_env_value", lambda key, default="":
                        default if value is None or key != "PORTAL_CONNECTED_APPS_ENABLED" else value)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    import security
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-key-12345")
    flag(monkeypatch, "true")
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
    assert test_client.get("/api/pixel/connections/enabled").status_code == 401


@pytest.mark.parametrize("value,enabled", [(None, True), ("true", True), (" TRUE ", True), ("false", False),
                                           (" False ", False), ("", True)])
def test_settings_learns_whether_connected_apps_are_on_without_asking_the_runtime(client, mock_request, monkeypatch,
                                                                                   value, enabled):
    flag(monkeypatch, value)
    assert client.get("/api/pixel/connections/enabled").status_code == 401
    response = client.get("/api/pixel/connections/enabled", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"enabled": enabled}
    assert response.headers["cache-control"] == "no-store"
    mock_request.assert_not_called()


def test_status_is_hidden_while_the_owner_has_connected_apps_off(client, mock_request, monkeypatch):
    flag(monkeypatch, "false")
    assert client.get("/api/pixel/connections", headers=AUTH).status_code == 404
    mock_request.assert_not_called()


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


def test_why_the_last_sign_in_did_not_connect_reaches_settings(client, mock_request, tmp_path):
    reply = host_reply(tmp_path, connected=False)
    reply["connectors"]["google"]["pending"] = {"action": "connect", "phase": "failed", "expiresAt": None,
                                                "error": "access-denied", "since": "2026-10-25T20:30:00Z"}
    mock_request.return_value = copy.deepcopy(reply)
    response = client.get("/api/pixel/connections", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["connectors"]["google"]["pending"]["error"] == "access-denied"
    reply["connectors"]["google"]["pending"]["error"] = None
    mock_request.return_value = reply
    assert client.get("/api/pixel/connections", headers=AUTH).status_code == 502


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
CHANGE_ID = "calendar-1761500000000-0badc0de"
# Calendar changes Portal suggested, as root lists them for the owner.
WAITING = {"proposalId": CHANGE_ID, "sha256": "c" * 64, "state": "waiting", "action": "create",
           "createdAt": "2026-10-26T15:00:00.000Z", "event": None, "tooLarge": False,
           "change": {"summary": "Dentist", "start": "2026-10-27T15:00:00-05:00", "end": "2026-10-27T16:00:00-05:00",
                      "allDay": False, "timeZone": "America/Chicago", "location": "12 Main St",
                      "description": "Bring\nthe form", "attendees": ["pat@example.com"], "sendUpdates": "all"}}
MOVE = {"proposalId": "calendar-1761500000001-0badc0de", "sha256": "d" * 64, "state": "unconfirmed",
        "action": "update", "createdAt": None, "tooLarge": False,
        "change": {"summary": None, "start": "2026-10-27", "end": "2026-10-28", "allDay": True, "timeZone": None,
                   "location": None, "description": None, "attendees": None, "sendUpdates": "none"},
        "event": {"title": "Team sync", "start": "2026-10-27T10:00:00-05:00", "end": None, "allDay": False}}
UNREADABLE = {"proposalId": "calendar-1761500000002-0badc0de", "sha256": None, "state": "invalid", "action": None,
              "createdAt": None, "change": None, "event": None, "tooLarge": False}
TOO_LARGE = dict(WAITING, proposalId="calendar-1761500000003-0badc0de", sha256=None, change=None, tooLarge=True)
CHANGES = {"proposals": [WAITING, MOVE, UNREADABLE, TOO_LARGE], "more": 2}
ACTIONS = [
    ("connect", CONNECT, "begin", {"flowId": FLOW, "expiresAt": "2026-10-25T20:45:00Z", "redirect": "loopback",
                                   "authUrl": "https://accounts.google.com/o/oauth2/auth?client_id=x&state=y"}, 90),
    ("complete", {"flowId": FLOW, "address": "http://127.0.0.1:53682/?state=s&code=c"}, "complete", CONNECTED, 180),
    ("cancel", {"flowId": FLOW}, "cancel", {"outcome": "cancelled"}, 45),
    ("disconnect", {}, "disconnect", {"outcome": "disconnected", "revoked": True}, 180),
    ("sync", {}, "sync", {"outcome": "started"}, 45),
    ("finish", {}, "finish", {"outcome": "applied"}, 330),
    ("changes/approve", {"proposalId": CHANGE_ID, "sha256": "c" * 64}, "approve",
     {"proposalId": CHANGE_ID, "outcome": "applied"}, 180),
    ("changes/decline", {"proposalId": CHANGE_ID}, "decline", {"proposalId": CHANGE_ID, "outcome": "declined"}, 45),
    ("changes/check", {"proposalId": CHANGE_ID}, "check", {"proposalId": CHANGE_ID, "outcome": "unconfirmed"}, 180),
    ("activity/undo", {"proposalId": CHANGE_ID}, "undo", {"proposalId": CHANGE_ID, "outcome": "applied"}, 180),
]


def test_main_registers_the_action_routes(test_client):
    assert test_client.post("/api/pixel/connections/google/sync", json={}).status_code == 401


@pytest.mark.parametrize("action,body,host,reply,timeout", ACTIONS)
def test_actions_are_hidden_while_the_owner_has_connected_apps_off(client, mock_request, monkeypatch,
                                                                action, body, host, reply, timeout):
    flag(monkeypatch, "false")
    response = client.post(f"/api/pixel/connections/google/{action}", json=body, headers=AUTH)
    assert response.status_code == 404
    mock_request.assert_not_called()


@pytest.mark.parametrize("action,body,host,reply,timeout", ACTIONS)
def test_each_action_reaches_its_host_route_with_exactly_the_owners_choices(client, mock_request,
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
def test_refusals_say_what_happened_and_what_to_do(client, mock_request, failure, status_code, code,
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
    ("changes/approve", {"proposalId": CHANGE_ID}),
    ("changes/approve", {"proposalId": CHANGE_ID, "sha256": "C" * 64}),
    ("changes/approve", {"proposalId": "calendar-1-0badc0de", "sha256": "c" * 64}),
    ("changes/decline", {"proposalId": CHANGE_ID, "sha256": "c" * 64}),
    ("changes/check", {}),
])
def test_requests_outside_the_contract_never_reach_the_host(client, mock_request, action, body):
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
    ("changes/approve", {"proposalId": CHANGE_ID, "sha256": "c" * 64}, {"proposalId": "private-sentinel",
                                                                        "outcome": "applied"}),
    ("changes/decline", {"proposalId": CHANGE_ID}, {"proposalId": CHANGE_ID, "outcome": "private-sentinel"}),
])
def test_a_reply_of_any_other_shape_is_refused_without_echoing_it(client, mock_request, action, body, reply):
    mock_request.return_value = reply
    response = client.post(f"/api/pixel/connections/google/{action}", json=body, headers=AUTH)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "connection-change-unconfirmed"
    assert "private-sentinel" not in response.text


def test_the_calendar_changes_portal_suggested_reach_the_owner_exactly(client, mock_request):
    assert client.get("/api/pixel/connections/google/changes").status_code == 401
    mock_request.return_value = copy.deepcopy(CHANGES)
    response = client.get("/api/pixel/connections/google/changes", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == CHANGES and response.headers["cache-control"] == "no-store"
    mock_request.assert_awaited_once_with("POST", "/v1/pixel/connections/google/changes", payload={}, timeout=45)


ACTIVITY = {"changes": [
    {"proposalId": CHANGE_ID, "action": "create", "title": "Focus time", "start": "2026-10-27T15:00:00-05:00",
     "end": "2026-10-27T16:00:00-05:00", "allDay": False, "previous": None, "appliedAt": "2026-10-26T15:00:01Z",
     "undo": "available"},
    {"proposalId": "calendar-1761500000001-0badc0de", "action": "update", "title": None, "start": "2026-10-28",
     "end": "2026-10-29", "allDay": True, "previous": {"start": "2026-10-27", "end": "2026-10-28", "allDay": True},
     "appliedAt": None, "undo": "done"}], "more": 1}


def test_what_portal_changed_by_itself_reaches_the_owner_exactly(client, mock_request):
    assert client.get("/api/pixel/connections/google/activity").status_code == 401
    mock_request.return_value = copy.deepcopy(ACTIVITY)
    response = client.get("/api/pixel/connections/google/activity", headers=AUTH)
    assert response.status_code == 200 and response.json() == ACTIVITY
    mock_request.assert_awaited_once_with("POST", "/v1/pixel/connections/google/activity", payload={}, timeout=45)
    for change in (lambda value: value["changes"][0].update(undo="private-sentinel"),
                   lambda value: value["changes"][0].update(action="delete"),
                   lambda value: value["changes"][1].update(previous={"start": "private-sentinel"})):
        reply = copy.deepcopy(ACTIVITY)
        change(reply)
        mock_request.return_value = reply
        refused = client.get("/api/pixel/connections/google/activity", headers=AUTH)
        assert refused.status_code == 502 and "private-sentinel" not in refused.text


def test_calendar_changes_are_hidden_while_the_owner_has_connected_apps_off(client, mock_request, monkeypatch):
    flag(monkeypatch, "false")
    assert client.get("/api/pixel/connections/google/changes", headers=AUTH).status_code == 404
    mock_request.assert_not_called()


@pytest.mark.parametrize("change", [
    lambda value: value["proposals"][0].update(sha256=None),
    lambda value: value["proposals"][0].update(token="private-sentinel"),
    lambda value: value["proposals"][0]["change"].update(attendees=["private-sentinel"] * 101),
    lambda value: value["proposals"][0]["change"].update(description="private-sentinel" * 2000),
    lambda value: value["proposals"][1].update(event={"title": "private-sentinel"}),
    lambda value: value["proposals"][2].update(action="create"),
    lambda value: value["proposals"][3].update(change=WAITING["change"]),
    lambda value: value.update(more=-1),
    lambda value: value.update(proposals=[WAITING] * 21),
])
def test_a_change_list_of_any_other_shape_is_refused_without_echoing_it(client, mock_request, change):
    reply = copy.deepcopy(CHANGES)
    change(reply)
    mock_request.return_value = reply
    response = client.get("/api/pixel/connections/google/changes", headers=AUTH)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "connection-change-unconfirmed"
    assert "private-sentinel" not in response.text


def test_every_calendar_refusal_tells_the_owner_what_to_do():
    for code in ("calendar-changes-off", "calendar-change-mismatch", "calendar-change-unknown", "calendar-change-invalid",
                 "calendar-change-unconfirmed", "calendar-change-not-made", "calendar-change-not-checkable",
                 "calendar-change-result-invalid", "calendar-change-unsafe", "calendar-undo-unavailable"):
        message, _needs_help = api.MESSAGES[code]
        assert message.endswith(".") and "error" not in message.lower(), code


def test_acting_on_a_change_refreshes_the_chat_line_at_once(client, mock_request, monkeypatch):
    monkeypatch.setattr(api, "_attention", {"until": 10 ** 9, "value": {"step": "changes-waiting"}})
    mock_request.return_value = {"proposalId": CHANGE_ID, "outcome": "declined"}
    assert client.post("/api/pixel/connections/google/changes/decline", json={"proposalId": CHANGE_ID},
                       headers=AUTH).status_code == 200
    assert api._attention["until"] == 0.0
    monkeypatch.setattr(api, "_attention", {"until": 10 ** 9, "value": None})
    mock_request.side_effect = AgentHTTPError(409, "calendar-change-mismatch")
    response = client.post("/api/pixel/connections/google/changes/approve",
                           json={"proposalId": CHANGE_ID, "sha256": "c" * 64}, headers=AUTH)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "calendar-change-mismatch"
    assert api._attention["until"] == 0.0


# ---------------------------------------------------------------- chat attention

@pytest.fixture
def fresh_attention(monkeypatch):
    monkeypatch.setattr(api, "_attention", {"until": 0.0, "value": None})


def with_google(reply, **changes):
    reply["connectors"]["google"].update(changes)
    return reply


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,expected", [
    ({"state": "needs-reconnect", "reason": "invalid-grant"},
     {"state": "needs-reconnect", "reason": "invalid-grant", "step": None, "waiting": 0}),
    ({"state": "missing-permission", "reason": "missing-scope"},
     {"state": "missing-permission", "reason": "missing-scope", "step": None, "waiting": 0}),
    ({"state": "behind", "reason": "sync-stopped"}, {"state": "behind", "reason": "sync-stopped", "step": None, "waiting": 0}),
    ({"waitingChanges": 2}, {"state": "connected", "reason": None, "step": "changes-waiting", "waiting": 2}),
    # Reconnecting comes first: an approval cannot be made without the sign-in.
    ({"state": "needs-reconnect", "reason": "invalid-grant", "waitingChanges": 1},
     {"state": "needs-reconnect", "reason": "invalid-grant", "step": None, "waiting": 1}),
    ({"state": "behind", "reason": "network"}, None),
    ({"state": "first-sync", "reason": None}, None),
    ({}, None),
    ({"pending": {"action": "connect", "phase": "activating", "expiresAt": None, "error": "runtime-busy",
                  "since": "2026-10-25T20:30:00Z"}},
     {"state": "connected", "reason": None, "step": "update-stopped", "waiting": 0}),
    ({"pending": {"action": "connect", "phase": "activating", "expiresAt": None, "error": None,
                  "since": "2026-10-25T20:30:00Z"}}, None),
])
async def test_chat_is_pointed_to_connected_apps_only_when_the_owner_must_act(monkeypatch, fresh_attention, tmp_path,
                                                                             changes, expected):
    flag(monkeypatch, "true")
    monkeypatch.setattr(api, "request_agent_json", AsyncMock(return_value=with_google(host_reply(tmp_path), **changes)))
    assert await api.owner_attention() == expected


@pytest.mark.asyncio
async def test_chat_attention_asks_the_runtime_at_most_once_a_minute_and_never_while_off(monkeypatch, fresh_attention,
                                                                                      tmp_path):
    flag(monkeypatch, "true")
    host = AsyncMock(return_value=with_google(host_reply(tmp_path), state="needs-reconnect", reason="sign-in-missing"))
    monkeypatch.setattr(api, "request_agent_json", host)
    clock = [1000.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    assert (await api.owner_attention())["state"] == "needs-reconnect"
    clock[0] += 59
    assert (await api.owner_attention())["state"] == "needs-reconnect"
    assert host.await_count == 1
    clock[0] += 2
    host.return_value = host_reply(tmp_path)
    assert await api.owner_attention() is None
    assert host.await_count == 2
    flag(monkeypatch, "false")
    clock[0] += 120
    assert await api.owner_attention() is None
    assert host.await_count == 2


@pytest.mark.asyncio
async def test_unknown_status_interrupts_no_chat(monkeypatch, fresh_attention, tmp_path):
    flag(monkeypatch, "true")
    for failure in (AgentUnavailable("private-sentinel"), AgentProtocolError("private-sentinel"),
                    AgentHTTPError(503, "connections-unavailable")):
        monkeypatch.setattr(api, "_attention", {"until": 0.0, "value": None})
        monkeypatch.setattr(api, "request_agent_json", AsyncMock(side_effect=failure))
        assert await api.owner_attention() is None
    monkeypatch.setattr(api, "_attention", {"until": 0.0, "value": None})
    monkeypatch.setattr(api, "request_agent_json", AsyncMock(return_value={"token": "private-sentinel"}))
    assert await api.owner_attention() is None
