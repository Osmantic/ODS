"""Deterministic pre-update compatibility assessment for extension desired state.

This module is deliberately pure. It validates an exact durable lockfile and an
exact candidate planning catalog, then reports whether the candidate ODS core
can retain the current desired state or requires a separately approved
extension plan. It performs no checkout, image, file, service, or lockfile
mutation.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from assistant_first_planner import (
    PlanningError,
    adapt_manifest,
    canonical_json_bytes,
    semver_precedence,
)
from extension_lockfile import ExtensionLockfileError, validate_lockfile_envelope
from extension_planning_contract import (
    computed_catalog_revision,
    manifest_from_catalog_entry,
)


ASSESSMENT_SCHEMA = "ods.extensions.update-compatibility.v1"
ASSESSMENT_ENVELOPE_SCHEMA = "ods.extensions.update-compatibility-envelope.v1"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_CATALOG_ENTRIES = 512


class ExtensionUpdateCompatibilityError(RuntimeError):
    """A stable compatibility failure containing only bounded public details."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = copy.deepcopy(details)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "details": copy.deepcopy(self.details)}


def _fail(code: str, **details: Any) -> None:
    raise ExtensionUpdateCompatibilityError(code, **details)


def _hash(value: Any, code: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _version(value: Any, code: str) -> tuple[int, int, int, int, tuple]:
    try:
        return semver_precedence(value, code)
    except PlanningError as exc:
        raise ExtensionUpdateCompatibilityError(code) from exc


def _compatible(
    version: tuple[int, int, int, int, tuple], compatibility: Mapping[str, Any]
) -> bool:
    minimum = _version(compatibility.get("minimum"), "invalid-extension-compatibility")
    maximum_value = compatibility.get("maximum")
    if version < minimum:
        return False
    if maximum_value is not None and version > _version(
        maximum_value, "invalid-extension-compatibility"
    ):
        return False
    return True


def _candidate_record(entry: Any) -> dict[str, Any]:
    try:
        manifest = manifest_from_catalog_entry(entry)
        return adapt_manifest(manifest)
    except PlanningError as exc:
        details: dict[str, Any] = {}
        service_id = entry.get("id") if isinstance(entry, Mapping) else None
        if isinstance(service_id, str):
            details["serviceId"] = service_id
        details["catalogCode"] = exc.code
        raise ExtensionUpdateCompatibilityError(
            "invalid-candidate-catalog", **details
        ) from exc


def _public_candidate(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": record["version"],
        "manifestSchemaVersion": record["schemaVersion"],
        "dataSchemaVersion": record["dataSchemaVersion"],
        "odsCompatibility": copy.deepcopy(record["odsCompatibility"]),
        "definitionHashes": {
            "manifestSha256": record["definitionSha256"] or None,
            "composeSha256": record["composeSha256"] or None,
        },
        "imageDigests": sorted(
            {item["digest"] for item in record["artifacts"]["images"]}
        ),
    }


def _locked_identity_matches(
    locked: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    return (
        locked["manifestSchemaVersion"] == candidate["schemaVersion"]
        and locked["dataSchemaVersion"] == candidate["dataSchemaVersion"]
        and locked["odsCompatibility"] == candidate["odsCompatibility"]
        and locked["definitionHashes"]
        == {
            "manifestSha256": candidate["definitionSha256"] or None,
            "composeSha256": candidate["composeSha256"] or None,
        }
        and locked["imageDigests"]
        == sorted({item["digest"] for item in candidate["artifacts"]["images"]})
    )


def _has_immutable_artifact(candidate: Mapping[str, Any]) -> bool:
    return candidate["serviceType"] != "docker" or bool(
        candidate["artifacts"]["images"] or candidate["artifacts"]["builds"]
    )


def _ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=canonical_json_bytes)


def assess_update_compatibility(
    source_lockfile: Any,
    *,
    installed_ods_version: Any,
    candidate_ods_version: Any,
    candidate_catalog: Any,
    candidate_catalog_revision: Any,
) -> dict[str, Any]:
    """Return a canonical, hash-bound assessment without mutating its inputs.

    Enabled extensions fail closed when the candidate catalog omits them,
    regresses their version, rewrites an immutable version, or cannot provide a
    newer compatible definition when the locked definition excludes the target
    core. Disabled extensions are reported but do not block a core update.
    """

    try:
        validated = validate_lockfile_envelope(source_lockfile)
    except ExtensionLockfileError as exc:
        raise ExtensionUpdateCompatibilityError(
            "invalid-source-lockfile", lockfileCode=exc.code
        ) from exc

    installed_key = _version(
        installed_ods_version, "invalid-installed-ods-version"
    )
    candidate_key = _version(candidate_ods_version, "invalid-candidate-ods-version")
    if candidate_key < installed_key:
        _fail("candidate-ods-version-regression")
    document = validated["lockfile"]
    if installed_ods_version != document["odsVersion"]:
        _fail("stale-source-lockfile")

    supplied_revision = _hash(
        candidate_catalog_revision, "invalid-candidate-catalog-revision"
    )
    if not isinstance(candidate_catalog, Sequence) or isinstance(
        candidate_catalog, (str, bytes, bytearray)
    ):
        _fail("invalid-candidate-catalog")
    if len(candidate_catalog) > _MAX_CATALOG_ENTRIES:
        _fail("candidate-catalog-too-large")
    try:
        actual_revision = computed_catalog_revision(candidate_catalog)
    except (PlanningError, TypeError) as exc:
        raise ExtensionUpdateCompatibilityError("invalid-candidate-catalog") from exc
    if actual_revision != supplied_revision:
        _fail("stale-candidate-catalog", currentRevision=actual_revision)

    candidates: dict[str, dict[str, Any]] = {}
    for entry in candidate_catalog:
        record = _candidate_record(entry)
        service_id = record["id"]
        if service_id in candidates:
            _fail("duplicate-candidate-extension", serviceId=service_id)
        candidates[service_id] = record

    decisions: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    required_upgrades: list[dict[str, Any]] = []

    for locked in document["extensions"]:
        service_id = locked["id"]
        enabled = locked["desiredState"] == "enabled"
        locked_key = _version(locked["version"], "invalid-locked-extension-version")
        locked_compatible = _compatible(candidate_key, locked["odsCompatibility"])
        candidate = candidates.get(service_id)
        candidate_compatible: bool | None = None
        disposition = "retain" if enabled else "disabled-retain"
        issue: dict[str, Any] | None = None

        if candidate is None:
            disposition = "blocked" if enabled else "disabled-incompatible"
            issue = {"code": "candidate-extension-missing", "serviceId": service_id}
        elif candidate["legacy"]:
            disposition = "blocked" if enabled else "disabled-incompatible"
            issue = {"code": "candidate-extension-legacy", "serviceId": service_id}
        elif not candidate["definitionSha256"]:
            disposition = "blocked" if enabled else "disabled-incompatible"
            issue = {"code": "candidate-definition-missing", "serviceId": service_id}
        else:
            candidate_compatible = _compatible(
                candidate_key, candidate["odsCompatibility"]
            )
            candidate_version_key = _version(
                candidate["version"], "invalid-candidate-extension-version"
            )
            if candidate_version_key < locked_key:
                disposition = "blocked" if enabled else "disabled-incompatible"
                issue = {
                    "code": "candidate-extension-version-regression",
                    "serviceId": service_id,
                }
            elif candidate["version"] == locked[
                "version"
            ] and not _locked_identity_matches(locked, candidate):
                disposition = "blocked" if enabled else "disabled-incompatible"
                issue = {
                    "code": "candidate-extension-definition-drift",
                    "serviceId": service_id,
                }
            elif (
                candidate_version_key == locked_key
                and candidate["version"] != locked["version"]
            ):
                disposition = "blocked" if enabled else "disabled-incompatible"
                issue = {
                    "code": "candidate-extension-version-ambiguous",
                    "serviceId": service_id,
                }
            elif not _has_immutable_artifact(candidate) and not locked_compatible:
                disposition = "blocked" if enabled else "disabled-incompatible"
                issue = {
                    "code": "candidate-extension-artifact-missing",
                    "serviceId": service_id,
                }
            elif locked_compatible:
                if candidate_version_key > locked_key and candidate_compatible:
                    disposition = (
                        "upgrade-available" if enabled else "disabled-upgrade-available"
                    )
                    warnings.append(
                        {"code": "extension-upgrade-available", "serviceId": service_id}
                    )
            elif candidate_version_key > locked_key and candidate_compatible:
                if enabled:
                    disposition = "upgrade-required"
                    required_upgrades.append(
                        {
                            "serviceId": service_id,
                            "fromVersion": locked["version"],
                            "toVersion": candidate["version"],
                            "definitionHashes": {
                                "manifestSha256": candidate["definitionSha256"],
                                "composeSha256": candidate["composeSha256"] or None,
                            },
                            "imageDigests": sorted(
                                {
                                    item["digest"]
                                    for item in candidate["artifacts"]["images"]
                                }
                            ),
                        }
                    )
                else:
                    disposition = "disabled-upgrade-available"
                    warnings.append(
                        {
                            "code": "disabled-extension-incompatible",
                            "serviceId": service_id,
                        }
                    )
            else:
                disposition = "blocked" if enabled else "disabled-incompatible"
                issue = {
                    "code": "candidate-extension-incompatible",
                    "serviceId": service_id,
                }

        if issue is not None:
            (blockers if enabled else warnings).append(issue)
        decisions.append(
            {
                "serviceId": service_id,
                "desiredState": locked["desiredState"],
                "lockedVersion": locked["version"],
                "lockedCompatible": locked_compatible,
                "candidateCompatible": candidate_compatible,
                "candidate": None
                if candidate is None
                else _public_candidate(candidate),
                "disposition": disposition,
            }
        )

    decisions.sort(key=lambda item: item["serviceId"])
    required_upgrades.sort(key=lambda item: item["serviceId"])
    blockers = _ordered(blockers)
    warnings = _ordered(warnings)
    assessment = {
        "schema": ASSESSMENT_SCHEMA,
        "sourceLockfileHash": validated["lockfileHash"],
        "installedOdsVersion": installed_ods_version,
        "candidateOdsVersion": candidate_ods_version,
        "candidateCatalogRevision": supplied_revision,
        "decisions": decisions,
        "requiredUpgrades": required_upgrades,
        "warnings": warnings,
        "blockers": blockers,
        "canUpdate": not blockers,
        "requiresExtensionPlan": bool(required_upgrades),
    }
    assessment_hash = hashlib.sha256(canonical_json_bytes(assessment)).hexdigest()
    return {
        "schema": ASSESSMENT_ENVELOPE_SCHEMA,
        "sourceLockfileHash": validated["lockfileHash"],
        "candidateCatalogRevision": supplied_revision,
        "assessmentHash": assessment_hash,
        "assessment": assessment,
    }


__all__ = [
    "ASSESSMENT_ENVELOPE_SCHEMA",
    "ASSESSMENT_SCHEMA",
    "ExtensionUpdateCompatibilityError",
    "assess_update_compatibility",
]
