"""Exercise bounded request caching with native HTTP and real session lookup."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import httpx
import pytest
from cachetools import TTLCache
from fastapi.testclient import TestClient

from . import test_streaming_proxy as streaming

proxy = streaming.proxy


@pytest.fixture
def cached_proxy(monkeypatch):
    clock = [0]
    received = []
    state = {"status": 200}

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, body))
            self.send_response(state["status"])
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(proxy, "TARGET_API_BASE", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setattr(proxy, "TARGET_API_KEY", "not-needed")
    monkeypatch.setattr(proxy, "CACHE_ENABLED", True)
    monkeypatch.setattr(proxy, "CACHE_SIZE", 2)
    monkeypatch.setattr(proxy, "CACHE_TTL", 10)
    monkeypatch.setattr(
        proxy, "sessions", TTLCache(maxsize=2, ttl=3600, timer=lambda: clock[0])
    )
    monkeypatch.setattr(
        proxy, "TTLCache", lambda **kwargs: TTLCache(timer=lambda: clock[0], **kwargs)
    )
    monkeypatch.setattr(proxy, "http_client", httpx.AsyncClient())
    # Observe scans without replacing detector behavior or the get_session path.
    from pii_scrubber import PIIDetector
    scans = Mock(wraps=PIIDetector.scrub)
    monkeypatch.setattr(PIIDetector, "scrub", lambda self, text: scans(self, text))
    try:
        with TestClient(proxy.app) as client:
            yield client, clock, received, state, scans
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def send(client, text):
    result = client.post("/chat/completions", content=text, headers=streaming.AUTH)
    assert result.text == text
    return result


def test_real_http_cache_capacity_expiry_and_upstream_recovery(cached_proxy):
    client, clock, received, state, scans = cached_proxy
    first = "Contact alice@example.test"
    assert send(client, first).status_code == 200
    assert send(client, first).status_code == 200
    assert scans.call_count == 1
    for text in ("Contact bob@example.test", "Contact charlie@example.test", first):
        assert send(client, text).status_code == 200
    assert scans.call_count == 4  # the oldest result was evicted at capacity two
    assert len(proxy.sessions) == 1
    assert len(next(iter(proxy.sessions.values())).detector.pii_map) == 3
    clock[0] = 11
    state["status"] = 503
    assert send(client, first).status_code == 503
    assert scans.call_count == 5  # expired cache rescans even when upstream fails
    state["status"] = 200
    assert send(client, first).status_code == 200
    assert scans.call_count == 5
    assert all(path == "/v1/chat/completions" for path, _body in received)
    assert all(b"@example.test" not in body for _path, body in received)
    assert received[0][1] == received[1][1] == received[-1][1]


def test_session_expiry_never_reuses_another_detectors_cache(cached_proxy):
    client, clock, received, _state, scans = cached_proxy
    text = "Contact alice@example.test"
    send(client, text)
    original = next(iter(proxy.sessions.values()))
    clock[0] = 3601
    send(client, text)
    replacement = next(iter(proxy.sessions.values()))
    assert replacement is not original
    assert scans.call_count == 2
    assert received[0][1] != received[1][1]  # independent session token namespaces
    prior_tokens = received[0][1].decode()
    assert replacement.detector.restore(prior_tokens) == prior_tokens


@pytest.mark.parametrize("enabled,padding", [(False, ""), (True, "x" * 1000)])
def test_native_http_disabled_and_long_requests_always_scan(
    cached_proxy, monkeypatch, enabled, padding,
):
    client, _clock, received, _state, scans = cached_proxy
    monkeypatch.setattr(proxy, "CACHE_ENABLED", enabled)
    text = padding + "Contact alice@example.test"
    send(client, text)
    send(client, text)
    assert scans.call_count == 2
    assert all(b"alice@example.test" not in body for _path, body in received)
