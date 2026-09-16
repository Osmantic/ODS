"""Receipted configuration preflight for approved Manifest v2 library apps.

The library apply effect publishes non-secret configuration and obtains secret
values from host custody at application time.  This configuring operation has
no file or container effect: it proves that every mutable library service has
the exact owner-approved configuration and that required secrets remain in
host custody.  A started-only receipt can safely rerun this proof.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from extension_library_application_runtime import APPROVED_LIBRARY_SERVICES
from extension_library_configuration_binding import bind_library_configuration
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkValidationError,
)


CONFIGURATION_EVIDENCE_SCHEMA = "ods.assistant-first.library-configuration-evidence.v1"
_MUTABLE_ACTIONS = frozenset({"install", "enable", "repair", "update"})


def _deny(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _validate_command(command: Any) -> None:
    if type(command) is not LifecycleWorkCommand:
        _deny("library-configuration-command-invalid")
    if (
        command.operation_key != "configure"
        or type(command.service_ids) is not tuple
        or not command.service_ids
        or len(command.service_ids) > 64
        or any(type(item) is not str for item in command.service_ids)
        or len(set(command.service_ids)) != len(command.service_ids)
        or not set(command.service_ids) <= APPROVED_LIBRARY_SERVICES
        or command.payload != {"serviceIds": list(command.service_ids)}
    ):
        _deny("library-configuration-command-invalid")
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != "configuring"
        or material.attested_approval is not True
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or not material.operations
        or len(material.operations) != len(material.definitions)
    ):
        _deny("library-configuration-plan-mismatch")
    mutable: list[str] = []
    seen: set[str] = set()
    for operation, definition in zip(
        material.operations, material.definitions, strict=True
    ):
        if (
            type(operation) is not PlannedOperation
            or type(definition) is not PlannedDefinition
            or operation.service_id != definition.service_id
            or operation.service_id in seen
            or operation.action not in _MUTABLE_ACTIONS | {"noop"}
        ):
            _deny("library-configuration-plan-mismatch")
        seen.add(operation.service_id)
        if operation.action in _MUTABLE_ACTIONS:
            if (
                definition.service_type != "docker"
                or definition.manifest_schema_version != "ods.services.v2"
                or definition.definition_source != "library"
            ):
                _deny("library-configuration-definition-unsupported")
            mutable.append(operation.service_id)
    if command.service_ids != tuple(mutable):
        _deny("library-configuration-plan-mismatch")


class LibraryConfigurationDispatcher:
    """Validate one approved batch without materializing secret values."""

    def __init__(self, transaction_loader: Any, secret_status: Any) -> None:
        if not callable(transaction_loader) or not callable(secret_status):
            raise LifecycleWorkExecutionError(
                "library-configuration-runtime-invalid"
            ) from None
        self._transaction_loader = transaction_loader
        self._secret_status = secret_status

    def __call__(self, command: LifecycleWorkCommand) -> str:
        _validate_command(command)
        transaction_loaded = False
        transaction: Any = None
        status_request: Any = None
        status: Any = None

        def transaction_snapshot(transaction_id: str) -> Any:
            nonlocal transaction_loaded, transaction
            if transaction_id != command.transaction_id:
                raise ValueError("library-configuration-transaction-mismatch")
            if not transaction_loaded:
                transaction = self._transaction_loader(transaction_id)
                transaction_loaded = True
            return transaction

        def secret_snapshot(request: dict[str, Any]) -> Any:
            nonlocal status_request, status
            if status_request is None:
                status_request = request
                status = self._secret_status(request)
            elif request != status_request:
                raise ValueError("library-configuration-secret-mismatch")
            return status

        projections: list[dict[str, Any]] = []
        for service_id in command.service_ids:
            bound = bind_library_configuration(
                command,
                transaction_snapshot,
                secret_snapshot,
                expected_state="configuring",
                target_service_id=service_id,
            )
            projections.append(
                {
                    "serviceId": bound.service_id,
                    "schemaHash": bound.schema_hash,
                    "configured": bound.configured,
                    "values": dict(bound.values),
                    "secretKeys": list(bound.secret_keys),
                    "expectedSecretKeys": list(bound.expected_secret_keys),
                    "secretReference": bound.secret_reference,
                }
            )
        evidence = {
            "schema": CONFIGURATION_EVIDENCE_SCHEMA,
            "transactionId": command.transaction_id,
            "planHash": command.plan_hash,
            "operationKey": command.operation_key,
            "serviceIds": list(command.service_ids),
            "configurations": projections,
        }
        raw = (
            json.dumps(
                evidence,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class LibraryConfigurationRuntime:
    dispatcher: LibraryConfigurationDispatcher


def build_library_configuration_runtime(
    *, transaction_loader: Any, secret_status: Any
) -> LibraryConfigurationRuntime:
    """Compose the pure preflight; construction reads no transaction or secret."""

    return LibraryConfigurationRuntime(
        dispatcher=LibraryConfigurationDispatcher(transaction_loader, secret_status)
    )


__all__ = [
    "CONFIGURATION_EVIDENCE_SCHEMA",
    "LibraryConfigurationDispatcher",
    "LibraryConfigurationRuntime",
    "build_library_configuration_runtime",
]
