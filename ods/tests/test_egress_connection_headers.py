"""Exercise egress caller auth and header forwarding through its ASGI HTTP boundary."""
import importlib.util
import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
# The LiteLLM gateway key, as LiteLLM and dashboard-api present it.
CALLER = {"Authorization": "Bearer caller-token"}


@pytest.mark.parametrize("endpoint", ["chat/completions", "completions", "responses"])
@pytest.mark.parametrize("status", [200, 429])
def test_slow_drip_is_bounded_and_closed(egress, monkeypatch, endpoint, status):
    monkeypatch.setattr(egress, "UPSTREAM_TIMEOUT_SECONDS", 0.12)
    closed = []

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(18):
                await asyncio.sleep(0.02)
                yield b"data: {\"choices\": []}\n\n"
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.append(True)

    def provider(request):
        return httpx.Response(status, stream=Drip(), headers={"retry-after": "7"})

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        started = time.monotonic()
        response = client.post("/v1/" + endpoint, json={"stream": True}, headers=CALLER)
        elapsed = time.monotonic() - started
        client.portal.call(transport.aclose)
    assert response.status_code == status
    assert response.headers["retry-after"] == "7"
    assert b"data:" in response.content
    assert b"[DONE]" not in response.content
    assert elapsed < 0.3
    assert closed == [True]
    assert egress.app.state.completion_sample is None


@pytest.mark.parametrize("stream", [False, True])
def test_header_wait_uses_same_total_deadline(egress, monkeypatch, stream):
    monkeypatch.setattr(egress, "UPSTREAM_TIMEOUT_SECONDS", 0.08)
    cancelled = []

    async def provider(request):
        try:
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(200, json={"ok": True})

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        response = client.post("/v1/chat/completions", json={"stream": stream}, headers=CALLER)
        client.portal.call(transport.aclose)
    assert response.status_code == 504
    assert response.json()["error"]["type"] == "upstream_timeout"
    assert cancelled == [True]


@pytest.mark.parametrize("stream", [False, True])
def test_headers_and_body_share_one_budget(egress, monkeypatch, stream):
    monkeypatch.setattr(egress, "UPSTREAM_TIMEOUT_SECONDS", 0.16)
    closed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: first\n\n"
            await asyncio.sleep(0.1)
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.append(True)

    async def provider(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, stream=Body())

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        response = client.post("/v1/chat/completions", json={"stream": stream}, headers=CALLER)
        client.portal.call(transport.aclose)
    if stream:
        assert response.status_code == 200
        assert response.content == b"data: first\n\n"
    else:
        assert response.status_code == 504
        assert response.json()["error"]["type"] == "upstream_timeout"
    assert closed == [True]


