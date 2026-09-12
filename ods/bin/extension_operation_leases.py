"""Host-owned, transaction-bound leases for extension lifecycle mutations.

This module is deliberately transport-neutral and has no import-time effects.
The host agent can inject its existing per-service ``threading.Lock`` objects;
the lease manager then owns those locks until release or expiry. Lease-bearing
mutation calls use :meth:`ExtensionLeaseManager.use` instead of attempting to
re-acquire the same locks, preventing caller/callee self-deadlock.

The manager is process-local by design. If the host agent exits, its leases and
its injected locks disappear together. Production transaction execution stays
disabled until the HTTP boundary, client renewer, recovery, and real-host
qualification are implemented and reviewed.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

LEASE_SCHEMA = "ods.extension-operation-lease.v1"
DEFAULT_TTL_SECONDS = 600
MAX_TTL_SECONDS = 3600
MAX_LEASE_SERVICES = 128
MAX_SERVICE_ID_LENGTH = 128
MAX_ACTIVE_LEASES = 1024

_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TRANSACTION_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_PLAN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_LEASE_ID_RE = re.compile(r"^lease-[0-9a-f]{32}$")


class LeaseError(RuntimeError):
    """Base error carrying a stable, non-secret protocol code."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}:{detail}")


class LeaseConflict(LeaseError):
    """At least one requested service is already locked."""


class LeaseAuthorizationError(LeaseError):
    """The token or immutable transaction binding did not match."""


class LeaseExpired(LeaseError):
    """The lease is unknown, released, or expired and cannot be revived."""


class LeaseBusy(LeaseError):
    """A mutation is already active under the lease."""


@dataclass
class _Lease:
    lease_id: str
    token_digest: str
    transaction_id: str
    plan_hash: str
    service_ids: tuple[str, ...]
    locks: tuple[Any, ...]
    issued_at: float
    deadline: float
    active: bool = False
    expired: bool = False


def _canonical_service_ids(service_ids: Iterable[str]) -> tuple[str, ...]:
    if isinstance(service_ids, (str, bytes, dict)):
        raise LeaseError("invalid-service-ids")
    try:
        values = tuple(service_ids)
    except TypeError as exc:
        raise LeaseError("invalid-service-ids") from exc
    if not values:
        raise LeaseError("invalid-service-ids", "empty")
    if len(values) > MAX_LEASE_SERVICES:
        raise LeaseError("too-many-service-ids")
    for service_id in values:
        if (
            not isinstance(service_id, str)
            or len(service_id) > MAX_SERVICE_ID_LENGTH
            or _SERVICE_ID_RE.fullmatch(service_id) is None
        ):
            raise LeaseError("invalid-service-id")
    return tuple(sorted(set(values)))


def _validate_binding(transaction_id: str, plan_hash: str) -> None:
    if (
        not isinstance(transaction_id, str)
        or _TRANSACTION_ID_RE.fullmatch(transaction_id) is None
    ):
        raise LeaseError("invalid-transaction-id")
    if not isinstance(plan_hash, str) or _PLAN_HASH_RE.fullmatch(plan_hash) is None:
        raise LeaseError("invalid-plan-hash")


def _validate_ttl(ttl_seconds: int) -> int:
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds < 1
        or ttl_seconds > MAX_TTL_SECONDS
    ):
        raise LeaseError("invalid-lease-ttl")
    return ttl_seconds


