"""The advertised short-request cache must serve actual proxy traffic."""
from unittest.mock import Mock

import pytest
from cachetools import TTLCache

from . import test_streaming_proxy as streaming

proxy = streaming.proxy
client = streaming.client
install_upstream = streaming.install_upstream


def configure(monkeypatch, enabled, clock):
    monkeypatch.setattr(proxy, "CACHE_ENABLED", enabled)
    monkeypatch.setattr(proxy, "CACHE_SIZE", 2)
    monkeypatch.setattr(proxy, "CACHE_TTL", 10)
    monkeypatch.setattr(proxy, "TTLCache", lambda **kwargs: TTLCache(timer=lambda:clock[0], **kwargs))
    shield = proxy.CachedPrivacyShield()
    shield.detector.scrub = Mock(wraps=shield.detector.scrub)
    monkeypatch.setattr(proxy, "get_session", lambda request: shield)
    return shield


def echo_upstream(install_upstream):
    captured = []
    def receive(request):
        captured.append(request.content)
        assert b"alice@example.test" not in request.content
        return streaming._resp(200, {"content-type":"text/plain"}, [request.content])
    install_upstream(receive)
    return captured


@pytest.mark.parametrize("enabled,padding,expected_scans", [(True, "", 1), (False, "", 2), (True, "x" * 1000, 2)])
def test_proxy_uses_cache_only_for_enabled_short_requests(client, install_upstream, monkeypatch, enabled, padding, expected_scans):
    shield = configure(monkeypatch, enabled, [0])
    captured = echo_upstream(install_upstream)
    text = padding + "Contact alice@example.test"
    for _ in range(2):
        response = client.post("/v1/chat/completions", content=text, headers=streaming.AUTH)
        assert response.status_code == 200
        assert response.text == text
    assert len(captured) == 2
    assert shield.detector.scrub.call_count == expected_scans
    assert shield.detector.get_stats()["unique_pii_count"] == 1


def test_request_cache_expires_without_losing_response_restoration(client, install_upstream, monkeypatch):
    clock = [0]
    shield = configure(monkeypatch, True, clock)
    captured = echo_upstream(install_upstream)
    text = "Contact alice@example.test"
    for now, scans in [(0, 1), (9, 1), (11, 2)]:
        clock[0] = now
        response = client.post("/v1/chat/completions", content=text, headers=streaming.AUTH)
        assert response.text == text
        assert shield.detector.scrub.call_count == scans
    assert captured[0] == captured[1] == captured[2]
