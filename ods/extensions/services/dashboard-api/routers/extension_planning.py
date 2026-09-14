"""Authenticated, read-only Assistant First extension planning endpoint."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from assistant_first_planner import (
    PlanningError,
    adapt_manifest,
    build_plan,
    canonical_json_bytes as canonical_json_bytes,
)
from config import (
    EXTENSION_CATALOG,
    EXTENSION_CATALOG_REVISION,
    EXTENSION_PLANNING_POLICY,
)
from extension_planning_contract import (
    computed_catalog_revision as _computed_catalog_revision,
    computed_policy_revision as _computed_policy_revision,
    manifest_from_catalog_entry as _manifest_from_catalog_entry,
)
from security import verify_api_key


router = APIRouter(prefix="/api/extensions", tags=["extensions"])
MAX_REQUEST_BYTES = 64 * 1024

class PlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    catalogRevision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    requestedAction: str = Field(default="ensure", pattern=r"^ensure$")
    observedStateRevision: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    observedState: dict[str, Any]
    policyRevision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    validUntil: str = Field(
        min_length=20,
        max_length=20,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
    )
    requestedServices: list[str] = Field(default_factory=list, max_length=128)
    requestedCapabilities: list[str] = Field(default_factory=list, max_length=128)
    providerPreferences: dict[str, str] = Field(default_factory=dict, max_length=128)
    missingConfigKeys: list[str] = Field(default_factory=list, max_length=256)
    missingSecretKeys: list[str] = Field(default_factory=list, max_length=256)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _reject_number(_value: str) -> None:
    raise ValueError("floating-point-values-are-not-accepted")


async def _request_model(request: Request) -> PlanningRequest:
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Planning request is too large",
                headers={"Cache-Control": "no-store"},
            )
    try:
        decoded = json.loads(
            bytes(payload).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        return PlanningRequest.model_validate(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid planning request",
            headers={"Cache-Control": "no-store"},
        ) from exc



def _status_for(error: PlanningError) -> int:
    invalid_prefixes = (
        "invalid-",
        "duplicate-",
        "unknown-",
        "unsupported-",
        "unused-",
        "self-",
        "config-secret-",
    )
    return 422 if error.code.startswith(invalid_prefixes) else 409


@router.get("/planning-contract")
async def planning_contract(_api_key: str = Depends(verify_api_key)) -> JSONResponse:
    """Return immutable planner contract revisions without host or secret state."""

    if not EXTENSION_CATALOG_REVISION or not isinstance(EXTENSION_CATALOG, list):
        raise HTTPException(
            status_code=503,
            detail="Planning catalog is unavailable",
            headers={"Cache-Control": "no-store"},
        )
    try:
        if _computed_catalog_revision(EXTENSION_CATALOG) != EXTENSION_CATALOG_REVISION:
            raise PlanningError("invalid-catalog-revision")
        policy_revision = _computed_policy_revision(EXTENSION_PLANNING_POLICY)
    except PlanningError as exc:
        raise HTTPException(
            status_code=503,
            detail="Planning contract is invalid",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return JSONResponse(
        content={
            "schema": "ods.assistant-first.planning-contract.v1",
            "catalogRevision": EXTENSION_CATALOG_REVISION,
            "policyRevision": policy_revision,
            "planSchema": "ods.assistant-first.plan.v1",
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/plan")
async def plan_extensions(
    request: Request,
    _api_key: str = Depends(verify_api_key),
) -> JSONResponse:
    """Calculate a revision-bound plan without observing or changing the host."""

    model = await _request_model(request)
    if not EXTENSION_CATALOG_REVISION or not isinstance(EXTENSION_CATALOG, list):
        raise HTTPException(
            status_code=503,
            detail="Planning catalog is unavailable",
            headers={"Cache-Control": "no-store"},
        )
    try:
        computed_revision = _computed_catalog_revision(EXTENSION_CATALOG)
    except PlanningError as exc:
        raise HTTPException(
            status_code=503,
            detail="Planning catalog is invalid",
            headers={"Cache-Control": "no-store"},
        ) from exc
    if computed_revision != EXTENSION_CATALOG_REVISION:
        raise HTTPException(
            status_code=503,
            detail="Planning catalog revision is invalid",
            headers={"Cache-Control": "no-store"},
        )
    if model.catalogRevision != EXTENSION_CATALOG_REVISION:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "stale-catalog-revision",
                    "currentCatalogRevision": EXTENSION_CATALOG_REVISION,
                }
            },
            headers={"Cache-Control": "no-store"},
        )
    try:
        current_policy_revision = _computed_policy_revision(EXTENSION_PLANNING_POLICY)
    except PlanningError as exc:
        raise HTTPException(
            status_code=503,
            detail="Planning policy is invalid",
            headers={"Cache-Control": "no-store"},
        ) from exc
    if model.policyRevision != current_policy_revision:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "stale-policy",
                    "currentPolicyRevision": current_policy_revision,
                }
            },
            headers={"Cache-Control": "no-store"},
        )
    try:
        manifests = [_manifest_from_catalog_entry(entry) for entry in EXTENSION_CATALOG]
        for manifest in manifests:
            adapt_manifest(manifest)
    except PlanningError as exc:
        raise HTTPException(
            status_code=503,
            detail="Planning catalog is invalid",
            headers={"Cache-Control": "no-store"},
        ) from exc
    try:
        result = build_plan(
            manifests,
            requested_action=model.requestedAction,
            requested_services=model.requestedServices,
            requested_capabilities=model.requestedCapabilities,
            provider_preferences=model.providerPreferences,
            missing_config_keys=model.missingConfigKeys,
            missing_secret_keys=model.missingSecretKeys,
            catalog_revision=model.catalogRevision,
            observed_state_revision=model.observedStateRevision,
            observed_state=model.observedState,
            policy_revision=model.policyRevision,
            policy=EXTENSION_PLANNING_POLICY,
            valid_until=model.validUntil,
        )
    except PlanningError as exc:
        return JSONResponse(
            status_code=_status_for(exc),
            content={"error": exc.as_dict()},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})
