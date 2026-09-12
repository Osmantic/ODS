"""Unit tests for the dormant host-owned extension lease core."""

from __future__ import annotations

import collections
import importlib
import sys
import threading
from pathlib import Path

import pytest


BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

leases = importlib.import_module("extension_operation_leases")


TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
OTHER_PLAN_HASH = "3" * 64
TOKEN = "lease-token-" + "x" * 32


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TrackingLock:
    def __init__(self, service_id: str, events: list) -> None:
        self.service_id = service_id
        self.events = events
        self.lock = threading.Lock()

    def acquire(self, *, blocking: bool):
        acquired = self.lock.acquire(blocking=blocking)
        self.events.append(("acquire", self.service_id, acquired))
        return acquired

    def release(self) -> None:
        self.events.append(("release", self.service_id))
        self.lock.release()

    def locked(self) -> bool:
        return self.lock.locked()


def make_manager(
    *, clock=None, token_factory=None, lease_ids=None, max_active_leases=None
):
    events = []
    lock_map = {}

    def lock_provider(service_id):
        if service_id not in lock_map:
            lock_map[service_id] = TrackingLock(service_id, events)
        return lock_map[service_id]

    if lease_ids is None:
        lease_ids = iter(["lease-" + "a" * 32, "lease-" + "b" * 32])
    manager_args = {
        "clock": clock or FakeClock(),
        "lease_id_factory": lambda: next(lease_ids),
        "token_factory": token_factory or (lambda: TOKEN),
    }
    if max_active_leases is not None:
        manager_args["max_active_leases"] = max_active_leases
    manager = leases.ExtensionLeaseManager(lock_provider, **manager_args)
    return manager, lock_map, events


def acquire(manager, service_ids=("voice", "documents"), ttl_seconds=10):
    return manager.acquire(
        TRANSACTION_ID,
        PLAN_HASH,
        service_ids,
        ttl_seconds=ttl_seconds,
    )


def test_acquire_is_canonical_deduplicated_and_returns_token_once():
    manager, _locks, events = make_manager()

    grant = acquire(manager, ("voice", "documents", "voice"))

    assert grant == {
        "schema": leases.LEASE_SCHEMA,
        "leaseId": "lease-" + "a" * 32,
        "leaseToken": TOKEN,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": ["documents", "voice"],
        "ttlSeconds": 10,
    }
    assert events == [
        ("acquire", "documents", True),
        ("acquire", "voice", True),
    ]
    status = manager.describe(grant["leaseId"])
    assert "leaseToken" not in status
    assert "token" not in str(status).lower()


def test_conflict_unwinds_the_already_acquired_prefix():
    manager, lock_map, events = make_manager()
    voice = manager._lock_provider("voice")
    assert voice.acquire(blocking=False)
    events.clear()
    try:
        with pytest.raises(leases.LeaseConflict, match="service-lock-busy:voice"):
            acquire(manager)
    finally:
        voice.release()

    assert events[:3] == [
        ("acquire", "documents", True),
        ("acquire", "voice", False),
        ("release", "documents"),
    ]
    assert lock_map["documents"].acquire(blocking=False)
    lock_map["documents"].release()


@pytest.mark.parametrize(
    ("transaction_id", "plan_hash", "service_ids", "ttl", "code"),
    [
        ("bad", PLAN_HASH, ["voice"], 10, "invalid-transaction-id"),
        (TRANSACTION_ID, "bad", ["voice"], 10, "invalid-plan-hash"),
        (TRANSACTION_ID, PLAN_HASH, [], 10, "invalid-service-ids"),
        (TRANSACTION_ID, PLAN_HASH, "voice", 10, "invalid-service-ids"),
        (TRANSACTION_ID, PLAN_HASH, {"voice": True}, 10, "invalid-service-ids"),
        (TRANSACTION_ID, PLAN_HASH, ["../voice"], 10, "invalid-service-id"),
        (
            TRANSACTION_ID,
            PLAN_HASH,
            ["v" * (leases.MAX_SERVICE_ID_LENGTH + 1)],
            10,
            "invalid-service-id",
        ),
        (
            TRANSACTION_ID,
            PLAN_HASH,
            [f"service-{index}" for index in range(leases.MAX_LEASE_SERVICES + 1)],
            10,
            "too-many-service-ids",
        ),
        (TRANSACTION_ID, PLAN_HASH, ["voice"], 0, "invalid-lease-ttl"),
        (TRANSACTION_ID, PLAN_HASH, ["voice"], True, "invalid-lease-ttl"),
        (
            TRANSACTION_ID,
            PLAN_HASH,
            ["voice"],
            leases.MAX_TTL_SECONDS + 1,
            "invalid-lease-ttl",
        ),
    ],
)
def test_invalid_grant_fails_before_requesting_a_lock(
    transaction_id, plan_hash, service_ids, ttl, code
):
    manager, lock_map, _events = make_manager()

    with pytest.raises(leases.LeaseError, match=code):
        manager.acquire(
            transaction_id,
            plan_hash,
            service_ids,
            ttl_seconds=ttl,
        )

    assert lock_map == {}


