"""Authenticated Model Switchboard route-evidence proxy."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])

MODEL_ROUTER_URL = os.environ.get("MODEL_ROUTER_URL", "http://model-router:4010")
_EVIDENCE_TIMEOUT_SECONDS = 5.0
_EVIDENCE_ATTEMPTS = 3
_EVIDENCE_RETRY_DELAY_SECONDS = 0.5

_STRING_FIELDS = {
    "probeId",
    "requestedModel",
    "routedModel",
    "backend",
    "endpointId",
    "path",
    "responseModel",
    "lemonadeRoute",
    "instanceId",
}
_INT_FIELDS = {"routeSeq", "status"}


def _normal_probe_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid route probe id.") from exc
    normal = str(parsed)
    if value != normal:
        raise HTTPException(status_code=400, detail="Invalid route probe id.")
    return normal


def _router_internal_key() -> str:
    return os.environ.get("ODS_ROUTER_INTERNAL_KEY", "") or os.environ.get(
        "DASHBOARD_API_KEY", ""
    )


def _sanitize_evidence(payload: Any, probe_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Model router returned invalid evidence.")
    if payload.get("probeId") != probe_id:
        raise HTTPException(status_code=502, detail="Model router returned mismatched evidence.")

    clean: dict[str, Any] = {}
    for key in _STRING_FIELDS:
        value = payload.get(key)
        if isinstance(value, str):
            clean[key] = value
    for key in _INT_FIELDS:
        value = payload.get(key)
        if type(value) is int:
            clean[key] = value
    return clean


@router.get("/api/models/routes/{probe_id}", dependencies=[Depends(verify_api_key)])
async def get_model_route_evidence(probe_id: str) -> dict[str, Any]:
    """Fetch sanitized route evidence recorded by the internal model-router."""
    probe_id = _normal_probe_id(probe_id)
    internal_key = _router_internal_key()
    if not internal_key:
        raise HTTPException(status_code=503, detail="Model router evidence key is not configured.")

    url = f"{MODEL_ROUTER_URL.rstrip('/')}/internal/route-evidence/{probe_id}"
    attempts = max(1, _EVIDENCE_ATTEMPTS)
    response: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=_EVIDENCE_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {internal_key}"},
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt + 1 < attempts:
                await asyncio.sleep(_EVIDENCE_RETRY_DELAY_SECONDS)
                continue
            logger.warning("model-router route evidence unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="Model router is not reachable.") from exc
        except httpx.HTTPError as exc:
            logger.warning("model-router route evidence request failed: %s", exc)
            raise HTTPException(status_code=502, detail="Model router evidence request failed.") from exc

        if response.status_code == 404 and attempt + 1 < attempts:
            await asyncio.sleep(_EVIDENCE_RETRY_DELAY_SECONDS)
            continue
        if response.status_code in {502, 503, 504} and attempt + 1 < attempts:
            await asyncio.sleep(_EVIDENCE_RETRY_DELAY_SECONDS)
            continue
        break

    assert response is not None

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Route evidence not found.")
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=502, detail="Model router rejected the dashboard key.")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail="Model router evidence request failed.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Model router returned invalid evidence.") from exc
    return _sanitize_evidence(payload, probe_id)
