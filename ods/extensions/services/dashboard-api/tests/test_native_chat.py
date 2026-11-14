"""Native chat: llama-server's web UI behind the dashboard gate, key added server-side."""

import asyncio

import httpx
import pytest
from starlette.testclient import TestClient

from native_chat import NativeChatProxy, is_cross_site
from routers import dashboard_session

LLAMA_KEY = "llama-native-key-123"


def _fake_llama(request: httpx.Request) -> httpx.Response:
    """llama-server: public UI pages, every API route needs the key."""
    public = {"/", "/index.html", "/bundle.js", "/bundle.css", "/health", "/v1/health", "/models", "/v1/models"}
    if request.url.path not in public and request.headers.get("authorization") != f"Bearer {LLAMA_KEY}":
        return httpx.Response(401, json={"error": {"message": "Invalid API Key", "code": 401}})
    if request.url.path == "/v1/chat/completions":
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello from llama"}}],
                                         "seen_headers": sorted(request.headers.keys())})
    if request.url.path == "/":
        return httpx.Response(200, html="<!doctype html><script>boot('./')</script>",
                              headers={"Access-Control-Allow-Origin": "http://evil.example",
                                       "Access-Control-Allow-Credentials": "true",
                                       "Cross-Origin-Opener-Policy": "same-origin"})
    return httpx.Response(200, json={"path": request.url.path, "query": str(request.url.query, "ascii")})


def _as_network_response(handler):
    """MockTransport pre-reads plain bodies; a real connection streams them."""
    async def streamed(request):
        response = handler(request)
        if not isinstance(response, httpx.Response):
            response = await response
        if response.is_stream_consumed or hasattr(response, "_content"):
            body = response.content

            async def chunks():
                yield body
            headers = [(k, v) for k, v in response.headers.multi_items() if k.lower() != "content-length"]
            return httpx.Response(response.status_code, headers=headers, content=chunks())
        return response
    return streamed


def _proxy(handler=_fake_llama):
    return NativeChatProxy(
        upstream="http://127.0.0.1:8080", api_key=LLAMA_KEY,
        session_is_valid=dashboard_session.session_is_valid,
        session_cookie_name=dashboard_session.SESSION_COOKIE_NAME,
        transport=httpx.MockTransport(_as_network_response(handler)),
    )


def _client(host="127.0.0.1:3010", peer="127.0.0.1"):
    return TestClient(_proxy(), base_url=f"http://{host}", client=(peer, 52000))


def _assert_no_key(response):
    assert LLAMA_KEY not in response.text
    for name, value in response.headers.items():
        assert LLAMA_KEY not in value, name


def test_local_chat_reaches_llama_with_the_key_added():
    response = _client().post("/v1/chat/completions", json={"messages": []},
                              headers={"Origin": "http://127.0.0.1:3010"})
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hello from llama"
    _assert_no_key(response)


def test_fake_llama_refuses_a_keyless_request():
    with httpx.Client(transport=httpx.MockTransport(_fake_llama)) as direct:
        assert direct.post("http://127.0.0.1:8080/v1/chat/completions", json={}).status_code == 401


def test_ui_page_is_served_without_the_key_or_reflected_cors():
    response = _client().get("/")
    assert response.status_code == 200
    assert "boot('./')" in response.text
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    _assert_no_key(response)


def test_relative_ui_api_paths_and_queries_pass_through():
    for path in ("/props?autoload=false", "/slots?model=m", "/v1/models"):
        response = _client().get(path)
        assert response.status_code == 200, path
        assert response.json()["path"] == path.split("?")[0]


def test_untrusted_requests_need_a_dashboard_session():
    for client in (_client(host="ods.lan:3010"), _client(peer="192.168.1.5")):
        response = client.post("/v1/chat/completions", json={})
        assert response.status_code == 401
        assert response.json()["code"] == "dashboard_sign_in_required"
    response = _client().get("/", headers={"X-Forwarded-For": "203.0.113.4"})
    assert response.status_code == 401