def test_invalid_token_factory_unwinds_locks():
    manager, lock_map, _events = make_manager(token_factory=lambda: "short")

    with pytest.raises(leases.LeaseAuthorizationError, match="invalid-lease-token"):
        acquire(manager, ("documents",))

    assert lock_map["documents"].acquire(blocking=False)
    lock_map["documents"].release()


@pytest.mark.parametrize("capacity", [0, True, leases.MAX_ACTIVE_LEASES + 1])
def test_invalid_capacity_is_rejected_before_any_lock_is_requested(capacity):
    requested = []

    with pytest.raises(leases.LeaseError, match="invalid-lease-capacity"):
        leases.ExtensionLeaseManager(requested.append, max_active_leases=capacity)

    assert requested == []


def test_capacity_exhaustion_fails_before_requesting_another_service_lock():
    ids = iter(["lease-" + "a" * 32, "lease-" + "b" * 32])
    tokens = iter(["first-" + "x" * 32, "second-" + "x" * 32])
    manager, lock_map, _events = make_manager(
        lease_ids=ids,
        token_factory=lambda: next(tokens),
        max_active_leases=1,
    )
    first = acquire(manager, ("documents",))

    with pytest.raises(leases.LeaseConflict, match="lease-capacity-exhausted"):
        acquire(manager, ("voice",))

    assert "voice" not in lock_map
    manager.release(first["leaseId"], "first-" + "x" * 32, TRANSACTION_ID, PLAN_HASH)


def test_token_and_binding_mismatch_cannot_use_or_release_lease():
    manager, lock_map, _events = make_manager()
    grant = acquire(manager, ("documents",))

    with pytest.raises(leases.LeaseAuthorizationError, match="lease-token-mismatch"):
        manager.release(grant["leaseId"], "wrong-" + "x" * 32, TRANSACTION_ID, PLAN_HASH)
    with pytest.raises(leases.LeaseAuthorizationError, match="lease-binding-mismatch"):
        manager.release(grant["leaseId"], TOKEN, TRANSACTION_ID, OTHER_PLAN_HASH)
    assert lock_map["documents"].locked()

    released = manager.release(grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)
    assert released["released"] is True
    assert not lock_map["documents"].locked()
    with pytest.raises(leases.LeaseExpired, match="lease-not-active"):
        manager.release(grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)


def test_renew_extends_but_never_changes_the_binding_or_services():
    clock = FakeClock()
    manager, lock_map, _events = make_manager(clock=clock)
    grant = acquire(manager, ("documents",), ttl_seconds=2)
    clock.advance(1)

    renewed = manager.renew(
        grant["leaseId"],
        TOKEN,
        TRANSACTION_ID,
        PLAN_HASH,
        ttl_seconds=5,
    )
    clock.advance(2)

    assert renewed["serviceIds"] == ["documents"]
    assert renewed["transactionId"] == TRANSACTION_ID
    assert manager.sweep() == []
    assert lock_map["documents"].locked()


def test_expiry_releases_locks_and_lease_cannot_be_revived():
    clock = FakeClock()
    manager, lock_map, _events = make_manager(clock=clock)
    grant = acquire(manager, ("documents", "voice"), ttl_seconds=1)
    clock.advance(1)

    assert manager.sweep() == [grant["leaseId"]]
    assert not lock_map["documents"].locked()
    assert not lock_map["voice"].locked()
    with pytest.raises(leases.LeaseExpired, match="lease-not-active"):
        manager.renew(grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)


def test_release_after_natural_expiry_fails_closed_and_releases_locks():
    clock = FakeClock()
    manager, lock_map, _events = make_manager(clock=clock)
    grant = acquire(manager, ("documents",), ttl_seconds=1)
    clock.advance(1)

    with pytest.raises(leases.LeaseExpired, match="lease-not-active"):
        manager.release(grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)

    assert not lock_map["documents"].locked()


