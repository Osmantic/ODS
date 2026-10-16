"""Regression tests for privacy-shield hardening.

1. PII maps must not be shared across clients that use the same API key.
2. The PII map is bounded and lookups stay O(1).
3. JSON request bodies are scrubbed after decoding (escapes cannot bypass).
4. Prefix-only API keys, E.164 phones, 13-19 digit cards, compressed IPv6.
"""
import asyncio
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from .test_streaming_proxy import AUTH, _resp, proxy
from pii_scrubber import PIIDetector

TOKEN = re.compile(r"<PII_[a-z_]+_[0-9a-f]{12}>")


@pytest.fixture
def run_proxy(monkeypatch):
    clients = []

    def _run(handler):
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(proxy, "http_client", upstream)
        monkeypatch.setattr(proxy, "sessions", {})
        client = TestClient(proxy.app)
        clients.append((client, upstream))
        return client

    yield _run
    for client, upstream in clients:
        client.close()
        asyncio.run(upstream.aclose())


# ── 1. Cross-client isolation ──────────────────────────────────────────────

def test_token_from_one_client_is_not_restored_for_another(run_proxy):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        # Echo the token issued for the *first* request on every response.
        return _resp(200, {"content-type": "application/json"},
                     [json.dumps({"reply": TOKEN.search(seen[0]).group(0)}).encode()])

    client = run_proxy(handler)
    first = client.post("/v1/chat/completions", headers=AUTH,
                        json={"messages": [{"content": "alice@corp.com"}]})
    assert first.json() == {"reply": "alice@corp.com"}
    second = client.post("/v1/chat/completions", headers=AUTH,
                         json={"messages": [{"content": "hello"}]})
    assert "alice@corp.com" not in second.text


def test_opt_in_session_header_keeps_mapping_per_session(run_proxy):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return _resp(200, {"content-type": "application/json"},
                     [json.dumps({"reply": TOKEN.search(seen[0]).group(0)}).encode()])

    client = run_proxy(handler)
    a = {**AUTH, "X-Shield-Session": "conv-a"}
    client.post("/v1/x", headers=a, json={"content": "alice@corp.com"})
    same = client.post("/v1/x", headers=a, json={"content": "hi"})
    other = client.post("/v1/x", headers={**AUTH, "X-Shield-Session": "conv-b"},
                        json={"content": "hi"})
    assert same.json() == {"reply": "alice@corp.com"}
    assert "alice@corp.com" not in other.text


# ── 2. Bounded map, O(1) reuse ─────────────────────────────────────────────

def test_pii_map_is_bounded_and_fast():
    detector = PIIDetector(max_entries=1000)
    text = " ".join(f"user{i}@example.com" for i in range(20000))
    start = time.monotonic()
    detector.scrub(text)
    elapsed = time.monotonic() - start
    assert len(detector.pii_map) == 1000
    assert elapsed < 5, f"scrub took {elapsed:.1f}s"
    # Most recent mapping still restores; evicted ones are left as tokens.
    last = detector.scrub("user19999@example.com")
    assert detector.restore(last) == "user19999@example.com"


# ── 3. JSON-aware request scrubbing ────────────────────────────────────────

@pytest.mark.parametrize("raw_body,secret", [
    ('{"content": "mail alice\\u0040corp.com now"}', "alice@corp.com"),
    ('{"content": "line\\nbob.smith@corp.com"}', "bob.smith@corp.com"),
    ('{"bob.smith@corp.com": 1}', "bob.smith@corp.com"),
])
def test_json_body_is_scrubbed_after_decoding(run_proxy, raw_body, secret):
    forwarded = []

    def handler(request):
        forwarded.append(request.content.decode())
        return _resp(200, {"content-type": "application/json"}, [b"{}"])

    client = run_proxy(handler)
    response = client.post("/v1/chat/completions", content=raw_body,
                           headers={**AUTH, "content-type": "application/json"})
    assert response.status_code == 200
    decoded = json.loads(forwarded[0])  # must still be valid JSON
    assert secret not in json.dumps(decoded, ensure_ascii=False)
    assert TOKEN.search(forwarded[0])


def test_json_without_pii_is_forwarded_byte_exact(run_proxy):
    forwarded = []

    def handler(request):
        forwarded.append(request.content)
        return _resp(200, {"content-type": "application/json"}, [b"{}"])

    client = run_proxy(handler)
    raw = b'{ "a" :  "plain \\u00e9 text" }'
    client.post("/v1/x", content=raw, headers={**AUTH, "content-type": "application/json"})
    assert forwarded == [raw]


# ── 4. Detector coverage ───────────────────────────────────────────────────

@pytest.mark.parametrize("secret,pii_type", [
    ("sk-proj_abcDEF1234567890xyzQ", "api_key"),
    ("ghp_" + "a1B2" * 9, "api_key"),
    ("AKIAIOSFODNN7EXAMPLE", "api_key"),
    ("+44 20 7946 0958", "phone"),
    ("+91 98765 43210", "phone"),
    ("3782 822463 10005", "credit_card"),
    ("378282246310005", "credit_card"),
    ("6011111111111117", "credit_card"),
    ("4222222222222", "credit_card"),
    ("fe80::1ff:fe23:4567:890a", "ip_address"),
    ("2001:db8::1", "ip_address"),
])
def test_new_detectors_scrub_whole_value(secret, pii_type):
    detector = PIIDetector()
    text = f"value: {secret} end"
    scrubbed = detector.scrub(text)
    assert scrubbed == f"value: {detector._reverse[secret]} end"
    assert f"<PII_{pii_type}_" in scrubbed
    assert detector.restore(scrubbed) == text


@pytest.mark.parametrize("text", [
    "meeting at 12:30:45 today",
    "c++ and std::vector<int>",
    "order 378282246310006 placed",  # fails Luhn
    "sk-short",
    "build +12 passed",
])
def test_new_detectors_leave_non_pii_alone(text):
    assert PIIDetector().scrub(text) == text
