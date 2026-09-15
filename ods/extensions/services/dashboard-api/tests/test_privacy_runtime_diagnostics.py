"""Exercise the Dashboard HTTP boundary against a real shield HTTP listener."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from routers import privacy


@contextmanager
def shield_listener(payload):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            body = json.dumps(payload if self.headers.get("Authorization") == "Bearer shield-fixture-key"
                              else {"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("target,cache", [
    ("https://inference.example.test/nested/v1", False),
    ("http://host.docker.internal:8080/api/v1", True),
])
def test_status_uses_authenticated_runtime_configuration(test_client, monkeypatch, target, cache):
    monkeypatch.setenv("SHIELD_API_KEY", "shield-fixture-key")
    monkeypatch.setenv("TARGET_API_URL", "http://stale-dashboard-default/v1")
    monkeypatch.setenv("PII_CACHE_ENABLED", str(not cache).lower())
    with shield_listener({"status": "ok", "target_api": target, "cache_enabled": cache}) as (port, requests):
        monkeypatch.setitem(privacy.SERVICES, "privacy-shield", {
            "host": "127.0.0.1", "port": port, "external_port": 18085,
        })
        response = test_client.get("/api/privacy-shield/status", headers=test_client.auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["target_api"] == target
    assert data["pii_cache_enabled"] is cache
    assert data["configuration_verified"] is True
    assert data["port"] == 18085
    assert requests == [("/health", "Bearer shield-fixture-key")]
    assert "shield-fixture-key" not in response.text


@pytest.mark.parametrize("key", ["", "wrong-fixture-key"])
def test_minimal_health_does_not_claim_configuration_is_verified(test_client, monkeypatch, key):
    monkeypatch.setenv("SHIELD_API_KEY", key)
    monkeypatch.setenv("TARGET_API_URL", "http://configured-fallback/v1")
    with shield_listener({"status": "ok", "target_api": "http://runtime/v1", "cache_enabled": False}) as (port, requests):
        monkeypatch.setitem(privacy.SERVICES, "privacy-shield", {"host": "127.0.0.1", "port": port})
        response = test_client.get("/api/privacy-shield/status", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["configuration_verified"] is False
    assert response.json()["target_api"] == "http://configured-fallback/v1"
    assert requests[0][1] == ("Bearer " + key if key else None)


@pytest.mark.parametrize("payload", [
    {"status": "ok", "target_api": "http://runtime/v1", "cache_enabled": "false"},
    {"status": "ok", "target_api": None, "cache_enabled": False},
    {"status": "ok", "target_api": "", "cache_enabled": False},
    {"status": "ok"},
    ["not a configuration object"],
])
def test_incomplete_health_preserves_unverified_compatibility_fields(test_client, monkeypatch, payload):
    monkeypatch.setenv("SHIELD_API_KEY", "shield-fixture-key")
    monkeypatch.setenv("TARGET_API_URL", "http://configured-fallback/v1")
    monkeypatch.setenv("PII_CACHE_ENABLED", "true")
    with shield_listener(payload) as (port, _):
        monkeypatch.setitem(privacy.SERVICES, "privacy-shield", {"host": "127.0.0.1", "port": port})
        response = test_client.get("/api/privacy-shield/status", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["configuration_verified"] is False
    assert response.json()["target_api"] == "http://configured-fallback/v1"
    assert response.json()["pii_cache_enabled"] is True
