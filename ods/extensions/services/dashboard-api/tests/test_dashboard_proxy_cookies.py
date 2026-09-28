"""Real nginx -> ASGI cookie handoff (opt in with ODS_TEST_NGINX_IMAGE).

Uses the shipped nginx template and actual auth routers, with disposable state
and high loopback ports. Docker must support host networking (Linux CI/WSL).
"""

import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from http.cookies import SimpleCookie

import dashboard_password
from fastapi import FastAPI
import httpx
import pytest
import security
import session_signer
import uvicorn
from routers import auth, dashboard_session


pytestmark = pytest.mark.skipif(
    not os.environ.get("ODS_TEST_NGINX_IMAGE"),
    reason="set ODS_TEST_NGINX_IMAGE to run the Linux Docker nginx boundary test",
)


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def nginx_proxy(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("dashboard-nginx")
    app = FastAPI()
    app.include_router(dashboard_session.router)
    app.include_router(auth.router)
    api_port, local_port, remote_port = _port(), _port(), _port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=api_port, log_level="error", proxy_headers=False,
    ))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    template = Path(__file__).resolve().parents[2] / "dashboard" / "nginx.conf"
    config = template.read_text(encoding="utf-8")
    config = config.replace("listen 3001;", f"listen 127.0.0.1:{local_port};")
    config = config.replace("listen [::]:3001;", "")
    config = config.replace("listen 3011;", f"listen 127.0.0.1:{remote_port};")
    config = config.replace("listen [::]:3011;", "")
    config = config.replace("dashboard-api:3002", f"127.0.0.1:{api_port}")
    config = config.replace("__ODS_LOCAL_LISTENER__", str(local_port))
    config = config.replace("__PIXEL_PREVIEW_PORT__", "9437")
    config = config.replace("${DASHBOARD_API_KEY}", security.DASHBOARD_API_KEY)
    conf = tmp_path / "nginx.conf"
    conf.write_text(config, encoding="utf-8")
    container = None
    try:
        container = subprocess.check_output([
            "docker", "run", "--detach", "--rm", "--network", "host",
            "--mount", f"type=bind,src={conf},dst=/etc/nginx/conf.d/default.conf,readonly",
            os.environ["ODS_TEST_NGINX_IMAGE"],
        ], text=True).strip()
        with (httpx.Client(base_url=f"http://127.0.0.1:{remote_port}", timeout=5) as client,
              httpx.Client(base_url=f"http://127.0.0.1:{local_port}", timeout=5) as owner):
            deadline = time.monotonic() + 20
            while True:
                with socket.socket() as probe:
                    listening = probe.connect_ex(("127.0.0.1", remote_port)) == 0
                if listening and server.started:
                    break
                if time.monotonic() >= deadline:
                    logs = subprocess.check_output(["docker", "logs", container], text=True)
                    pytest.fail(f"nginx/API did not become ready: {logs}")
                time.sleep(0.05)
            assert client.get("/api/auth/dashboard-session").status_code == 401
            yield client, owner
    finally:
        if container:
            subprocess.run(["docker", "stop", container], check=True, capture_output=True)
        server.should_exit = True
        worker.join(timeout=10)
        assert not worker.is_alive()


@pytest.fixture()
def proxy(nginx_proxy, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_password, "PASSWORD_FILE", tmp_path / "password.json")
    monkeypatch.setattr(session_signer, "_SECRET", b"proxy-cookie-test-secret")
    dashboard_session._FAILURES.clear()
    dashboard_session._LOGIN_LINKS.clear()
    for client in nginx_proxy:
        client.cookies.clear()
        client.headers.pop("Cookie", None)
        client.headers.pop("X-Forwarded-Proto", None)
    yield nginx_proxy
    dashboard_session._FAILURES.clear()
    dashboard_session._LOGIN_LINKS.clear()


def _cookie(response):
    assert response.status_code == 200, response.text
    cookies = SimpleCookie()
    for value in response.headers.get_list("set-cookie"):
        cookies.load(value)
    return cookies[dashboard_session.SESSION_COOKIE_NAME]


@pytest.mark.parametrize("scheme,secure", [("https", True), ("HTTPS, http", True), ("http", False)])
def test_password_rotation_preserves_transport_and_revokes_old_session(proxy, scheme, secure):
    """The recovery/password flow must never downgrade the login cookie."""
    proxy, owner = proxy
    link = owner.post("/api/auth/dashboard-session/link")
    assert link.status_code == 200, link.text
    proxy.headers["X-Forwarded-Proto"] = scheme
    response = proxy.post("/api/auth/dashboard-session/login", json={"token": link.json()["token"]})
    login = _cookie(response)
    assert bool(login["secure"]) is secure
    # The test uses HTTP behind a simulated TLS terminator, so forward the
    # Secure cookie explicitly, as the browser/terminator would over HTTPS.
    proxy.headers["Cookie"] = f"{login.key}={login.value}"
    assert proxy.get("/api/auth/dashboard-session").status_code == 200
    response = proxy.post("/api/auth/dashboard-session/password", json={"password": "new-password"})
    rotated = _cookie(response)
    assert bool(rotated["secure"]) is secure
    assert rotated["httponly"] and rotated["samesite"] == "strict"
    assert not rotated["domain"]
    denied = proxy.get("/api/auth/dashboard-session")
    assert denied.status_code == 401
    assert denied.headers["x-ods-sign-in"] == "required"
    proxy.headers["Cookie"] = f"{rotated.key}={rotated.value}"
    assert proxy.get("/api/auth/dashboard-session").status_code == 200
    assert proxy.post("/api/auth/dashboard-session/logout").status_code == 200
    del proxy.headers["Cookie"]
    proxy.cookies.clear()
    assert proxy.get("/api/auth/dashboard-session").status_code == 401
    signed_in = _cookie(proxy.post("/api/auth/dashboard-session/login", json={"password": "new-password"}))
    assert bool(signed_in["secure"]) is secure
    proxy.headers["Cookie"] = f"{signed_in.key}={signed_in.value}"
    admin = proxy.post("/api/auth/admin-session")
    assert admin.status_code == 200, admin.text
    cookies = SimpleCookie()
    cookies.load(admin.headers["set-cookie"])
    assert bool(cookies["ods-session"]["secure"]) is secure


def test_forwarded_https_does_not_authorize_password_changes(proxy):
    proxy, _owner = proxy
    response = proxy.post("/api/auth/dashboard-session/password",
                          headers={"X-Forwarded-Proto": "https"}, json={"password": "untrusted"})
    assert response.status_code == 401
    assert response.headers["x-ods-sign-in"] == "required"
    assert not dashboard_password.PASSWORD_FILE.exists()