def test_downstream_cancellation_closes_upstream(egress, monkeypatch):
    entered = asyncio.Event()
    closed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.sleep(30)
            yield b"unused"

        async def aclose(self):
            closed.append(True)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Body()))) as upstream:
            monkeypatch.setattr(egress, "_http_client", lambda key="": upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=egress.app),
                                        base_url="http://fixture") as caller:
                task = asyncio.create_task(caller.post("/v1/chat/completions",
                    json={"stream": True}, headers=CALLER))
                await asyncio.wait_for(entered.wait(), timeout=2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert closed == [True]

    asyncio.run(exercise())


def test_real_httpx_slow_drip_releases_connection(egress, monkeypatch):
    """Use real HTTPX/httpcore reads, with only the destination redirected locally."""
    from dataclasses import replace

    monkeypatch.setattr(egress, "UPSTREAM_TIMEOUT_SECONDS", 0.12)
    prepare = egress.prepare_upstream_request

    async def exercise():
        released = asyncio.Event()

        async def provider(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            try:
                while True:
                    writer.write(b"data: partial\n\n")
                    await writer.drain()
                    await asyncio.sleep(0.02)
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                released.set()

        server = await asyncio.start_server(provider, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        def private_destination(**kwargs):
            request = prepare(**kwargs)
            return replace(request, url=f"http://127.0.0.1:{port}/v1/chat/completions",
                           tls_server_name="")

        monkeypatch.setattr(egress, "prepare_upstream_request", private_destination)
        try:
            async with server, httpx.AsyncClient(trust_env=False) as upstream:
                monkeypatch.setattr(egress, "_http_client", lambda key="": upstream)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=egress.app),
                                            base_url="http://fixture") as caller:
                    response = await asyncio.wait_for(caller.post("/v1/chat/completions",
                        json={"stream": True}, headers=CALLER), timeout=1)
                assert response.status_code == 200
                assert b"data: partial" in response.content
                await asyncio.wait_for(released.wait(), timeout=1)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


@pytest.fixture
def egress(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "egress_connection_test_app",
        ROOT / "extensions/services/remote-provider-egress/app/main.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_load_route", lambda: {
        "enabled": True, "transport": "direct",
        "routeFingerprint": "a" * 64,
        "provider": {"baseUrl": "https://provider.example/v1", "model": "real-model"},
    })
    monkeypatch.setattr(module, "validate_direct_provider_resolution", lambda route: [])
    monkeypatch.setattr(module, "read_provider_secret", lambda path: "provider-token")
    monkeypatch.setattr(module, "CALLER_KEY", "caller-token")
    yield module


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('ending', ['success', 'error', 'missing-usage', 'incomplete'])
def test_completion_telemetry_preserves_response_and_only_records_confirmed_usage(egress, monkeypatch, stream, ending):
    import json
    import itertools
    import time
    from types import SimpleNamespace
    from remote_provider import telemetry

    ticks = itertools.count(1000.0, 0.5)
    # Mocked providers finish within one Windows clock tick. Keep observer
    # timing deterministic without changing the ASGI/event-loop clocks.
    monkeypatch.setattr(telemetry, 'time', SimpleNamespace(
        monotonic=lambda: next(ticks), time=time.time))
    body = {'choices': [{'message': {'content': 'private answer'}}], 'usage': {'completion_tokens': 20}}
    if ending == 'missing-usage':
        body.pop('usage')
    if ending == 'error':
        body['error'] = {'message': 'private provider error'}
    wire = json.dumps(body).encode()
    if stream:
        wire = b'data: ' + wire + b'\n\n'
        if ending != 'incomplete':
            wire += b'data: [DONE]\n\n'

    class Fragmented(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(wire), 7):
                yield wire[offset:offset + 7]

    def provider(request):
        return httpx.Response(429 if ending == 'error' else 200, stream=Fragmented())

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, '_http_client', lambda key='': transport)
        response = client.post('/v1/chat/completions', json={'model': 'ods/current', 'stream': stream},
                               headers=CALLER)
        assert response.content == wire
        sample = client.get('/telemetry').json()['sample']
        if ending == 'success' or ending == 'incomplete' and not stream:
            assert sample['completionTokens'] == 20
            assert sample['elapsedMs'] == pytest.approx(500.0)
            assert sample['model'] == 'real-model'
            assert 'private' not in json.dumps(sample)
            route = egress._load_route()
            monkeypatch.setattr(egress, '_load_route', lambda: {**route, 'routeFingerprint': 'b' * 64})
            assert client.get('/telemetry').json() == {'sample': None}
        else:
            assert sample is None
        client.portal.call(transport.aclose)


