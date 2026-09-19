"""Authenticated Model Switchboard route-evidence proxy."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])

MODEL_ROUTER_URL = os.environ.get("MODEL_ROUTER_URL", "http://model-router:4010")
_EVIDENCE_TIMEOUT_SECONDS = 5.0
_EVIDENCE_ATTEMPTS = 3
_EVIDENCE_RETRY_DELAY_SECONDS = 0.5
_BATCH_CONCURRENCY = 8

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


class RouteEvidenceBatchRequest(BaseModel):
    probe_ids: list[str] = Field(alias="probeIds", min_length=1, max_length=50)

    @field_validator("probe_ids")
    @classmethod
    def canonical_unique_probe_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            try:
                parsed = str(uuid.UUID(value))
            except (TypeError, ValueError) as exc:
                raise ValueError("probe ids must be canonical UUIDs") from exc
            if value != parsed:
                raise ValueError("probe ids must be canonical UUIDs")
            if value in normalized:
                raise ValueError("probe ids must be unique")
            normalized.append(value)
        return normalized


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


async def _fetch_route_evidence(
    client: httpx.AsyncClient, probe_id: str, internal_key: str
) -> dict[str, Any]:
    url = f"{MODEL_ROUTER_URL.rstrip('/')}/internal/route-evidence/{probe_id}"
    attempts = max(1, _EVIDENCE_ATTEMPTS)
    response: httpx.Response | None = None
    for attempt in range(attempts):
        try:
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


@router.post("/api/models/routes/query", dependencies=[Depends(verify_api_key)])
async def query_model_route_evidence(request: RouteEvidenceBatchRequest) -> dict[str, Any]:
    """Fetch several route receipts with bounded concurrency and per-item status."""
    internal_key = _router_internal_key()
    if not internal_key:
        raise HTTPException(status_code=503, detail="Model router evidence key is not configured.")

    semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)
    async with httpx.AsyncClient(timeout=_EVIDENCE_TIMEOUT_SECONDS) as client:

        async def fetch_one(probe_id: str) -> dict[str, Any]:
            async with semaphore:
                try:
                    evidence = await _fetch_route_evidence(client, probe_id, internal_key)
                except HTTPException as exc:
                    status = "not_found" if exc.status_code == 404 else "error"
                    return {
                        "probeId": probe_id,
                        "status": status,
                        "statusCode": exc.status_code,
                        "detail": exc.detail,
                    }
                return {"probeId": probe_id, "status": "found", "evidence": evidence}

        results = await asyncio.gather(
            *(fetch_one(probe_id) for probe_id in request.probe_ids)
        )

    return {
        "results": results,
        "summary": {
            "requested": len(results),
            "found": sum(item["status"] == "found" for item in results),
            "notFound": sum(item["status"] == "not_found" for item in results),
            "errors": sum(item["status"] == "error" for item in results),
        },
    }


@router.get("/api/models/routes/{probe_id}", dependencies=[Depends(verify_api_key)])
async def get_model_route_evidence(probe_id: str) -> dict[str, Any]:
    """Fetch sanitized route evidence recorded by the internal model-router."""
    probe_id = _normal_probe_id(probe_id)
    internal_key = _router_internal_key()
    if not internal_key:
        raise HTTPException(status_code=503, detail="Model router evidence key is not configured.")

    async with httpx.AsyncClient(timeout=_EVIDENCE_TIMEOUT_SECONDS) as client:
        return await _fetch_route_evidence(client, probe_id, internal_key)
