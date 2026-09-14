from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

import pytest

from extension_lease_client import (
    ExtensionLeaseClient,
    ExtensionLeaseError,
    LeaseGrant,
)
from extension_lease_lock_factory import ExtensionLeaseLockFactory
from extension_transaction_executor import ExecutionBinding

TRANSACTION_ID = "txn-" + "1" * 24
OTHER_TRANSACTION_ID = "txn-" + "9" * 24
PLAN_HASH = "2" * 64
LEASE_ID = "lease-" + "3" * 32
LEASE_TOKEN = "private-lease-token-" + "4" * 32
BINDING = ExecutionBinding(TRANSACTION_ID, PLAN_HASH)
SERVICE_IDS = ("aider", "ollama")


def _grant(
    binding: ExecutionBinding,
    service_ids: tuple[str, ...],
    ttl_seconds: int,
) -> LeaseGrant:
    def response(_method: str, _path: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "schema": "ods.extension-operation-lease.v1",
            "leaseId": LEASE_ID,
            "leaseToken": LEASE_TOKEN,
            "transactionId": binding.transaction_id,
            "planHash": binding.plan_hash,
            "serviceIds": list(service_ids),
            "ttlSeconds": ttl_seconds,
        }

    return ExtensionLeaseClient(response).acquire(
        binding, service_ids, ttl_seconds=ttl_seconds
    )


class StubLeaseClient(ExtensionLeaseClient):
    def __init__(self, events: list[str]) -> None:
        super().__init__(lambda *_args, **_kwargs: {})
        self.events = events
        self.acquire_error: Exception | None = None
        self.release_error: Exception | None = None
        self.last_ids: tuple[str, ...] | None = None

    def acquire(
        self,
        binding: ExecutionBinding,
        service_ids: Any,
        *,
        ttl_seconds: int = 600,
    ) -> LeaseGrant:
        values = tuple(service_ids)
        self.events.append("acquire")
        self.last_ids = values
        if self.acquire_error is not None:
            raise self.acquire_error
        return _grant(binding, values, ttl_seconds)

    def release(self, grant: LeaseGrant) -> Any:
        self.events.append("release")
        if self.release_error is not None:
            raise self.release_error
        return grant


class StubRenewer:
    def __init__(
        self,
        _client: ExtensionLeaseClient,
        grant: LeaseGrant,
        *,
        events: list[str],
        start_error: Exception | None = None,
        check_error: Exception | None = None,
        stop_error: Exception | None = None,
        **kwargs: Any,
    ) -> None:
        events.append("renewer:init")
        self.events = events
        self.grant = grant
        self.start_error = start_error
        self.check_error = check_error
        self.stop_error = stop_error
        self.kwargs = kwargs

    def start(self) -> None:
        self.events.append("renewer:start")
        if self.start_error is not None:
            raise self.start_error

    def check(self) -> LeaseGrant:
        self.events.append("renewer:check")
        if self.check_error is not None:
            raise self.check_error
        return self.grant

    def stop(self) -> LeaseGrant:
        self.events.append("renewer:stop")
        if self.stop_error is not None:
            raise self.stop_error
        return self.grant


def factory(
    events: list[str],
    *,
    client: StubLeaseClient | None = None,
    start_error: Exception | None = None,
    check_error: Exception | None = None,
    stop_error: Exception | None = None,
    **kwargs: Any,
) -> tuple[ExtensionLeaseLockFactory, StubLeaseClient, list[StubRenewer]]:
    lease_client = client or StubLeaseClient(events)
    renewers: list[StubRenewer] = []

    def make_renewer(
        supplied_client: ExtensionLeaseClient,
        grant: LeaseGrant,
        **renewer_kwargs: Any,
    ) -> Any:
        renewer = StubRenewer(
            supplied_client,
            grant,
            events=events,
            start_error=start_error,
            check_error=check_error,
            stop_error=stop_error,
            **renewer_kwargs,
        )
        renewers.append(renewer)
        return renewer

    return (
        ExtensionLeaseLockFactory(
            lease_client,
            _renewer_factory=make_renewer,
            **kwargs,
        ),
        lease_client,
        renewers,
    )


def test_lock_services_is_inert_until_enter_and_custodies_full_canonical_set() -> None:
    events: list[str] = []
    lock_factory, client, renewers = factory(
        events,
        ttl_seconds=90,
        renew_interval_seconds=20,
        shutdown_timeout_seconds=3,
    )

    scope = lock_factory.lock_services(BINDING, ["ollama", "aider", "ollama"])
    assert events == []

    with scope as entered:
        assert entered is scope
        grant = lock_factory.current_grant(BINDING, ["aider", "ollama"])
        assert grant.binding == BINDING
        assert grant.service_ids == SERVICE_IDS
        assert client.last_ids == SERVICE_IDS
        assert renewers[0].kwargs == {
            "interval_seconds": 20,
            "shutdown_timeout_seconds": 3.0,
        }
        assert events == [
            "acquire",
            "renewer:init",
            "renewer:start",
            "renewer:check",
        ]

    assert events[-2:] == ["renewer:stop", "release"]


