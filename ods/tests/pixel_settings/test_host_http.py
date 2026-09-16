"""Actual owner-authenticated host HTTP and storage; no installed runtime claims."""
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading

import pytest


@pytest.fixture(scope="module")
def server():
    path = Path(__file__).resolve().parents[2] / "bin/ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("_pixel_settings_http_agent", path)
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    spec.loader.exec_module(agent)
    agent.AGENT_API_KEY = "synthetic-settings-key"
    listener = ThreadingHTTPServer(("127.0.0.1", 0), agent.AgentHandler)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        yield agent, listener
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
        sys.modules.pop(spec.name, None)


@pytest.fixture
def api_request(server, tmp_path):
    agent, listener = server
    agent.DATA_DIR = tmp_path

    def call(method="GET", body=None, *, raw=None, token="synthetic-settings-key", path=None):
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
            target = path or ("/v1/pixel/settings/save" if method == "POST" else "/v1/pixel/settings")
            connection.request(method, target, body=payload, headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            result = (response.status, json.loads(response.read()))
            if response.status == 200:
                assert "no-store" in response.getheader("Cache-Control", "")
            return result
        finally:
            connection.close()
    return call


def test_pristine_get_and_auth_do_not_create_state(api_request, tmp_path):
    status, result = api_request()
    assert status == 200
    assert result["configuration"] == {"schemaVersion": 1, "revision": 0, "preferences": {}}
    assert result["runtime"]["status"] == "not-inspected"
    assert api_request(token="wrong")[0] == 403
    assert api_request("POST", {}, token="wrong")[0] == 403
    assert list(tmp_path.iterdir()) == []


def test_actual_sparse_save_reset_reload_and_stale(api_request, tmp_path):
    status, saved = api_request("POST", {"expectedRevision": 0,
        "changes": {"contextTokens": 65536, "verbosity": "full"}})
    assert status == 200
    assert saved["configuration"]["revision"] == 1
    assert saved["runtime"]["status"] == "not-inspected"
    status, reset = api_request("POST", {"expectedRevision": 1, "changes": {"contextTokens": None}})
    assert status == 200
    assert reset["configuration"]["preferences"] == {"contextTokens": None, "verbosity": "full"}
    assert api_request() == (200, reset)
    assert api_request("POST", {"expectedRevision": 1, "changes": {}})[0] == 409
    assert not (tmp_path / "pixel-providers/provider-config.json").exists()


@pytest.mark.parametrize("raw", [b"NaN", b"[]", b"{}", b'"\xff"',
    b'{"expectedRevision":true,"changes":{}}',
    b'{"expectedRevision":0,"changes":{},"changes":{}}',
    b'{"expectedRevision":0,"changes":{"contextTokens":true}}',
    b'{"expectedRevision":0,"changes":{"apiKey":"do-not-echo"}}',
    b'{"expectedRevision":0,"changes":{},"applied":true}',
    b'{"expectedRevision":0,"changes":{"temperature":1e999}}'])
def test_invalid_requests_do_not_create_state_or_echo_input(api_request, tmp_path, raw):
    status, result = api_request("POST", raw=raw)
    assert status == 400
    assert "do-not-echo" not in json.dumps(result)
    assert list(tmp_path.iterdir()) == []


def test_oversize_and_unknown_routes(api_request, tmp_path):
    assert api_request("POST", raw=b"x" * (256 * 1024 + 1))[0] == 413
    assert api_request(path="/v1/pixel/settings?applied=true")[0] == 404
    assert api_request("POST", {}, path="/v1/pixel/settings/apply")[0] == 404
    assert list(tmp_path.iterdir()) == []


def test_concurrent_writers_have_one_winner(api_request):
    # Provision first so this tests CAS, not first-root creation arbitration.
    assert api_request("POST", {"expectedRevision": 0, "changes": {}})[0] == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda value: api_request("POST", {"expectedRevision": 1,
            "changes": {"verbosity": value}})[0], ["full", "on"]))
    assert sorted(results) == [200, 409]
    assert api_request()[1]["configuration"]["revision"] == 2


def test_corrupt_disk_is_unavailable_not_defaults_or_secret_leak(api_request, tmp_path):
    assert api_request("POST", {"expectedRevision": 0, "changes": {}})[0] == 200
    path = tmp_path / "pixel-providers/pixel-settings.json"
    damaged = b'{"private":"do-not-echo"}'
    path.write_bytes(damaged)
    status, result = api_request()
    assert status == 503
    assert "do-not-echo" not in json.dumps(result)
    assert path.read_bytes() == damaged


@pytest.mark.parametrize("headers", [
    [("Content-Length", "2"), ("Content-Length", "2")],
    [("Content-Length", "2"), ("Transfer-Encoding", "chunked")],
    [("Content-Length", "+2")], [("Content-Length", "0")], [],
])
@pytest.mark.parametrize("path", ["/v1/pixel/settings/save", "/v1/pixel/settings/runtime"])
def test_ambiguous_http_framing_rejected_before_state(server, tmp_path, headers, path):
    agent, listener = server
    agent.DATA_DIR = tmp_path
    connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Authorization", "Bearer synthetic-settings-key")
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders(b"{}")
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_runtime_requires_owner_before_controller(api_request, monkeypatch, method):
    import pixel_access_client
    monkeypatch.setattr(pixel_access_client, "request_access", lambda *_a, **_k: pytest.fail("controller called"))
    assert api_request(method, {}, token="wrong", path="/v1/pixel/settings/runtime")[0] == 403


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"NaN", b'"\xff"',
    b'{"operation":"apply","revision":"a","settingsRevision":0}',
    b'{"operation":"apply","operation":"recover","revision":"a","settingsRevision":0}',
    b'{"operation":"apply","revision":"a","settingsRevision":true}'])
def test_runtime_invalid_request_never_reaches_controller(api_request, monkeypatch, raw):
    import pixel_access_client
    monkeypatch.setattr(pixel_access_client, "request_access", lambda *_a, **_k: pytest.fail("controller called"))
    assert api_request("POST", raw=raw, path="/v1/pixel/settings/runtime")[0] == 400


def test_runtime_oversize_and_query_rejected(api_request, monkeypatch):
    import pixel_access_client
    monkeypatch.setattr(pixel_access_client, "request_access", lambda *_a, **_k: pytest.fail("controller called"))
    assert api_request("POST", raw=b"x" * 2049, path="/v1/pixel/settings/runtime")[0] == 413
    assert api_request(path="/v1/pixel/settings/runtime?path=/etc")[0] == 404
