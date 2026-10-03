"""Owner-authenticated, bounded proxy for Pixel settings persistence."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentUnavailable,
    async_request_json as request_agent_json,
)
from pixel_settings_public import normalize_edit, normalize_response, normalize_runtime, normalize_runtime_change, normalize_runtime_outcome
from routers.pixel_providers import (
    _check_depth,
    _constant,
    _float,
    _pairs,
    MAX_BYTES,
)
from security import verify_api_key

router = APIRouter(tags=["pixel-settings"])


async def _body(request: Request, validator=normalize_edit) -> dict[str, Any]:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Settings request exceeds size limit")
        raw.extend(chunk)
    try:
        text = bytes(raw).decode("utf-8")
        _check_depth(text)
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=_float,
            parse_constant=_constant,
        )
        return validator(value)
    except (ValueError, RecursionError):
        raise HTTPException(400, "Invalid settings request") from None


async def _request(method: str, path: str, payload: Any = None, *, validator=normalize_response, timeout=10):
    try:
        raw = await request_agent_json(method, path, payload=payload, timeout=timeout)
    except AgentHTTPError as exc:
        code = exc.status_code if exc.status_code in (400, 409, 413, 503) else 502
        raise HTTPException(code, "Settings request failed") from None
    except AgentUnavailable:
        raise HTTPException(503, "Settings are unavailable") from None
    except AgentProtocolError:
        raise HTTPException(502, "Invalid settings response") from None
    try:
        normalized = validator(raw)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(502, "Invalid settings response") from None
    return normalized


def _no_store_response(content: Any) -> JSONResponse:
    return JSONResponse(content=content, headers={"Cache-Control": "no-store"})


@router.get("/api/pixel/settings")
async def get_settings(_key: str = Depends(verify_api_key)):
    return _no_store_response(await _request("GET", "/v1/pixel/settings"))


@router.post("/api/pixel/settings/save")
async def save_settings(request: Request, _key: str = Depends(verify_api_key)):
    payload = await _body(request)
    response = await _request("POST", "/v1/pixel/settings/save", payload=payload)
    actual_revision = response["configuration"]["revision"]
    preferences = response["configuration"]["preferences"]
    if (actual_revision != payload["expectedRevision"] + 1
            or any(key not in preferences or preferences[key] != value
                   for key, value in payload["changes"].items())):
        raise HTTPException(502, "Settings revision mismatch")
    return _no_store_response(response)


@router.get("/api/pixel/settings/runtime")
async def get_runtime(_key: str = Depends(verify_api_key)):
    return _no_store_response(await _request("GET", "/v1/pixel/settings/runtime", validator=normalize_runtime, timeout=65))


@router.post("/api/pixel/settings/runtime")
async def change_runtime(request: Request, _key: str = Depends(verify_api_key)):
    payload = await _body(request, normalize_runtime_change)
    response = await _request("POST", "/v1/pixel/settings/runtime", payload, validator=normalize_runtime_outcome, timeout=340)
    if response["outcome"] == "applied" and response["appliedRevision"] != payload["settingsRevision"]:
        raise HTTPException(502, "Settings runtime revision mismatch")
    return _no_store_response(response)


@router.get("/api/pixel/settings/export")
async def export_workspace_snapshot(_key: str = Depends(verify_api_key)):
    try:
        settings_raw = await request_agent_json("GET", "/v1/pixel/settings", timeout=10)
        settings = normalize_response(settings_raw)

        providers_raw = await request_agent_json("GET", "/v1/pixel/providers", timeout=10)
        from pixel_provider_public import normalize_public
        providers = normalize_public(providers_raw)

        content = {
            "schemaVersion": 1,
            "type": "ods-workspace-snapshot",
            "scope": "pixel-configuration",
            "excludes": ["credentials", "conversations", "workspace-files"],
            "settings": settings["configuration"],
            "providers": providers
        }
        return JSONResponse(
            content=content,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'attachment; filename="ods-workspace-snapshot.json"'
            }
        )
    except AgentHTTPError as exc:
        code = exc.status_code if exc.status_code in (400, 409, 413, 503) else 502
        raise HTTPException(code, "Snapshot export failed", headers={"Cache-Control": "no-store"}) from None
    except AgentUnavailable:
        raise HTTPException(503, "Settings are unavailable for export", headers={"Cache-Control": "no-store"}) from None
    except (AgentProtocolError, ValueError, TypeError, RecursionError):
        raise HTTPException(502, "Invalid response during export", headers={"Cache-Control": "no-store"}) from None


