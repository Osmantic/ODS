"""Dormant transaction-wide host lease custody for extension execution.

The factory implements the transaction executor's ``ServiceLockFactory``
shape without wiring an executor.  One context owns one complete canonical
service set, supervises renewal in memory, and makes the latest redacted grant
available only to its owner thread and exact execution binding.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Callable, Iterable
from types import TracebackType

from extension_lease_client import (
    DEFAULT_TTL_SECONDS,
    MAX_LEASE_SERVICES,
    MAX_SERVICE_ID_LENGTH,
    MAX_TTL_SECONDS,
    ExtensionLeaseClient,
    ExtensionLeaseError,
    LeaseGrant,
)
from extension_lease_renewer import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    LeaseRenewer,
)
from extension_transaction_executor import ExecutionBinding

_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_RenewerFactory = Callable[..., LeaseRenewer]


def _fail(
    code: str, *, retryable: bool = False, ambiguous: bool = False
) -> None:
    raise ExtensionLeaseError(
        code, retryable=retryable, ambiguous=ambiguous
    ) from None


def _canonical_service_ids(service_ids: Iterable[str]) -> tuple[str, ...]:
    if isinstance(service_ids, (str, bytes, dict)):
        _fail("lease-lock-invalid-service-ids")
    try:
        values = tuple(service_ids)
    except TypeError:
        _fail("lease-lock-invalid-service-ids")
    if not values or len(values) > MAX_LEASE_SERVICES:
        _fail("lease-lock-invalid-service-ids")
    if any(
        not isinstance(service_id, str)
        or len(service_id) > MAX_SERVICE_ID_LENGTH
        or _SERVICE_ID_RE.fullmatch(service_id) is None
        for service_id in values
    ):
        _fail("lease-lock-invalid-service-ids")
    return tuple(sorted(set(values)))


def _validate_seconds(
    value: object, *, code: str, maximum: float | None = None
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or (maximum is not None and value >= maximum)
    ):
        _fail(code)
    return float(value)


class ExtensionLeaseLockFactory:
    """Create one exact-binding, transaction-wide host lease context.

    The factory is deliberately single-scope.  A caller must leave the active
    context before reusing it, and an ambiguous shutdown poisons the instance
    so a possibly live renewal worker can never overlap a later client call.
    """

    def __init__(
        self,
        client: ExtensionLeaseClient,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        renew_interval_seconds: float | None = None,
        shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        _renewer_factory: _RenewerFactory = LeaseRenewer,
    ) -> None:
        if not isinstance(client, ExtensionLeaseClient):
            _fail("lease-lock-invalid-client")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= MAX_TTL_SECONDS
        ):
            _fail("lease-lock-invalid-ttl")
        if renew_interval_seconds is not None:
            _validate_seconds(
                renew_interval_seconds,
                code="lease-lock-invalid-renew-interval",
                maximum=float(ttl_seconds),
            )
        _validate_seconds(
            shutdown_timeout_seconds,
            code="lease-lock-invalid-shutdown-timeout",
        )
        if not callable(_renewer_factory):
            _fail("lease-lock-invalid-renewer-factory")

        self._client = client
        self._ttl_seconds = ttl_seconds
        self._renew_interval_seconds = renew_interval_seconds
        self._shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._renewer_factory = _renewer_factory
        self._guard = threading.Lock()
        self._scope: _LeaseScope | None = None
        self._poisoned = False

    def __repr__(self) -> str:
        with self._guard:
            active = self._scope is not None
            poisoned = self._poisoned
        return (
            "ExtensionLeaseLockFactory("
            f"active={active!r}, poisoned={poisoned!r})"
        )

    def lock_services(
        self, binding: ExecutionBinding, service_ids: list[str]
    ) -> _LeaseScope:
        """Return an inert context; host acquisition occurs only on enter."""

        if not isinstance(binding, ExecutionBinding):
            _fail("lease-lock-invalid-binding")
        canonical_ids = _canonical_service_ids(service_ids)
        return _LeaseScope(self, binding, canonical_ids)

    def current_grant(
        self,
        binding: ExecutionBinding,
        required_service_ids: Iterable[str],
    ) -> LeaseGrant:
        """Return custody when the exact scope covers the requested subset."""

        if not isinstance(binding, ExecutionBinding):
            _fail("lease-lock-invalid-binding")
        required_ids = _canonical_service_ids(required_service_ids)
        with self._guard:
            scope = self._scope
            if self._poisoned:
                _fail("lease-lock-poisoned", ambiguous=True)
            if scope is None or scope._state != "active":
                _fail("lease-lock-not-active")
            if scope._owner_thread != threading.get_ident():
                _fail("lease-lock-wrong-thread")
            if scope._binding != binding:
                _fail("lease-lock-binding-mismatch")
            if not set(required_ids).issubset(scope._service_ids):
                _fail("lease-lock-service-mismatch")
            renewer = scope._renewer
        if renewer is None:
            _fail("lease-lock-not-active")
        try:
            return renewer.check()
        except ExtensionLeaseError:
            raise
        except Exception:
            _fail("lease-lock-renewer-internal", ambiguous=True)

    def _reserve(self, scope: _LeaseScope) -> None:
        with self._guard:
            if self._poisoned:
                _fail("lease-lock-poisoned", ambiguous=True)
            if self._scope is not None:
                _fail("lease-lock-already-active")
            self._scope = scope
            scope._state = "entering"
            scope._owner_thread = threading.get_ident()

    def _activate(self, scope: _LeaseScope, renewer: LeaseRenewer) -> None:
        with self._guard:
            if self._scope is not scope or scope._state != "entering":
                _fail("lease-lock-internal", ambiguous=True)
            scope._renewer = renewer
            scope._state = "active"

    def _begin_close(self, scope: _LeaseScope) -> LeaseRenewer:
        with self._guard:
            if self._scope is not scope or scope._state != "active":
                _fail("lease-lock-not-active")
            if scope._owner_thread != threading.get_ident():
                _fail("lease-lock-wrong-thread")
            renewer = scope._renewer
            if renewer is None:
                _fail("lease-lock-internal", ambiguous=True)
            scope._state = "closing"
            return renewer

    def _clear(self, scope: _LeaseScope, *, poison: bool = False) -> None:
        with self._guard:
            if self._scope is scope:
                if poison:
                    self._poisoned = True
                    scope._state = "poisoned"
                else:
                    self._scope = None
                    scope._state = "closed"


class _LeaseScope:
    """Single-use context returned by :class:`ExtensionLeaseLockFactory`."""

    def __init__(
        self,
        factory: ExtensionLeaseLockFactory,
        binding: ExecutionBinding,
        service_ids: tuple[str, ...],
    ) -> None:
        self._factory = factory
        self._binding = binding
        self._service_ids = service_ids
        self._owner_thread: int | None = None
        self._renewer: LeaseRenewer | None = None
        self._state = "new"

    def __repr__(self) -> str:
        return (
            "_LeaseScope("
            f"binding={self._binding!r}, service_ids={self._service_ids!r}, "
            f"state={self._state!r})"
        )

    def __enter__(self) -> _LeaseScope:
        if self._state != "new":
            _fail("lease-lock-reentry")
        factory = self._factory
        factory._reserve(self)

        grant: LeaseGrant | None = None
        try:
            grant = factory._client.acquire(
                self._binding,
                self._service_ids,
                ttl_seconds=factory._ttl_seconds,
            )
        except ExtensionLeaseError:
            factory._clear(self)
            raise
        except Exception:
            factory._clear(self, poison=True)
            _fail("lease-lock-acquire-internal", ambiguous=True)

        try:
            renewer = factory._renewer_factory(
                factory._client,
                grant,
                interval_seconds=factory._renew_interval_seconds,
                shutdown_timeout_seconds=factory._shutdown_timeout_seconds,
            )
            self._renewer = renewer
        except ExtensionLeaseError as start_error:
            self._cleanup_unstarted(grant, start_error)
        except Exception:
            self._cleanup_unstarted(
                grant,
                ExtensionLeaseError("lease-lock-renewer-internal"),
            )

        try:
            renewer.start()
            factory._activate(self, renewer)
            return self
        except Exception as start_error:
            if not isinstance(start_error, ExtensionLeaseError):
                factory._clear(self, poison=True)
                _fail("lease-lock-start-ambiguous", ambiguous=True)
            self._cleanup_unstarted(grant, start_error)

    def _cleanup_unstarted(
        self,
        grant: LeaseGrant,
        start_error: ExtensionLeaseError,
    ) -> None:
        """Release a grant when renewal is proven not to have started."""

        factory = self._factory
        try:
            factory._client.release(grant)
        except ExtensionLeaseError as cleanup_error:
            factory._clear(self, poison=cleanup_error.ambiguous)
            if cleanup_error.ambiguous:
                raise ExtensionLeaseError(
                    "lease-lock-start-cleanup-ambiguous", ambiguous=True
                ) from start_error
            raise start_error from cleanup_error
        except Exception:
            factory._clear(self, poison=True)
            raise ExtensionLeaseError(
                "lease-lock-start-cleanup-ambiguous", ambiguous=True
            ) from start_error
        factory._clear(self)
        raise start_error from None

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        factory = self._factory
        try:
            renewer = factory._begin_close(self)
        except ExtensionLeaseError as close_error:
            if exc_value is not None:
                raise exc_value.with_traceback(traceback) from close_error
            raise

        close_error: ExtensionLeaseError | None = None
        latest: LeaseGrant | None = None
        try:
            latest = renewer.stop()
        except ExtensionLeaseError as error:
            close_error = error
        except Exception:
            close_error = ExtensionLeaseError(
                "lease-lock-stop-internal", ambiguous=True
            )

        if close_error is None and latest is not None:
            try:
                factory._client.release(latest)
            except ExtensionLeaseError as error:
                close_error = error
            except Exception:
                close_error = ExtensionLeaseError(
                    "lease-lock-release-internal", ambiguous=True
                )

        if close_error is None:
            factory._clear(self)
        else:
            factory._clear(self, poison=close_error.ambiguous)
            if exc_value is not None:
                raise exc_value.with_traceback(traceback) from close_error
            raise close_error from None
        return False


__all__ = ["ExtensionLeaseLockFactory"]
