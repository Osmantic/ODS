import hashlib
import json
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hermes_auth
import hermes_bridge
import session_signer
from routers import auth


def test_managed_credentials_are_stable_separate_and_do_not_write_config(tmp_path):
    config = tmp_path / "config.yaml"
    raw = b'model:\n  default: existing-model\n'
    config.write_bytes(raw)
    env = {"HERMES_DASHBOARD_SESSION_TOKEN": "fixture-installation-seed"}
    first = hermes_auth.settings(env, config)
    assert first == hermes_auth.settings(env, config)
    assert len({first["password"], first["secret"], env["HERMES_DASHBOARD_SESSION_TOKEN"]}) == 3
    assert first["managed"] is True
    assert config.read_bytes() == raw


@pytest.mark.parametrize("operator", [
    {"basic_auth": {"username": "owner", "password_hash": "scrypt$custom"}},
    {"oauth": {"client_id": "owned-provider"}},
    {"self_hosted": {"issuer": "https://owned.example"}},
])
def test_operator_config_never_gets_a_managed_password(tmp_path, operator):
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"dashboard": operator}))
    before = config.read_bytes()
    assert hermes_auth.settings({"HERMES_DASHBOARD_SESSION_TOKEN": "seed"}, config) is None
    assert config.read_bytes() == before


def test_explicit_password_and_hash_only_are_preserved(tmp_path):
    absent = tmp_path / "absent"
    env = {"HERMES_DASHBOARD_BASIC_AUTH_USERNAME": "owner", "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD": "owner-password"}
    assert hermes_auth.settings(env, absent) == {"username": "owner", "password": "owner-password", "managed": False}
    del env["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"]
    env["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"] = "hash-only"
    env["HERMES_DASHBOARD_SESSION_TOKEN"] = "seed"
    assert hermes_auth.settings(env, absent) is None


def test_upstream_empty_auth_defaults_do_not_disable_managed_restart(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({'dashboard': {'oauth': {'client_id': '', 'portal_url': ''},
        'basic_auth': {'username': '', 'password_hash': '', 'password': '', 'secret': 'operator-signing-secret', 'session_ttl_seconds': 0}}}))
    value = hermes_auth.settings({'HERMES_DASHBOARD_SESSION_TOKEN': 'seed'}, config)
    assert value['managed'] is True and value['secret'] == 'operator-signing-secret'


def test_bridge_policy_missing_stale_unknown_operator_fail_closed(tmp_path):
    path = tmp_path / "policy.json"
    env = {"HERMES_DASHBOARD_SESSION_TOKEN": "seed", "HERMES_AUTH_POLICY_PATH": str(path)}
    assert hermes_auth.settings(env) is None
    policy = {"schemaVersion": 1, "managed": True, "seedDigest": hashlib.sha256(b'seed').hexdigest()}
    path.write_text(json.dumps(policy))
    assert hermes_auth.settings(env)["managed"] is True
    for change in ({"schemaVersion": 2}, {"managed": False}, {"seedDigest": "stale"}):
        path.write_text(json.dumps({**policy, **change}))
        assert hermes_auth.settings(env) is None
    path.write_text(json.dumps(policy))
    env["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"] = "operator-override"
    assert hermes_auth.settings(env) is None


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(auth.router)
    session_signer._set_secret_for_tests("ods-auth-fixture")
    with TestClient(app) as value:
        yield value
    session_signer._set_secret_for_tests("")


@pytest.mark.parametrize("operator", [False, True])
def test_restored_seed_and_config_regenerate_auth_policy_before_upstream_start(tmp_path, monkeypatch, operator):
    import os
    home = tmp_path / "hermes"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text(json.dumps({"dashboard": {"basic_auth": {"password_hash": "operator-hash"}}} if operator else {}))
    policy = tmp_path / "policy"
    policy.mkdir()
    path = policy / "policy.json"
    path.write_text(json.dumps({"schemaVersion": 1, "managed": True, "seedDigest": hashlib.sha256(b'old-seed').hexdigest()}))
    for key in list(os.environ):
        if key.startswith('HERMES_DASHBOARD_') or key == 'HERMES_ODS_MANAGED_AUTH':
            monkeypatch.delenv(key)
    env = {'HERMES_HOME': str(home), 'HERMES_AUTH_POLICY_DIR': str(policy),
           'HERMES_AUTH_POLICY_PATH': str(path), 'HERMES_DASHBOARD_SESSION_TOKEN': 'restored-seed'}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert hermes_auth.settings(env) is None
    before = config.read_bytes()
    monkeypatch.setattr(os, 'getuid', lambda: 1000, raising=False)
    class UpstreamStarted(Exception):
        pass
    def exec_upstream(*args):
        receipt = json.loads(path.read_text())
        assert receipt == {'schemaVersion': 1, 'managed': not operator,
                           'seedDigest': hashlib.sha256(b'restored-seed').hexdigest()}
        assert not (policy / 'policy.json.tmp').exists()
        raise UpstreamStarted
    monkeypatch.setattr(os, 'execv', exec_upstream)
    with pytest.raises(UpstreamStarted):
        hermes_auth.bootstrap()
    assert config.read_bytes() == before
    value = hermes_auth.settings(env)
    assert (value is None) if operator else value['managed'] is True


