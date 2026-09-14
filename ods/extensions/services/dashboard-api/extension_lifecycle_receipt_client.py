"""Strict client for dormant host-owned extension lifecycle receipts.

The client publishes and observes receipt evidence only. It is intentionally
not wired into transaction execution, performs no retries, and never persists
or logs submitted values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from host_agent_client import (
    AgentClientError,
    AgentHTTPError,
    AgentProtocolError,
    AgentTimeout,
    AgentUnavailable,
    request_bounded_strict_json_200,
)

RECEIPT_SCHEMA = "ods.extension-lifecycle-receipt-api.v1"
MAX_RESPONSE_BYTES = 32 * 1024
MAX_SERVICE_ID_COUNT = 64
MAX_SERVICE_ID_LENGTH = 128

_TRANSACTION_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_HASH64_RE = re.compile(r"^[0-9a-f]{64}$")
# Match the catalog, lease, and host-agent service namespace. The receipt store
# accepts a broader storage grammar, but callers may name only managed services.
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_BATCH_OPERATION_KEYS = frozenset(
    {
        "download-and-verify",
        "stage",
        "backup",
        "configure",
        "verify",
        "restore",
        "release",
        "observe",
    }
)
_PER_SERVICE_OPERATION_PREFIXES = ("reserve:", "apply:", "compensate:")
_RECEIPT_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "kind",
        "transactionId",
        "planHash",
        "operationKey",
        "requestHash",
        "serviceIds",
        "eventHash",
        "outcome",
        "evidenceHash",
        "startedEventHash",
    }
)
_SNAPSHOT_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "transactionId",
        "planHash",
        "operationKey",
        "state",
        "startedReceipt",
        "terminalReceipt",
    }
)
_INTEGRITY_CODES = frozenset({"lifecycle-receipt-integrity"})
_ROUTES = {
    "begin": "/v1/extension/lifecycle-receipt/begin",
    "finish": "/v1/extension/lifecycle-receipt/finish",
    "snapshot": "/v1/extension/lifecycle-receipt/snapshot",
}
_TIMEOUTS = {"begin": 10.0, "finish": 10.0, "snapshot": 5.0}


class LifecycleReceiptClientError(RuntimeError):
    """Public-safe error with explicit retry and ambiguity classification."""

    def __init__(
        self, code: str, *, retryable: bool = False, ambiguous: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous

    def __repr__(self) -> str:
        return (
            f"LifecycleReceiptClientError({self.code!r}, "
            f"retryable={self.retryable!r}, ambiguous={self.ambiguous!r})"
        )


@dataclass(frozen=True)
class ReceiptResult:
    schema: str
    kind: str
    transaction_id: str
    plan_hash: str
    operation_key: str
    request_hash: str
    service_ids: tuple[str, ...]
    event_hash: str
    outcome: str | None
    evidence_hash: str | None
    started_event_hash: str | None


@dataclass(frozen=True)
class SnapshotResult:
    schema: str
    transaction_id: str
    plan_hash: str
    operation_key: str
    state: str
    started_receipt: ReceiptResult | None
    terminal_receipt: ReceiptResult | None


def _fail(code: str, *, retryable: bool = False, ambiguous: bool = False) -> None:
    raise LifecycleReceiptClientError(
        code, retryable=retryable, ambiguous=ambiguous
    ) from None


def _validate_transaction_id(value: Any) -> str:
    if not isinstance(value, str) or _TRANSACTION_ID_RE.fullmatch(value) is None:
        _fail("receipt-invalid-request")
    return value


def _validate_hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH64_RE.fullmatch(value) is None:
        _fail("receipt-invalid-request")
    return value


def _validate_service_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _fail("receipt-invalid-request")
    try:
        values = tuple(value)
    except TypeError:
        _fail("receipt-invalid-request")
    if not values or len(values) > MAX_SERVICE_ID_COUNT:
        _fail("receipt-invalid-request")
    if any(
        not isinstance(service_id, str)
        or not 1 <= len(service_id) <= MAX_SERVICE_ID_LENGTH
        or _SERVICE_ID_RE.fullmatch(service_id) is None
        for service_id in values
    ):
        _fail("receipt-invalid-request")
    if len(set(values)) != len(values):
        _fail("receipt-invalid-request")
    return values


def _validate_operation_key(
    operation_key: Any, service_ids: tuple[str, ...] | None
) -> str:
    if not isinstance(operation_key, str):
        _fail("receipt-invalid-request")
    if operation_key in _BATCH_OPERATION_KEYS:
        return operation_key
    for prefix in _PER_SERVICE_OPERATION_PREFIXES:
        if operation_key.startswith(prefix):
            suffix = operation_key[len(prefix) :]
            if _SERVICE_ID_RE.fullmatch(suffix) is None:
                _fail("receipt-invalid-request")
            if service_ids is not None and suffix not in service_ids:
                _fail("receipt-invalid-request")
            return operation_key
    _fail("receipt-invalid-request")


def _validate_outcome(value: Any) -> str:
    if not isinstance(value, str) or value not in {"completed", "failed"}:
        _fail("receipt-invalid-request")
    return value


def _response_hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH64_RE.fullmatch(value) is None:
        _fail("receipt-invalid-response")
    return value


def _response_service_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail("receipt-invalid-response")
    values = tuple(value)
    if not values or len(values) > MAX_SERVICE_ID_COUNT:
        _fail("receipt-invalid-response")
    if any(
        not isinstance(service_id, str)
        or not 1 <= len(service_id) <= MAX_SERVICE_ID_LENGTH
        or _SERVICE_ID_RE.fullmatch(service_id) is None
        for service_id in values
    ):
        _fail("receipt-invalid-response")
    if len(set(values)) != len(values):
        _fail("receipt-invalid-response")
    return values


def _validate_receipt(
    value: Any,
    *,
    expected_kind: str,
    transaction_id: str,
    plan_hash: str,
    operation_key: str,
    request_hash: str | None = None,
    service_ids: tuple[str, ...] | None = None,
) -> ReceiptResult:
    if not isinstance(value, dict) or set(value) != _RECEIPT_RESPONSE_KEYS:
        _fail("receipt-invalid-response")
    if value["schema"] != RECEIPT_SCHEMA or value["kind"] != expected_kind:
        _fail("receipt-invalid-response")
    if (
        value["transactionId"] != transaction_id
        or value["planHash"] != plan_hash
        or value["operationKey"] != operation_key
    ):
        _fail("receipt-binding-mismatch")

    actual_request_hash = _response_hash(value["requestHash"])
    actual_service_ids = _response_service_ids(value["serviceIds"])
    event_hash = _response_hash(value["eventHash"])
    if request_hash is not None and actual_request_hash != request_hash:
        _fail("receipt-binding-mismatch")
    if service_ids is not None and actual_service_ids != service_ids:
        _fail("receipt-binding-mismatch")

    outcome = value["outcome"]
    evidence_hash = value["evidenceHash"]
    started_event_hash = value["startedEventHash"]
    if expected_kind == "started":
        if outcome is not None or evidence_hash is not None or started_event_hash is not None:
            _fail("receipt-invalid-response")
    elif expected_kind == "terminal":
        if not isinstance(outcome, str) or outcome not in {"completed", "failed"}:
            _fail("receipt-invalid-response")
        evidence_hash = _response_hash(evidence_hash)
        started_event_hash = _response_hash(started_event_hash)
    else:
        _fail("receipt-invalid-response")

    return ReceiptResult(
        schema=RECEIPT_SCHEMA,
        kind=expected_kind,
        transaction_id=transaction_id,
        plan_hash=plan_hash,
        operation_key=operation_key,
        request_hash=actual_request_hash,
        service_ids=actual_service_ids,
        event_hash=event_hash,
        outcome=outcome,
        evidence_hash=evidence_hash,
        started_event_hash=started_event_hash,
    )


def _error_code(error: AgentHTTPError) -> str | None:
    try:
        value = json.loads(error.detail)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"code"}:
        return None
    code = value["code"]
    return code if isinstance(code, str) else None


def _translate_transport_error(error: AgentClientError, *, operation: str) -> None:
    mutation = operation in {"begin", "finish"}
    if isinstance(error, AgentHTTPError):
        code = _error_code(error)
        if error.status_code in {401, 403}:
            _fail("receipt-host-auth")
        if error.status_code == 404:
            _fail("receipt-boundary-disabled")
        if error.status_code == 409:
            if code in _INTEGRITY_CODES:
                _fail("receipt-integrity")
            _fail("receipt-conflict")
        if error.status_code in {400, 413, 422}:
            _fail("receipt-invalid-request")
        if error.status_code >= 500:
            if mutation:
                _fail("receipt-operation-ambiguous", ambiguous=True)
            _fail("receipt-unavailable", retryable=True)
        _fail("receipt-host-rejected")
    if isinstance(error, AgentProtocolError):
        if mutation:
            _fail("receipt-operation-ambiguous", ambiguous=True)
        _fail("receipt-invalid-response")
    if isinstance(error, (AgentTimeout, AgentUnavailable)):
        if mutation:
            _fail("receipt-operation-ambiguous", ambiguous=True)
        _fail("receipt-unavailable", retryable=True)
    if mutation:
        _fail("receipt-operation-ambiguous", ambiguous=True)
    _fail("receipt-unavailable", retryable=True)


def _payload_size(payload: dict[str, Any]) -> None:
    try:
        size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        _fail("receipt-invalid-request")
    if size > MAX_RESPONSE_BYTES:
        _fail("receipt-invalid-request")


class ExtensionLifecycleReceiptClient:
    """Call fixed receipt routes without activating extension execution."""

    def __init__(
        self,
        requester: Callable[..., dict[str, Any]] = request_bounded_strict_json_200,
    ) -> None:
        self._request = requester

    def _request_host(
        self, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        _payload_size(payload)
        try:
            return self._request(
                "POST",
                _ROUTES[operation],
                payload=payload,
                timeout=_TIMEOUTS[operation],
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except AgentClientError as error:
            _translate_transport_error(error, operation=operation)

    def begin(
        self,
        transaction_id: str,
        plan_hash: str,
        operation_key: str,
        request_hash: str,
        service_ids: list[str] | tuple[str, ...],
    ) -> ReceiptResult:
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_hash(plan_hash)
        request_hash = _validate_hash(request_hash)
        service_ids = _validate_service_ids(service_ids)
        operation_key = _validate_operation_key(operation_key, service_ids)
        response = self._request_host(
            "begin",
            {
                "schema": RECEIPT_SCHEMA,
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "operationKey": operation_key,
                "requestHash": request_hash,
                "serviceIds": list(service_ids),
            },
        )
        if not isinstance(response, dict):
            _fail("receipt-invalid-response")
        kind = response.get("kind")
        if not isinstance(kind, str) or kind not in {"started", "terminal"}:
            _fail("receipt-invalid-response")
        return _validate_receipt(
            response,
            expected_kind=kind,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            operation_key=operation_key,
            request_hash=request_hash,
            service_ids=service_ids,
        )

    def finish(
        self,
        transaction_id: str,
        plan_hash: str,
        operation_key: str,
        request_hash: str,
        service_ids: list[str] | tuple[str, ...],
        outcome: str,
        evidence_hash: str,
    ) -> ReceiptResult:
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_hash(plan_hash)
        request_hash = _validate_hash(request_hash)
        service_ids = _validate_service_ids(service_ids)
        operation_key = _validate_operation_key(operation_key, service_ids)
        outcome = _validate_outcome(outcome)
        evidence_hash = _validate_hash(evidence_hash)
        response = self._request_host(
            "finish",
            {
                "schema": RECEIPT_SCHEMA,
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "operationKey": operation_key,
                "requestHash": request_hash,
                "serviceIds": list(service_ids),
                "outcome": outcome,
                "evidenceHash": evidence_hash,
            },
        )
        result = _validate_receipt(
            response,
            expected_kind="terminal",
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            operation_key=operation_key,
            request_hash=request_hash,
            service_ids=service_ids,
        )
        if result.outcome != outcome or result.evidence_hash != evidence_hash:
            _fail("receipt-binding-mismatch")
        return result

    def snapshot(
        self, transaction_id: str, plan_hash: str, operation_key: str
    ) -> SnapshotResult:
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_hash(plan_hash)
        operation_key = _validate_operation_key(operation_key, None)
        response = self._request_host(
            "snapshot",
            {
                "schema": RECEIPT_SCHEMA,
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "operationKey": operation_key,
            },
        )
        if not isinstance(response, dict) or set(response) != _SNAPSHOT_RESPONSE_KEYS:
            _fail("receipt-invalid-response")
        if response["schema"] != RECEIPT_SCHEMA:
            _fail("receipt-invalid-response")
        if (
            response["transactionId"] != transaction_id
            or response["planHash"] != plan_hash
            or response["operationKey"] != operation_key
        ):
            _fail("receipt-binding-mismatch")
        state = response["state"]
        if not isinstance(state, str) or state not in {
            "absent",
            "started",
            "completed",
            "failed",
        }:
            _fail("receipt-invalid-response")

        started_value = response["startedReceipt"]
        terminal_value = response["terminalReceipt"]
        if state == "absent":
            if started_value is not None or terminal_value is not None:
                _fail("receipt-invalid-response")
            started = terminal = None
        else:
            started = _validate_receipt(
                started_value,
                expected_kind="started",
                transaction_id=transaction_id,
                plan_hash=plan_hash,
                operation_key=operation_key,
            )
            if state == "started":
                if terminal_value is not None:
                    _fail("receipt-invalid-response")
                terminal = None
            else:
                terminal = _validate_receipt(
                    terminal_value,
                    expected_kind="terminal",
                    transaction_id=transaction_id,
                    plan_hash=plan_hash,
                    operation_key=operation_key,
                )
                if (
                    terminal.request_hash != started.request_hash
                    or terminal.service_ids != started.service_ids
                    or terminal.started_event_hash != started.event_hash
                    or terminal.outcome != state
                ):
                    _fail("receipt-binding-mismatch")
        return SnapshotResult(
            schema=RECEIPT_SCHEMA,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            operation_key=operation_key,
            state=state,
            started_receipt=started,
            terminal_receipt=terminal,
        )


__all__ = [
    "ExtensionLifecycleReceiptClient",
    "LifecycleReceiptClientError",
    "ReceiptResult",
    "SnapshotResult",
]
