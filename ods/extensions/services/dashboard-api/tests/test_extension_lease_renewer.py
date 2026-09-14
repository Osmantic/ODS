from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from extension_lease_client import (
    ExtensionLeaseClient,
    ExtensionLeaseError,
    LeaseGrant,
)
from extension_lease_renewer import LeaseRenewer
from extension_transaction_executor import ExecutionBinding

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
LEASE_ID = "lease-" + "3" * 32
LEASE_TOKEN = "private-lease-token-" + "4" * 32
BINDING = ExecutionBinding(TRANSACTION_ID, PLAN_HASH)
SERVICE_IDS = ("aider", "ollama")


def acquire_response(ttl_seconds: int = 600) -> dict:
    return {
        "schema": "ods.extension-operation-lease.v1",
        "leaseId": LEASE_ID,
        "leaseToken": LEASE_TOKEN,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": list(SERVICE_IDS),
        "ttlSeconds": ttl_seconds,
    }


def renew_response(ttl_seconds: int = 600) -> dict:
    return {
        "schema": "ods.extension-operation-lease.v1",
        "leaseId": LEASE_ID,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": list(SERVICE_IDS),
        "active": False,
        "ttlSeconds": ttl_seconds,
    }


def grant(*, ttl_seconds: int = 600) -> LeaseGrant:
    return ExtensionLeaseClient(
        lambda *_args, **_kwargs: acquire_response(ttl_seconds)
    ).acquire(BINDING, SERVICE_IDS, ttl_seconds=ttl_seconds)


class GateWaiter:
    def __init__(self, renewals: int) -> None:
        self._remaining = renewals
        self.timeouts: list[float] = []

    def __call__(self, stop_event: threading.Event, timeout: float) -> bool:
        self.timeouts.append(timeout)
        if stop_event.is_set():
            return True
        if self._remaining:
            self._remaining -= 1
            return False
        return stop_event.wait()


class FailingClient(ExtensionLeaseClient):
    def __init__(self, error: Exception, attempted: threading.Event) -> None:
        super().__init__(lambda *_args, **_kwargs: renew_response())
        self.error = error
        self.attempted = attempted
        self.calls = 0

    def renew(self, current: LeaseGrant, *, ttl_seconds: int = 600) -> LeaseGrant:
        del current, ttl_seconds
        self.calls += 1
        self.attempted.set()
        raise self.error


def test_renews_on_one_third_ttl_and_calls_only_renew() -> None:
    current = grant(ttl_seconds=90)
    waiter = GateWaiter(1)
    renewed = threading.Event()
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        renewed.set()
        return renew_response(kwargs["payload"]["ttlSeconds"])

    renewer = LeaseRenewer(
        ExtensionLeaseClient(request), current, _waiter=waiter
    )
    renewer.start()
    assert renewed.wait(5)
    latest = renewer.stop()

    assert latest is not current
    assert waiter.timeouts == [30.0, 30.0]
    assert [(method, path) for method, path, _kwargs in calls] == [
        ("POST", "/v1/extension/lease/renew")
    ]
    assert calls[0][2]["payload"]["ttlSeconds"] == 90