def test_unauthorized_cannot_get_hermes_cookies(client, monkeypatch):
    login = AsyncMock()
    monkeypatch.setattr(hermes_bridge, "login_dashboard", login)
    response = client.get("/api/auth/hermes-session", follow_redirects=False)
    assert response.status_code == 401
    assert not response.headers.get("set-cookie")
    login.assert_not_called()


def test_launch_rejects_arbitrary_destination(client, monkeypatch):
    client.cookies.set("ods-session", session_signer.issue(ttl_seconds=60))
    login = AsyncMock()
    monkeypatch.setattr(hermes_bridge, "login_dashboard", login)
    response = client.get("/api/auth/hermes-session?next=https://evil.example", follow_redirects=False)
    assert response.status_code == 400
    login.assert_not_called()


def test_launch_only_relays_host_only_secure_allowlisted_cookies(client, monkeypatch):
    client.cookies.set("ods-session", session_signer.issue(ttl_seconds=60))
    monkeypatch.setattr(hermes_auth, "settings", lambda: {"username": "ods", "password": "never-return"})
    cookies = SimpleCookie()
    cookies.load('hermes_session_at=fixture; Domain=evil.example; Path=/unsafe; Max-Age=60; SameSite=Lax')
    cookies['unrelated'] = 'do-not-relay'
    monkeypatch.setattr(hermes_bridge, "login_dashboard", AsyncMock(return_value=list(cookies.values())))
    response = client.get("/api/auth/hermes-session", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/'
    header = response.headers['set-cookie']
    assert 'hermes_session_at=fixture' in header and 'HttpOnly' in header and 'Secure' in header
    assert 'Path=/' in header and 'Domain=' not in header and 'unrelated' not in header
    assert 'never-return' not in response.text + str(response.headers)
    assert response.headers['cache-control'] == 'no-store'


def test_hash_only_operator_uses_native_login_without_managed_cookie(client, monkeypatch):
    client.cookies.set("ods-session", session_signer.issue(ttl_seconds=60))
    monkeypatch.setattr(hermes_auth, "settings", lambda: None)
    login = AsyncMock()
    monkeypatch.setattr(hermes_bridge, "login_dashboard", login)
    response = client.get("/api/auth/hermes-session", follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/login'
    assert 'set-cookie' not in response.headers
    login.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,managed,legacy", [(404, True, True), (404, False, False), (401, True, False), (403, True, False), (500, True, False)])
async def test_login_only_downgrades_missing_legacy_route(status, managed, legacy):
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {"error": "fixture"}
    response = Response()
    response.status = status
    class Session:
        def post(self, url, **kwargs):
            assert url.endswith('/auth/password-login')
            assert kwargs['allow_redirects'] is False
            return response
    auth_value = {"username": "ods", "password": "fixture", "managed": managed}
    if legacy:
        assert await hermes_bridge.login_dashboard(Session(), auth_value) is None
    else:
        with pytest.raises(hermes_bridge.HermesUnavailable):
            await hermes_bridge.login_dashboard(Session(), auth_value)


@pytest.mark.asyncio
async def test_ticket_protocol_does_not_scrape_or_reuse_legacy_token(monkeypatch):
    monkeypatch.setattr(hermes_auth, 'settings', lambda: {'username': 'ods', 'password': 'fixture'})
    monkeypatch.setattr(hermes_bridge, 'login_dashboard', AsyncMock(return_value=[]))
    scrape = AsyncMock()
    monkeypatch.setattr(hermes_bridge, '_fetch_hermes_token', scrape)
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return {'ticket': 'single-use+ticket/fixture'}
    class Session:
        def get(self, url, **kwargs):
            assert url.endswith('/api/status')
            response = Response()
            response.json = AsyncMock(return_value={'version': '0.21.5'})
            return response
        def post(self, url, **kwargs):
            assert url.endswith('/api/auth/ws-ticket') and kwargs['allow_redirects'] is False
            return Response()
        async def ws_connect(self, url):
            assert url.endswith('/api/ws?ticket=single-use%2Bticket%2Ffixture')
            return 'connected'
    assert await hermes_bridge._connect_ws(Session()) == 'connected'
    scrape.assert_not_called()
