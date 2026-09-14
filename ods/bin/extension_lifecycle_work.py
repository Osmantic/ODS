"""Pure host boundary for one synchronous extension lifecycle operation.

This module validates the exact request produced by the Dashboard lifecycle
client and invokes only an injected dispatcher.  It has no import-time effects,
does not know lease credentials, and cannot mutate an ODS installation by
itself.  The host agent owns authentication, strict HTTP framing, lease
admission, and response mapping around this core.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

REQUEST_SCHEMA = "ods.extension-lifecycle-work-request.v1"
RESULT_SCHEMA = "ods.extension-lifecycle-work-result.v1"
FAILURE_SCHEMA = "ods.extension-lifecycle-work-failure.v1"
MAX_WORK_REQUEST_BYTES = 32 * 1024
MAX_HTTP_REQUEST_BYTES = 40 * 1024
MAX_SERVICE_IDS = 64

REQUEST_KEYS = frozenset(
    {
        "schema",
        "transactionId",
        "planHash",
        "operationKey",
        "serviceIds",
        "payload",
        "requestHash",
    }
)

_TRANSACTION_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SAFE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")
_BATCH_OPERATION_TIMEOUTS = {
    "download-and-verify": 1800,
    "stage": 600,
    "backup": 600,
    "configure": 600,
    "verify": 600,
    "restore": 600,
    "release": 30,
}
_PER_SERVICE_OPERATION_TIMEOUTS = {
    "reserve": 30,
    "apply": 900,
    "compensate": 900,
}


class LifecycleWorkError(RuntimeError):
    """Base failure carrying only a stable public protocol code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LifecycleWorkValidationError(LifecycleWorkError):
    """The submitted immutable request is invalid or misbound."""


class LifecycleWorkUnavailable(LifecycleWorkError):
    """No reviewed dispatcher is available for this operation."""


class LifecycleWorkExecutionError(LifecycleWorkError):
    """The dispatcher failed or returned unverifiable evidence."""


@dataclass(frozen=True)
class LifecycleWorkCommand:
    """Validated command passed to a host-owned dispatcher without a token."""

    transaction_id: str
    plan_hash: str
    operation_key: str
    request_hash: str
    service_ids: tuple[str, ...]
    payload: dict[str, Any]
    timeout_seconds: int
    plan_material: Any = field(default=None, repr=False, compare=False)


def _invalid(code: str = "invalid-lifecycle-work-request") -> None:
    raise LifecycleWorkValidationError(code) from None


