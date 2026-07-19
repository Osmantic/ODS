"""Privacy Shield management endpoints."""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

import aiohttp
from fastapi import APIRouter, Depends

from config import AGENT_URL, ODS_AGENT_KEY, SERVICES
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
        url = f"{AGENT_URL}/v1/extension/{action}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ODS_AGENT_KEY}",
        }
        data = json.dumps({"service_id": "privacy-shield"}).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200

    try:
        ok = await asyncio.to_thread(_call_agent)
        if ok:
            msg = "Privacy Shield started. PII scrubbing is now active." if request.enable else "Privacy Shield stopped."
            return {"success": True, "message": msg}
        return {"success": False, "message": f"Host agent returned failure for {action}"}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
        except (OSError, UnicodeDecodeError):
            # Reading/decoding the error body is best-effort — the HTTP status
            # code below is the meaningful signal. Only tolerate the specific
            # failures that a body read can raise; anything else propagates.
            pass
        logger.warning("Privacy Shield toggle failed: HTTP %d: %s", e.code, body)
        return {"success": False, "message": f"Host agent returned error ({e.code}): {body or e.reason}"}
    except urllib.error.URLError:
        return {"success": False, "message": "Host agent not reachable", "note": "Ensure the ods host agent is running"}
    except asyncio.TimeoutError:
        return {"success": False, "message": "Operation timed out"}
    except OSError:
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
