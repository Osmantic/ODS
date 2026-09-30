"""Owner-managed custom integrations: list, add, check and remove.

ODS services already report through /api/status. These endpoints cover the
systems ODS does not run (hosted decision APIs, external data engines, custom
services) so the owner can see whether they answer. See custom_integrations.py
for what a check does and does not do.
"""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from config import DATA_DIR
from custom_integrations import (
    CHECK_TIMEOUT_SECONDS,
    MAX_INTEGRATIONS,
    STALE_AFTER_SECONDS,
    CustomIntegrationStore,
    IntegrationConflict,
    IntegrationError,
    IntegrationStoreUnreadable,
)
from security import verify_api_key

router = APIRouter(tags=["integrations"])
NO_STORE = {"Cache-Control": "no-store"}
MAX_BODY_BYTES = 4096

_store: Optional[CustomIntegrationStore] = None


def integrations_path() -> Path:
    return Path(DATA_DIR) / "integrations" / "custom.json"


def get_store() -> CustomIntegrationStore:
    global _store
    path = integrations_path()
    if _store is None or _store.path != path:
        _store = CustomIntegrationStore(path)
    return _store


def _unreadable(error: IntegrationStoreUnreadable) -> HTTPException:
    return HTTPException(503, f"{error} ({integrations_path().name} in the ODS data folder)", headers=NO_STORE)


async def _body(request: Request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Integration request is too large", headers=NO_STORE)
        raw.extend(chunk)
    try:
        value = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400, "Send the integration as JSON", headers=NO_STORE) from None
    if not isinstance(value, dict) or not set(value) <= {"name", "url", "notes"}:
        raise HTTPException(400, "Send only name, url and notes", headers=NO_STORE)
    return value


def _envelope(integrations: list) -> dict:
    return {
        "schemaVersion": 1,
        "integrations": integrations,
        "policy": {
            "maximum": MAX_INTEGRATIONS,
            "staleAfterSeconds": int(STALE_AFTER_SECONDS),
            "timeoutSeconds": int(CHECK_TIMEOUT_SECONDS),
            "method": "GET",
            "followsRedirects": False,
            "sendsCredentials": False,
            "readsResponseBody": False,
        },
    }


@router.get("/api/integrations/custom")
async def list_custom_integrations(refresh: str = "stale", _key: str = Depends(verify_api_key)):
    """List custom integrations, re-checking any whose last check is stale."""
    if refresh not in {"stale", "none"}:
        raise HTTPException(400, "refresh must be 'stale' or 'none'", headers=NO_STORE)
    try:
        integrations = await get_store().list(refresh=refresh)
    except IntegrationStoreUnreadable as error:
        raise _unreadable(error) from None
    return JSONResponse(_envelope(integrations), headers=NO_STORE)


@router.post("/api/integrations/custom", status_code=201)
async def add_custom_integration(request: Request, _key: str = Depends(verify_api_key)):
    body = await _body(request)
    try:
        integration = await get_store().add(body.get("name"), body.get("url"), body.get("notes"))
    except IntegrationConflict as error:
        raise HTTPException(409, str(error), headers=NO_STORE) from None
    except IntegrationError as error:
        raise HTTPException(400, str(error), headers=NO_STORE) from None
    except IntegrationStoreUnreadable as error:
        raise _unreadable(error) from None
    return JSONResponse({"integration": integration}, status_code=201, headers=NO_STORE)


@router.post("/api/integrations/custom/{integration_id}/check")
async def check_custom_integration(integration_id: str, _key: str = Depends(verify_api_key)):
    try:
        integration = await get_store().check(integration_id)
    except IntegrationStoreUnreadable as error:
        raise _unreadable(error) from None
    if integration is None:
        raise HTTPException(404, "Integration not found", headers=NO_STORE)
    return JSONResponse({"integration": integration}, headers=NO_STORE)


@router.delete("/api/integrations/custom/{integration_id}", status_code=204)
async def remove_custom_integration(integration_id: str, _key: str = Depends(verify_api_key)):
    try:
        removed = await get_store().remove(integration_id)
    except IntegrationStoreUnreadable as error:
        raise _unreadable(error) from None
    if not removed:
        raise HTTPException(404, "Integration not found", headers=NO_STORE)
    return Response(status_code=204, headers=NO_STORE)
