from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("X402_CONFIG_PATH", "../../../config/x402/config.example.json")

from app.config import GatewayConfig
from app.main import create_app


CONFIG = GatewayConfig.model_validate(
    {
        "enabled": True,
        "seller": {
            "network": "eip155:84532",
            "asset": "USDC",
            "recipient": "0x0000000000000000000000000000000000000000",
            "facilitatorUrl": "https://x402.org/facilitator",
        },
        "policy": {
            "mode": "allowlist",
            "unprotectedByDefault": True,
            "devBypass": True,
        },
        "vendor": {
            "id": "dream-test-node",
            "name": "Dream Test Node",
            "description": "Test vendor node",
            "protocolVersion": "dream-server-v1",
            "version": "0.1.0",
            "operator": {"displayName": "Tester"},
        },
        "limits": {
            "maxPromptChars": 50000,
            "maxContextItems": 20,
            "maxFileBytes": 200000,
            "maxOutputTokens": 4096,
            "supportsStreaming": True,
            "supportsFiles": False,
            "timeouts": {"defaultSeconds": 60, "maxSeconds": 300},
            "rateLimits": {"requestsPerMinute": 10, "concurrentRequests": 2},
        },
        "capabilities": [
            {
                "id": "local_chat",
                "description": "General local LLM chat completion.",
                "path": "/v1/capabilities/local_chat",
                "streaming": True,
                "riskLevel": "low",
                "pricing": {"mode": "per_request", "amount": "0.001", "currency": "USDC"},
            },
            {
                "id": "coding_help",
                "description": "Explain, generate, or debug pasted code snippets.",
                "path": "/v1/capabilities/coding_help",
                "streaming": True,
                "riskLevel": "medium",
                "pricing": {"mode": "per_request", "amount": "0.003", "currency": "USDC"},
            },
            {
                "id": "coding_review",
                "description": "Review pasted code or diffs and return findings.",
                "path": "/v1/capabilities/coding_review",
                "streaming": True,
                "riskLevel": "medium",
                "pricing": {"mode": "per_request", "amount": "0.005", "currency": "USDC"},
            },
        ],
        "rules": [
            {
                "name": "local_chat",
                "kind": "http_route",
                "path": "/v1/capabilities/local_chat",
                "methods": ["POST"],
                "upstream": "http://llama-server:8080/v1/chat/completions",
                "price": {"mode": "per_request", "amount": "0.001", "currency": "USDC"},
            }
        ],
    }
)


def client() -> TestClient:
    return TestClient(create_app(CONFIG))


def test_vendor_contract_control_endpoints_are_public() -> None:
    app_client = client()

    assert app_client.get("/v1/health").status_code == 200
    assert app_client.get("/v1/health/ready").status_code == 200
    assert app_client.get("/v1/vendor").status_code == 200
    assert app_client.get("/v1/limits").status_code == 200
    assert app_client.get("/v1/capabilities").status_code == 200


def test_health_payload_uses_vendor_protocol_metadata() -> None:
    payload = client().get("/v1/health").json()

    assert payload["status"] == "ok"
    assert payload["service"] == "dream-server-x402-gateway"
    assert payload["version"] == "0.1.0"
    assert payload["protocolVersion"] == "dream-server-v1"
    assert payload["enabled"] is True


def test_readiness_reports_vendor_components() -> None:
    payload = client().get("/v1/health/ready").json()

    assert payload == {
        "status": "ok",
        "checks": {
            "api": "ok",
            "capability_registry": "ok",
            "payment_gateway": "ok",
            "payment_rules": "ok",
            "usage_metering": "ok",
        },
    }


def test_capabilities_advertise_v1_sellable_services() -> None:
    payload = client().get("/v1/capabilities").json()

    assert payload["provider"]["id"] == "dream-test-node"
    capabilities = {capability["id"]: capability for capability in payload["capabilities"]}
    assert set(capabilities) == {"local_chat", "coding_help", "coding_review"}
    assert capabilities["local_chat"]["streaming"] is True
    assert capabilities["local_chat"]["riskLevel"] == "low"
    assert capabilities["coding_review"]["pricing"] == {
        "amount": "0.005",
        "currency": "USDC",
        "mode": "per_request",
    }


def test_limits_advertise_streaming_and_request_bounds() -> None:
    payload = client().get("/v1/limits").json()

    assert payload["supportsStreaming"] is True
    assert payload["supportsFiles"] is False
    assert payload["maxPromptChars"] == 50000
    assert payload["timeouts"] == {"defaultSeconds": 60, "maxSeconds": 300}
    assert payload["rateLimits"] == {"requestsPerMinute": 10, "concurrentRequests": 2}
