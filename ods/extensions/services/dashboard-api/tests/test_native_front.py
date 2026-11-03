"""The Windows Desktop dashboard boundary: who gets the server-held key.

Ports the dev-api-access.js localRequest() matrix. The inner app echoes the
Authorization header it received, so injection and stripping are observable.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request

import native_front
from native_front import DashboardFiles, DashboardGate
from routers.dashboard_session import SESSION_COOKIE_NAME, issue_session

KEY = "server-held-key"
LISTENER = "http://127.0.0.1:3101"


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>ODS</html>", encoding="utf-8")
    (tmp_path / "assets" / "app.3f9a.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "sw.js").write_text("// sw", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("secret", encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(dist):
    inner = FastAPI()

    @inner.get("/api/echo")
    @inner.get("/api/talk/echo")
    @inner.post("/api/auth/dashboard-session/login")
    async def echo(request: Request):
        return {"authorization": request.headers.get("authorization")}

    inner.mount("/", DashboardFiles(directory=dist, html=True), name="dashboard")
    return DashboardGate(inner, api_key=KEY)


def get(app, path, *, client=("127.0.0.1", 50123), base=LISTENER, headers=None, method="GET"):
    async def go():
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url=base) as http:
            return await http.request(method, path, headers=headers)
    return asyncio.run(go())


def admitted(response):
    return response.status_code == 200 and response.json()["authorization"] == f"Bearer {KEY}"


def refused(response):
    return (response.status_code == 401 and response.headers["x-ods-sign-in"] == "required"
            and response.json()["code"] == "dashboard_sign_in_required")


@pytest.mark.parametrize("base", ["http://127.0.0.1:3101", "http://localhost:3101", "http://[::1]:3101"])
def test_the_local_browser_gets_the_key(app, base):
    client = ("::1", 50123) if "::1" in base else ("127.0.0.1", 50123)
    assert admitted(get(app, "/api/echo", base=base, client=client))


def test_same_origin_browser_requests_are_trusted(app):
    assert admitted(get(app, "/api/echo", headers={"Origin": LISTENER, "Sec-Fetch-Site": "same-origin"}))
    assert admitted(get(app, "/api/echo", headers={"Sec-Fetch-Site": "none"}))


def test_a_client_authorization_is_replaced_not_trusted(app):
    assert admitted(get(app, "/api/echo", headers={"Authorization": "Bearer attacker"}))


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example:3101"},                 # DNS rebinding
    {"Host": "localhost:9999"},                    # another listener's name
    {"X-Forwarded-For": "203.0.113.9"},            # a proxy in front of localhost
    {"Forwarded": "for=203.0.113.9"},
    {"Via": "1.1 tailscale"},
    {"X-Real-IP": "203.0.113.9"},
    {"Origin": "http://evil.example"},             # another website posting to localhost
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_untrusted_requests_need_a_session(app, headers):
    assert refused(get(app, "/api/echo", headers=headers))


def test_duplicate_host_headers_are_untrusted(app):
    async def go():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50123))
        async with httpx.AsyncClient(transport=transport, base_url=LISTENER) as http:
            request = http.build_request("GET", "/api/echo")
            request.headers = httpx.Headers([*request.headers.raw, (b"host", b"127.0.0.1:3101")])
            return await http.send(request)
    assert refused(asyncio.run(go()))


def test_a_network_peer_needs_a_session(app):
    assert refused(get(app, "/api/echo", client=("192.168.1.20", 50123)))


def test_a_valid_session_cookie_admits_an_untrusted_request(app):
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={issue_session()}"}
    assert admitted(get(app, "/api/echo", client=("192.168.1.20", 50123), headers=cookie))
    forged = {"Cookie": f"{SESSION_COOKIE_NAME}=forged.value"}
    assert refused(get(app, "/api/echo", client=("192.168.1.20", 50123), headers=forged))


@pytest.mark.parametrize("path, method", [("/api/talk/echo", "GET"), ("/api/auth/dashboard-session/login", "POST")])
def test_talk_and_sign_in_never_receive_the_admin_key(app, path, method):
    for client in (("127.0.0.1", 50123), ("192.168.1.20", 50123)):
        response = get(app, path, client=client, method=method, headers={"Authorization": "Bearer attacker"})
        assert response.status_code == 200 and response.json()["authorization"] is None


def test_pixel_previews_do_not_exist_here(app):
    assert get(app, "/pixel-preview/abc/index.html").status_code == 404


def test_the_dashboard_and_its_client_routes_are_served(app):
    for path in ("/", "/settings", "/models/library"):
        response = get(app, path)
        assert response.status_code == 200 and "ODS" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_an_unknown_api_path_is_a_json_404_not_the_dashboard(app):
    response = get(app, "/api/does-not-exist")
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


def test_hashed_assets_are_cached_and_missing_files_are_404(app):
    asset = get(app, "/assets/app.3f9a.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert get(app, "/sw.js").headers["cache-control"] == "no-cache"
    assert get(app, "/assets/missing.js").status_code == 404


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"])
def test_files_outside_the_dashboard_are_never_served(app, path):
    response = get(app, path)
    assert "secret" not in response.text


def test_every_response_carries_the_security_headers(app):
    for response in (get(app, "/"), get(app, "/api/echo"), get(app, "/api/echo", client=("192.168.1.20", 1))):
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in response.headers["content-security-policy"]


def test_dist_dir_must_be_an_absolute_built_dashboard(dist, tmp_path):
    assert native_front.dist_dir({}) is None
    assert native_front.dist_dir({"ODS_DASHBOARD_DIST": str(dist)}) == dist
    for bad in ("dist", str(tmp_path / "empty")):
        with pytest.raises(ValueError, match="ODS_DASHBOARD_DIST"):
            native_front.dist_dir({"ODS_DASHBOARD_DIST": bad})
