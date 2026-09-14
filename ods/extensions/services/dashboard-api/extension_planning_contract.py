"""Pure shared catalog-to-planner contract for extension planning.

Both the read-only planning route and the Phase 3 authorization boundary use
these helpers.  Keeping this conversion and revision material in one module
prevents the authorization path from drifting away from the plan a user saw.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from assistant_first_planner import (
    PlanningError,
    canonical_json_bytes,
    normalize_policy,
)


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


def manifest_from_catalog_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a strict catalog entry into the planner's raw manifest shape."""
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
            raise PlanningError(
                "invalid-catalog-entry", serviceId=service_id, section=name
            )
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
                    **{
                        key: value
                        for key, value in item.items()
                        if key != "restartBehavior"
                    },
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
            **(
                {"ods_max": compatibility.get("maximum")}
                if compatibility.get("maximum")
                else {}
            ),
        },
        "service": service,
        "_catalog": {
            "definition_sha256": planning.get("definitionSha256"),
            "compose_sha256": planning.get("composeSha256"),
        },
    }


def computed_catalog_revision(entries: Sequence[Mapping[str, Any]]) -> str:
    """Hash only the canonical planning catalog material."""
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


def computed_policy_revision(policy: Any) -> str:
    """Hash the planner-normalized policy."""
    return hashlib.sha256(canonical_json_bytes(normalize_policy(policy))).hexdigest()
