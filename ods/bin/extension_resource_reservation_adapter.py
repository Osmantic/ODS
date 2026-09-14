"""Bind plan-bound reservation operations to one immutable store reservation.

This dormant adapter is the only bridge between the read-only plan material
and the immutable reservation store.  It validates the complete reserve command
before opening a definition, re-proves the LifecyclePlanMaterial binding,
targets exactly one non-noop operation/definition and exact payload, requires
claims before effect, converts/sorts plan ports to store form, preserves
exclusive order, owns a strict UTC-second clock, maps every
ReservationStoreError to the fixed value-free lifecycle-work-reservation-failed,
validates the exact returned ReservationRecord binding/status/claims/hex evidence,
returns ``record_sha256``. Invoking it performs only the bound
reservation-store mutation via ``ResourceReservationStore.reserve``: it writes
the owner-private reservation record and has no process, network, service,
container, or Compose effect.

It does not create roots, discover definitions, evaluate Compose, or operate
services and containers.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedHostPort,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkValidationError,
)
from extension_resource_reservation_store import (
    HostPort,
    ReservationClaims,
    ReservationRecord,
    ReservationStoreError,
    ResourceReservationStore,
)

_HASH_RE = re.compile(r"[0-9a-f]{64}")


class ResourceReservationAdapterError(LifecycleWorkExecutionError):
    """Stable, value-free failure after command binding was accepted."""


def _execution_error(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise ResourceReservationAdapterError(code) from None
    raise ResourceReservationAdapterError(code) from cause


def _validation_error(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _now_utc_second() -> str:
    """Strict UTC-second wall clock for reservation timestamps."""
    dt = datetime.now(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _map_store_error(exc: ReservationStoreError) -> None:
    """Map all ReservationStoreError codes to one fixed lifecycle code."""
    _execution_error("lifecycle-work-reservation-failed", exc)


def _validate_store(store: Any) -> ResourceReservationStore:
    if type(store) is not ResourceReservationStore:
        _execution_error("lifecycle-work-reservation-adapter-invalid")
    return store


def _plan_ports_to_store(
    ports: tuple[PlannedHostPort, ...],
) -> tuple[HostPort, ...]:
    """Convert plan ports to store form.

    Validates each item is exactly PlannedHostPort, converts the tuple,
    and sorts by (port, protocol) ascending for store canonical order.
    Duplicate (port, protocol) pairs are passed through to let the
    ReservationClaims constructor reject them.
    """
    converted: list[HostPort] = []
    for hp in ports:
        if type(hp) is not PlannedHostPort:
            _validation_error("lifecycle-work-plan-mismatch")
        converted.append(HostPort(port=hp.port, protocol=hp.protocol))
    converted.sort(key=lambda hp: (hp.port, hp.protocol))
    return tuple(converted)


def _validate_record(
    record: Any,
    command: LifecycleWorkCommand,
    action: str,
    claims: ReservationClaims,
) -> ReservationRecord:
    """Validate the returned ReservationRecord matches the expected binding."""
    if type(record) is not ReservationRecord:
        _execution_error("lifecycle-work-reservation-evidence-mismatch")
    if (
        record.transaction_id != command.transaction_id
        or record.plan_hash != command.plan_hash
        or record.service_id != command.service_ids[0]
        or record.status != "active"
        or record.action != action
        or record.claims != claims
        or type(record.duplicate) is not bool
        or not isinstance(record.record_sha256, str)
        or _HASH_RE.fullmatch(record.record_sha256) is None
    ):
        _execution_error("lifecycle-work-reservation-evidence-mismatch")
    return record


class ResourceReservationAdapter:
    """Callable dispatcher for only the plan-bound ``reserve`` operation."""

    def __init__(self, store: ResourceReservationStore) -> None:
        _validate_store(store)
        self._store = store

    def __call__(self, command: LifecycleWorkCommand) -> str:
        """Re-prove the plan, extract one definition, and reserve once.

        Returns the reservation record's ``record_sha256`` hex digest.
        """
        if type(command) is not LifecycleWorkCommand:
            _validation_error("lifecycle-work-command-invalid")

        plan_material = command.plan_material
        if plan_material is None:
            _validation_error("lifecycle-work-plan-missing")
        if type(plan_material) is not LifecyclePlanMaterial:
            _validation_error("lifecycle-work-plan-mismatch")
        if plan_material.schema != PLAN_MATERIAL_SCHEMA:
            _validation_error("lifecycle-work-plan-mismatch")
        if plan_material.transaction_id != command.transaction_id:
            _validation_error("lifecycle-work-plan-mismatch")
        if plan_material.plan_hash != command.plan_hash:
            _validation_error("lifecycle-work-plan-mismatch")
        if plan_material.state != "reserved":
            _validation_error("lifecycle-work-plan-mismatch")

        # Prove operations and definitions are exact nonempty tuples of correct types
        if (
            type(plan_material.operations) is not tuple
            or type(plan_material.definitions) is not tuple
        ):
            _validation_error("lifecycle-work-plan-mismatch")
        if not plan_material.operations or not plan_material.definitions:
            _validation_error("lifecycle-work-plan-mismatch")
        if len(plan_material.operations) != len(plan_material.definitions):
            _validation_error("lifecycle-work-plan-mismatch")
        for op in plan_material.operations:
            if type(op) is not PlannedOperation:
                _validation_error("lifecycle-work-plan-mismatch")
        for d in plan_material.definitions:
            if type(d) is not PlannedDefinition:
                _validation_error("lifecycle-work-plan-mismatch")

        # Positionally compare every paired operation service_id to its
        # corresponding definition service_id to reject crossing material.
        for op, d in zip(plan_material.operations, plan_material.definitions):
            if op.service_id != d.service_id:
                _validation_error("lifecycle-work-plan-mismatch")

        # Prove service_ids is an exact tuple of length 1 with a string
        if type(command.service_ids) is not tuple:
            _validation_error("lifecycle-work-command-invalid")
        if len(command.service_ids) != 1:
            _validation_error("lifecycle-work-command-invalid")
        service_id = command.service_ids[0]
        if not isinstance(service_id, str):
            _validation_error("lifecycle-work-command-invalid")

        # Must be a reserve operation: operation_key must be exactly "reserve:{service_id}"
        expected_key = f"reserve:{service_id}"
        if command.operation_key != expected_key:
            _validation_error("lifecycle-work-operation-mismatch")

        # Find the non-noop operation for this service
        operations = [
            op
            for op in plan_material.operations
            if op.action != "noop" and op.service_id == service_id
        ]
        if len(operations) != 1:
            _validation_error("lifecycle-work-plan-mismatch")
        operation = operations[0]
        action = operation.action

        # Reject actions outside install/enable/repair/update
        if action not in ("install", "enable", "repair", "update"):
            _validation_error("lifecycle-work-plan-mismatch")

        # Validate payload matches the single-operation reserve expectation
        expected_payload = {"operation": {"serviceId": service_id, "action": action}}
        if command.payload != expected_payload:
            _validation_error("lifecycle-work-plan-mismatch")

        # Find the targeted definition with claims
        definitions = [
            d for d in plan_material.definitions if d.service_id == service_id
        ]
        if len(definitions) != 1:
            _validation_error("lifecycle-work-plan-mismatch")
        definition = definitions[0]
        if type(definition) is not PlannedDefinition:
            _validation_error("lifecycle-work-plan-mismatch")
        if definition.host_ports is None or definition.exclusive is None:
            _execution_error("lifecycle-work-reservation-claims-missing")

        # Require host_ports and exclusive to be exact tuples
        if (type(definition.host_ports) is not tuple
                or type(definition.exclusive) is not tuple):
            _validation_error("lifecycle-work-plan-mismatch")

        # Convert plan ports to store form and build claims
        try:
            store_ports = _plan_ports_to_store(definition.host_ports)
        except (TypeError, ValueError):
            _validation_error("lifecycle-work-plan-mismatch")

        # Exclusive tokens are already in ascending planner-canonical order
        try:
            claims = ReservationClaims(
                host_ports=store_ports,
                exclusive=definition.exclusive,
            )
        except (TypeError, ValueError):
            _validation_error("lifecycle-work-plan-mismatch")

        # Strict UTC-second clock
        now = _now_utc_second()

        # Call store and map all errors
        try:
            record = self._store.reserve(
                transaction_id=command.transaction_id,
                plan_hash=command.plan_hash,
                service_id=service_id,
                action=action,
                claims=claims,
                now=now,
            )
        except ReservationStoreError as exc:
            _map_store_error(exc)
        except Exception as exc:  # noqa: BLE001 -- stable value-free fail-closed mapping; raw exception text is never exposed in the error code
            _execution_error("lifecycle-work-reservation-failed", exc)

        # Validate exact returned evidence
        _validate_record(record, command, action, claims)
        return record.record_sha256


__all__ = [
    "ResourceReservationAdapter",
    "ResourceReservationAdapterError",
]