def test_use_pins_expired_lease_until_mutation_exits():
    clock = FakeClock()
    manager, lock_map, _events = make_manager(clock=clock)
    grant = acquire(manager, ("documents", "voice"), ttl_seconds=1)

    with manager.use(
        grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH, ["documents"]
    ) as evidence:
        assert evidence["planHash"] == PLAN_HASH
        assert evidence["serviceIds"] == ["documents"]
        with pytest.raises(
            leases.LeaseBusy, match="lease-mutation-active"
        ), manager.use(
            grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH, ["voice"]
        ):
            pass
        with pytest.raises(leases.LeaseBusy, match="lease-mutation-active"):
            manager.release(grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)
        clock.advance(1)
        assert manager.sweep() == []
        assert lock_map["documents"].locked()
        assert lock_map["voice"].locked()

    assert not lock_map["documents"].locked()
    assert not lock_map["voice"].locked()
    with pytest.raises(leases.LeaseExpired, match="lease-not-active"):
        manager.describe(grant["leaseId"])


def test_use_rejects_a_service_outside_the_exact_grant():
    manager, lock_map, _events = make_manager()
    grant = acquire(manager, ("documents",))

    with pytest.raises(
        leases.LeaseAuthorizationError, match="lease-service-not-covered"
    ), manager.use(
        grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH, ["voice"]
    ):
        pass

    assert lock_map["documents"].locked()
    manager.release(grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)


def test_use_body_exception_clears_active_state_without_releasing_live_lease():
    manager, lock_map, _events = make_manager()
    grant = acquire(manager, ("documents",))

    with pytest.raises(RuntimeError, match="mutation-failed"), manager.use(
        grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH, ["documents"]
    ):
        raise RuntimeError("mutation-failed")

    assert manager.describe(grant["leaseId"])["active"] is False
    assert lock_map["documents"].locked()
    manager.release(grant["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)


def test_concurrent_acquire_allows_one_exact_winner_without_lock_leak():
    ids = iter(["lease-" + "a" * 32, "lease-" + "b" * 32])
    tokens = iter(["first-" + "x" * 32, "second-" + "x" * 32])
    manager, lock_map, _events = make_manager(
        lease_ids=ids,
        token_factory=lambda: next(tokens),
    )
    barrier = threading.Barrier(3)
    outcomes = []

    def contend():
        barrier.wait()
        try:
            outcomes.append(("grant", acquire(manager, ("documents",))))
        except leases.LeaseConflict as exc:
            outcomes.append(("conflict", exc.code))

    threads = [threading.Thread(target=contend) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item[0] for item in outcomes) == ["conflict", "grant"]
    grant = next(item[1] for item in outcomes if item[0] == "grant")
    token = (
        "first-" + "x" * 32
        if grant["leaseId"].endswith("a" * 32)
        else "second-" + "x" * 32
    )
    assert lock_map["documents"].locked()
    manager.release(grant["leaseId"], token, TRANSACTION_ID, PLAN_HASH)
    assert not lock_map["documents"].locked()


def test_second_lease_for_same_service_is_rejected_until_release():
    ids = iter(["lease-" + "a" * 32, "lease-" + "b" * 32])
    events = []
    lock_map = collections.defaultdict(lambda: TrackingLock("documents", events))
    manager = leases.ExtensionLeaseManager(
        lambda service_id: lock_map[service_id],
        clock=FakeClock(),
        lease_id_factory=lambda: next(ids),
        token_factory=lambda: TOKEN,
    )
    first = acquire(manager, ("documents",))

    with pytest.raises(leases.LeaseConflict, match="service-lock-busy"):
        acquire(manager, ("documents",))

    manager.release(first["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)
    second = acquire(manager, ("documents",))
    assert second["leaseId"] == "lease-" + "b" * 32


def test_active_lease_tokens_cannot_be_reused_across_grants():
    manager, lock_map, _events = make_manager()
    first = acquire(manager, ("documents",))

    with pytest.raises(leases.LeaseError, match="duplicate-lease-token"):
        acquire(manager, ("voice",))

    assert lock_map["documents"].locked()
    assert lock_map["voice"].acquire(blocking=False)
    lock_map["voice"].release()
    manager.release(first["leaseId"], TOKEN, TRANSACTION_ID, PLAN_HASH)
