"""Privacy toggle must not turn an accepted host retry into active protection."""
import json

import httpx
import pytest

import host_agent_client


@pytest.mark.parametrize("pending", [True, False])
def test_toggle_preserves_host_start_receipt(test_client, monkeypatch, pending):
    requests = []

    def host(request):
        requests.append(request)
        # These are the payloads from _start_enable_retry and _handle_extension.
        return httpx.Response(
            202 if pending else 200,
            json={"status": "retrying" if pending else "ok",
                  "service_id": "privacy-shield", "action": "start"},
        )

    with httpx.Client(base_url="http://host-agent", transport=httpx.MockTransport(host)) as client:
        monkeypatch.setattr(host_agent_client, "_sync_client", client)
        response = test_client.post(
            "/api/privacy-shield/toggle", json={"enable": True},
            headers=test_client.auth_headers,
        )

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/v1/extension/start"
    assert json.loads(requests[0].content) == {"service_id": "privacy-shield"}
    assert response.status_code == (202 if pending else 200)
    receipt = response.json()
    assert receipt["success"] is True
    if pending:
        assert receipt["pending"] is True
        assert receipt["status"] == "retrying"
        assert "active" not in receipt["message"].lower()
        assert "accepted" in receipt["message"].lower()
    else:
        assert "started" in receipt["message"].lower()
        assert not receipt.get("pending")
