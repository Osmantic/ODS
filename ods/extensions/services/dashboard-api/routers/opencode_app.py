"""OpenCode as a dashboard application: live status, start, and setup.

OpenCode is a host process managed by the native service manager (systemd
user unit, LaunchAgent, or scheduled task), not a Docker container. The host
agent reports its lifecycle; this router shapes it for the dashboard's
OpenCode page and Applications entry and forwards the two owner actions:

* ``start`` - start the installed ODS service and wait for its health route.
* ``setup`` - Linux only: install the reviewed release and its user service
  in the background (OpenCode is an opt-in extension there).

OpenCode's web server stays bound to host loopback. Nothing here proxies or
exposes it; the dashboard links a browser on the ODS machine to it directly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from config import DATA_DIR, SERVICES
from helpers import (
    get_cached_services,
    refresh_cached_service_status,
)
from host_agent_client import (
    AgentClientError,
    AgentHTTPError,
    async_request_json as request_agent_json,
)
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["apps"])

_STATES = {"running", "starting", "installing", "stopped", "not_installed"}
_PROGRESS_STATES = {"pulling", "starting", "started", "error"}


def _progress() -> dict[str, Any] | None:
    """Return the last setup progress record written by the host agent."""
    path = Path(DATA_DIR) / "extension-progress" / "opencode.json"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") not in _PROGRESS_STATES:
        return None
    error = data.get("error")
    return {
        "status": data["status"],
        "phaseLabel": str(data.get("phase_label") or "")[:200],
        "error": str(error)[:500] if isinstance(error, str) and error else None,
        "updatedAt": str(data.get("updated_at") or "")[:64] or None,
    }


def _shape(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the host-agent lifecycle into the dashboard contract."""
    state = payload.get("state")
    if state not in _STATES:
        raise HTTPException(status_code=502, detail="The host agent returned an unknown OpenCode state")
    config = SERVICES.get("opencode", {})
    try:
        port = int(payload.get("port") or config.get("external_port") or 3003)
    except (TypeError, ValueError):
        port = 3003
    version = payload.get("version")
    progress = _progress()
    if progress and (
        state in ("running", "stopped", "starting")
        or (state != "installing" and progress["status"] != "error")
    ):
        # A finished run is history. Surface an in-flight setup, or the failure
        # of the last setup while OpenCode is still not set up.
        progress = None
    return {
        "id": "opencode",
        "name": "OpenCode",
        "state": state,
        "running": state == "running",
        "installed": bool(payload.get("installed")),
        "version": version if isinstance(version, str) else None,
        "platform": str(payload.get("platform") or ""),
        "port": port,
        "portInUse": bool(payload.get("portInUse")),
        "startSupported": bool(payload.get("startSupported")),
        "setupSupported": bool(payload.get("setupSupported")),
        "setupIssue": payload.get("setupIssue") if isinstance(payload.get("setupIssue"), str) else None,
        "localUrl": f"http://localhost:{port}/",
        "publicUrl": config.get("public_url") or None,
        "progress": progress,
    }


def _agent_error_body(exc: AgentHTTPError) -> dict[str, Any]:
    try:
        body = json.loads(exc.response_text or "{}")
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


async def _refresh_service_cache() -> None:
    """Keep the sidebar's cached service health in step with an action."""
    if get_cached_services() is None:
        return
    try:
        await refresh_cached_service_status("opencode")
    except Exception:  # noqa: BLE001 - the next background poll repairs it
        logger.debug("Could not refresh the cached OpenCode status", exc_info=True)


def _unavailable(exc: AgentClientError) -> HTTPException:
    if isinstance(exc, AgentHTTPError) and exc.status_code == 404:
        return HTTPException(
            status_code=503,
            detail="The ODS host agent is older than this dashboard. Restart it with 'ods agent restart' to manage OpenCode here.",
        )
    return HTTPException(status_code=503, detail="The ODS host agent is not reachable, so OpenCode cannot be managed right now.")


@router.get("/api/apps/opencode")
async def opencode_status(api_key: str = Depends(verify_api_key)):
    try:
        payload = await request_agent_json("GET", "/v1/opencode/status", timeout=10)
    except AgentClientError as exc:
        raise _unavailable(exc) from exc
    return _shape(payload)


async def _action(action: str, timeout: float) -> JSONResponse:
    try:
        body = await request_agent_json("POST", f"/v1/opencode/{action}", timeout=timeout)
        code = 202 if action == "setup" and body.get("accepted") else 200
    except AgentHTTPError as exc:
        if exc.status_code == 404:
            raise _unavailable(exc) from exc
        body = _agent_error_body(exc)
        status = body.get("status")
        detail = {
            "detail": exc.detail,
            "code": body.get("code") if isinstance(body.get("code"), str) else f"opencode_{action}_failed",
        }
        if isinstance(status, dict) and status.get("state") in _STATES:
            detail["opencode"] = _shape(status)
        return JSONResponse(status_code=exc.status_code if exc.status_code in (409, 502, 504) else 502, content=detail)
    except AgentClientError as exc:
        raise _unavailable(exc) from exc
    status = body.get("status") if isinstance(body.get("status"), dict) else {}
    shaped = _shape(status) if status else None
    await _refresh_service_cache()
    return JSONResponse(status_code=code, content={"opencode": shaped})


@router.post("/api/apps/opencode/start")
async def start_opencode(api_key: str = Depends(verify_api_key)):
    """Start the installed ODS OpenCode service and wait until it answers."""
    return await _action("start", timeout=150)


@router.post("/api/apps/opencode/setup")
async def setup_opencode(api_key: str = Depends(verify_api_key)):
    """Set OpenCode up on Linux; progress is reported by GET /api/apps/opencode."""
    return await _action("setup", timeout=30)
