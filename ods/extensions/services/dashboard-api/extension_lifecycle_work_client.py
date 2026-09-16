"""Strict dormant client for synchronous lease-authorized host lifecycle work.

The client accepts only the typed request produced by the receipt-bound
lifecycle adapter, submits the in-memory lease credential to one fixed host
route, and returns only a terminal evidence hash.  It does not retry an
operation after request bytes may have reached the host, persist values, log
payloads, or wire itself into production execution.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from extension_lease_client import (
    ExtensionLeaseError,
    LeaseGrant,
    _lease_authorization_payload,
)
from extension_receipted_lifecycle_adapter import (
    MAX_WORK_REQUEST_BYTES,
    REQUEST_SCHEMA,
    LifecycleWorkRequest,
    LifecycleWorkResult,
)
from extension_transaction_executor import ExecutionBinding
from host_agent_client import (
    AgentClientError,
    AgentHTTPError,
    AgentProtocolError,
    AgentTimeout,
    AgentUnavailable,
    request_bounded_strict_json_200,
)

RESULT_SCHEMA = "ods.extension-lifecycle-work-result.v1"
HOST_WORK_PATH = "/v1/extension/lifecycle-work"
HOST_OBSERVATION_PATH = "/v1/extension/application-observation"
OBSERVATION_SCHEMA = "ods.extension-application-observation-result.v1"
MAX_REQUEST_BYTES = 40 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
MAX_SERVICE_IDS = 64

_TRANSACTION_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_BATCH_OPERATION_TIMEOUTS = {
    "download-and-verify": 1800.0,
    "stage": 600.0,
    "backup": 600.0,
    "configure": 600.0,
    "verify": 600.0,
    "restore": 600.0,
    "release": 30.0,
}
_PER_SERVICE_OPERATION_TIMEOUTS = {
    "reserve": 30.0,
    "apply": 900.0,
    "compensate": 900.0,
}
_RETRYABLE_CONFLICT_CODES = frozenset(
    {"lease-mutation-active", "lifecycle-work-busy", "service-lock-busy"}
)
_LEASE_AUTHORIZATION_CODES = frozenset(
    {
        "invalid-lease-id",
        "invalid-lease-token",
        "lease-binding-mismatch",
        "lease-service-not-covered",
        "lease-token-mismatch",
    }
)
_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "transactionId",
        "planHash",
        "operationKey",
        "requestHash",
        "serviceIds",
        "completed",
        "outcome",
        "evidenceHash",
    }
)
_OBSERVATION_KEYS = frozenset({
    "schema", "transactionId", "planHash", "operationKey", "requestHash",
    "serviceId", "classification", "identityHash", "recordHash",
})


@dataclass(frozen=True)
class ApplicationObservationResult:
    service_id: str
    classification: str
    identity_hash: str
    record_hash: str | None


class LifecycleHostWorkError(RuntimeError):
    """Public-safe host-work failure with explicit ambiguity semantics."""

    def __init__(
        self, code: str, *, retryable: bool = False, ambiguous: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous

    def __repr__(self) -> str:
        return (
            f"LifecycleHostWorkError({self.code!r}, "
            f"retryable={self.retryable!r}, ambiguous={self.ambiguous!r})"
        )


def _fail(code: str, *, retryable: bool = False, ambiguous: bool = False) -> None:
    raise LifecycleHostWorkError(
        code, retryable=retryable, ambiguous=ambiguous
    ) from None


def _canonical_clone(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        _fail("host-work-invalid-request")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [_canonical_clone(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            _fail("host-work-invalid-request")
        return {
            key: _canonical_clone(item, depth=depth + 1) for key, item in value.items()
        }
    _fail("host-work-invalid-request")


def _canonical_bytes(value: dict[str, Any], *, limit: int) -> bytes:
    try:
        encoded = json.dumps(
            _canonical_clone(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("host-work-invalid-request")
    if len(encoded) > limit:
        _fail("host-work-request-size")
    return encoded


def _validate_binding(request: LifecycleWorkRequest) -> None:
    binding = request.binding
    if (
        not isinstance(binding, ExecutionBinding)
        or not isinstance(binding.transaction_id, str)
        or not isinstance(binding.plan_hash, str)
        or _TRANSACTION_ID_RE.fullmatch(binding.transaction_id) is None
        or _HASH_RE.fullmatch(binding.plan_hash) is None
    ):
        _fail("host-work-invalid-request")


def _validate_service_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not 1 <= len(value) <= MAX_SERVICE_IDS:
        _fail("host-work-invalid-request")
    if any(
        not isinstance(service_id, str)
        or len(service_id) > 128
        or _SERVICE_ID_RE.fullmatch(service_id) is None
        for service_id in value
    ):
        _fail("host-work-invalid-request")
    if len(set(value)) != len(value):
        _fail("host-work-invalid-request")
    return value


def _operation_timeout(operation_key: Any, service_ids: tuple[str, ...]) -> float:
    if not isinstance(operation_key, str):
        _fail("host-work-invalid-request")
    if operation_key in _BATCH_OPERATION_TIMEOUTS:
        return _BATCH_OPERATION_TIMEOUTS[operation_key]
    prefix, separator, suffix = operation_key.partition(":")
    if (
        separator != ":"
        or prefix not in _PER_SERVICE_OPERATION_TIMEOUTS
        or len(service_ids) != 1
        or suffix != service_ids[0]
    ):
        _fail("host-work-invalid-request")
    return _PER_SERVICE_OPERATION_TIMEOUTS[prefix]


def _validate_operation_payload(
    operation_key: str,
    service_ids: tuple[str, ...],
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _fail("host-work-invalid-request")
    cloned = _canonical_clone(payload)
    if not isinstance(cloned, dict):
        _fail("host-work-invalid-request")

    if ":" in operation_key:
        if set(cloned) != {"operation"}:
            _fail("host-work-invalid-request")
        operation = cloned["operation"]
        if (
            not isinstance(operation, dict)
            or operation.get("serviceId") != service_ids[0]
        ):
            _fail("host-work-invalid-request")
        return cloned

    if operation_key in {"download-and-verify", "stage"}:
        if set(cloned) != {"operations"}:
            _fail("host-work-invalid-request")
        operations = cloned["operations"]
        if not isinstance(operations, list) or [
            item.get("serviceId") if isinstance(item, dict) else None
            for item in operations
        ] != list(service_ids):
            _fail("host-work-invalid-request")
        return cloned

    if set(cloned) != {"serviceIds"} or cloned["serviceIds"] != list(service_ids):
        _fail("host-work-invalid-request")
    return cloned


def _validated_request(
    request: LifecycleWorkRequest,
) -> tuple[dict[str, Any], float]:
    if not isinstance(request, LifecycleWorkRequest):
        _fail("host-work-invalid-request")
    _validate_binding(request)
    service_ids = _validate_service_ids(request.service_ids)
    timeout = _operation_timeout(request.operation_key, service_ids)
    payload = _validate_operation_payload(
        request.operation_key, service_ids, request.payload
    )
    unsigned = {
        "schema": REQUEST_SCHEMA,
        "transactionId": request.binding.transaction_id,
        "planHash": request.binding.plan_hash,
        "operationKey": request.operation_key,
        "serviceIds": list(service_ids),
        "payload": payload,
    }
    encoded = _canonical_bytes(unsigned, limit=MAX_WORK_REQUEST_BYTES)
    expected_hash = hashlib.sha256(encoded).hexdigest()
    if request.request_hash != expected_hash:
        _fail("host-work-request-hash-mismatch")
    return {**unsigned, "requestHash": expected_hash}, timeout


def _host_error_code(error: AgentHTTPError) -> str | None:
    try:
        value = json.loads(error.detail)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"code"}:
        return None
    code = value["code"]
    return code if isinstance(code, str) else None


def _translate_transport_error(error: AgentClientError) -> None:
    if isinstance(error, AgentHTTPError):
        code = _host_error_code(error)
        if error.status_code in {401, 403}:
            if code in _LEASE_AUTHORIZATION_CODES:
                _fail(code)
            _fail("host-work-host-auth")
        if error.status_code == 404:
            _fail("host-work-boundary-disabled")
        if error.status_code == 409:
            if code in _RETRYABLE_CONFLICT_CODES:
                _fail(code, retryable=True)
            _fail("host-work-conflict")
        if error.status_code == 410:
            _fail("lease-not-active")
        if error.status_code in {400, 413, 415, 422}:
            _fail("host-work-invalid-request")
        if error.status_code >= 500:
            _fail("host-work-operation-ambiguous", ambiguous=True)
        _fail("host-work-host-rejected")
    if isinstance(error, (AgentProtocolError, AgentTimeout, AgentUnavailable)):
        _fail("host-work-operation-ambiguous", ambiguous=True)
    _fail("host-work-operation-ambiguous", ambiguous=True)


def _validate_response(
    response: Any, request: LifecycleWorkRequest
) -> LifecycleWorkResult:
    if not isinstance(response, dict) or set(response) != _RESPONSE_KEYS:
        _fail("host-work-invalid-response", ambiguous=True)
    if response["schema"] != RESULT_SCHEMA:
        _fail("host-work-invalid-response", ambiguous=True)
    if (
        response["transactionId"] != request.binding.transaction_id
        or response["planHash"] != request.binding.plan_hash
        or response["operationKey"] != request.operation_key
        or response["requestHash"] != request.request_hash
        or response["serviceIds"] != list(request.service_ids)
    ):
        _fail("host-work-binding-mismatch", ambiguous=True)
    if response["completed"] is not True or response["outcome"] != "completed":
        _fail("host-work-invalid-response", ambiguous=True)
    evidence_hash = response["evidenceHash"]
    if not isinstance(evidence_hash, str) or _HASH_RE.fullmatch(evidence_hash) is None:
        _fail("host-work-invalid-response", ambiguous=True)
    return LifecycleWorkResult(evidence_hash=evidence_hash)


class ExtensionLifecycleWorkClient:
    """Submit exactly one synchronous lifecycle operation to the host."""

    def __init__(
        self,
        requester: Callable[..., dict[str, Any]] = (request_bounded_strict_json_200),
    ) -> None:
        if not callable(requester):
            _fail("host-work-invalid-requester")
        self._request = requester

    def run(
        self, grant: LeaseGrant, request: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        unsigned, timeout = _validated_request(request)
        try:
            lease = _lease_authorization_payload(
                grant, request.binding, request.service_ids
            )
        except ExtensionLeaseError:
            _fail("host-work-invalid-lease")
        body = {**unsigned, "lease": lease}
        _canonical_bytes(body, limit=MAX_REQUEST_BYTES)
        try:
            response = self._request(
                "POST",
                HOST_WORK_PATH,
                payload=body,
                timeout=timeout,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except AgentClientError as error:
            _translate_transport_error(error)
        except Exception:
            _fail("host-work-operation-ambiguous", ambiguous=True)
        return _validate_response(response, request)

    def observe_application(
        self, grant: LeaseGrant, request: LifecycleWorkRequest
    ) -> ApplicationObservationResult:
        """Read current host evidence without beginning or finishing a receipt."""
        unsigned, _apply_timeout = _validated_request(request)
        if (
            not request.operation_key.startswith("apply:")
            or len(request.service_ids) != 1
            or request.operation_key != f"apply:{request.service_ids[0]}"
        ):
            _fail("host-work-invalid-request")
        try:
            lease = _lease_authorization_payload(
                grant, request.binding, request.service_ids
            )
        except ExtensionLeaseError:
            _fail("host-work-invalid-lease")
        body = {**unsigned, "lease": lease}
        _canonical_bytes(body, limit=MAX_REQUEST_BYTES)
        try:
            response = self._request(
                "POST",
                HOST_OBSERVATION_PATH,
                payload=body,
                timeout=120.0,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except AgentClientError as error:
            _translate_transport_error(error)
        except Exception:
            _fail("host-work-observation-unavailable", ambiguous=True)
        if not isinstance(response, dict) or set(response) != _OBSERVATION_KEYS:
            _fail("host-work-invalid-observation", ambiguous=True)
        if (
            response["schema"] != OBSERVATION_SCHEMA
            or response["transactionId"] != request.binding.transaction_id
            or response["planHash"] != request.binding.plan_hash
            or response["operationKey"] != request.operation_key
            or response["requestHash"] != request.request_hash
            or response["serviceId"] != request.service_ids[0]
            or type(response["classification"]) is not str
            or response["classification"] not in {"ABSENT", "APPLIED"}
            or type(response["identityHash"]) is not str
            or _HASH_RE.fullmatch(response["identityHash"]) is None
            or (
                response["recordHash"] is not None
                and (
                    type(response["recordHash"]) is not str
                    or _HASH_RE.fullmatch(response["recordHash"]) is None
                )
            )
            or (
                response["classification"] == "APPLIED"
                and response["recordHash"] is None
            )
        ):
            _fail("host-work-invalid-observation", ambiguous=True)
        return ApplicationObservationResult(
            service_id=response["serviceId"],
            classification=response["classification"],
            identity_hash=response["identityHash"],
            record_hash=response["recordHash"],
        )

    __call__ = run


__all__ = [
    "ApplicationObservationResult",
    "ExtensionLifecycleWorkClient",
    "LifecycleHostWorkError",
]