def test_default_renewer_stops_before_release_without_a_background_renewal() -> None:
    calls: list[str] = []

    def request(_method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(path)
        payload = kwargs["payload"]
        if path.endswith("/acquire"):
            return {
                "schema": "ods.extension-operation-lease.v1",
                "leaseId": LEASE_ID,
                "leaseToken": LEASE_TOKEN,
                "transactionId": TRANSACTION_ID,
                "planHash": PLAN_HASH,
                "serviceIds": list(SERVICE_IDS),
                "ttlSeconds": payload["ttlSeconds"],
            }
        if path.endswith("/release"):
            return {
                "schema": "ods.extension-operation-lease.v1",
                "leaseId": LEASE_ID,
                "transactionId": TRANSACTION_ID,
                "planHash": PLAN_HASH,
                "released": True,
            }
        raise AssertionError(f"unexpected request: {path}")

    lock_factory = ExtensionLeaseLockFactory(ExtensionLeaseClient(request))
    with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
        assert lock_factory.current_grant(BINDING, SERVICE_IDS).lease_id == LEASE_ID

    assert calls == [
        "/v1/extension/lease/acquire",
        "/v1/extension/lease/release",
    ]


def test_scope_is_single_use_and_factory_rejects_multiple_active_scopes() -> None:
    events: list[str] = []
    lock_factory, _client, _renewers = factory(events)
    first = lock_factory.lock_services(BINDING, list(SERVICE_IDS))
    second = lock_factory.lock_services(BINDING, list(SERVICE_IDS))
    first.__enter__()
    try:
        with pytest.raises(ExtensionLeaseError, match="lease-lock-already-active"):
            second.__enter__()
    finally:
        first.__exit__(None, None, None)

    with pytest.raises(ExtensionLeaseError, match="lease-lock-reentry"):
        first.__enter__()


def test_current_grant_requires_exact_binding_covered_services_and_owner_thread() -> None:
    events: list[str] = []
    lock_factory, _client, _renewers = factory(events)
    other = ExecutionBinding(OTHER_TRANSACTION_ID, PLAN_HASH)

    with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
        with pytest.raises(ExtensionLeaseError, match="lease-lock-binding-mismatch"):
            lock_factory.current_grant(other, SERVICE_IDS)
        assert lock_factory.current_grant(BINDING, ["aider"]).lease_id == LEASE_ID
        with pytest.raises(ExtensionLeaseError, match="lease-lock-service-mismatch"):
            lock_factory.current_grant(BINDING, ["aider", "qdrant"])

        observed: list[ExtensionLeaseError] = []

        def cross_thread() -> None:
            try:
                lock_factory.current_grant(BINDING, SERVICE_IDS)
            except ExtensionLeaseError as error:
                observed.append(error)

        thread = threading.Thread(target=cross_thread)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        assert [error.code for error in observed] == ["lease-lock-wrong-thread"]


def test_acquire_failure_has_no_renewer_or_release() -> None:
    events: list[str] = []
    client = StubLeaseClient(events)
    expected = ExtensionLeaseError("lease-capacity-exhausted", retryable=True)
    client.acquire_error = expected
    lock_factory, _client, _renewers = factory(events, client=client)

    with pytest.raises(ExtensionLeaseError) as caught:
        with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
            raise AssertionError("unreachable")
    assert caught.value is expected
    assert events == ["acquire"]


def test_unexpected_acquire_failure_is_redacted_ambiguous_and_poisoning() -> None:
    events: list[str] = []
    client = StubLeaseClient(events)
    client.acquire_error = RuntimeError(LEASE_TOKEN)
    lock_factory, _client, _renewers = factory(events, client=client)

    with pytest.raises(ExtensionLeaseError) as caught:
        lock_factory.lock_services(BINDING, list(SERVICE_IDS)).__enter__()
    assert caught.value.code == "lease-lock-acquire-internal"
    assert caught.value.ambiguous is True
    assert LEASE_TOKEN not in "".join(traceback.format_exception(caught.value))
    with pytest.raises(ExtensionLeaseError, match="lease-lock-poisoned"):
        lock_factory.lock_services(BINDING, list(SERVICE_IDS)).__enter__()


def test_renewer_start_failure_releases_before_returning_exact_error() -> None:
    events: list[str] = []
    expected = ExtensionLeaseError("lease-renewer-start-failed")
    lock_factory, _client, _renewers = factory(events, start_error=expected)

    with pytest.raises(ExtensionLeaseError) as caught:
        lock_factory.lock_services(BINDING, list(SERVICE_IDS)).__enter__()
    assert caught.value is expected
    assert events == ["acquire", "renewer:init", "renewer:start", "release"]
    assert "poisoned=False" in repr(lock_factory)


def test_ambiguous_start_cleanup_failure_poisoned_without_value_leak() -> None:
    events: list[str] = []
    client = StubLeaseClient(events)
    client.release_error = ExtensionLeaseError(
        "lease-operation-ambiguous", ambiguous=True
    )
    lock_factory, _client, _renewers = factory(
        events,
        client=client,
        start_error=ExtensionLeaseError("lease-renewer-start-failed"),
    )

    with pytest.raises(ExtensionLeaseError) as caught:
        lock_factory.lock_services(BINDING, list(SERVICE_IDS)).__enter__()
    assert caught.value.code == "lease-lock-start-cleanup-ambiguous"
    assert caught.value.ambiguous is True
    assert "poisoned=True" in repr(lock_factory)
    assert LEASE_TOKEN not in "".join(traceback.format_exception(caught.value))


def test_stop_failure_never_calls_release_and_ambiguous_stop_poisoned() -> None:
    events: list[str] = []
    stop_error = ExtensionLeaseError(
        "lease-renewer-shutdown-timeout", ambiguous=True
    )
    lock_factory, _client, _renewers = factory(events, stop_error=stop_error)

    with pytest.raises(ExtensionLeaseError) as caught:
        with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
            pass
    assert caught.value is stop_error
    assert events[-1] == "renewer:stop"
    assert "release" not in events
    assert "poisoned=True" in repr(lock_factory)


def test_release_failure_happens_only_after_clean_stop() -> None:
    events: list[str] = []
    client = StubLeaseClient(events)
    expected = ExtensionLeaseError("lease-operation-ambiguous", ambiguous=True)
    client.release_error = expected
    lock_factory, _client, _renewers = factory(events, client=client)

    with pytest.raises(ExtensionLeaseError) as caught:
        with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
            pass
    assert caught.value is expected
    assert events[-2:] == ["renewer:stop", "release"]
    assert "poisoned=True" in repr(lock_factory)


def test_body_exception_wins_and_cleanup_failure_is_chained() -> None:
    events: list[str] = []
    stop_error = ExtensionLeaseError("lease-not-active")
    lock_factory, _client, _renewers = factory(events, stop_error=stop_error)

    with pytest.raises(ValueError, match="operation failed") as caught:
        with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
            raise ValueError("operation failed")
    assert caught.value.__cause__ is stop_error
    assert "release" not in events


def test_unexpected_check_and_stop_errors_are_code_only() -> None:
    for phase in ("check", "stop"):
        events: list[str] = []
        kwargs = {f"{phase}_error": RuntimeError(LEASE_TOKEN)}
        lock_factory, _client, _renewers = factory(events, **kwargs)
        with pytest.raises(ExtensionLeaseError) as caught:
            with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
                if phase == "check":
                    lock_factory.current_grant(BINDING, SERVICE_IDS)
        assert caught.value.ambiguous is True
        assert LEASE_TOKEN not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"client": object()}, "lease-lock-invalid-client"),
        ({"ttl_seconds": True}, "lease-lock-invalid-ttl"),
        ({"ttl_seconds": 0}, "lease-lock-invalid-ttl"),
        ({"renew_interval_seconds": 600}, "lease-lock-invalid-renew-interval"),
        ({"renew_interval_seconds": float("nan")}, "lease-lock-invalid-renew-interval"),
        ({"shutdown_timeout_seconds": 0}, "lease-lock-invalid-shutdown-timeout"),
        ({"_renewer_factory": None}, "lease-lock-invalid-renewer-factory"),
    ],
)
def test_constructor_rejects_invalid_inputs(kwargs: dict[str, Any], code: str) -> None:
    client = kwargs.pop("client", StubLeaseClient([]))
    with pytest.raises(ExtensionLeaseError, match=code):
        ExtensionLeaseLockFactory(client, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "service_ids",
    [[], ["Bad"], ["a.b"], [""], "aider", None],
)
def test_lock_services_rejects_invalid_service_sets_without_network(
    service_ids: Any,
) -> None:
    events: list[str] = []
    lock_factory, _client, _renewers = factory(events)
    with pytest.raises(ExtensionLeaseError, match="lease-lock-invalid-service-ids"):
        lock_factory.lock_services(BINDING, service_ids)
    assert events == []


