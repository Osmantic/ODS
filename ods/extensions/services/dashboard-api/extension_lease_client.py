"""Value-safe client for host-owned extension-operation leases.

The client is intentionally not wired into transaction execution yet. It keeps
lease credentials in memory, validates every host echo, and classifies failures
without performing retries or logging submitted values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from extension_transaction_executor import ExecutionBinding
from host_agent_client import (
    AgentClientError,
    AgentHTTPError,
    AgentProtocolError,
    AgentTimeout,
    AgentUnavailable,
    request_bounded_json,
)

LEASE_SCHEMA = "ods.extension-operation-lease.v1"
DEFAULT_TTL_SECONDS = 600
MAX_TTL_SECONDS = 3600
MAX_LEASE_SERVICES = 128
MAX_SERVICE_ID_LENGTH = 128
MAX_RESPONSE_BYTES = 32 * 1024

_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TRANSACTION_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_PLAN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_LEASE_ID_RE = re.compile(r"^lease-[0-9a-f]{32}$")
_TOKEN_MIN_LENGTH = 32
_TOKEN_MAX_LENGTH = 256

_ACQUIRE_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "leaseId",
        "leaseToken",
        "transactionId",
        "planHash",
        "serviceIds",
        "ttlSeconds",
    }
)
_RENEW_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "leaseId",
        "transactionId",
        "planHash",
        "serviceIds",
        "active",
        "ttlSeconds",
    }
)
_STATUS_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "leaseId",
        "transactionId",
        "planHash",
        "serviceIds",
        "active",
    }
)
_RELEASE_RESPONSE_KEYS = frozenset(
    {"schema", "leaseId", "transactionId", "planHash", "released"}
)
_RETRYABLE_CONFLICT_CODES = frozenset(
    {"lease-capacity-exhausted", "lease-mutation-active", "service-lock-busy"}
)
_AUTHORIZATION_CODES = frozenset(
    {
        "invalid-lease-id",
        "invalid-lease-token",
        "lease-binding-mismatch",
        "lease-token-mismatch",
    }
)
_VALIDATION_CODES = frozenset(
    {
        "duplicate-lease-token",
        "incomplete-lease-request",
        "invalid-lease-request",
        "invalid-lease-request-framing",
        "invalid-lease-ttl",
        "invalid-or-duplicate-lease-id",
        "invalid-plan-hash",
        "invalid-service-id",
        "invalid-service-ids",
        "invalid-transaction-id",
        "lease-request-size",
        "too-many-service-ids",
    }
)


class ExtensionLeaseError(RuntimeError):
    """Stable failure with explicit retry and ambiguous-outcome classification."""

    def __init__(
        self, code: str, *, retryable: bool = False, ambiguous: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous

    def __repr__(self) -> str:
        return (
            f"ExtensionLeaseError({self.code!r}, retryable={self.retryable!r}, "
            f"ambiguous={self.ambiguous!r})"
        )


class _LeaseToken:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "<extension lease token: redacted>"

    __str__ = __repr__


@dataclass(frozen=True)
class LeaseGrant:
    """In-memory lease custody; its credential is excluded from representations."""

    binding: ExecutionBinding
    lease_id: str
    service_ids: tuple[str, ...]
    ttl_seconds: int
    _token: _LeaseToken = field(repr=False)

    def __repr__(self) -> str:
        return (
            "LeaseGrant("
            f"binding={self.binding!r}, lease_id={self.lease_id!r}, "
            f"service_ids={self.service_ids!r}, ttl_seconds={self.ttl_seconds!r}, "
            "lease_token=<redacted>)"
        )


@dataclass(frozen=True)
class LeaseStatus:
    binding: ExecutionBinding
    lease_id: str
    service_ids: tuple[str, ...]
    active: bool


@dataclass(frozen=True)
class LeaseRelease:
    binding: ExecutionBinding
    lease_id: str
    released: bool


def _fail(code: str, *, retryable: bool = False, ambiguous: bool = False) -> None:
    raise ExtensionLeaseError(code, retryable=retryable, ambiguous=ambiguous) from None


def _validate_binding(binding: ExecutionBinding) -> None:
    if not isinstance(binding, ExecutionBinding):
        _fail("lease-invalid-binding")
    if _TRANSACTION_ID_RE.fullmatch(binding.transaction_id) is None:
        _fail("lease-invalid-binding")
    if _PLAN_HASH_RE.fullmatch(binding.plan_hash) is None:
        _fail("lease-invalid-binding")


def _validate_ttl(ttl_seconds: int) -> int:
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= MAX_TTL_SECONDS
    ):
        _fail("lease-invalid-ttl")
    return ttl_seconds


def _validate_response_ttl(ttl_seconds: Any) -> int:
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= MAX_TTL_SECONDS
    ):
        _fail("lease-invalid-response")
    return ttl_seconds


def _canonical_service_ids(service_ids: Iterable[str]) -> tuple[str, ...]:
    if isinstance(service_ids, (str, bytes, dict)):
        _fail("lease-invalid-service-ids")
    try:
        values = tuple(service_ids)
    except TypeError:
        _fail("lease-invalid-service-ids")
    if not values or len(values) > MAX_LEASE_SERVICES:
        _fail("lease-invalid-service-ids")
    if any(
        not isinstance(service_id, str)
        or len(service_id) > MAX_SERVICE_ID_LENGTH
        or _SERVICE_ID_RE.fullmatch(service_id) is None
        for service_id in values
    ):
        _fail("lease-invalid-service-ids")
    return tuple(sorted(set(values)))


def _validate_lease_id(lease_id: Any) -> str:
    if not isinstance(lease_id, str) or _LEASE_ID_RE.fullmatch(lease_id) is None:
        _fail("lease-invalid-response")
    return lease_id


def _validate_token(token: Any) -> str:
    if (
        not isinstance(token, str)
        or not _TOKEN_MIN_LENGTH <= len(token) <= _TOKEN_MAX_LENGTH
    ):
        _fail("lease-invalid-response")
    return token


def _validate_response_binding(
    response: dict[str, Any], binding: ExecutionBinding
) -> None:
    if response["schema"] != LEASE_SCHEMA:
        _fail("lease-invalid-response")
    if (
        response["transactionId"] != binding.transaction_id
        or response["planHash"] != binding.plan_hash
    ):
        _fail("lease-binding-mismatch")


def _validate_grant(
    grant: LeaseGrant,
) -> tuple[ExecutionBinding, str, str, tuple[str, ...]]:
    if not isinstance(grant, LeaseGrant) or not isinstance(grant._token, _LeaseToken):
        _fail("lease-invalid-grant")
    _validate_binding(grant.binding)
    lease_id = _validate_lease_id(grant.lease_id)
    service_ids = _canonical_service_ids(grant.service_ids)
    if service_ids != grant.service_ids:
        _fail("lease-invalid-grant")
    _validate_ttl(grant.ttl_seconds)
    token = _validate_token(grant._token.reveal())
    return grant.binding, lease_id, token, service_ids


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
    mutation = operation != "status"
    if isinstance(error, AgentHTTPError):
        code = _error_code(error)
        if error.status_code in {401, 403}:
            _fail(code if code in _AUTHORIZATION_CODES else "lease-host-auth")
        if error.status_code == 404:
            _fail("lease-boundary-disabled")
        if error.status_code == 409:
            _fail(
                code if code in _RETRYABLE_CONFLICT_CODES else "lease-conflict",
                retryable=True,
            )
        if error.status_code == 410:
            _fail("lease-not-active")
        if error.status_code == 422:
            _fail(code if code in _VALIDATION_CODES else "lease-invalid-request")
        if error.status_code == 503 and code == "extension-lease-manager-unavailable":
            _fail("lease-unavailable", retryable=True)
        if error.status_code >= 500:
            if mutation:
                _fail("lease-operation-ambiguous", ambiguous=True)
            _fail("lease-unavailable", retryable=True)
        _fail("lease-host-rejected")

    if isinstance(error, AgentProtocolError):
        if mutation:
            _fail("lease-operation-ambiguous", ambiguous=True)
        _fail("lease-invalid-response")
    if isinstance(error, (AgentTimeout, AgentUnavailable)):
        if mutation:
            _fail("lease-operation-ambiguous", ambiguous=True)
        _fail("lease-unavailable", retryable=True)
    if mutation:
        _fail("lease-operation-ambiguous", ambiguous=True)
    _fail("lease-unavailable", retryable=True)


def _payload_size(payload: dict[str, Any]) -> None:
    try:
        size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        _fail("lease-invalid-request")
    if size > MAX_RESPONSE_BYTES:
        _fail("lease-invalid-request")


class ExtensionLeaseClient:
    """Call fixed host lease routes without persisting or logging credentials."""

    def __init__(
        self, requester: Callable[..., dict[str, Any]] = request_bounded_json
    ) -> None:
        self._request = requester

    def _request_host(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        _payload_size(payload)
        try:
            return self._request(
                "POST",
                f"/v1/extension/lease/{operation}",
                payload=payload,
                timeout=timeout,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except AgentClientError as error:
            _translate_transport_error(error, operation=operation)

    def acquire(
        self,
        binding: ExecutionBinding,
        service_ids: Iterable[str],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> LeaseGrant:
        _validate_binding(binding)
        canonical_ids = _canonical_service_ids(service_ids)
        ttl = _validate_ttl(ttl_seconds)
        response = self._request_host(
            "acquire",
            {
                "schema": LEASE_SCHEMA,
                "transactionId": binding.transaction_id,
                "planHash": binding.plan_hash,
                "serviceIds": list(canonical_ids),
                "ttlSeconds": ttl,
            },
            timeout=10.0,
        )
        if not isinstance(response, dict) or set(response) != _ACQUIRE_RESPONSE_KEYS:
            _fail("lease-invalid-response")
        _validate_response_binding(response, binding)
        if response["serviceIds"] != list(canonical_ids):
            _fail("lease-binding-mismatch")
        if _validate_response_ttl(response["ttlSeconds"]) != ttl:
            _fail("lease-binding-mismatch")
        return LeaseGrant(
            binding=binding,
            lease_id=_validate_lease_id(response["leaseId"]),
            service_ids=canonical_ids,
            ttl_seconds=ttl,
            _token=_LeaseToken(_validate_token(response["leaseToken"])),
        )

    def renew(
        self, grant: LeaseGrant, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> LeaseGrant:
        binding, lease_id, token, service_ids = _validate_grant(grant)
        ttl = _validate_ttl(ttl_seconds)
        response = self._request_host(
            "renew",
            {
                "schema": LEASE_SCHEMA,
                "leaseId": lease_id,
                "leaseToken": token,
                "transactionId": binding.transaction_id,
                "planHash": binding.plan_hash,
                "ttlSeconds": ttl,
            },
            timeout=5.0,
        )
        if not isinstance(response, dict) or set(response) != _RENEW_RESPONSE_KEYS:
            _fail("lease-invalid-response")
        _validate_response_binding(response, binding)
        if _validate_lease_id(response["leaseId"]) != lease_id:
            _fail("lease-binding-mismatch")
        if response["serviceIds"] != list(service_ids):
            _fail("lease-binding-mismatch")
        if type(response["active"]) is not bool:
            _fail("lease-invalid-response")
        if _validate_response_ttl(response["ttlSeconds"]) != ttl:
            _fail("lease-binding-mismatch")
        return LeaseGrant(
            binding=binding,
            lease_id=lease_id,
            service_ids=service_ids,
            ttl_seconds=ttl,
            _token=grant._token,
        )

    def status(self, grant: LeaseGrant) -> LeaseStatus:
        binding, lease_id, token, service_ids = _validate_grant(grant)
        response = self._request_host(
            "status",
            {
                "schema": LEASE_SCHEMA,
                "leaseId": lease_id,
                "leaseToken": token,
                "transactionId": binding.transaction_id,
                "planHash": binding.plan_hash,
            },
            timeout=5.0,
        )
        if not isinstance(response, dict) or set(response) != _STATUS_RESPONSE_KEYS:
            _fail("lease-invalid-response")
        _validate_response_binding(response, binding)
        if _validate_lease_id(response["leaseId"]) != lease_id:
            _fail("lease-binding-mismatch")
        if response["serviceIds"] != list(service_ids):
            _fail("lease-binding-mismatch")
        if type(response["active"]) is not bool:
            _fail("lease-invalid-response")
        return LeaseStatus(
            binding=binding,
            lease_id=lease_id,
            service_ids=service_ids,
            active=response["active"],
        )

    def release(self, grant: LeaseGrant) -> LeaseRelease:
        binding, lease_id, token, _service_ids = _validate_grant(grant)
        response = self._request_host(
            "release",
            {
                "schema": LEASE_SCHEMA,
                "leaseId": lease_id,
                "leaseToken": token,
                "transactionId": binding.transaction_id,
                "planHash": binding.plan_hash,
            },
            timeout=5.0,
        )
        if not isinstance(response, dict) or set(response) != _RELEASE_RESPONSE_KEYS:
            _fail("lease-invalid-response")
        _validate_response_binding(response, binding)
        if _validate_lease_id(response["leaseId"]) != lease_id:
            _fail("lease-binding-mismatch")
        if response["released"] is not True:
            _fail("lease-invalid-response")
        return LeaseRelease(binding=binding, lease_id=lease_id, released=True)


__all__ = [
    "ExtensionLeaseClient",
    "ExtensionLeaseError",
    "LeaseGrant",
    "LeaseRelease",
    "LeaseStatus",
]
