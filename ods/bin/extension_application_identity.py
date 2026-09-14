"""Pure, dormant application-identity label contract.

Defines the frozen identity labels that a truthful host installed-state
observer will later compare against Docker inspect output, fixed-root
active definition/config evidence, and receipts.

This module performs no filesystem, subprocess, Docker, network, environment,
secret, or mutation operations.  It imports only stdlib and the existing
lifecycle-plan / lifecycle-work types.  It is intentionally dormant: it
produces labels and validates observed labels; it does not activate any
executor or dispatcher.

The contract operates on an already plan-bound ``LifecycleWorkCommand`` whose
exact ``operation_key`` is ``apply:<serviceId>``.  It re-proves every binding
(transaction ID, plan hash, operation/service/action, PlannedDefinition
binding, definition digest, optional Compose digest, version) and emits a
frozen ``ApplicationIdentity`` plus deterministic Docker/Compose labels under
the fixed ``io.osmantic.ods.extension.*`` namespace.

A later host observer must compare these labels with current ``docker inspect``
plus fixed-root active definition/config evidence and receipts.  Successful
label parsing does **not** claim the service is running, healthy, configured,
applied, or current.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LABEL_NAMESPACE = "io.osmantic.ods.extension"
COMPOSE_DIGEST_SENTINEL = "absent"

LABEL_KEYS = (
    "service_id",
    "version",
    "action",
    "transaction_id",
    "plan_sha256",
    "request_sha256",
    "definition_sha256",
    "compose_sha256",
    "identity_sha256",
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLAN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTION_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ACTION_RE = re.compile(r"^(install|enable|repair|update)$")

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ApplicationIdentityError(LifecycleWorkError):
    """Failure carrying only a stable public code; never contains values."""


# ---------------------------------------------------------------------------
# Frozen output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicationIdentity:
    """Frozen, fully bound application identity for one apply operation."""

    service_id: str
    version: str
    action: str
    transaction_id: str
    plan_sha256: str
    request_sha256: str
    definition_sha256: str
    compose_sha256: str
    identity_sha256: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bad(code: str = "application-identity-error") -> None:
    raise ApplicationIdentityError(code) from None


def _verify_apply_command(command: Any) -> None:
    """Verify command is a plan-bound apply:<serviceId> command."""
    if not isinstance(command, LifecycleWorkCommand):
        _bad("not-a-command")
    if command.plan_material is None:
        _bad("plan-material-required")
    if not isinstance(command.plan_material, LifecyclePlanMaterial):
        _bad("plan-material-required")
    material = command.plan_material
    if material.schema != PLAN_MATERIAL_SCHEMA:
        _bad("plan-material-invalid")
    if _TRANSACTION_ID_RE.fullmatch(command.transaction_id) is None:
        _bad("transaction-id-invalid")
    if _PLAN_HASH_RE.fullmatch(command.plan_hash) is None:
        _bad("plan-hash-invalid")
    if _PLAN_HASH_RE.fullmatch(command.request_hash) is None:
        _bad("request-hash-invalid")
    prefix, sep, suffix = command.operation_key.partition(":")
    if prefix != "apply" or sep != ":" or len(command.service_ids) != 1:
        _bad("operation-must-be-apply-service")
    if suffix != command.service_ids[0]:
        _bad("operation-must-be-apply-service")
    if _SERVICE_ID_RE.fullmatch(suffix) is None:
        _bad("service-id-invalid")
    if material.state != "applying":
        _bad("plan-material-invalid")


def _find_definition(
    command: LifecycleWorkCommand,
    material: LifecyclePlanMaterial,
) -> PlannedDefinition:
    """Return the single PlannedDefinition matching command.service_ids[0]."""
    service_id = command.service_ids[0]
    matches = [d for d in material.definitions if d.service_id == service_id]
    if len(matches) != 1:
        _bad("definition-binding-required")
    return matches[0]


def _build_identity(
    command: LifecycleWorkCommand,
    definition: PlannedDefinition,
) -> ApplicationIdentity:
    """Build a frozen ApplicationIdentity from verified inputs."""

    material: LifecyclePlanMaterial = command.plan_material  # type: ignore[assignment]
    assert isinstance(material, LifecyclePlanMaterial)

    # Re-prove transaction ID
    if material.transaction_id != command.transaction_id:
        _bad("transaction-id-mismatch")
    # Re-prove plan hash
    if material.plan_hash != command.plan_hash:
        _bad("plan-hash-mismatch")

    # Extract action from payload (apply:<svc> payload format)
    payload = command.payload
    if not isinstance(payload, dict) or set(payload) != {"operation"}:
        _bad("payload-required")
    operation = payload.get("operation")
    if not isinstance(operation, dict):
        _bad("payload-required")
    action = operation.get("action")
    if not isinstance(action, str) or _ACTION_RE.fullmatch(action) is None:
        _bad("action-invalid")
    op_service_id = operation.get("serviceId")
    if op_service_id != command.service_ids[0]:
        _bad("service-id-mismatch")

    service_id = command.service_ids[0]
    planned_operations = [
        item for item in material.operations if item.service_id == service_id
    ]
    if len(planned_operations) != 1 or planned_operations[0].action != action:
        _bad("operation-binding-mismatch")
    if definition.service_id != service_id:
        _bad("definition-binding-required")

    # Definition digest
    definition_sha256 = definition.definition_sha256
    if not isinstance(definition_sha256, str) or _DIGEST_RE.fullmatch(definition_sha256) is None:
        _bad("definition-digest-invalid")

    # Compose digest (optional sentinel)
    if definition.compose_sha256 is not None:
        compose_sha256 = definition.compose_sha256
        if not _DIGEST_RE.fullmatch(compose_sha256):
            _bad("compose-digest-invalid")
    else:
        compose_sha256 = COMPOSE_DIGEST_SENTINEL

    version = definition.version
    if not _valid_version(version):
        _bad("version-required")

    # Compute identity SHA-256 over canonical bytes of the preceding fields
    identity_payload: dict[str, str] = {
        "action": action,
        "composeSha256": compose_sha256,
        "definitionSha256": definition_sha256,
        "planSha256": material.plan_hash,
        "requestSha256": command.request_hash,
        "serviceId": service_id,
        "transactionId": material.transaction_id,
        "version": version,
    }
    canonical = (
        json.dumps(
            identity_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    identity_sha256 = hashlib.sha256(canonical).hexdigest()

    return ApplicationIdentity(
        service_id=service_id,
        version=version,
        action=action,
        transaction_id=material.transaction_id,
        plan_sha256=material.plan_hash,
        request_sha256=command.request_hash,
        definition_sha256=definition_sha256,
        compose_sha256=compose_sha256,
        identity_sha256=identity_sha256,
    )


# ---------------------------------------------------------------------------
# Public API: produce
# ---------------------------------------------------------------------------


def produce_application_identity(
    command: LifecycleWorkCommand,
) -> ApplicationIdentity:
    """Return a frozen ApplicationIdentity for a verified apply:<serviceId> command.

    Raises ``ApplicationIdentityError`` on any binding failure.
    """
    _verify_apply_command(command)
    material: LifecyclePlanMaterial = command.plan_material  # type: ignore[assignment]
    definition = _find_definition(command, material)
    return _build_identity(command, definition)


# ---------------------------------------------------------------------------
# Label encoding
# ---------------------------------------------------------------------------


def _label_key(name: str) -> str:
    return f"{LABEL_NAMESPACE}.{name}"


def _valid_version(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 128
        and not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    )


def _identity_digest(identity: ApplicationIdentity) -> str:
    document = {
        "action": identity.action,
        "composeSha256": identity.compose_sha256,
        "definitionSha256": identity.definition_sha256,
        "planSha256": identity.plan_sha256,
        "requestSha256": identity.request_sha256,
        "serviceId": identity.service_id,
        "transactionId": identity.transaction_id,
        "version": identity.version,
    }
    canonical = (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_identity(identity: Any) -> None:
    if not isinstance(identity, ApplicationIdentity):
        _bad("identity-invalid")
    if _SERVICE_ID_RE.fullmatch(identity.service_id) is None:
        _bad("identity-invalid")
    if not _valid_version(identity.version):
        _bad("identity-invalid")
    if _ACTION_RE.fullmatch(identity.action) is None:
        _bad("identity-invalid")
    if _TRANSACTION_ID_RE.fullmatch(identity.transaction_id) is None:
        _bad("identity-invalid")
    if _PLAN_HASH_RE.fullmatch(identity.plan_sha256) is None:
        _bad("identity-invalid")
    if _PLAN_HASH_RE.fullmatch(identity.request_sha256) is None:
        _bad("identity-invalid")
    if _DIGEST_RE.fullmatch(identity.definition_sha256) is None:
        _bad("identity-invalid")
    if (
        identity.compose_sha256 != COMPOSE_DIGEST_SENTINEL
        and _DIGEST_RE.fullmatch(identity.compose_sha256) is None
    ):
        _bad("identity-invalid")
    if _PLAN_HASH_RE.fullmatch(identity.identity_sha256) is None:
        _bad("identity-invalid")
    if not hmac.compare_digest(_identity_digest(identity), identity.identity_sha256):
        _bad("identity-digest-mismatch")


def identity_labels(identity: ApplicationIdentity) -> dict[str, str]:
    """Return deterministic Docker/Compose labels for an ApplicationIdentity.

    Keys are under ``io.osmantic.ods.extension.*``.  Values are canonical
    strings.  The dict is ordered by key for deterministic YAML serialization.
    """
    _validate_identity(identity)
    return {
        _label_key("action"): identity.action,
        _label_key("compose_sha256"): identity.compose_sha256,
        _label_key("definition_sha256"): identity.definition_sha256,
        _label_key("identity_sha256"): identity.identity_sha256,
        _label_key("plan_sha256"): identity.plan_sha256,
        _label_key("request_sha256"): identity.request_sha256,
        _label_key("service_id"): identity.service_id,
        _label_key("transaction_id"): identity.transaction_id,
        _label_key("version"): identity.version,
    }


# ---------------------------------------------------------------------------
# Public API: parse / validate observed labels
# ---------------------------------------------------------------------------


def parse_observed_labels(
    labels: dict[str, str],
) -> ApplicationIdentity:
    """Parse and validate an observed Docker label mapping.

    Ignores unrelated Docker/Compose labels.  Rejects missing, duplicate,
    malformed required labels and any unknown label under the ODS extension
    namespace.  Recomputes the identity digest and fails closed on mismatch.

    Does **not** claim the service is running, healthy, configured, applied,
    or current.  A later host observer must compare these labels with current
    ``docker inspect`` plus fixed-root active definition/config evidence and
    receipts.

    Raises ``ApplicationIdentityError`` on any failure.
    """
    if not isinstance(labels, dict):
        _bad("labels-must-be-mapping")

    # Collect only ODS extension namespace labels
    ods_labels: dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not isinstance(value, str):
            _bad("labels-must-be-string")
        if key.startswith(LABEL_NAMESPACE + "."):
            suffix = key[len(LABEL_NAMESPACE) + 1:]
            if suffix in ods_labels:
                _bad("duplicate-label")
            ods_labels[suffix] = value

    # Reject unknown ODS namespace labels
    known_suffixes = set(LABEL_KEYS)
    unknown = set(ods_labels) - known_suffixes
    if unknown:
        _bad("unknown-ods-label")

    # Require all labels present
    for key_name in LABEL_KEYS:
        if key_name not in ods_labels:
            _bad(f"missing-label-{key_name}")

    # Validate individual fields
    service_id = ods_labels["service_id"]
    if not _SERVICE_ID_RE.fullmatch(service_id):
        _bad("label-value-invalid")

    version = ods_labels["version"]
    if not _valid_version(version):
        _bad("label-value-invalid")

    action = ods_labels["action"]
    if not _ACTION_RE.fullmatch(action):
        _bad("label-value-invalid")

    transaction_id = ods_labels["transaction_id"]
    if _TRANSACTION_ID_RE.fullmatch(transaction_id) is None:
        _bad("label-value-invalid")

    plan_sha256 = ods_labels["plan_sha256"]
    if not _PLAN_HASH_RE.fullmatch(plan_sha256):
        _bad("label-value-invalid")

    request_sha256 = ods_labels["request_sha256"]
    if _PLAN_HASH_RE.fullmatch(request_sha256) is None:
        _bad("label-value-invalid")

    definition_sha256 = ods_labels["definition_sha256"]
    if not _DIGEST_RE.fullmatch(definition_sha256):
        _bad("label-value-invalid")

    compose_sha256 = ods_labels["compose_sha256"]
    if (
        compose_sha256 != COMPOSE_DIGEST_SENTINEL
        and _DIGEST_RE.fullmatch(compose_sha256) is None
    ):
        _bad("label-value-invalid")

    identity_sha256 = ods_labels["identity_sha256"]
    if not _PLAN_HASH_RE.fullmatch(identity_sha256):
        _bad("label-value-invalid")

    # Recompute identity digest and verify
    expected_payload: dict[str, str] = {
        "action": action,
        "composeSha256": compose_sha256,
        "definitionSha256": definition_sha256,
        "planSha256": plan_sha256,
        "requestSha256": request_sha256,
        "serviceId": service_id,
        "transactionId": transaction_id,
        "version": version,
    }
    canonical = (
        json.dumps(
            expected_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    expected_identity = hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(expected_identity, identity_sha256):
        _bad("identity-digest-mismatch")

    return ApplicationIdentity(
        service_id=service_id,
        version=version,
        action=action,
        transaction_id=transaction_id,
        plan_sha256=plan_sha256,
        request_sha256=request_sha256,
        definition_sha256=definition_sha256,
        compose_sha256=compose_sha256,
        identity_sha256=identity_sha256,
    )


__all__ = [
    "COMPOSE_DIGEST_SENTINEL",
    "LABEL_KEYS",
    "LABEL_NAMESPACE",
    "ApplicationIdentity",
    "ApplicationIdentityError",
    "identity_labels",
    "parse_observed_labels",
    "produce_application_identity",
]