@pytest.mark.parametrize("endpoint", ["chat/completions", "completions", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_request_connection_options_do_not_reach_provider(egress, monkeypatch, endpoint, stream):
    seen = []

    def provider(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        response = client.post("/v1/" + endpoint, json={"model": "ods/current", "stream": stream},
                               headers=[
                                   ("Connection", " X-Hop-One , authorization, content-type "),
                                   ("Connection", "x-HOP-two"),
                                   ("X-Hop-One", "local-only-one"),
                                   ("X-Hop-Two", "local-only-two"),
                                   ("Authorization", "Bearer caller-token"),
                                   ("X-Request-ID", "retained"),
                               ])
        client.portal.call(transport.aclose)
    assert response.status_code == 200
    assert len(seen) == 1
    headers = seen[0].headers
    assert "x-hop-one" not in headers
    assert "x-hop-two" not in headers
    assert headers["authorization"] == "Bearer provider-token"
    assert headers["content-type"] == "application/json"
    assert headers["x-request-id"] == "retained"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [200, 429])
def test_response_connection_options_stay_on_provider_hop(egress, monkeypatch, stream, status):
    def provider(request):
        return httpx.Response(status, content=b'{"ok": true}', headers=[
            ("Connection", " x-hop-one, content-type "),
            ("Connection", "X-HOP-TWO"),
            ("X-Hop-One", "internal-one"), ("X-Hop-Two", "internal-two"),
            ("Content-Type", "application/provider-local"),
            ("X-Request-ID", "retained"), ("Retry-After", "7"),
        ])

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        response = client.post("/v1/chat/completions", json={"stream": stream}, headers=CALLER)
        client.portal.call(transport.aclose)
    assert response.status_code == status
    assert response.content == b'{"ok": true}'
    assert "x-hop-one" not in response.headers
    assert "x-hop-two" not in response.headers
    assert response.headers.get("content-type") != "application/provider-local"
    assert response.headers["x-request-id"] == "retained"
    assert response.headers["retry-after"] == "7"
    assert response.headers["x-ods-provider-model"] == "real-model"


def _record_provider(egress, monkeypatch, client):
    seen = []

    def provider(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
    return seen, transport


@pytest.mark.parametrize("authorization", [
    None, "", "Bearer", "Bearer wrong-token", "Bearer caller-token-extra", "Basic caller-token",
    "caller-token", "Bearer provider-token",
])
@pytest.mark.parametrize("endpoint", ["chat/completions", "completions", "responses"])
def test_forwarding_requires_the_gateway_key(egress, monkeypatch, authorization, endpoint):
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(egress.app) as client:
        seen, transport = _record_provider(egress, monkeypatch, client)
        response = client.post("/v1/" + endpoint, json={"model": "ods/current"}, headers=headers)
        client.portal.call(transport.aclose)
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "caller_unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"
    assert seen == []


@pytest.mark.parametrize("authorization", ["Bearer caller-token", "bearer caller-token", "Bearer  caller-token "])
def test_gateway_key_is_accepted_and_never_forwarded(egress, monkeypatch, authorization):
    with TestClient(egress.app) as client:
        seen, transport = _record_provider(egress, monkeypatch, client)
        response = client.post("/v1/chat/completions", json={"model": "ods/current"},
                               headers={"Authorization": authorization})
        client.portal.call(transport.aclose)
    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer provider-token"
    assert "caller-token" not in str(seen[0].headers)


def test_probe_and_model_list_require_the_gateway_key(egress, monkeypatch):
    probed = []
    monkeypatch.setattr(egress, "probe_route_response", lambda *args, **kwargs: probed.append(1) or {})
    with TestClient(egress.app) as client:
        assert client.post("/probe").status_code == 401
        assert client.get("/v1/models").status_code == 401
        assert client.post("/probe", headers=CALLER).status_code == 200
        assert client.get("/v1/models", headers=CALLER).status_code == 200
    assert probed == [1]


@pytest.mark.parametrize("method, path", [
    ("POST", "/health"), ("DELETE", "/health"), ("POST", "/telemetry"), ("GET", "/health/"),
    ("GET", "/v1/chat/completions"), ("GET", "/anything"),
])
def test_only_status_reads_are_open(egress, method, path):
    with TestClient(egress.app) as client:
        assert client.request(method, path).status_code == 401


def test_status_reads_need_no_key(egress, monkeypatch):
    monkeypatch.setattr(egress, "provider_secret_status",
                        lambda path: {"configured": True, "path": str(path), "bytes": 14})
    with TestClient(egress.app) as client:
        health = client.get("/health")
        telemetry = client.get("/telemetry")
    assert health.status_code == 200 and health.json()["ready"] is True
    assert telemetry.status_code == 200 and telemetry.json() == {"sample": None}


def test_missing_gateway_key_fails_closed(egress, monkeypatch):
    monkeypatch.setattr(egress, "CALLER_KEY", "")
    monkeypatch.setattr(egress, "provider_secret_status",
                        lambda path: {"configured": True, "path": str(path), "bytes": 14})
    with TestClient(egress.app) as client:
        seen, transport = _record_provider(egress, monkeypatch, client)
        for headers in ({}, {"Authorization": "Bearer "}, CALLER):
            response = client.post("/v1/chat/completions", json={"model": "ods/current"}, headers=headers)
            assert response.status_code == 503
            assert response.json()["error"]["type"] == "missing_caller_key"
        health = client.get("/health").json()
        client.portal.call(transport.aclose)
    assert seen == []
    assert health["ready"] is False
    assert health["reason"] == "missing_caller_key"


def test_a_provider_probe_does_not_stall_other_requests(egress, monkeypatch):
    """The probe's blocking HTTP calls must run off the event loop (#2699)."""
    import asyncio
    import threading
    import time

    probed = {}

    def slow_probe(route, **options):
        # Stands in for the real probe's blocking urllib calls.
        probed["thread"] = threading.get_ident()
        probed["start"] = time.monotonic()
        time.sleep(0.4)
        probed["end"] = time.monotonic()
        return {"ok": True, "verifiedAt": options["verified_at"]}

    monkeypatch.setattr(egress, "probe_route_response", slow_probe)
    ticks = []

    async def scenario():
        async def other_work():
            for _ in range(30):
                await asyncio.sleep(0.02)
                ticks.append(time.monotonic())

        response, _ = await asyncio.gather(egress.probe(), other_work())
        return threading.get_ident(), response

    loop_thread, response = asyncio.run(scenario())
    assert response.status_code == 200
    assert probed["thread"] != loop_thread
    # The event loop kept serving other work while the probe was blocked.
    assert sum(probed["start"] < tick < probed["end"] for tick in ticks) >= 5


def test_direct_provider_clients_are_bounded_least_recently_used_first(egress):
    """Past provider endpoints do not keep connection pools open forever (#2701)."""
    import asyncio

    def key(number):
        return f"https:provider-{number}.example:443"

    async def scenario():
        egress.app.state.direct_http_clients = {}
        first = [egress._http_client(key(number)) for number in range(4)]
        # Reusing an endpoint keeps its client and makes it the most recent.
        assert egress._http_client(key(0)) is first[0]
        for number in (4, 5):
            egress._http_client(key(number))
        while egress._closing_clients:
            await asyncio.sleep(0)
        kept = list(egress.app.state.direct_http_clients)
        closed = [client.is_closed for client in first]
        for client in egress.app.state.direct_http_clients.values():
            await client.aclose()
        return kept, closed

    kept, closed = asyncio.run(scenario())
    assert len(kept) == egress.MAX_DIRECT_HTTP_CLIENTS == 4
    assert kept == [key(3), key(0), key(4), key(5)]
    assert closed == [False, True, True, False]


def test_a_provider_transport_error_is_not_echoed_to_the_caller(egress, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("provider-side detail 203.0.113.7:443", request=request)

    with TestClient(egress.app) as client:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
        monkeypatch.setattr(egress, "_http_client", lambda key="": transport)
        response = client.post("/v1/chat/completions", json={"model": "ods/current"}, headers=CALLER)
        client.portal.call(transport.aclose)
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "upstream_unavailable"
    assert "203.0.113.7" not in response.text and "provider-side detail" not in response.text