def _token_digest(token: str) -> str:
    if not isinstance(token, str) or not 32 <= len(token) <= 256:
        raise LeaseAuthorizationError("invalid-lease-token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ExtensionLeaseManager:
    """Own exact service locks on behalf of one approved transaction."""

    def __init__(
        self,
        lock_provider: Callable[[str], Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        lease_id_factory: Callable[[], str] | None = None,
        token_factory: Callable[[], str] | None = None,
        max_active_leases: int = MAX_ACTIVE_LEASES,
    ) -> None:
        if (
            isinstance(max_active_leases, bool)
            or not isinstance(max_active_leases, int)
            or max_active_leases < 1
            or max_active_leases > MAX_ACTIVE_LEASES
        ):
            raise LeaseError("invalid-lease-capacity")
        self._lock_provider = lock_provider
        self._clock = clock
        self._lease_id_factory = lease_id_factory or (
            lambda: "lease-" + uuid.uuid4().hex
        )
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._max_active_leases = max_active_leases
        self._guard = threading.Lock()
        self._leases: dict[str, _Lease] = {}

    def acquire(
        self,
        transaction_id: str,
        plan_hash: str,
        service_ids: Iterable[str],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Acquire a complete canonical lock set or fail without a partial grant."""
        _validate_binding(transaction_id, plan_hash)
        canonical_ids = _canonical_service_ids(service_ids)
        ttl = _validate_ttl(ttl_seconds)
        now = self._clock()
        held: list[Any] = []

        with self._guard:
            self._expire_locked(now)
            if len(self._leases) >= self._max_active_leases:
                raise LeaseConflict("lease-capacity-exhausted")
            try:
                for service_id in canonical_ids:
                    lock = self._lock_provider(service_id)
                    if lock.acquire(blocking=False) is not True:
                        raise LeaseConflict("service-lock-busy", service_id)
                    held.append(lock)
            except Exception:
                for lock in reversed(held):
                    lock.release()
                raise

            try:
                lease_id = self._lease_id_factory()
                token = self._token_factory()
                if (
                    not isinstance(lease_id, str)
                    or _LEASE_ID_RE.fullmatch(lease_id) is None
                    or lease_id in self._leases
                ):
                    raise LeaseError("invalid-or-duplicate-lease-id")
                digest = _token_digest(token)
                if any(
                    hmac.compare_digest(item.token_digest, digest)
                    for item in self._leases.values()
                ):
                    raise LeaseError("duplicate-lease-token")
            except Exception:
                for lock in reversed(held):
                    lock.release()
                raise
            lease = _Lease(
                lease_id=lease_id,
                token_digest=digest,
                transaction_id=transaction_id,
                plan_hash=plan_hash,
                service_ids=canonical_ids,
                locks=tuple(held),
                issued_at=now,
                deadline=now + ttl,
            )
            self._leases[lease_id] = lease
            return {
                "schema": LEASE_SCHEMA,
                "leaseId": lease_id,
                "leaseToken": token,
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "serviceIds": list(canonical_ids),
                "ttlSeconds": ttl,
            }

    def renew(
        self,
        lease_id: str,
        token: str,
        transaction_id: str,
        plan_hash: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Extend an active lease without changing its binding or service set."""
        _validate_binding(transaction_id, plan_hash)
        ttl = _validate_ttl(ttl_seconds)
        now = self._clock()
        with self._guard:
            lease = self._authorize_locked(
                lease_id, token, transaction_id, plan_hash, now
            )
            lease.deadline = now + ttl
            return self._public_record(lease, ttl_seconds=ttl)

    def release(
        self,
        lease_id: str,
        token: str,
        transaction_id: str,
        plan_hash: str,
    ) -> dict[str, Any]:
        """Release an inactive exact lease; released IDs cannot be revived."""
        _validate_binding(transaction_id, plan_hash)
        now = self._clock()
        with self._guard:
            lease = self._authorize_locked(
                lease_id, token, transaction_id, plan_hash, now
            )
            if lease.active:
                raise LeaseBusy("lease-mutation-active")
            self._release_locked(lease)
            return {
                "schema": LEASE_SCHEMA,
                "leaseId": lease_id,
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "released": True,
            }

    @contextlib.contextmanager
    def use(
        self,
        lease_id: str,
        token: str,
        transaction_id: str,
        plan_hash: str,
        service_ids: Iterable[str],
    ):
        """Pin locks around one lease-authorized mutation without re-acquiring."""
        _validate_binding(transaction_id, plan_hash)
        requested_ids = _canonical_service_ids(service_ids)
        with self._guard:
            lease = self._authorize_locked(
                lease_id, token, transaction_id, plan_hash, self._clock()
            )
            if lease.active:
                raise LeaseBusy("lease-mutation-active")
            if not set(requested_ids).issubset(lease.service_ids):
                raise LeaseAuthorizationError("lease-service-not-covered")
            lease.active = True
        try:
            yield {
                "schema": LEASE_SCHEMA,
                "leaseId": lease.lease_id,
                "transactionId": lease.transaction_id,
                "planHash": lease.plan_hash,
                "serviceIds": list(requested_ids),
            }
        finally:
            with self._guard:
                current = self._leases.get(lease.lease_id)
                if current is lease:
                    lease.active = False
                    self._expire_locked(self._clock())

    def sweep(self) -> list[str]:
        """Release expired, inactive leases and return their public IDs."""
        with self._guard:
            return self._expire_locked(self._clock())

    def describe(self, lease_id: str) -> dict[str, Any]:
        """Return non-secret status for diagnostics and tests."""
        with self._guard:
            self._expire_locked(self._clock())
            lease = self._leases.get(lease_id)
            if lease is None or lease.expired:
                raise LeaseExpired("lease-not-active")
            return self._public_record(lease)

    def _authorize_locked(
        self,
        lease_id: str,
        token: str,
        transaction_id: str,
        plan_hash: str,
        now: float,
    ) -> _Lease:
        if not isinstance(lease_id, str) or _LEASE_ID_RE.fullmatch(lease_id) is None:
            raise LeaseAuthorizationError("invalid-lease-id")
        digest = _token_digest(token)
        self._expire_locked(now)
        lease = self._leases.get(lease_id)
        if lease is None or lease.expired:
            raise LeaseExpired("lease-not-active")
        if not hmac.compare_digest(lease.token_digest, digest):
            raise LeaseAuthorizationError("lease-token-mismatch")
        if lease.transaction_id != transaction_id or lease.plan_hash != plan_hash:
            raise LeaseAuthorizationError("lease-binding-mismatch")
        return lease

    def _expire_locked(self, now: float) -> list[str]:
        released = []
        for lease in list(self._leases.values()):
            if now < lease.deadline:
                continue
            lease.expired = True
            if lease.active:
                continue
            released.append(lease.lease_id)
            self._release_locked(lease)
        return released

    def _release_locked(self, lease: _Lease) -> None:
        self._leases.pop(lease.lease_id, None)
        for lock in reversed(lease.locks):
            lock.release()

    @staticmethod
    def _public_record(lease: _Lease, *, ttl_seconds: int | None = None) -> dict[str, Any]:
        record = {
            "schema": LEASE_SCHEMA,
            "leaseId": lease.lease_id,
            "transactionId": lease.transaction_id,
            "planHash": lease.plan_hash,
            "serviceIds": list(lease.service_ids),
            "active": lease.active,
        }
        if ttl_seconds is not None:
            record["ttlSeconds"] = ttl_seconds
        return record
