"""In-memory renewal custody for a future synchronous extension operation.

This module is deliberately inert: it has no call sites and never acquires,
releases, persists, inspects, or logs a lease.  A caller that starts a renewer
must stop it (or leave its context) so any background failure is surfaced.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from types import TracebackType

from extension_lease_client import (
    ExtensionLeaseClient,
    ExtensionLeaseError,
    LeaseGrant,
)

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 6.0
_RENEWAL_FRACTION = 1 / 3

_Waiter = Callable[[threading.Event, float], bool]


def _wait_for_stop(stop_event: threading.Event, timeout: float) -> bool:
    return stop_event.wait(timeout)


def _positive_seconds(value: object, *, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExtensionLeaseError(code) from None
    try:
        seconds = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ExtensionLeaseError(code) from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ExtensionLeaseError(code) from None
    return seconds


class LeaseRenewer:
    """Renew one in-memory grant until a synchronous owner stops the loop.

    Only the worker thread may call methods on ``client`` while the renewer is
    running.  ``stop`` is bounded and either returns the latest validated grant
    or raises a renewal/shutdown error; it never reports clean completion while
    the worker is still alive.
    """

    def __init__(
        self,
        client: ExtensionLeaseClient,
        grant: LeaseGrant,
        *,
        interval_seconds: float | None = None,
        shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        _waiter: _Waiter = _wait_for_stop,
    ) -> None:
        if not isinstance(client, ExtensionLeaseClient):
            raise ExtensionLeaseError("lease-renewer-invalid-client") from None
        if not isinstance(grant, LeaseGrant):
            raise ExtensionLeaseError("lease-renewer-invalid-grant") from None
        if not callable(_waiter):
            raise ExtensionLeaseError("lease-renewer-invalid-waiter") from None

        grant_ttl = _positive_seconds(
            grant.ttl_seconds, code="lease-renewer-invalid-grant"
        )
        interval = (
            grant_ttl * _RENEWAL_FRACTION
            if interval_seconds is None
            else _positive_seconds(
                interval_seconds, code="lease-renewer-invalid-interval"
            )
        )
        if interval >= grant_ttl:
            raise ExtensionLeaseError("lease-renewer-invalid-interval") from None

        self._client = client
        self._grant = grant
        self._interval_seconds = interval
        self._shutdown_timeout_seconds = _positive_seconds(
            shutdown_timeout_seconds,
            code="lease-renewer-invalid-shutdown-timeout",
        )
        self._waiter = _waiter
        self._stop_event = threading.Event()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._failure: ExtensionLeaseError | None = None

    def start(self) -> None:
        """Start exactly once; reentrant or repeated starts are rejected."""
        with self._guard:
            if self._thread is not None:
                raise ExtensionLeaseError("lease-renewer-already-started") from None
            thread = threading.Thread(
                target=self._run,
                name="ods-extension-lease-renewer",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                raise ExtensionLeaseError("lease-renewer-start-failed") from None

    def check(self) -> LeaseGrant:
        """Return the latest grant or raise the exact first worker failure."""
        with self._guard:
            if self._thread is None:
                raise ExtensionLeaseError("lease-renewer-not-started") from None
            failure = self._failure
            grant = self._grant
        if failure is not None:
            raise failure from None
        return grant

    def stop(self) -> LeaseGrant:
        """Signal shutdown, join for a fixed bound, and surface every failure."""
        with self._guard:
            thread = self._thread
        if thread is None:
            raise ExtensionLeaseError("lease-renewer-not-started") from None

        self._stop_event.set()
        if threading.current_thread() is thread:
            raise ExtensionLeaseError(
                "lease-renewer-reentrant-stop", ambiguous=True
            ) from None
        thread.join(self._shutdown_timeout_seconds)
        if thread.is_alive():
            raise ExtensionLeaseError(
                "lease-renewer-shutdown-timeout", ambiguous=True
            ) from None
        return self.check()

    def __enter__(self) -> LeaseRenewer:
        self.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            self.stop()
        except ExtensionLeaseError as renewal_error:
            if exc_value is None:
                raise
            raise exc_value.with_traceback(traceback) from renewal_error
        return False

    def _record_failure(self, failure: ExtensionLeaseError) -> None:
        with self._guard:
            if self._failure is None:
                self._failure = failure
        self._stop_event.set()

    def _run(self) -> None:
        try:
            while not self._waiter(self._stop_event, self._interval_seconds):
                with self._guard:
                    current = self._grant
                try:
                    renewed = self._client.renew(
                        current, ttl_seconds=current.ttl_seconds
                    )
                except ExtensionLeaseError as error:
                    self._record_failure(error)
                    return
                except Exception:
                    self._record_failure(
                        ExtensionLeaseError(
                            "lease-renewer-internal", ambiguous=True
                        )
                    )
                    return
                with self._guard:
                    self._grant = renewed
        except Exception:
            self._record_failure(
                ExtensionLeaseError("lease-renewer-internal", ambiguous=True)
            )


__all__ = [
    "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS",
    "LeaseRenewer",
]