def test_factory_is_dormant_and_production_remains_disabled() -> None:
    source_root = Path(__file__).resolve().parents[1]
    module = source_root / "extension_lease_lock_factory.py"
    source = module.read_text(encoding="utf-8")
    production = (source_root / "extension_transaction_production.py").read_text(
        encoding="utf-8"
    )
    importers = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts
        and path != module
        and "extension_lease_lock_factory" in path.read_text(encoding="utf-8")
    }

    assert importers == {"extension_receipted_lifecycle_adapter.py"}
    assert "extension_lease_lock_factory" not in production
    assert "executor=None" in production
    for forbidden in (
        "import logging",
        "logging.",
        "logger =",
        "print(",
        "open(",
        "pathlib",
        "subprocess",
        "AGENT_URL",
        "ODS_AGENT_KEY",
    ):
        assert forbidden not in source


def test_factory_and_scope_representations_never_expose_lease_token() -> None:
    events: list[str] = []
    lock_factory, _client, _renewers = factory(events)
    scope = lock_factory.lock_services(BINDING, list(SERVICE_IDS))
    with scope:
        rendered = repr(lock_factory) + repr(scope)
        rendered += repr(lock_factory.current_grant(BINDING, SERVICE_IDS))
    assert LEASE_TOKEN not in rendered