def test_a_dashboard_session_opens_chat_remotely():
    cookie = dashboard_session.issue_session()
    response = _client(host="ods.lan:3010", peer="192.168.1.5").post(
        "/v1/chat/completions", json={},
        headers={"Cookie": f"{dashboard_session.SESSION_COOKIE_NAME}={cookie}", "Origin": "http://ods.lan:3010"})
    assert response.status_code == 200
    # The session cookie and caller credentials stay at the proxy.
    assert "cookie" not in response.json()["seen_headers"]


def test_caller_authorization_is_replaced_not_forwarded():
    response = _client().get("/props", headers={"Authorization": "Bearer guessed"})
    assert response.status_code == 200


@pytest.mark.parametrize("method,headers", [
    ("POST", {"Origin": "http://evil.example"}),
    ("POST", {"Sec-Fetch-Site": "cross-site"}),
    ("GET", {"Origin": "http://evil.example"}),
    ("GET", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}),
    ("GET", {"Origin": "http://127.0.0.1:3002"}),  # the dashboard is another origin
])
def test_cross_site_requests_are_rejected(method, headers):
    response = _client().request(method, "/v1/chat/completions", headers=headers)
    assert response.status_code == 403


def test_cross_site_navigation_to_the_ui_is_allowed():
    headers = [(b"host", b"127.0.0.1:3010"), (b"sec-fetch-site", b"cross-site"), (b"sec-fetch-mode", b"navigate")]
    assert not is_cross_site("GET", headers)


def test_mcp_cors_proxy_is_not_exposed():
    assert _client().get("/cors-proxy?url=http://169.254.169.254/").status_code == 404


def test_oversized_request_is_refused(monkeypatch):
    import native_chat
    monkeypatch.setattr(native_chat, "MAX_REQUEST_BYTES", 10)
    assert _client().post("/v1/chat/completions", content=b"x" * 11).status_code == 413


def test_unreachable_llama_is_502():
    def down(request):
        raise httpx.ConnectError("refused", request=request)
    client = TestClient(NativeChatProxy(
        upstream="http://127.0.0.1:8080", api_key=LLAMA_KEY,
        session_is_valid=dashboard_session.session_is_valid,
        session_cookie_name=dashboard_session.SESSION_COOKIE_NAME,
        transport=httpx.MockTransport(down)), base_url="http://127.0.0.1:3010", client=("127.0.0.1", 1))
    assert client.get("/props").status_code == 502


def test_chat_streams_without_buffering():
    """The first SSE event reaches the browser before llama-server sends the next."""
    first_event_delivered = asyncio.Event()

    async def events():
        yield b"data: {\"delta\":\"one\"}\n\n"
        await asyncio.wait_for(first_event_delivered.wait(), timeout=5)
        yield b"data: [DONE]\n\n"

    async def streaming_llama(request):
        assert request.headers["authorization"] == f"Bearer {LLAMA_KEY}"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=events())

    proxy = _proxy(streaming_llama)
    received = []

    async def send(message):
        received.append(message)
        if message.get("body", b"").startswith(b"data: {"):
            first_event_delivered.set()

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "query_string": b"",
             "scheme": "http", "client": ("127.0.0.1", 5), "server": ("127.0.0.1", 3010),
             "headers": [(b"host", b"127.0.0.1:3010"), (b"content-type", b"application/json")]}
    asyncio.run(proxy(scope, receive, send))
    bodies = [m["body"] for m in received if m["type"] == "http.response.body" and m["body"]]
    assert bodies == [b"data: {\"delta\":\"one\"}\n\n", b"data: [DONE]\n\n"]
    start = next(m for m in received if m["type"] == "http.response.start")
    assert all(LLAMA_KEY.encode() not in value for _, value in start["headers"])


def test_proxy_requires_the_llama_key():
    with pytest.raises(ValueError):
        NativeChatProxy(upstream="http://127.0.0.1:8080", api_key="", session_is_valid=lambda _: False,
                        session_cookie_name="x")
