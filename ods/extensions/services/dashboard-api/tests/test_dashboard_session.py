"""Dashboard sign-in for access from another device (routers/dashboard_session.py)."""

from http.cookies import SimpleCookie

import dashboard_password
import pytest
import security
import session_signer
from routers import dashboard_session as ds

COOKIE = ds.SESSION_COOKIE_NAME


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_password, "PASSWORD_FILE", tmp_path / "dashboard-password.json")
    ds._FAILURES.clear()
    ds._LOGIN_LINKS.clear()
    yield
    ds._FAILURES.clear()
    ds._LOGIN_LINKS.clear()


def _set_cookies(resp):
    cookies = SimpleCookie()
    for key, value in resp.headers.raw:
        if key.lower() == b"set-cookie":
            cookies.load(value.decode("latin1"))
    return cookies


def _login(client, **payload):
    client.cookies.clear()
    return client.post("/api/auth/dashboard-session/login", json=payload)


def test_gate_rejects_missing_tampered_expired_and_foreign_cookies(test_client, monkeypatch):
    assert test_client.get("/api/auth/dashboard-session/verify").status_code == 401

    valid = ds.issue_session()
    test_client.cookies.set(COOKIE, valid)
    assert test_client.get("/api/auth/dashboard-session/verify").status_code == 204

    for value in (valid[:-2] + "xx", ds.issue_session(now=0), "v1.99999999999.abc.def", "garbage"):
        test_client.cookies.set(COOKIE, value)
        assert test_client.get("/api/auth/dashboard-session/verify").status_code == 401, value

    # An ods-session (magic link / Talk) value is never a dashboard session.
    session_signer._set_secret_for_tests("unit-test-session-secret")
    test_client.cookies.set(COOKIE, session_signer.issue(ttl_seconds=600))
    assert test_client.get("/api/auth/dashboard-session/verify").status_code == 401

    # Rotating the dashboard key signs every device out.
    test_client.cookies.set(COOKIE, valid)
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "rotated-dashboard-key")
    assert test_client.get("/api/auth/dashboard-session/verify").status_code == 401


def test_key_sign_in_sets_a_private_host_only_session(test_client):
    resp = _login(test_client, key=security.DASHBOARD_API_KEY)

    assert resp.status_code == 200, resp.text
    raw = b" ".join(v for k, v in resp.headers.raw if k.lower() == b"set-cookie").lower()
    assert b"httponly" in raw and b"samesite=strict" in raw and b"path=/" in raw
    assert b"domain=" not in raw
    assert f"max-age={ds.SESSION_TTL_SECONDS}".encode() in raw
    assert ds.session_is_valid(_set_cookies(resp)[COOKIE].value)


