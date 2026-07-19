"""Privacy Shield management endpoints."""

import asyncio
import logging
import os

import aiohttp
from fastapi import APIRouter, Depends

from config import SERVICES
from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentTimeout,
    AgentUnavailable,
    request_json as request_agent_json,
)
from models import PrivacyShieldStatus, PrivacyShieldToggle
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["privacy"])


@router.get("/api/privacy-shield/status", response_model=PrivacyShieldStatus)
async def get_privacy_shield_status(api_key: str = Depends(verify_api_key)):
    """Get Privacy Shield status and configuration."""
    _ps = SERVICES.get("privacy-shield", {})
    shield_port = int(os.environ.get("SHIELD_PORT", str(_ps.get("port", 0))))
    shield_url = f"http://{_ps.get('host', 'privacy-shield')}:{shield_port}"

    # Check health directly — no Docker socket needed
    service_healthy = False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            async with session.get(f"{shield_url}/health") as resp:
                service_healthy = resp.status == 200
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
        logger.debug("Privacy-shield health check failed")

    container_running = service_healthy

    return PrivacyShieldStatus(
        enabled=container_running and service_healthy,
        container_running=container_running,
        port=shield_port,
        target_api=os.environ.get("TARGET_API_URL", f"http://{SERVICES.get('llama-server', {}).get('host', 'llama-server')}:{SERVICES.get('llama-server', {}).get('port', 0)}/v1"),
        pii_cache_enabled=os.environ.get("PII_CACHE_ENABLED", "true").lower() == "true",
        message="Privacy Shield is active" if (container_running and service_healthy) else "Privacy Shield is not running. Check: docker compose ps privacy-shield"
    )


@router.post("/api/privacy-shield/toggle")
async def toggle_privacy_shield(request: PrivacyShieldToggle, api_key: str = Depends(verify_api_key)):
    """Enable or disable Privacy Shield via host agent."""
    action = "start" if request.enable else "stop"

    def _call_agent():
        request_agent_json(
            "POST",
            f"/v1/extension/{action}",
            payload={"service_id": "privacy-shield"},
            timeout=30,
        )
        return True

    try:
        ok = await asyncio.to_thread(_call_agent)
        if ok:
            msg = "Privacy Shield started. PII scrubbing is now active." if request.enable else "Privacy Shield stopped."
            return {"success": True, "message": msg}
        return {"success": False, "message": f"Host agent returned failure for {action}"}
    except AgentHTTPError as exc:
        logger.warning(
            "Privacy Shield toggle failed: HTTP %d: %s",
            exc.status_code,
            exc.response_text,
        )
        return {
            "success": False,
            "message": f"Host agent returned error ({exc.status_code}): {exc.response_text or exc.detail}",
        }
    except AgentTimeout:
        return {"success": False, "message": "Operation timed out"}
    except AgentUnavailable:
        return {"success": False, "message": "Host agent not reachable", "note": "Ensure the ods host agent is running"}
    except (AgentProtocolError, OSError):
        logger.exception("Privacy Shield toggle failed")
        return {"success": False, "message": "Privacy Shield operation failed"}


@router.get("/api/privacy-shield/stats")
async def get_privacy_shield_stats(api_key: str = Depends(verify_api_key)):
    """Get Privacy Shield usage statistics."""
    _ps = SERVICES.get("privacy-shield", {})
    shield_port = int(os.environ.get("SHIELD_PORT", str(_ps.get("port", 0))))
    shield_url = f"http://{_ps.get('host', 'privacy-shield')}:{shield_port}"
    shield_api_key = os.environ.get("SHIELD_API_KEY", "")
    if not shield_api_key:
        return {"error": "SHIELD_API_KEY not configured", "enabled": False}
    headers = {"Authorization": f"Bearer {shield_api_key}"}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(f"{shield_url}/stats", headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {"error": "Privacy Shield not responding", "status": resp.status}
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
        logger.exception("Cannot reach Privacy Shield")
        return {"error": "Cannot reach Privacy Shield", "enabled": False}
