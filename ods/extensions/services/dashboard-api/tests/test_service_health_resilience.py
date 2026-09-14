"""Tests for service health resilience and fallback status mapping in helpers.py."""

from unittest.mock import patch
import pytest

from helpers import (
    _service_status_from_config,
    _check_tailscale_health,
    _check_host_systemd_health,
)
from host_agent_client import AgentClientError


def test_service_status_from_config_handles_missing_keys():
    status = _service_status_from_config("test-service", {}, "healthy")
    assert status.id == "test-service"
    assert status.name == "test-service"
    assert status.port == 0
    assert status.external_port == 0
    assert status.status == "healthy"


def test_service_status_from_config_handles_non_dict_config():
    status = _service_status_from_config("svc-1", None, "down")
    assert status.id == "svc-1"
    assert status.name == "svc-1"
    assert status.status == "down"


@pytest.mark.asyncio
async def test_check_tailscale_health_not_deployed_on_error():
    with patch("helpers.request_agent_json", side_effect=AgentClientError("Unreachable")):
        res = await _check_tailscale_health("tailscale", {"name": "Tailscale", "port": 41641})
        assert res.status == "not_deployed"


@pytest.mark.asyncio
async def test_check_tailscale_health_healthy_when_authenticated():
    with patch("helpers.request_agent_json", return_value={"running": True, "authenticated": True}):
        res = await _check_tailscale_health("tailscale", {"name": "Tailscale", "port": 41641})
        assert res.status == "healthy"


@pytest.mark.asyncio
async def test_check_host_systemd_health_handles_invalid_port():
    res = await _check_host_systemd_health("opencode", {"name": "OpenCode", "port": "invalid"})
    assert res.status == "not_deployed"


@pytest.mark.asyncio
async def test_check_host_systemd_health_down_on_agent_error():
    with patch("helpers.request_agent_json", side_effect=AgentClientError("AgentError")):
        res = await _check_host_systemd_health("opencode", {"name": "OpenCode", "port": 4096})
        assert res.status == "down"
