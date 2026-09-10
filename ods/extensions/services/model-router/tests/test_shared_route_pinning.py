import asyncio
import threading
import time

import httpx
import pytest
from test_router import router  # noqa: F401


def _make_pins(catalog=None, model=None, route=None):
    """Helper to construct pin headers, omitting None values."""
    headers = {}
    if catalog is not None:
        headers["X-ODS-Expected-Catalog"] = catalog
    if model is not None:
        headers["X-ODS-Expected-Model"] = model
    if route is not None:
        headers["X-ODS-Expected-Route"] = str(route)
    return headers


@pytest.fixture
def valid_pins():
    return {
        "X-ODS-Expected-Catalog": "concrete",
        "X-ODS-Expected-Model": "Concrete.gguf",
        "X-ODS-Expected-Route": "7",
    }


def test_queued_pin_cannot_run_on_replacement_model(router, valid_pins):
    mod, client, write_state, calls = router
    write_state(queue=True)
    responses = []
    worker = threading.Thread(target=lambda: responses.append(client.post('/v1/chat/completions',
        json={'model':'ods/shared','messages':[{'role':'user','content':'synthetic'}]},headers=valid_pins)))
    worker.start()
    try:
        deadline = time.monotonic()+2
        while mod._inflight == 0 and time.monotonic()<deadline:
            time.sleep(.01)
        assert mod._inflight == 1
        write_state(runtime='Replacement.gguf',route_seq=8)
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert responses[0].status_code == 409 and calls == []
        assert mod._inflight == 0
    finally:
        write_state()
        worker.join(timeout=2)


def test_matching_pin_200_one_call(router, valid_pins):
    mod, client, write_state, calls = router
    write_state()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "ods/shared", "messages": [{"role": "user", "content": "synthetic"}]},
        headers=valid_pins,
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["model"] == "Concrete.gguf"


@pytest.mark.parametrize('streaming', [False, True])
def test_backend_reported_identity_change_is_not_hidden_by_alias(router, valid_pins, streaming):
    mod, client, write_state, _calls = router
    write_state()
    def handler(request):
        if streaming:
            return httpx.Response(200, content=b'data: {"model":"Wrong.gguf","choices":[]}\n\ndata: [DONE]\n\n',
                headers={'content-type':'text/event-stream'})
        return httpx.Response(200,json={'model':'Wrong.gguf','choices':[]})
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    body = {'model':'ods/shared','messages':[{'role':'user','content':'synthetic'}],'stream':streaming}
    if streaming:
        with pytest.raises(mod.RouterError,match='Backend response identity changed'):
            client.post('/v1/chat/completions',json=body,headers=valid_pins)
    else:
        response = client.post('/v1/chat/completions',json=body,headers=valid_pins)
        assert response.status_code == 502
        assert response.json()['error']['type'] == 'response_identity_mismatch'
    assert mod._inflight == 0


@pytest.mark.parametrize("field", ["catalog", "model", "route"])
def test_mismatch_field_409_zero_calls(router, valid_pins, field):
    mod, client, write_state, calls = router
    write_state()
    pins = dict(valid_pins)
    if field == "catalog":
        pins["X-ODS-Expected-Catalog"] = "wrong-catalog"
    elif field == "model":
        pins["X-ODS-Expected-Model"] = "Wrong.gguf"
    elif field == "route":
        pins["X-ODS-Expected-Route"] = "99"

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "ods/shared", "messages": [{"role": "user", "content": "synthetic"}]},
        headers=pins,
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body.get("error", {}).get("type") == "route_changed"
    assert len(calls) == 0


@pytest.mark.parametrize("pins", [
    {"X-ODS-Expected-Catalog": "concrete"},
    {"X-ODS-Expected-Model": "Concrete.gguf"},
    {"X-ODS-Expected-Route": "7"},
    {"X-ODS-Expected-Catalog": "concrete", "X-ODS-Expected-Model": "Concrete.gguf"},
    {"X-ODS-Expected-Catalog": "concrete", "X-ODS-Expected-Route": "7"},
    {"X-ODS-Expected-Model": "Concrete.gguf", "X-ODS-Expected-Route": "7"},
    {"X-ODS-Expected-Catalog": "concrete", "X-ODS-Expected-Model": "Concrete.gguf", "X-ODS-Expected-Route": "not_int"},
    {"X-ODS-Expected-Catalog": "concrete", "X-ODS-Expected-Model": "Concrete.gguf", "X-ODS-Expected-Route": ""},
])
def test_partial_or_invalid_pin_400_zero_calls(router, pins):
    mod, client, write_state, calls = router
    write_state()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "ods/shared", "messages": [{"role": "user", "content": "synthetic"}]},
        headers=pins,
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("error", {}).get("type") == "route_precondition_invalid"
    assert len(calls) == 0


def test_legacy_empty_headers_200_one_call(router):
    mod, client, write_state, calls = router
    write_state()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "ods/shared", "messages": [{"role": "user", "content": "synthetic"}]},
        headers={},
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["model"] == "Concrete.gguf"


def test_get_metadata_and_sanitize_headers(router):
    mod, client, write_state, calls = router
    write_state()

    # Test GET /v1/models metadata
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    metadata = resp.json()['ods']
    assert metadata['catalogId'] == 'concrete'
    assert metadata['routedModel'] == 'Concrete.gguf'
    assert metadata['routeSeq'] == 7

    # Test _sanitize_headers strips pin headers
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [
            (b"x-ods-expected-catalog", b"concrete"),
            (b"x-ods-expected-model", b"Concrete.gguf"),
            (b"x-ods-expected-route", b"7"),
            (b"authorization", b"Bearer token"),
            (b"content-type", b"application/json"),
        ],
    }
    from starlette.requests import Request
    req = Request(scope)
    sanitized = mod._sanitize_headers(req)
    assert "x-ods-expected-catalog" not in sanitized
    assert "x-ods-expected-model" not in sanitized
    assert "x-ods-expected-route" not in sanitized
    assert "authorization" not in sanitized
    assert "content-type" in sanitized
