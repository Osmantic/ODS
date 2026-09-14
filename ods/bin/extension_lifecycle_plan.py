"""Bind one host lifecycle command to its exact owner-approved plan.

The lifecycle request hash proves that a request was not changed in transit,
and the host lease proves exclusion for one transaction/plan tuple.  Neither
proves that the tuple names an approved plan or that the requested operation
and definition material came from that plan.  This module closes that gap
before a concrete host dispatcher may run.

It is intentionally stdlib-only and performs no filesystem, subprocess,
container, network, or secret operation.  The host agent injects the result of
its owner-private TransactionStore read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkValidationError,
)


PLAN_MATERIAL_SCHEMA = "ods.extension-lifecycle-plan-material.v1"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ACTIONS = frozenset({"install", "enable", "repair", "update", "noop"})
_DEFINITION_KEYS = frozenset(
    {
        "id",
        "serviceType",
        "manifestSchemaVersion",
        "version",
        "dataSchemaVersion",
        "odsCompatibility",
        "definitionSha256",
        "composeSha256",
        "dependsOn",
        "provides",
        "requires",
        "conflicts",
        "requirements",
        "estimates",
        "configuration",
        "artifacts",
        "resources",
        "lifecycle",
        "data",
        "trust",
        "support",
    }
)
_IMAGE_KEYS = frozenset({"reference", "digest", "downloadBytes"})
_BUILD_KEYS = frozenset(
    {"source", "revision", "contextDigest", "output", "downloadBytes"}
)
_ALLOWED_STATES = {
    "reserve": frozenset({"reserved"}),
    "download-and-verify": frozenset({"downloading"}),
    "stage": frozenset({"staged"}),
    "backup": frozenset({"configuring"}),
    "configure": frozenset({"configuring"}),
    "apply": frozenset({"applying"}),
    "verify": frozenset({"verifying"}),
    "compensate": frozenset({"reconciling"}),
    "restore": frozenset({"reconciling"}),
    "release": frozenset({"verifying", "reconciling"}),
}


@dataclass(frozen=True)
class PlannedOperation:
    service_id: str
    action: str


@dataclass(frozen=True)
class PlannedImage:
    reference: str
    digest: str
    download_bytes: int


@dataclass(frozen=True)
class PlannedBuild:
    source: str
    revision: str
    context_digest: str
    output: str
    download_bytes: int


@dataclass(frozen=True)
class PlannedDefinition:
    service_id: str
    service_type: str
    manifest_schema_version: str
    version: str
    data_schema_version: str
    definition_sha256: str
    compose_sha256: str | None
    images: tuple[PlannedImage, ...]
    builds: tuple[PlannedBuild, ...]
    canonical_document: bytes


@dataclass(frozen=True)
class LifecyclePlanMaterial:
    schema: str
    transaction_id: str
    plan_hash: str
    state: str
    operations: tuple[PlannedOperation, ...]
    definitions: tuple[PlannedDefinition, ...]


def _reject() -> None:
    raise LifecycleWorkValidationError("lifecycle-work-plan-mismatch") from None


def _service_id(value: Any) -> str:
    if not isinstance(value, str) or _SERVICE_ID_RE.fullmatch(value) is None:
        _reject()
    return value


def _text(value: Any, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _reject()
    return value


def _digest(value: Any, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _reject()
    return value


def _bytes(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _reject()
    return value


def _canonical_document(value: dict[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        _reject()


def _operation(value: Any) -> PlannedOperation:
    if not isinstance(value, dict) or set(value) != {"serviceId", "action"}:
        _reject()
    service_id = _service_id(value["serviceId"])
    action = value["action"]
    if not isinstance(action, str) or action not in _ACTIONS:
        _reject()
    return PlannedOperation(service_id, action)


def _image(value: Any) -> PlannedImage:
    if not isinstance(value, dict) or set(value) != _IMAGE_KEYS:
        _reject()
    digest = _digest(value["digest"])
    assert isinstance(digest, str)
    return PlannedImage(
        reference=_text(value["reference"]),
        digest=digest,
        download_bytes=_bytes(value["downloadBytes"]),
    )


def _build(value: Any) -> PlannedBuild:
    if not isinstance(value, dict) or set(value) != _BUILD_KEYS:
        _reject()
    context_digest = _digest(value["contextDigest"])
    assert isinstance(context_digest, str)
    return PlannedBuild(
        source=_text(value["source"]),
        revision=_text(value["revision"], maximum=128),
        context_digest=context_digest,
        output=_text(value["output"]),
        download_bytes=_bytes(value["downloadBytes"]),
    )


def _definition(value: Any) -> PlannedDefinition:
    if not isinstance(value, dict) or set(value) != _DEFINITION_KEYS:
        _reject()
    service_id = _service_id(value["id"])
    artifacts = value["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {"images", "builds"}:
        _reject()
    raw_images = artifacts["images"]
    raw_builds = artifacts["builds"]
    if not isinstance(raw_images, list) or not isinstance(raw_builds, list):
        _reject()
    images = tuple(_image(item) for item in raw_images)
    builds = tuple(_build(item) for item in raw_builds)
    if len({item.reference for item in images}) != len(images):
        _reject()
    if len({item.output for item in builds}) != len(builds):
        _reject()
    if {item.reference for item in images} & {item.output for item in builds}:
        _reject()
    definition_sha256 = _digest(value["definitionSha256"])
    compose_sha256 = _digest(value["composeSha256"], optional=True)
    assert isinstance(definition_sha256, str)
    return PlannedDefinition(
        service_id=service_id,
        service_type=_text(value["serviceType"], maximum=64),
        manifest_schema_version=_text(value["manifestSchemaVersion"], maximum=64),
        version=_text(value["version"], maximum=128),
        data_schema_version=_text(value["dataSchemaVersion"], maximum=128),
        definition_sha256=definition_sha256,
        compose_sha256=compose_sha256,
        images=images,
        builds=builds,
        canonical_document=_canonical_document(value),
    )


def _expected_request(
    command: LifecycleWorkCommand,
    operations: tuple[PlannedOperation, ...],
) -> None:
    operation_key = command.operation_key
    prefix = operation_key.partition(":")[0]
    allowed_states = _ALLOWED_STATES.get(prefix)
    if allowed_states is None:
        _reject()

    mutable = tuple(item for item in operations if item.action != "noop")
    mutable_documents = [
        {"serviceId": item.service_id, "action": item.action} for item in mutable
    ]

    if prefix in {"reserve", "apply", "compensate"}:
        matches = [item for item in mutable if item.service_id == command.service_ids[0]]
        if len(matches) != 1:
            _reject()
        item = matches[0]
        expected_payload = {
            "operation": {"serviceId": item.service_id, "action": item.action}
        }
        expected_services = (item.service_id,)
    elif prefix in {"download-and-verify", "stage"}:
        expected_payload = {"operations": mutable_documents}
        expected_services = tuple(item.service_id for item in mutable)
    elif prefix == "verify":
        expected_services = tuple(sorted(item.service_id for item in operations))
        expected_payload = {"serviceIds": list(expected_services)}
    else:
        expected_services = tuple(item.service_id for item in mutable)
        expected_payload = {"serviceIds": list(expected_services)}

    if command.service_ids != expected_services or command.payload != expected_payload:
        _reject()


def bind_lifecycle_plan(
    command: LifecycleWorkCommand,
    transaction: Any,
) -> LifecycleWorkCommand:
    """Return the command with immutable material from one exact approved plan."""

    if not isinstance(command, LifecycleWorkCommand) or command.plan_material is not None:
        _reject()
    if not isinstance(transaction, dict):
        _reject()
    if transaction.get("transactionId") != command.transaction_id:
        _reject()
    state = transaction.get("state")
    if not isinstance(state, str):
        _reject()

    envelope = transaction.get("envelope")
    approval = transaction.get("approval")
    if not isinstance(envelope, dict) or not isinstance(approval, dict):
        _reject()
    if (
        envelope.get("planHash") != command.plan_hash
        or approval.get("transactionId") != command.transaction_id
        or approval.get("planHash") != command.plan_hash
    ):
        _reject()
    plan = envelope.get("plan")
    if not isinstance(plan, dict):
        _reject()

    raw_operations = plan.get("operations")
    selected_services = plan.get("selectedServices")
    raw_definitions = plan.get("definitions")
    if (
        not isinstance(raw_operations, list)
        or not isinstance(selected_services, list)
        or not isinstance(raw_definitions, list)
    ):
        _reject()
    operations = tuple(_operation(item) for item in raw_operations)
    operation_services = tuple(item.service_id for item in operations)
    if (
        not operations
        or len(set(operation_services)) != len(operation_services)
        or selected_services != list(operation_services)
    ):
        _reject()
    definitions = tuple(_definition(item) for item in raw_definitions)
    if tuple(item.service_id for item in definitions) != operation_services:
        _reject()

    prefix = command.operation_key.partition(":")[0]
    if state not in _ALLOWED_STATES.get(prefix, frozenset()):
        _reject()
    _expected_request(command, operations)

    material = LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=command.transaction_id,
        plan_hash=command.plan_hash,
        state=state,
        operations=operations,
        definitions=definitions,
    )
    return replace(command, plan_material=material)


__all__ = [
    "LifecyclePlanMaterial",
    "PLAN_MATERIAL_SCHEMA",
    "PlannedBuild",
    "PlannedDefinition",
    "PlannedImage",
    "PlannedOperation",
    "bind_lifecycle_plan",
]