def test_https_terminator_sets_secure_session_cookie(test_client):
    response = test_client.post("/api/auth/dashboard-session/login",
                                json={"key": security.DASHBOARD_API_KEY},
                                headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert _set_cookies(response)[COOKIE]["secure"] is True


@pytest.mark.parametrize("payload", [
    {"key": "not-the-dashboard-key"},
    {"token": "not-a-real-link"},
])
def test_wrong_credentials_are_rejected_without_a_session(test_client, payload):
    resp = _login(test_client, **payload)
    assert resp.status_code == 401
    assert COOKIE not in _set_cookies(resp)


@pytest.mark.parametrize("body", [None, {}, [], {"key": ""}, {"key": 5}, {"unknown": "x"},
                                  {"key": "a", "token": "b"}, {"key": "x" * 600}])
def test_malformed_sign_in_bodies_are_rejected(test_client, body):
    test_client.cookies.clear()
    resp = test_client.post("/api/auth/dashboard-session/login", json=body)
    assert resp.status_code == 422


def test_repeated_failures_are_rate_limited(test_client):
    for _ in range(ds._FAILURE_LIMIT):
        assert _login(test_client, key="wrong").status_code == 401
    assert _login(test_client, key=security.DASHBOARD_API_KEY).status_code == 429


def test_login_links_need_the_key_and_work_once(test_client):
    assert test_client.post("/api/auth/dashboard-session/link").status_code == 401

    link = test_client.post("/api/auth/dashboard-session/link", headers=test_client.auth_headers)
    assert link.status_code == 200
    body = link.json()
    assert body["fragment"] == f"#ods-login={body['token']}"
    assert body["expiresIn"] == ds.LOGIN_LINK_TTL_SECONDS

    first = _login(test_client, token=body["token"])
    assert first.status_code == 200
    assert ds.session_is_valid(_set_cookies(first)[COOKIE].value)
    assert _login(test_client, token=body["token"]).status_code == 401


def test_expired_login_links_are_rejected(test_client, monkeypatch):
    link = test_client.post("/api/auth/dashboard-session/link", headers=test_client.auth_headers).json()
    real_time = ds.time.time
    monkeypatch.setattr(ds.time, "time", lambda: real_time() + ds.LOGIN_LINK_TTL_SECONDS + 1)
    assert _login(test_client, token=link["token"]).status_code == 401


def test_status_reports_whether_a_session_cookie_is_in_use(test_client):
    test_client.cookies.clear()
    assert test_client.get("/api/auth/dashboard-session").status_code == 401
    assert test_client.get("/api/auth/dashboard-session", headers=test_client.auth_headers).json() == {
        "signedIn": True, "session": False, "passwordConfigured": False}
    test_client.cookies.set(COOKIE, ds.issue_session())
    assert test_client.get("/api/auth/dashboard-session", headers=test_client.auth_headers).json() == {
        "signedIn": True, "session": True, "passwordConfigured": False}


def test_logout_clears_the_session_cookie(test_client):
    test_client.cookies.set(COOKIE, ds.issue_session())
    resp = test_client.post("/api/auth/dashboard-session/logout")
    assert resp.status_code == 200
    raw = b" ".join(v for k, v in resp.headers.raw if k.lower() == b"set-cookie").lower()
    assert f"{COOKIE}=".encode() in raw and b"max-age=0" in raw


def test_password_setup_requires_owner_authority_and_hashes_at_rest(test_client):
    password = "abcdef"
    assert test_client.post("/api/auth/dashboard-session/password", json={"password": password}).status_code == 401
    result = test_client.post("/api/auth/dashboard-session/password", headers=test_client.auth_headers,
                              json={"password": password})
    assert result.status_code == 200
    stored = dashboard_password.PASSWORD_FILE.read_text()
    assert password not in stored
    assert dashboard_password.verify(password)
    assert not dashboard_password.verify("wrong password")
    assert _login(test_client, password=password).status_code == 200
    assert _login(test_client, password="wrong password").status_code == 401


def test_password_recovery_revokes_old_sessions_and_outstanding_links(test_client):
    dashboard_password.save("the original passphrase")
    old = ds.issue_session()
    link = test_client.post("/api/auth/dashboard-session/link", headers=test_client.auth_headers).json()
    recovery = test_client.post("/api/auth/dashboard-session/link", headers=test_client.auth_headers).json()
    signed_in = _login(test_client, token=recovery["token"])
    assert signed_in.status_code == 200 and signed_in.json()["passwordSetup"] is True
    result = test_client.post("/api/auth/dashboard-session/password", headers=test_client.auth_headers,
                              json={"password": "uvwxyz"})
    assert result.status_code == 200
    assert not ds.session_is_valid(old)
    assert ds.session_is_valid(_set_cookies(result)[COOKIE].value)
    assert _login(test_client, token=link["token"]).status_code == 401
    assert _login(test_client, password="the original passphrase").status_code == 401
    assert _login(test_client, password="uvwxyz").status_code == 200


@pytest.mark.parametrize("password", [None, 123, "short", "x" * 129])
def test_password_policy_preserves_existing_credential(test_client, password):
    dashboard_password.save("existing long passphrase")
    before = dashboard_password.PASSWORD_FILE.read_bytes()
    result = test_client.post("/api/auth/dashboard-session/password", headers=test_client.auth_headers,
                              json={"password": password})
    assert result.status_code == 422
    assert dashboard_password.PASSWORD_FILE.read_bytes() == before


def test_damaged_password_storage_fails_closed(test_client):
    dashboard_password.PASSWORD_FILE.write_text("not json")
    assert _login(test_client, password="any password").status_code == 503
    assert test_client.get("/api/auth/dashboard-session", headers=test_client.auth_headers).status_code == 503


def test_password_persists_across_module_reload():
    import importlib
    path = dashboard_password.PASSWORD_FILE
    dashboard_password.save("retained after restart")
    revision = dashboard_password.revision()
    try:
        importlib.reload(dashboard_password)
        dashboard_password.PASSWORD_FILE = path
        assert dashboard_password.verify("retained after restart")
        assert dashboard_password.revision() == revision
    finally:
        dashboard_password.PASSWORD_FILE = path


def test_failed_password_write_preserves_access(test_client, monkeypatch):
    dashboard_password.save("existing long passphrase")
    before = dashboard_password.PASSWORD_FILE.read_bytes()
    previous_session = ds.issue_session()
    def failed_replace(*args):
        raise OSError("fixture: storage full")
    monkeypatch.setattr(dashboard_password.os, "replace", failed_replace)
    result = test_client.post("/api/auth/dashboard-session/password", headers=test_client.auth_headers,
                              json={"password": "my replacement passphrase"})
    assert result.status_code == 503
    assert dashboard_password.PASSWORD_FILE.read_bytes() == before
    assert dashboard_password.verify("existing long passphrase")
    assert ds.session_is_valid(previous_session)
    assert list(dashboard_password.PASSWORD_FILE.parent.glob(".dashboard-password-*")) == []


def test_password_file_is_private_on_posix():
    import os
    import stat
    dashboard_password.save("my chosen long passphrase")
    if os.name == "posix":
        assert stat.S_IMODE(dashboard_password.PASSWORD_FILE.stat().st_mode) == 0o600
