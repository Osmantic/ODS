from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from routers import pixel_approval_terminal as terminal
from security import verify_api_key


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        terminal, "session_is_valid", lambda value: value == "private-session"
    )
    monkeypatch.setattr(
        terminal,
        "async_request_json",
        AsyncMock(
            return_value={"session": "a" * 64, "state": "running", "nextSequence": 0}
        ),
    )
    app = FastAPI()
    app.include_router(terminal.router)
    app.dependency_overrides[verify_api_key] = lambda: "server-key"
    with TestClient(app, base_url="http://localhost") as client:
        client.cookies.set("ods-dashboard-session", "private-session")
        yield client


def post(client, body, **headers):
    return client.post(
        "/api/pixel/approval-terminal",
        json=body,
        headers={
            "Origin": "http://localhost",
            "Sec-Fetch-Site": "same-origin",
            **headers,
        },
    )


def test_signed_browser_only_fixed_host_route(client):
    value = post(
        client,
        {"action": "start", "job": "ops-1790800000000-" + "a" * 12, "plan": "b" * 64},
    )
    assert value.status_code == 200 and value.headers["cache-control"] == "no-store"
    call = terminal.async_request_json.call_args
    assert call.args == ("POST", "/v1/pixel/approval-terminal")
    assert len(call.kwargs["payload"]["owner"]) == 64
    assert "private-session" not in str(call)


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null"},
        {"Origin": "http://localhost/forged"},
    ],
)
def test_cross_origin_never_reaches_host(client, headers):
    assert (
        post(
            client, {"action": "poll", "session": "a" * 64, "cursor": 0}, **headers
        ).status_code
        == 403
    )
    terminal.async_request_json.assert_not_awaited()


def test_api_auth_alone_cannot_open_human_channel(client):
    client.cookies.clear()
    result = post(client, {"action": "start", "job": "x", "plan": "y"})
    assert result.status_code == 401 and result.headers["x-ods-sign-in"] == "required"
    terminal.async_request_json.assert_not_awaited()


def test_invalid_input_never_echoes_secret_in_validation_response(client):
    secret = "private-password-DO-NOT-LOG"
    result = post(
        client,
        {
            "action": "input",
            "session": "a" * 64,
            "sequence": 0,
            "line": secret,
            "command": "id",
        },
    )
    assert result.status_code == 400 and secret not in result.text
    terminal.async_request_json.assert_not_awaited()


def test_real_host_http_route_is_authenticated_bounded_and_does_not_log_input(
    monkeypatch, caplog
):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    from test_host_agent import _mod

    class Manager:
        def __init__(self):
            self.seen = []

        def request(self, body):
            self.seen.append(body)
            return {"accepted": True, "nextSequence": 1}

    manager = Manager()
    monkeypatch.setattr(_mod, "_approval_terminals", manager)
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "qa-private-key")
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "POST",
            "/v1/pixel/approval-terminal",
            body="{}",
            headers={"Content-Type": "application/json"},
        )
        result = connection.getresponse()
        result.read()
        assert result.status in {401, 403}
        connection.close()
        secret = "private-terminal-password"
        import json

        body = json.dumps(
            {
                "action": "input",
                "owner": "a" * 64,
                "session": "b" * 64,
                "sequence": 0,
                "line": secret,
            }
        )
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "POST",
            "/v1/pixel/approval-terminal",
            body=body,
            headers={
                "Authorization": "Bearer qa-private-key",
                "Content-Type": "application/json",
            },
        )
        result = connection.getresponse()
        response = result.read()
        connection.close()
        assert result.status == 200 and secret.encode() not in response
        assert manager.seen[0]["line"] == secret
        assert secret not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_real_dashboard_login_cookie_reaches_terminal_without_auth_mocks(
    tmp_path, monkeypatch
):
    import dashboard_password
    import security
    from routers import dashboard_session

    monkeypatch.setattr(dashboard_password, "PASSWORD_FILE", tmp_path / "password.json")
    dashboard_password.save("isolated-test-password")
    host = AsyncMock(return_value={"session": "a" * 64, "state": "running"})
    monkeypatch.setattr(terminal, "async_request_json", host)
    app = FastAPI()
    app.include_router(dashboard_session.router)
    app.include_router(terminal.router)
    with TestClient(app, base_url="http://localhost") as browser:
        login = browser.post(
            "/api/auth/dashboard-session/login",
            json={"password": "isolated-test-password"},
        )
        assert login.status_code == 200
        assert browser.get("/api/auth/dashboard-session/verify").status_code == 204
        headers = {
            "Authorization": "Bearer " + security.DASHBOARD_API_KEY,
            "Origin": "http://localhost",
            "Sec-Fetch-Site": "same-origin",
        }
        body = {
            "action": "start",
            "job": "ops-1790800000000-" + "a" * 12,
            "plan": "b" * 64,
        }
        assert (
            browser.post(
                "/api/pixel/approval-terminal", json=body, headers=headers
            ).status_code
            == 200
        )
        host.assert_awaited_once()
        host.reset_mock()
        # Genuine rotation invalidates the old signature, even with current API auth.
        dashboard_password.save("rotated-test-password")
        assert (
            browser.post(
                "/api/pixel/approval-terminal", json=body, headers=headers
            ).status_code
            == 401
        )
        host.assert_not_awaited()
