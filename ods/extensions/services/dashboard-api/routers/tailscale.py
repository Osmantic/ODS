"""Tailscale (remote access) — dashboard-api proxy in front of the host-agent.

The host-agent has /v1/tailscale/status, which queries the ods-tailscale
container or falls back to host-native Tailscale when the container is not
running. This module exposes that to the dashboard UI via
/api/tailscale/status.

For the typical lifecycle the operator runs:
  1. Generate an auth key at https://login.tailscale.com/admin/settings/keys
  2. Set TS_AUTHKEY in .env (via the existing Settings page or `ods env`)
  3. Enable the tailscale extension (via Extensions page or `ods enable tailscale`)
  4. Container starts, joins the tailnet, shows up in `tailscale status`
  5. The device is reachable as <hostname>.<tailnet>.ts.net from any
     other tailnet member

The status endpoint is what powers the "Remote Access" section in the
dashboard's Settings page — it shows the user whether their device is
on the tailnet, what its tailnet hostname is, and whether the daemon
is authenticated.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentTimeout,
    AgentUnavailable,
    request_json as request_agent_json,
)
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tailscale"])


def _proxy_agent(path: str, timeout: int = 15) -> dict:
    """Forward a GET to the host-agent. Translates HTTPError → HTTPException."""
    try:
        return request_agent_json("GET", path, timeout=timeout)
    except AgentHTTPError as exc:
        logger.info("host-agent GET %s -> %s", path, exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except AgentTimeout as exc:
        logger.warning("host-agent GET %s timed out", path)
        raise HTTPException(status_code=504, detail="ODS host agent request timed out.") from exc
    except AgentUnavailable as exc:
        logger.warning("host-agent GET %s unreachable: %s", path, exc)
        raise HTTPException(status_code=503, detail="ODS host agent is not reachable.") from exc
    except AgentProtocolError as exc:
        logger.exception("host-agent GET %s failed", path)
        raise HTTPException(status_code=500, detail=f"Host agent call failed: {exc}") from exc


@router.get("/api/tailscale/status", dependencies=[Depends(verify_api_key)])
async def tailscale_status() -> dict:
    """Current Tailscale state for this device.

    Three shapes (always 200, never an exception for "not configured"):
      * `{"running": false}` — extension not enabled (no container)
      * `{"running": true, "authenticated": false, ...}` — extension up
        but no TS_AUTHKEY, or auth was rejected
      * `{"running": true, "authenticated": true, "self": {hostname,
        dns_name, ips, online}, "magic_dns_suffix": "...", "tailnet_name": "..."}`
        — fully on the tailnet
    """
    return await asyncio.to_thread(_proxy_agent, "/v1/tailscale/status", 15)
