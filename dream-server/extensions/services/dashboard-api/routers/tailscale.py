"""Tailscale (remote access) — dashboard-api proxy in front of the host-agent.

The host-agent has /v1/tailscale/status, which queries the dream-tailscale
container or falls back to host-native Tailscale when the container is not
running. This module exposes that to the dashboard UI via
/api/tailscale/status.

For the typical lifecycle the operator runs:
  1. Generate an auth key at https://login.tailscale.com/admin/settings/keys
  2. Set TS_AUTHKEY in .env (via the existing Settings page or `dream env`)
  3. Enable the tailscale extension (via Extensions page or `dream enable tailscale`)
  4. Container starts, joins the tailnet, shows up in `tailscale status`
  5. The device is reachable as <hostname>.<tailnet>.ts.net from any
     other tailnet member

The status endpoint is what powers the "Remote Access" section in the
dashboard's Settings page — it shows the user whether their device is
on the tailnet, what its tailnet hostname is, and whether the daemon
is authenticated.
"""

import asyncio
import json
import logging
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException

from config import AGENT_URL, DREAM_AGENT_KEY
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tailscale"])


def _proxy_agent(path: str, timeout: int = 15) -> dict:
    """Forward a GET to the host-agent. Translates HTTPError → HTTPException."""
    headers = {"Authorization": f"Bearer {DREAM_AGENT_KEY}"}
    req = urllib.request.Request(f"{AGENT_URL}{path}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = f"Host agent returned HTTP {exc.code}"
        try:
            err_payload = json.loads(exc.read().decode("utf-8"))
            detail = err_payload.get("error", detail)
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            pass
        logger.info("host-agent GET %s -> %s", path, exc.code)
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except TimeoutError as exc:
        logger.warning("host-agent GET %s timed out", path)
        raise HTTPException(status_code=504, detail="Dream host agent request timed out.") from exc
    except urllib.error.URLError as exc:
        logger.warning("host-agent GET %s unreachable: %s", path, exc)
        raise HTTPException(status_code=503, detail="Dream host agent is not reachable.") from exc
    except (OSError, json.JSONDecodeError) as exc:
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