def test_multiple_successes_preserve_latest_validated_grant() -> None:
    waiter = GateWaiter(3)
    complete = threading.Event()
    calls = 0

    def request(_method, _path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            complete.set()
        return renew_response(kwargs["payload"]["ttlSeconds"])

    renewer = LeaseRenewer(
        ExtensionLeaseClient(request), grant(), _waiter=waiter
    )
    renewer.start()
    assert complete.wait(5)

    assert renewer.stop().lease_id == LEASE_ID
    assert calls == 3


def test_stop_wakes_default_wait_without_renewing() -> None:
    calls = 0

    def request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return renew_response()

    renewer = LeaseRenewer(ExtensionLeaseClient(request), grant())
    renewer.start()

    assert renewer.stop().lease_id == LEASE_ID
    assert calls == 0


@pytest.mark.parametrize(
    "error",
    [
        ExtensionLeaseError("lease-unavailable", retryable=True),
        ExtensionLeaseError("lease-operation-ambiguous", ambiguous=True),
        ExtensionLeaseError("lease-not-active"),
    ],
)
def test_first_lease_failure_is_exact_and_never_retried(
    error: ExtensionLeaseError,
) -> None:
    attempted = threading.Event()
    client = FailingClient(error, attempted)
    renewer = LeaseRenewer(client, grant(), _waiter=GateWaiter(5))
    renewer.start()
    assert attempted.wait(5)

    with pytest.raises(ExtensionLeaseError) as caught:
        renewer.stop()
    assert caught.value is error
    assert client.calls == 1
    with pytest.raises(ExtensionLeaseError) as repeated:
        renewer.check()
    assert repeated.value is error


def test_unexpected_worker_failure_is_redacted_and_ambiguous() -> None:
    attempted = threading.Event()
    client = FailingClient(RuntimeError(LEASE_TOKEN), attempted)
    renewer = LeaseRenewer(client, grant(), _waiter=GateWaiter(1))
    renewer.start()
    assert attempted.wait(5)

    with pytest.raises(ExtensionLeaseError) as caught:
        renewer.stop()
    assert caught.value.code == "lease-renewer-internal"
    assert caught.value.ambiguous is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert LEASE_TOKEN not in repr(caught.value)
    assert LEASE_TOKEN not in repr(renewer)


def test_context_manager_surfaces_exact_background_failure() -> None:
    error = ExtensionLeaseError("lease-not-active")
    attempted = threading.Event()
    renewer = LeaseRenewer(
        FailingClient(error, attempted), grant(), _waiter=GateWaiter(1)
    )

    with pytest.raises(ExtensionLeaseError) as caught:
        with renewer:
            assert attempted.wait(5)
    assert caught.value is error


def test_body_error_wins_and_background_failure_is_chained() -> None:
    renewal_error = ExtensionLeaseError("lease-not-active")
    attempted = threading.Event()
    renewer = LeaseRenewer(
        FailingClient(renewal_error, attempted), grant(), _waiter=GateWaiter(1)
    )

    with pytest.raises(ValueError) as caught:
        with renewer:
            assert attempted.wait(5)
            raise ValueError("operation failed")
    assert caught.value.__cause__ is renewal_error


def test_body_error_is_preserved_when_shutdown_is_clean() -> None:
    renewer = LeaseRenewer(
        ExtensionLeaseClient(lambda *_args, **_kwargs: renew_response()), grant()
    )

    with pytest.raises(ValueError, match="operation failed"):
        with renewer:
            raise ValueError("operation failed")


def test_start_is_single_use_and_unstarted_operations_fail() -> None:
    renewer = LeaseRenewer(
        ExtensionLeaseClient(lambda *_args, **_kwargs: renew_response()), grant()
    )
    with pytest.raises(ExtensionLeaseError, match="lease-renewer-not-started"):
        renewer.check()
    with pytest.raises(ExtensionLeaseError, match="lease-renewer-not-started"):
        renewer.stop()

    renewer.start()
    with pytest.raises(ExtensionLeaseError, match="lease-renewer-already-started"):
        renewer.start()
    renewer.stop()
    with pytest.raises(ExtensionLeaseError, match="lease-renewer-already-started"):
        renewer.start()


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"interval_seconds": 0}, "lease-renewer-invalid-interval"),
        ({"interval_seconds": True}, "lease-renewer-invalid-interval"),
        ({"interval_seconds": 600}, "lease-renewer-invalid-interval"),
        ({"interval_seconds": float("inf")}, "lease-renewer-invalid-interval"),
        ({"interval_seconds": 10**1000}, "lease-renewer-invalid-interval"),
        (
            {"shutdown_timeout_seconds": 0},
            "lease-renewer-invalid-shutdown-timeout",
        ),
        (
            {"shutdown_timeout_seconds": float("nan")},
            "lease-renewer-invalid-shutdown-timeout",
        ),
        (
            {"shutdown_timeout_seconds": 10**1000},
            "lease-renewer-invalid-shutdown-timeout",
        ),
        ({"_waiter": None}, "lease-renewer-invalid-waiter"),
    ],
)
def test_constructor_rejects_invalid_timing(kwargs, code: str) -> None:
    with pytest.raises(ExtensionLeaseError, match=code):
        LeaseRenewer(
            ExtensionLeaseClient(lambda *_args, **_kwargs: renew_response()),
            grant(),
            **kwargs,
        )


def test_constructor_rejects_invalid_client_and_grant() -> None:
    with pytest.raises(
        ExtensionLeaseError, match="lease-renewer-invalid-client"
    ):
        LeaseRenewer(object(), grant())  # type: ignore[arg-type]
    with pytest.raises(
        ExtensionLeaseError, match="lease-renewer-invalid-grant"
    ):
        LeaseRenewer(
            ExtensionLeaseClient(lambda *_args, **_kwargs: renew_response()),
            object(),  # type: ignore[arg-type]
        )


def test_shutdown_is_bounded_and_worker_is_daemon() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked_waiter(_stop_event: threading.Event, _timeout: float) -> bool:
        entered.set()
        return release.wait()

    renewer = LeaseRenewer(
        ExtensionLeaseClient(lambda *_args, **_kwargs: renew_response()),
        grant(),
        shutdown_timeout_seconds=0.01,
        _waiter=blocked_waiter,
    )
    renewer.start()
    assert entered.wait(5)
    assert renewer._thread is not None
    assert renewer._thread.daemon is True

    with pytest.raises(ExtensionLeaseError) as caught:
        renewer.stop()
    assert caught.value.code == "lease-renewer-shutdown-timeout"
    assert caught.value.ambiguous is True

    release.set()
    assert renewer.stop().lease_id == LEASE_ID


def test_worker_cannot_reentrantly_stop_itself() -> None:
    attempted = threading.Event()
    holder: dict[str, LeaseRenewer] = {}

    class ReentrantClient(ExtensionLeaseClient):
        def renew(
            self, current: LeaseGrant, *, ttl_seconds: int = 600
        ) -> LeaseGrant:
            del current, ttl_seconds
            attempted.set()
            holder["renewer"].stop()
            raise AssertionError("unreachable")

    renewer = LeaseRenewer(
        ReentrantClient(lambda *_args, **_kwargs: renew_response()),
        grant(),
        _waiter=GateWaiter(1),
    )
    holder["renewer"] = renewer
    renewer.start()
    assert attempted.wait(5)

    with pytest.raises(ExtensionLeaseError) as caught:
        renewer.stop()
    assert caught.value.code == "lease-renewer-reentrant-stop"
    assert caught.value.ambiguous is True


def test_renewer_has_no_runtime_wiring_or_other_lease_operations() -> None:
    source_root = Path(__file__).resolve().parents[1]
    module = source_root / "extension_lease_renewer.py"
    source = module.read_text(encoding="utf-8")
    importers = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts
        and path != module
        and "extension_lease_renewer" in path.read_text(encoding="utf-8")
    }
    method_calls = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert importers == set()
    assert "renew" in method_calls
    assert method_calls.isdisjoint({"acquire", "release", "status"})
    for forbidden in ("import logging", "logging.", "logger =", "print(", "open("):
        assert forbidden not in source
