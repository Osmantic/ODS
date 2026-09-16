"""Bind a read-only library verification request to one approved plan.

This module selects the exact Manifest v2 library services that a future host
verifier must observe. It does not infer health from an apply receipt and has
no filesystem, Docker, network, or installation effect.
"""

from __future__ import annotations

from dataclasses import dataclass

from extension_library_application_runtime import APPROVED_LIBRARY_SERVICES
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkValidationError

_ACTIONS = frozenset({"install", "enable", "repair", "update", "noop"})


@dataclass(frozen=True)
class LibraryVerifySelection:
    service_id: str
    action: str
    definition: PlannedDefinition


def _deny() -> None:
    raise LifecycleWorkValidationError("library-verify-plan-mismatch") from None


def bind_library_verify(
    command: LifecycleWorkCommand,
) -> tuple[LibraryVerifySelection, ...]:
    """Return all selected library services, including prior installed noops."""

    if (
        type(command) is not LifecycleWorkCommand
        or command.operation_key != "verify"
        or type(command.service_ids) is not tuple
        or not command.service_ids
        or command.service_ids != tuple(sorted(set(command.service_ids)))
        or command.payload != {"serviceIds": list(command.service_ids)}
    ):
        _deny()
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != "verifying"
        or material.attested_approval is not True
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or not material.operations
        or len(material.operations) != len(material.definitions)
        or len(material.operations) != len(command.service_ids)
    ):
        _deny()

    selected: list[LibraryVerifySelection] = []
    for operation, definition in zip(
        material.operations, material.definitions, strict=True
    ):
        if (
            type(operation) is not PlannedOperation
            or type(definition) is not PlannedDefinition
            or operation.service_id != definition.service_id
            or operation.service_id not in APPROVED_LIBRARY_SERVICES
            or operation.action not in _ACTIONS
            or definition.service_type != "docker"
            or definition.manifest_schema_version != "ods.services.v2"
            or definition.definition_source != "library"
            or definition.compose_file != "compose.yaml"
            or definition.compose_sha256 is None
            or definition.source_tree_sha256 is None
        ):
            _deny()
        selected.append(
            LibraryVerifySelection(
                service_id=operation.service_id,
                action=operation.action,
                definition=definition,
            )
        )
    if tuple(sorted(item.service_id for item in selected)) != command.service_ids:
        _deny()
    return tuple(sorted(selected, key=lambda item: item.service_id))


__all__ = ["LibraryVerifySelection", "bind_library_verify"]