def _canonical_clone(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        _invalid()
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [_canonical_clone(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            _invalid()
        return {
            key: _canonical_clone(item, depth=depth + 1) for key, item in value.items()
        }
    _invalid()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            _canonical_clone(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _invalid()
    if len(encoded) > MAX_WORK_REQUEST_BYTES:
        _invalid("lifecycle-work-request-size")
    return encoded


def _service_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SERVICE_IDS:
        _invalid()
    if any(
        not isinstance(service_id, str)
        or len(service_id) > 128
        or _SERVICE_ID_RE.fullmatch(service_id) is None
        for service_id in value
    ):
        _invalid()
    if len(set(value)) != len(value):
        _invalid()
    return tuple(value)


def _operation_key(value: Any, service_ids: tuple[str, ...]) -> tuple[str, int]:
    if not isinstance(value, str):
        _invalid()
    if value in _BATCH_OPERATION_TIMEOUTS:
        return value, _BATCH_OPERATION_TIMEOUTS[value]
    prefix, separator, suffix = value.partition(":")
    if (
        separator != ":"
        or prefix not in _PER_SERVICE_OPERATION_TIMEOUTS
        or len(service_ids) != 1
        or suffix != service_ids[0]
    ):
        _invalid()
    return value, _PER_SERVICE_OPERATION_TIMEOUTS[prefix]


def _operation_payload(
    operation_key: str,
    service_ids: tuple[str, ...],
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _invalid()
    payload = _canonical_clone(value)
    if not isinstance(payload, dict):  # defensive for type checkers
        _invalid()

    if ":" in operation_key:
        if set(payload) != {"operation"}:
            _invalid()
        operation = payload["operation"]
        if (
            not isinstance(operation, dict)
            or operation.get("serviceId") != service_ids[0]
        ):
            _invalid()
        return payload

    if operation_key in {"download-and-verify", "stage"}:
        if set(payload) != {"operations"}:
            _invalid()
        operations = payload["operations"]
        if not isinstance(operations, list) or [
            item.get("serviceId") if isinstance(item, dict) else None
            for item in operations
        ] != list(service_ids):
            _invalid()
        return payload

    if set(payload) != {"serviceIds"} or payload["serviceIds"] != list(service_ids):
        _invalid()
    return payload


def parse_lifecycle_work_request(body: Any) -> LifecycleWorkCommand:
    """Validate and clone one exact request, including its canonical hash."""

    if not isinstance(body, dict) or set(body) != REQUEST_KEYS:
        _invalid()
    if body["schema"] != REQUEST_SCHEMA:
        _invalid()

    transaction_id = body["transactionId"]
    plan_hash = body["planHash"]
    request_hash = body["requestHash"]
    if (
        not isinstance(transaction_id, str)
        or _TRANSACTION_ID_RE.fullmatch(transaction_id) is None
        or not isinstance(plan_hash, str)
        or _HASH_RE.fullmatch(plan_hash) is None
        or not isinstance(request_hash, str)
        or _HASH_RE.fullmatch(request_hash) is None
    ):
        _invalid()

    service_ids = _service_ids(body["serviceIds"])
    operation_key, timeout_seconds = _operation_key(body["operationKey"], service_ids)
    payload = _operation_payload(operation_key, service_ids, body["payload"])
    unsigned = {
        "schema": REQUEST_SCHEMA,
        "transactionId": transaction_id,
        "planHash": plan_hash,
        "operationKey": operation_key,
        "serviceIds": list(service_ids),
        "payload": payload,
    }
    expected_hash = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    if not hmac.compare_digest(request_hash, expected_hash):
        _invalid("lifecycle-work-request-hash-mismatch")

    return LifecycleWorkCommand(
        transaction_id=transaction_id,
        plan_hash=plan_hash,
        operation_key=operation_key,
        request_hash=expected_hash,
        service_ids=service_ids,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )


def dispatch_lifecycle_work(
    command: LifecycleWorkCommand,
    dispatcher: Callable[[LifecycleWorkCommand], str] | None,
) -> dict[str, Any]:
    """Run one plan-bound synchronous dispatcher and build an exact result."""

    if not isinstance(command, LifecycleWorkCommand):
        _invalid()
    if not callable(dispatcher):
        raise LifecycleWorkUnavailable("lifecycle-work-dispatcher-unavailable")
    if command.plan_material is None:
        raise LifecycleWorkValidationError("lifecycle-work-plan-mismatch")
    try:
        evidence_hash = dispatcher(command)
    except LifecycleWorkError:
        raise
    except Exception as exc:
        raise LifecycleWorkExecutionError("lifecycle-work-operation-failed") from exc
    if not isinstance(evidence_hash, str) or _HASH_RE.fullmatch(evidence_hash) is None:
        raise LifecycleWorkExecutionError("lifecycle-work-invalid-result")
    return _completed_result(command, evidence_hash)


def _completed_result(
    command: LifecycleWorkCommand,
    evidence_hash: str,
) -> dict[str, Any]:
    if not isinstance(evidence_hash, str) or _HASH_RE.fullmatch(evidence_hash) is None:
        raise LifecycleWorkExecutionError("lifecycle-work-invalid-result")
    return {
        "schema": RESULT_SCHEMA,
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "operationKey": command.operation_key,
        "requestHash": command.request_hash,
        "serviceIds": list(command.service_ids),
        "completed": True,
        "outcome": "completed",
        "evidenceHash": evidence_hash,
    }


def _failure_evidence_hash(command: LifecycleWorkCommand, code: Any) -> str:
    safe_code = code if isinstance(code, str) else "lifecycle-work-operation-failed"
    if _SAFE_CODE_RE.fullmatch(safe_code) is None:
        safe_code = "lifecycle-work-operation-failed"
    return hashlib.sha256(
        _canonical_bytes(
            {
                "schema": FAILURE_SCHEMA,
                "transactionId": command.transaction_id,
                "planHash": command.plan_hash,
                "operationKey": command.operation_key,
                "requestHash": command.request_hash,
                "serviceIds": list(command.service_ids),
                "outcome": "failed",
                "code": safe_code,
            }
        )
    ).hexdigest()


def _require_receipt_binding(receipt: Any, command: LifecycleWorkCommand) -> None:
    if (
        receipt is None
        or getattr(receipt, "transaction_id", None) != command.transaction_id
        or getattr(receipt, "plan_hash", None) != command.plan_hash
        or getattr(receipt, "operation_key", None) != command.operation_key
        or getattr(receipt, "request_hash", None) != command.request_hash
        or getattr(receipt, "service_ids", None) != command.service_ids
        or not isinstance(getattr(receipt, "event_hash", None), str)
        or _HASH_RE.fullmatch(receipt.event_hash) is None
    ):
        raise LifecycleWorkValidationError("lifecycle-work-receipt-mismatch")


def _terminal_from_snapshot(snapshot: Any, command: LifecycleWorkCommand) -> Any:
    if (
        snapshot is None
        or getattr(snapshot, "transaction_id", None) != command.transaction_id
        or getattr(snapshot, "operation_key", None) != command.operation_key
    ):
        raise LifecycleWorkValidationError("lifecycle-work-receipt-mismatch")
    state = getattr(snapshot, "state", None)
    started = getattr(snapshot, "started_receipt", None)
    terminal = getattr(snapshot, "terminal_receipt", None)
    if state == "absent":
        if started is not None or terminal is not None:
            raise LifecycleWorkValidationError("lifecycle-work-receipt-mismatch")
        raise LifecycleWorkValidationError(
            "lifecycle-work-started-receipt-required"
        )
    _require_receipt_binding(started, command)
    if state == "started":
        if terminal is not None:
            raise LifecycleWorkValidationError("lifecycle-work-receipt-mismatch")
        return None
    if state not in {"completed", "failed"}:
        raise LifecycleWorkValidationError("lifecycle-work-receipt-mismatch")
    return _require_terminal_receipt(started, terminal, state, command)


def _require_terminal_receipt(
    started: Any,
    terminal: Any,
    state: str,
    command: LifecycleWorkCommand,
) -> Any:
    _require_receipt_binding(started, command)
    _require_receipt_binding(terminal, command)
    if (
        getattr(terminal, "outcome", None) != state
        or getattr(terminal, "started_event_hash", None) != started.event_hash
        or not isinstance(getattr(terminal, "evidence_hash", None), str)
        or _HASH_RE.fullmatch(terminal.evidence_hash) is None
    ):
        raise LifecycleWorkValidationError("lifecycle-work-receipt-mismatch")
    return terminal


def dispatch_receipted_lifecycle_work(
    command: LifecycleWorkCommand,
    dispatcher: Callable[[LifecycleWorkCommand], str] | None,
    receipt_store: Any,
    plan_loader: Callable[[LifecycleWorkCommand], LifecycleWorkCommand] | None,
) -> dict[str, Any]:
    """Run at most one host operation and durably terminalize its receipt.

    The Dashboard publishes the immutable started receipt before submitting
    host work.  The host verifies that exact binding and publishes the matching
    terminal receipt before returning.  A completed terminal is replayed
    without another dispatcher call; a failed terminal is never retried.
    """

    if not isinstance(command, LifecycleWorkCommand):
        _invalid()
    if not callable(dispatcher):
        raise LifecycleWorkUnavailable("lifecycle-work-dispatcher-unavailable")
    if not callable(plan_loader):
        raise LifecycleWorkUnavailable("lifecycle-work-plan-loader-unavailable")
    if receipt_store is None or not callable(getattr(receipt_store, "snapshot", None)):
        raise LifecycleWorkUnavailable("lifecycle-work-receipt-store-unavailable")
    if not callable(getattr(receipt_store, "finish", None)):
        raise LifecycleWorkUnavailable("lifecycle-work-receipt-store-unavailable")

    try:
        snapshot = receipt_store.snapshot(command.transaction_id, command.operation_key)
    except Exception as exc:
        raise LifecycleWorkExecutionError(
            "lifecycle-work-receipt-store-unavailable"
        ) from exc
    terminal = _terminal_from_snapshot(snapshot, command)
    if terminal is not None:
        if terminal.outcome == "completed":
            return _completed_result(command, terminal.evidence_hash)
        raise LifecycleWorkExecutionError("lifecycle-work-terminal-failed")

    try:
        bound_command = plan_loader(command)
        if (
            not isinstance(bound_command, LifecycleWorkCommand)
            or bound_command.plan_material is None
            or any(
                getattr(bound_command, field_name) != getattr(command, field_name)
                for field_name in (
                    "transaction_id",
                    "plan_hash",
                    "operation_key",
                    "request_hash",
                    "service_ids",
                    "payload",
                    "timeout_seconds",
                )
            )
        ):
            raise LifecycleWorkValidationError("lifecycle-work-plan-mismatch")
        result = dispatch_lifecycle_work(bound_command, dispatcher)
    except LifecycleWorkError as dispatch_error:
        evidence_hash = _failure_evidence_hash(command, dispatch_error.code)
        try:
            terminal = receipt_store.finish(
                command.transaction_id,
                command.plan_hash,
                command.operation_key,
                command.request_hash,
                command.service_ids,
                "failed",
                evidence_hash,
            )
            _require_terminal_receipt(
                snapshot.started_receipt,
                terminal,
                "failed",
                command,
            )
        except LifecycleWorkError:
            raise
        except Exception as exc:
            raise LifecycleWorkExecutionError(
                "lifecycle-work-receipt-store-unavailable"
            ) from exc
        raise dispatch_error

    evidence_hash = result["evidenceHash"]
    try:
        terminal = receipt_store.finish(
            command.transaction_id,
            command.plan_hash,
            command.operation_key,
            command.request_hash,
            command.service_ids,
            "completed",
            evidence_hash,
        )
        validated = _require_terminal_receipt(
            snapshot.started_receipt,
            terminal,
            "completed",
            command,
        )
    except LifecycleWorkError:
        raise
    except Exception as exc:
        raise LifecycleWorkExecutionError(
            "lifecycle-work-receipt-store-unavailable"
        ) from exc
    return _completed_result(command, validated.evidence_hash)


__all__ = [
    "LifecycleWorkCommand",
    "LifecycleWorkError",
    "LifecycleWorkExecutionError",
    "LifecycleWorkUnavailable",
    "LifecycleWorkValidationError",
    "FAILURE_SCHEMA",
    "MAX_HTTP_REQUEST_BYTES",
    "MAX_SERVICE_IDS",
    "MAX_WORK_REQUEST_BYTES",
    "REQUEST_KEYS",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "dispatch_lifecycle_work",
    "dispatch_receipted_lifecycle_work",
    "parse_lifecycle_work_request",
]
