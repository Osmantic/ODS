"""PII filtering must not turn JSON inference parameters into invalid JSON."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from .test_streaming_proxy import AUTH, _resp, proxy


@pytest.mark.parametrize("content_type", ["application/json", "application/problem+json"])
def test_json_numeric_parameters_and_keys_remain_intact(monkeypatch, content_type):
    phone = "2125550199"
    body = {"seed": int(phone), "stream": False, "metadata": {phone: None},
            "messages": [{"role": "user", "content": f'Call "{phone}" or mail seed@example.com.' }]}
    requests = []

    def upstream_response(request):
        data = json.loads(request.content)
        requests.append(data)
        return _resp(200, {"content-type": "application/json"},
                     [json.dumps({"reply": data["messages"][0]["content"]}).encode()])

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream_response))
    monkeypatch.setattr(proxy, "http_client", upstream)
    monkeypatch.setattr(proxy, "sessions", {})
    client = TestClient(proxy.app)
    try:
        response = client.post("/v1/chat/completions",
                               headers={**AUTH, "Content-Type": content_type},
                               content=json.dumps(body).encode())
        assert response.status_code == 200
        assert len(requests) == 1
        sent = requests[0]
        assert sent["seed"] == body["seed"] and type(sent["seed"]) is int
        assert sent["stream"] is False
        assert sent["metadata"] == {phone: None}
        assert phone not in sent["messages"][0]["content"]
        assert "seed@example.com" not in sent["messages"][0]["content"]
        assert response.json() == {"reply": body["messages"][0]["content"]}
    finally:
        client.close()
        asyncio.run(upstream.aclose())


def test_plain_text_still_scrubs_number_like_pii(monkeypatch):
    captured = []

    def upstream_response(request):
        captured.append(request.content)
        return _resp(200, {"content-type": "text/plain"}, [request.content])

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream_response))
    monkeypatch.setattr(proxy, "http_client", upstream)
    monkeypatch.setattr(proxy, "sessions", {})
    client = TestClient(proxy.app)
    try:
        response = client.post("/echo", headers={**AUTH, "Content-Type": "text/plain"},
                               content=b"Call 2125550199")
        assert response.status_code == 200
        assert b"2125550199" not in captured[0]
        assert response.text == "Call 2125550199"
    finally:
        client.close()
        asyncio.run(upstream.aclose())
