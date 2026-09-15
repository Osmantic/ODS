import pytest
import time
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from pixel_connection_public import normalize_connection_result

def test_valid_connection_result():
    expiry = int(time.time()) + 3600
    valid_payload = {
        "schemaVersion": 1,
        "endpoint": "http://localhost:8080/v1",
        "deviceId": "device-0123456789abcdef",
        "expiresAt": expiry,
        "expected": {
            "catalogId": "hermes-3-8b",
            "runtimeModelId": "hermes-3-8b-instruct",
        },
        "metadata": {
            "catalogId": "hermes-3-8b",
            "routedModel": "hermes-3-8b-instruct",
            "identitySource": "ods-verified-route",
            "routeSeq": 1,
            "contextLength": 32768,
            "capabilities": {
                "chat": True,
                "tools": True,
                "vision": False,
                "agentViable": True,
            },
            "maxOutputTokens": 4096,
            "expiresAt": expiry,
            "execution": "client-owned",
        },
    }
    normalized = normalize_connection_result(valid_payload)
    assert normalized["schemaVersion"] == 1
    assert normalized["deviceId"] == "device-0123456789abcdef"

def test_invalid_device_id_rejected():
    with pytest.raises(ValueError, match="invalid-connection-result"):
        normalize_connection_result({"schemaVersion": 1, "deviceId": "invalid!"})
