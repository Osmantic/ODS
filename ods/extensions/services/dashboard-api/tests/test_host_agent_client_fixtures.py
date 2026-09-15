import pytest
from pathlib import Path
import sys
import httpx

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from host_agent_client import (
    AgentUnavailable,
    AgentTimeout,
    AgentHTTPError,
    AgentProtocolError,
    _decode_json,
    _raise_for_status,
)

def test_raise_for_status_on_success():
    req = httpx.Request("GET", "http://agent/v1/health")
    resp = httpx.Response(200, request=req, json={"status": "ok"})
    # Should not raise
    _raise_for_status(resp)

def test_raise_for_status_on_error():
    req = httpx.Request("GET", "http://agent/v1/status")
    resp = httpx.Response(502, request=req, json={"detail": "Daemon unhealthy"})
    with pytest.raises(AgentHTTPError) as exc_info:
        _raise_for_status(resp)
    assert exc_info.value.status_code == 502
    assert "Daemon unhealthy" in exc_info.value.detail

def test_decode_json_invalid_payload():
    req = httpx.Request("GET", "http://agent/v1/status")
    resp = httpx.Response(200, request=req, text="NOT JSON")
    with pytest.raises(AgentProtocolError):
        _decode_json(resp)
