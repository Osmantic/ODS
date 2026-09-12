"""Authenticated, read-only Assistant First extension planning endpoint."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from assistant_first_planner import (
    PlanningError,
    adapt_manifest,
    build_plan,
    canonical_json_bytes,
    normalize_policy,
)
from config import (
    EXTENSION_CATALOG,
    EXTENSION_CATALOG_REVISION,
    EXTENSION_PLANNING_POLICY,
)
from security import verify_api_key


router = APIRouter(prefix="/api/extensions", tags=["extensions"])
MAX_REQUEST_BYTES = 64 * 1024
_V1_PLANNING_KEYS = {
    "serviceType",
    "version",
    "dataSchemaVersion",
    "odsCompatibility",
    "definitionSha256",
    "composeSha256",
    "dependsOn",
    "legacy",
}
_V2_PLANNING_KEYS = _V1_PLANNING_KEYS | {
    "provides",
    "requires",
    "optional",
    "conflicts",
    "providerPriority",
    "requirements",
    "estimates",
    "resources",
    "configuration",
    "artifacts",
    "lifecycle",
    "data",
    "trust",
    "support",
}


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


def _manifest_from_catalog_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise PlanningError("invalid-catalog-entry")
    service_id = entry.get("id")
    schema_version = entry.get("manifest_schema_version")
    planning = entry.get("planning")
    expected = (
        _V1_PLANNING_KEYS
        if schema_version == "ods.services.v1"
        else _V2_PLANNING_KEYS
        if schema_version == "ods.services.v2"
        else frozenset()
    )
    if not isinstance(planning, dict) or set(planning) != expected:
        raise PlanningError("invalid-catalog-entry", serviceId=service_id)
    def section(name: str) -> dict[str, Any]:
        value = planning.get(name)
        if not isinstance(value, dict):
            raise PlanningError("invalid-catalog-entry", serviceId=service_id, section=name)
        return value

    service: dict[str, Any] = {
        "id": service_id,
        "type": planning.get("serviceType"),
        "depends_on": planning.get("dependsOn"),
    }
    if schema_version == "ods.services.v2":
        requirements = section("requirements")
        estimates = section("estimates")
        resources = section("resources")
        artifacts = section("artifacts")
        lifecycle = section("lifecycle")
        trust = section("trust")
        support = section("support")
        service["version"] = planning.get("version")
        service["data_schema_version"] = planning.get("dataSchemaVersion")
        service["planning"] = {
            "provides": planning.get("provides"),
            "requires": planning.get("requires"),
            "optional": planning.get("optional"),
            "conflicts": planning.get("conflicts"),
            "provider_priority": planning.get("providerPriority"),
            "requirements": {
                "platforms": requirements.get("platforms"),
                "architectures": requirements.get("architectures"),
                "container_runtimes": requirements.get("containerRuntimes"),
                "gpu_backends": requirements.get("gpuBackends"),
                "min_driver_version": requirements.get("minDriverVersion"),
            },
            "estimates": {
                "download_bytes": estimates.get("downloadBytes"),
                "disk_bytes": estimates.get("diskBytes"),
                "cpu_millicores": estimates.get("cpuMillicores"),
                "ram_bytes": estimates.get("ramBytes"),
                "vram_bytes": estimates.get("vramBytes"),
                "gpu_count": estimates.get("gpuCount"),
            },
            "resources": {
                "host_ports": resources.get("hostPorts"),
                "container_ports": resources.get("containerPorts"),
                "networks": resources.get("networks"),
                "volumes": resources.get("volumes"),
                "devices": resources.get("devices"),
                "exclusive": resources.get("exclusive"),
                "linux_capabilities": resources.get("linuxCapabilities"),
                "host_permissions": resources.get("hostPermissions"),
            },
            "configuration": [
                {
                    **{key: value for key, value in item.items() if key not in {"restartBehavior"}},
                    "restart_behavior": item.get("restartBehavior"),
                }
                if isinstance(item, dict)
                else item
                for item in planning.get("configuration", [])
            ],
            "artifacts": {
                "images": [
                    {
                        "reference": item.get("reference"),
                        "digest": item.get("digest"),
                        "download_bytes": item.get("downloadBytes"),
                    }
                    if isinstance(item, dict)
                    else item
                    for item in artifacts.get("images", [])
                ],
                "builds": [
                    {
                        "source": item.get("source"),
                        "revision": item.get("revision"),
                        "context_digest": item.get("contextDigest"),
                        "output": item.get("output"),
                        "download_bytes": item.get("downloadBytes"),
                    }
                    if isinstance(item, dict)
                    else item
                    for item in artifacts.get("builds", [])
                ],
            },
            "lifecycle": {
                "health_checks": lifecycle.get("healthChecks"),
                "readiness": lifecycle.get("readiness"),
                "setup_hook": lifecycle.get("setupHook"),
                "migration_hook": lifecycle.get("migrationHook"),
                "rollback": lifecycle.get("rollback"),
                "timeout_seconds": lifecycle.get("timeoutSeconds"),
            },
            "data": [
                {
                    "path": item.get("path"),
                    "backup_class": item.get("backupClass"),
                    "owner": item.get("owner"),
                    "uninstall": item.get("uninstall"),
                    "purge": item.get("purge"),
                }
                if isinstance(item, dict)
                else item
                for item in planning.get("data", [])
            ],
            "trust": {
                "tier": trust.get("tier"),
                "publisher": trust.get("publisher"),
                "definition_signature": trust.get("definitionSignature"),
            },
            "support": {"status": support.get("status"), "url": support.get("url")},
        }
    compatibility = section("odsCompatibility")
    return {
        "schema_version": schema_version,
        "compatibility": {
            "ods_min": compatibility.get("minimum"),
            **({"ods_max": compatibility.get("maximum")} if compatibility.get("maximum") else {}),
        },
        "service": service,
        "_catalog": {
            "definition_sha256": planning.get("definitionSha256"),
            "compose_sha256": planning.get("composeSha256"),
        },
    }


def _computed_catalog_revision(entries: list[dict[str, Any]]) -> str:
    if any(not isinstance(entry, dict) for entry in entries):
        raise PlanningError("invalid-catalog-entry")
    material = {
        "schema": "ods.extensions.planning-catalog.v1",
        "extensions": [
            {
                "id": entry.get("id"),
                "manifestSchemaVersion": entry.get("manifest_schema_version"),
                "planning": entry.get("planning"),
            }
            for entry in sorted(entries, key=lambda item: str(item.get("id", "")))
        ],
    }
    return hashlib.sha256(canonical_json_bytes(material)).hexdigest()


def _computed_policy_revision(policy: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(normalize_policy(policy))).hexdigest()


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
