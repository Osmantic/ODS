"""Exercise egress caller auth and header forwarding through its ASGI HTTP boundary."""
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
# The LiteLLM gateway key, as LiteLLM and dashboard-api present it.
CALLER = {"Authorization": "Bearer caller-token"}


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
