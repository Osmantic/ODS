"""Native runtime front door: nginx's sign-in gate, key injection and SPA serving."""

import asyncio

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

import native_gateway
from native_gateway import NativeGateway, api_route, is_trusted_local
from routers import dashboard_session

KEY = "native-gateway-test-key"
ECHO_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]


def _echo_app() -> FastAPI:
    echo = FastAPI()

    @echo.get("/health")
    async def health():
        return {"status": "ok"}

    @echo.api_route("/api/{path:path}", methods=ECHO_METHODS)
    async def anything(request: Request):
        body = await request.body()
        return {
            "path": request.url.path,
            "authorization": request.headers.get("authorization"),
            "x_real_ip": request.headers.get("x-real-ip"),
            "x_forwarded_for": request.headers.get("x-forwarded-for"),
            "x_forwarded_proto": request.headers.get("x-forwarded-proto"),
            "body_bytes": len(body),
        }

    return echo


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text('<!doctype html><script src="/assets/app-1a2b.js"></script>', encoding="utf-8")
    (root / "assets" / "app-1a2b.js").write_text("console.log('dashboard')", encoding="utf-8")
    (root / "sw.js").write_text("self.addEventListener('fetch', () => {})", encoding="utf-8")
    (root / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build", encoding="utf-8")
    return root


def _gateway(dist, app=None, **overrides):
    options = {
        "dist_dir": dist,
        "api_key": KEY,
        "session_is_valid": dashboard_session.session_is_valid,
        "session_cookie_name": dashboard_session.SESSION_COOKIE_NAME,
    }
    options.update(overrides)
    return NativeGateway(app or _echo_app(), **options)


def _client(gateway, host="localhost:3002", peer="127.0.0.1"):
    return TestClient(gateway, base_url=f"http://{host}", client=(peer, 51000))


def _assert_no_key(response):
    assert KEY not in response.text
    for name, value in response.headers.items():
        assert KEY not in value, name


# --- the sign-in gate ---------------------------------------------------------

@pytest.mark.parametrize("host", ["localhost:3002", "127.0.0.1:3002", "[::1]:3002", "localhost"])
def test_local_browser_gets_the_key_added_server_side(dist, host):
    response = _client(_gateway(dist), host=host).get("/api/status")
    assert response.status_code == 200
    assert response.json()["authorization"] == f"Bearer {KEY}"
    assert response.json()["x_real_ip"] == "127.0.0.1"
    assert response.json()["x_forwarded_for"] == "127.0.0.1"
    assert response.json()["x_forwarded_proto"] == "http"


def test_ipv4_mapped_and_ipv6_loopback_peers_are_local(dist):
    for peer in ("::1", "::ffff:127.0.0.1"):
        assert _client(_gateway(dist), peer=peer).get("/api/status").status_code == 200


@pytest.mark.parametrize("host", ["ods.lan:3002", "evil.example", "127.0.0.2:3002", "localhost.evil.example"])
def test_non_loopback_host_needs_a_session(dist, host):
    response = _client(_gateway(dist), host=host).get("/api/status")
    assert response.status_code == 401
    assert response.headers["x-ods-sign-in"] == "required"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "dashboard_sign_in_required"


def test_non_loopback_peer_needs_a_session(dist):
    assert _client(_gateway(dist), peer="192.168.1.20").get("/api/status").status_code == 401


@pytest.mark.parametrize("header", [
    "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded", "X-Real-IP", "Via",
    "X-Forwarded-Port",
])
def test_forwarding_headers_are_never_trusted_local(dist, header):
    response = _client(_gateway(dist)).get("/api/status", headers={header: "203.0.113.9"})
    assert response.status_code == 401


def test_duplicate_host_headers_are_not_local():
    headers = [(b"host", b"localhost:3002"), (b"host", b"evil.example")]
    assert not is_trusted_local(("127.0.0.1", 1), headers)
    assert is_trusted_local(("127.0.0.1", 1), headers[:1])
    # nginx's Host match is case-sensitive.
    assert not is_trusted_local(("127.0.0.1", 1), [(b"host", b"LOCALHOST:3002")])


def test_valid_dashboard_session_opens_the_gate_from_anywhere(dist):
    cookie = dashboard_session.issue_session()
    client = _client(_gateway(dist), host="ods.lan:3002", peer="192.168.1.20")
    response = client.get("/api/status", headers={"Cookie": f"{dashboard_session.SESSION_COOKIE_NAME}={cookie}"})
    assert response.status_code == 200
    assert response.json()["authorization"] == f"Bearer {KEY}"
    assert response.json()["x_real_ip"] == "192.168.1.20"


def test_forged_session_cookie_is_rejected(dist):
    client = _client(_gateway(dist), host="ods.lan:3002", peer="192.168.1.20")
    forged = "v1.9999999999.abc.not-a-signature"
    response = client.get("/api/status", headers={"Cookie": f"{dashboard_session.SESSION_COOKIE_NAME}={forged}"})
    assert response.status_code == 401


def test_proxy_headers_are_rewritten_like_nginx(dist):
    cookie = dashboard_session.issue_session()
    client = _client(_gateway(dist), host="ods.lan:3002", peer="192.168.1.20")
    response = client.get("/api/status", headers={
        "Cookie": f"{dashboard_session.SESSION_COOKIE_NAME}={cookie}",
        "X-Real-IP": "10.9.9.9", "X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https",
    })
    body = response.json()
    assert body["x_real_ip"] == "192.168.1.20"  # never the client's claim
    assert body["x_forwarded_for"] == "203.0.113.9, 192.168.1.20"
    assert body["x_forwarded_proto"] == "http"


def test_sign_in_routes_are_public_and_never_get_the_key(dist):
    client = _client(_gateway(dist), host="ods.lan:3002", peer="192.168.1.20")
    for path in ("/api/auth/dashboard-session/login", "/api/auth/dashboard-session/logout"):
        response = client.post(path, json={}, headers={"Authorization": "Bearer client-supplied",
                                                       "X-Forwarded-Proto": "https"})
        assert response.status_code == 200
        assert response.json()["authorization"] is None
        assert response.json()["x_forwarded_proto"] == "https"  # Secure cookie behind TLS


def test_a_client_that_presents_its_own_key_is_a_direct_api_client(dist):
    response = _client(_gateway(dist), host="ods.lan:3002", peer="192.168.1.20").get(
        "/api/status", headers={"Authorization": "Bearer caller-key"})
    assert response.status_code == 200
    assert response.json()["authorization"] == "Bearer caller-key"


def test_direct_keyless_request_through_the_real_app_is_401(dist):
    import main
    client = _client(_gateway(dist, app=main.app), host="ods.lan:3002", peer="192.168.1.20")
    assert client.get("/api/auth/dashboard-session").status_code == 401
    assert client.get("/api/auth/dashboard-session",
                      headers={"Authorization": "Bearer wrong"}).status_code == 403


def test_local_browser_reaches_the_real_app_with_the_server_key(dist, monkeypatch):
    import main
    import security
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", KEY)
    response = _client(_gateway(dist, app=main.app)).get("/api/auth/dashboard-session")
    assert response.status_code == 200
    assert response.json()["signedIn"] is True
    _assert_no_key(response)


def test_cross_site_post_is_still_rejected_by_the_app(dist, monkeypatch):
    import main
    import security
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", KEY)
    client = _client(_gateway(dist, app=main.app))
    response = client.post("/api/auth/dashboard-session/link", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    response = client.post("/api/auth/dashboard-session/link", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    same = client.post("/api/auth/dashboard-session/link", headers={"Origin": "http://localhost:3002"})
    assert same.status_code == 200
    _assert_no_key(same)


# --- Docker-only features --------------------------------------------------------

@pytest.mark.parametrize("method,path,feature", [
    ("GET", "/api/talk/status", "talk"),
    ("POST", "/api/extensions/n8n/enable", "extensions"),
    ("GET", "/api/services/resources", "resources"),
    ("GET", "/api/preflight/docker", "preflight-docker"),
    ("GET", "/api/tailscale/status", "tailscale"),
    ("GET", "/api/setup/wifi-scan", "wifi"),
    ("POST", "/api/pixel/chat/stream", "pixel"),
    ("GET", "/pixel-preview/abc/index.html", "pixel"),
])
def test_docker_only_routes_answer_native_unavailable(dist, method, path, feature):
    response = _client(_gateway(dist)).request(method, path)
    assert response.status_code == 404
    assert response.json() == {"detail": response.json()["detail"], "code": "native_unavailable", "feature": feature}


def test_extension_catalog_reads_stay_available(dist):
    assert _client(_gateway(dist)).get("/api/extensions/catalog").status_code == 200


# --- body limits -------------------------------------------------------------------

@pytest.mark.parametrize("path,limit", [
    ("/api/settings/env", 1024 * 1024),
    ("/api/auth/dashboard-session/login", 4 * 1024),
    ("/api/models/recovery", 2 * 1024),
    ("/api/pixel/access-mode", 2 * 1024),
])
def test_body_limits_follow_nginx(dist, path, limit):
    client = _client(_gateway(dist))
    assert client.post(path, content=b"x" * limit).json()["body_bytes"] == limit
    assert client.post(path, content=b"x" * (limit + 1)).status_code == 413


def test_chunked_body_over_the_limit_is_cut_off(dist):
    def chunks():
        for _ in range(3):
            yield b"x" * (512 * 1024)
    response = _client(_gateway(dist)).post("/api/settings/env", content=chunks())
    assert response.status_code == 413


def test_api_route_table():
    assert api_route("/api/talk/message").public
    assert api_route("/api/talk/message").body_limit == 26 * 1024 * 1024
    assert api_route("/api/pixel/images/abc/img-" + "a" * 32).body_limit == 8 * 1024 * 1024
    assert api_route("/api/pixel/approval-terminal").cookie_scheme
    assert not api_route("/api/status").public


# --- static files -------------------------------------------------------------------

def test_root_serves_index_with_nginx_headers(dist):
    response = _client(_gateway(dist)).get("/")
    assert response.status_code == 200
    assert response.text.startswith("<!doctype html>")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "http://localhost:9437" in response.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/models", "/settings", "/dashboard/deep/link", "/api", "/talk"])
def test_spa_routes_fall_back_to_index(dist, path):
    response = _client(_gateway(dist)).get(path)
    assert response.status_code == 200
    assert response.text.startswith("<!doctype html>")


def test_hashed_assets_are_immutable_and_missing_assets_404(dist):
    client = _client(_gateway(dist))
    asset = client.get("/assets/app-1a2b.js")
    assert asset.status_code == 200
    assert asset.headers["content-type"] == "application/javascript"
    assert asset.headers["cache-control"] == "max-age=31536000, public, immutable"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/sw.js").headers["cache-control"] == "no-cache"
    assert client.get("/manifest.webmanifest").headers["content-type"] == "application/manifest+json"


@pytest.mark.parametrize("path", ["/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt",
                                  "/C:/Windows/win.ini", "/assets\\..\\..\\secret.txt"])
def test_paths_cannot_leave_the_build(dist, path):
    response = _client(_gateway(dist)).get(path)
    assert "outside the build" not in response.text


def test_static_files_are_read_only(dist):
    assert _client(_gateway(dist)).post("/").status_code == 405


def test_health_reaches_the_app_without_the_gate(dist):
    response = _client(_gateway(dist), host="ods.lan:3002", peer="192.168.1.20").get("/health")
    assert response.json() == {"status": "ok"}


def test_key_is_never_served(dist):
    client = _client(_gateway(dist))
    for path in ("/", "/assets/app-1a2b.js", "/sw.js", "/settings", "/api/talk/status"):
        _assert_no_key(client.get(path))


def test_gateway_refuses_unsafe_configuration(dist, tmp_path):
    with pytest.raises(ValueError):
        _gateway(dist, api_key="has space")
    with pytest.raises(FileNotFoundError):
        _gateway(tmp_path / "missing")
    with pytest.raises(ValueError):
        _gateway(dist, preview_port=70000)


# --- streaming ------------------------------------------------------------------------

def test_api_responses_stream_without_buffering(dist):
    """The first chunk reaches the client before the app produces the second."""
    first_chunk_sent = asyncio.Event()

    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: one\n\n", "more_body": True})
        await asyncio.wait_for(first_chunk_sent.wait(), timeout=5)
        await send({"type": "http.response.body", "body": b"data: two\n\n", "more_body": False})

    gateway = _gateway(dist, app=streaming_app)
    received = []

    async def send(message):
        received.append(message)
        if message.get("body") == b"data: one\n\n":
            first_chunk_sent.set()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/api/pixel/x", "raw_path": b"/api/pixel/x",
             "query_string": b"", "scheme": "http", "client": ("127.0.0.1", 5), "server": ("127.0.0.1", 3002),
             "headers": [(b"host", b"localhost:3002")], "http_version": "1.1", "root_path": ""}
    asyncio.run(gateway(scope, receive, send))
    bodies = [m["body"] for m in received if m["type"] == "http.response.body"]
    assert bodies == [b"data: one\n\n", b"data: two\n\n"]


def test_websockets_are_refused(dist):
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(_gateway(dist)({"type": "websocket", "path": "/api/x", "headers": []}, None, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_security_header_values_come_from_one_template():
    headers = dict(native_gateway.security_headers(1234))
    assert "__PIXEL_PREVIEW_PORT__" not in headers["Content-Security-Policy"]
    assert "http://127.0.0.1:1234" in headers["Content-Security-Policy"]
