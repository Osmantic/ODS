from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from extension_lease_client import (
    ExtensionLeaseClient,
    ExtensionLeaseError,
    LeaseGrant,
)
from extension_lease_lock_factory import ExtensionLeaseLockFactory
from extension_lifecycle_receipt_client import (
    RECEIPT_SCHEMA,
    ExtensionLifecycleReceiptClient,
    LifecycleReceiptClientError,
    ReceiptResult,
    SnapshotResult,
)
from extension_receipted_lifecycle_adapter import (
    LifecycleWorkRequest,
    LifecycleWorkResult,
    ReceiptedLifecycleAdapter,
    ReceiptedLifecycleAdapterError,
    build_apply_observation_request,
)
from extension_transaction_executor import ExecutionBinding, TransactionExecutor

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
LEASE_ID = "lease-" + "3" * 32
LEASE_TOKEN = "private-lease-token-" + "4" * 32
EVIDENCE_HASH = "5" * 64
BINDING = ExecutionBinding(TRANSACTION_ID, PLAN_HASH)
SERVICE_IDS = ("aider", "ollama")


def _grant(service_ids: tuple[str, ...]) -> LeaseGrant:
    def response(_method: str, _path: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "schema": "ods.extension-operation-lease.v1",
            "leaseId": LEASE_ID,
            "leaseToken": LEASE_TOKEN,
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "serviceIds": list(service_ids),
            "ttlSeconds": kwargs["payload"]["ttlSeconds"],
        }

    return ExtensionLeaseClient(response).acquire(BINDING, service_ids)


class StubLeaseClient(ExtensionLeaseClient):
    def __init__(self, events: list[str]) -> None:
        super().__init__(lambda *_args, **_kwargs: {})
        self.events = events

    def acquire(
        self,
        binding: ExecutionBinding,
        service_ids: Any,
        *,
        ttl_seconds: int = 600,
    ) -> LeaseGrant:
        del binding, ttl_seconds
        self.events.append("lease:acquire")
        return _grant(tuple(service_ids))

    def release(self, grant: LeaseGrant) -> Any:
        del grant
        self.events.append("lease:release")
        return None


class SilentRenewer:
    def __init__(
        self,
        _client: ExtensionLeaseClient,
        grant: LeaseGrant,
        *,
        events: list[str],
        **_kwargs: Any,
    ) -> None:
        self.grant = grant
        self.events = events

    def start(self) -> None:
        self.events.append("renewer:start")

    def check(self) -> LeaseGrant:
        self.events.append("renewer:check")
        return self.grant

    def stop(self) -> LeaseGrant:
        self.events.append("renewer:stop")
        return self.grant


def custody(events: list[str]) -> ExtensionLeaseLockFactory:
    def make_renewer(
        client: ExtensionLeaseClient,
        grant: LeaseGrant,
        **kwargs: Any,
    ) -> Any:
        return SilentRenewer(client, grant, events=events, **kwargs)

    return ExtensionLeaseLockFactory(
        StubLeaseClient(events), _renewer_factory=make_renewer
    )


def _started(
    operation_key: str,
    request_hash: str,
    service_ids: tuple[str, ...],
) -> ReceiptResult:
    return ReceiptResult(
        schema=RECEIPT_SCHEMA,
        kind="started",
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        operation_key=operation_key,
        request_hash=request_hash,
        service_ids=service_ids,
        event_hash="a" * 64,
        outcome=None,
        evidence_hash=None,
        started_event_hash=None,
    )


def _terminal(
    started: ReceiptResult,
    outcome: str,
    evidence_hash: str,
) -> ReceiptResult:
    return ReceiptResult(
        schema=RECEIPT_SCHEMA,
        kind="terminal",
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        operation_key=started.operation_key,
        request_hash=started.request_hash,
        service_ids=started.service_ids,
        event_hash="b" * 64,
        outcome=outcome,
        evidence_hash=evidence_hash,
        started_event_hash=started.event_hash,
    )


class StubReceipts(ExtensionLifecycleReceiptClient):
    def __init__(self) -> None:
        super().__init__(lambda *_args, **_kwargs: {})
        self.states: dict[str, tuple[ReceiptResult, ReceiptResult | None]] = {}
        self.calls: list[str] = []
        self.begin_failures: list[tuple[Exception, str]] = []
        self.finish_failures: list[tuple[Exception, str]] = []
        self.snapshot_failures: list[Exception | None] = []

    def snapshot(
        self, transaction_id: str, plan_hash: str, operation_key: str
    ) -> SnapshotResult:
        self.calls.append(f"snapshot:{operation_key}")
        if self.snapshot_failures:
            failure = self.snapshot_failures.pop(0)
            if failure is not None:
                raise failure
        present = self.states.get(operation_key)
        if present is None:
            state = "absent"
            started = terminal = None
        else:
            started, terminal = present
            state = terminal.outcome if terminal is not None else "started"
        return SnapshotResult(
            schema=RECEIPT_SCHEMA,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            operation_key=operation_key,
            state=state,
            started_receipt=started,
            terminal_receipt=terminal,
        )

    def begin(
        self,
        transaction_id: str,
        plan_hash: str,
        operation_key: str,
        request_hash: str,
        service_ids: list[str] | tuple[str, ...],
    ) -> ReceiptResult:
        del transaction_id, plan_hash
        self.calls.append(f"begin:{operation_key}")
        started = _started(operation_key, request_hash, tuple(service_ids))
        if self.begin_failures:
            failure, state = self.begin_failures.pop(0)
            if state == "started":
                self.states[operation_key] = (started, None)
            raise failure
        self.states[operation_key] = (started, None)
        return started

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
        del transaction_id, plan_hash, request_hash, service_ids
        self.calls.append(f"finish:{operation_key}:{outcome}")
        started, _prior = self.states[operation_key]
        terminal = _terminal(started, outcome, evidence_hash)
        if self.finish_failures:
            failure, state = self.finish_failures.pop(0)
            if state == "terminal":
                self.states[operation_key] = (started, terminal)
            raise failure
        self.states[operation_key] = (started, terminal)
        return terminal


def adapter(
    events: list[str],
    receipts: StubReceipts,
    worker: Callable[[LeaseGrant, LifecycleWorkRequest], LifecycleWorkResult],
) -> tuple[ReceiptedLifecycleAdapter, ExtensionLeaseLockFactory]:
    lock_factory = custody(events)
    return ReceiptedLifecycleAdapter(lock_factory, receipts, worker), lock_factory


def _success_worker(
    seen: list[LifecycleWorkRequest], events: list[str]
) -> Callable[[LeaseGrant, LifecycleWorkRequest], LifecycleWorkResult]:
    def run(grant: LeaseGrant, work: LifecycleWorkRequest) -> LifecycleWorkResult:
        assert grant.lease_id == LEASE_ID
        events.append(f"worker:{work.operation_key}")
        seen.append(work)
        return LifecycleWorkResult(EVIDENCE_HASH)

    return run


def test_every_lifecycle_method_maps_complete_payload_under_one_lease() -> None:
    events: list[str] = []
    seen: list[LifecycleWorkRequest] = []
    receipts = StubReceipts()
    lifecycle, lock_factory = adapter(
        events, receipts, _success_worker(seen, events)
    )
    operation_a = {
        "serviceId": "aider",
        "action": "install",
        "definitionSha256": "sha256:" + "6" * 64,
    }
    operation_b = {
        "serviceId": "ollama",
        "action": "update",
        "definitionSha256": "sha256:" + "7" * 64,
    }
    operations = [operation_a, operation_b]

    with lock_factory.lock_services(BINDING, list(SERVICE_IDS)):
        results = [
            lifecycle.reserve(BINDING, operation_a),
            lifecycle.download_and_verify_all(BINDING, operations),
            lifecycle.stage_all(BINDING, operations),
            lifecycle.backup_all(BINDING, list(SERVICE_IDS)),
            lifecycle.configure_all(BINDING, list(SERVICE_IDS)),
            lifecycle.apply_one(BINDING, operation_a),
            lifecycle.verify_all(BINDING, list(SERVICE_IDS)),
            lifecycle.compensate_one(BINDING, operation_b),
            lifecycle.restore_all(BINDING, list(SERVICE_IDS)),
            lifecycle.release(BINDING, list(SERVICE_IDS)),
        ]

    assert [work.operation_key for work in seen] == [
        "reserve:aider",
        "download-and-verify",
        "stage",
        "backup",
        "configure",
        "apply:aider",
        "verify",
        "compensate:ollama",
        "restore",
        "release",
    ]
    assert seen[0].payload == {"operation": operation_a}
    assert seen[1].payload == {"operations": operations}
    assert all(result["ok"] is True and result["completed"] is True for result in results)
    for result in results:
        TransactionExecutor._require_completed(
            result, result["operationKey"], BINDING
        )
        assert "accepted" not in result
        assert result.get("statusCode") != 202
    assert events.index("worker:release") < events.index("renewer:stop")
    assert events[-1] == "lease:release"


def test_request_hash_binds_the_complete_operation_payload() -> None:
    hashes: list[str] = []
    for action in ("install", "remove"):
        events: list[str] = []
        seen: list[LifecycleWorkRequest] = []
        receipts = StubReceipts()
        lifecycle, lock_factory = adapter(
            events, receipts, _success_worker(seen, events)
        )
        with lock_factory.lock_services(BINDING, ["aider"]):
            operation = {"serviceId": "aider", "action": action, "version": "1.2.3"}
            lifecycle.apply_one(BINDING, operation)
        assert build_apply_observation_request(BINDING, operation) == seen[0]
        hashes.append(seen[0].request_hash)
    assert hashes[0] != hashes[1]


def test_completed_replay_uses_terminal_receipt_without_rerunning_worker() -> None:
    events: list[str] = []
    seen: list[LifecycleWorkRequest] = []
    receipts = StubReceipts()
    lifecycle, lock_factory = adapter(
        events, receipts, _success_worker(seen, events)
    )
    operation = {"serviceId": "aider", "action": "install"}
    with lock_factory.lock_services(BINDING, ["aider"]):
        first = lifecycle.apply_one(BINDING, operation)
        second = lifecycle.apply_one(BINDING, operation)
    assert len(seen) == 1
    assert first == second
    assert receipts.calls.count("begin:apply:aider") == 1


def test_completed_verify_replay_requires_fresh_matching_worker_evidence() -> None:
    events: list[str] = []
    seen: list[LifecycleWorkRequest] = []
    receipts = StubReceipts()
    lifecycle, lock_factory = adapter(
        events, receipts, _success_worker(seen, events)
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        first = lifecycle.verify_all(BINDING, ["aider"])
        replay = lifecycle.verify_all(BINDING, ["aider"])

    assert replay == first
    assert len(seen) == 2
    assert all(work.operation_key == "verify" for work in seen)
    assert receipts.calls.count("begin:verify") == 1
    assert receipts.calls.count("finish:verify:completed") == 1


def test_completed_verify_replay_refuses_changed_current_evidence() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    calls = 0

    def changed(_grant: LeaseGrant, _work: LifecycleWorkRequest) -> LifecycleWorkResult:
        nonlocal calls
        calls += 1
        return LifecycleWorkResult(EVIDENCE_HASH if calls == 1 else "6" * 64)

    lifecycle, lock_factory = adapter(events, receipts, changed)
    with lock_factory.lock_services(BINDING, ["aider"]):
        lifecycle.verify_all(BINDING, ["aider"])
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.verify_all(BINDING, ["aider"])

    assert caught.value.code == "lifecycle-verify-current-mismatch"
    assert calls == 2
    assert receipts.calls.count("finish:verify:completed") == 1
    assert receipts.states["verify"][1].evidence_hash == EVIDENCE_HASH


def test_completed_verify_replay_fails_closed_on_worker_error() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    calls = 0

    def failed(_grant: LeaseGrant, _work: LifecycleWorkRequest) -> LifecycleWorkResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ExtensionLeaseError("lease-not-active", retryable=True)
        return LifecycleWorkResult(EVIDENCE_HASH)

    lifecycle, lock_factory = adapter(events, receipts, failed)
    with lock_factory.lock_services(BINDING, ["aider"]):
        lifecycle.verify_all(BINDING, ["aider"])
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.verify_all(BINDING, ["aider"])

    assert caught.value.code == "lifecycle-lease-not-active"
    assert caught.value.retryable is True
    assert calls == 2
    assert receipts.calls.count("finish:verify:completed") == 1


def test_started_replay_requires_observation_and_never_reruns_worker() -> None:
    events: list[str] = []
    calls = 0
    receipts = StubReceipts()

    def interrupted(
        _grant: LeaseGrant, _work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt

    lifecycle, lock_factory = adapter(events, receipts, interrupted)
    operation = {"serviceId": "aider", "action": "install"}
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(KeyboardInterrupt):
            lifecycle.apply_one(BINDING, operation)
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.apply_one(BINDING, operation)
    assert caught.value.code == "lifecycle-receipt-recovery-required"
    assert caught.value.ambiguous is True
    assert calls == 1


def test_failed_worker_is_receipted_once_and_terminal_failure_is_not_retried() -> None:
    events: list[str] = []
    calls = 0
    receipts = StubReceipts()

    def failing(
        _grant: LeaseGrant, _work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        nonlocal calls
        calls += 1
        raise ExtensionLeaseError(
            "lease-not-active", retryable=False, ambiguous=False
        )

    lifecycle, lock_factory = adapter(events, receipts, failing)
    operation = {"serviceId": "aider", "action": "install"}
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(ReceiptedLifecycleAdapterError) as first:
            lifecycle.apply_one(BINDING, operation)
        with pytest.raises(ReceiptedLifecycleAdapterError) as replay:
            lifecycle.apply_one(BINDING, operation)
    assert first.value.code == "lifecycle-lease-not-active"
    assert replay.value.code == "lifecycle-receipt-terminal-failed"
    assert calls == 1
    assert receipts.states["apply:aider"][1].outcome == "failed"


def test_ambiguous_worker_reconciles_host_completed_terminal() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    calls = 0

    def ambiguous_after_host_finish(
        _grant: LeaseGrant, work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        nonlocal calls
        calls += 1
        started, _terminal_receipt = receipts.states[work.operation_key]
        receipts.states[work.operation_key] = (
            started,
            _terminal(started, "completed", EVIDENCE_HASH),
        )
        raise ExtensionLeaseError(
            "host-work-operation-ambiguous", ambiguous=True
        )

    lifecycle, lock_factory = adapter(
        events, receipts, ambiguous_after_host_finish
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        result = lifecycle.apply_one(
            BINDING, {"serviceId": "aider", "action": "install"}
        )
        replay = lifecycle.apply_one(
            BINDING, {"serviceId": "aider", "action": "install"}
        )

    assert result["completed"] is True
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert replay == result
    assert calls == 1
    assert "finish:apply:aider:completed" not in receipts.calls


def test_ambiguous_worker_rejects_mismatched_completed_terminal() -> None:
    events: list[str] = []
    receipts = StubReceipts()

    def ambiguous_after_mismatched_finish(
        _grant: LeaseGrant, work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        mismatched = _started(work.operation_key, "f" * 64, work.service_ids)
        receipts.states[work.operation_key] = (
            mismatched,
            _terminal(mismatched, "completed", EVIDENCE_HASH),
        )
        raise ExtensionLeaseError(
            "host-work-operation-ambiguous", ambiguous=True
        )

    lifecycle, lock_factory = adapter(
        events, receipts, ambiguous_after_mismatched_finish
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )

    assert caught.value.code == "lifecycle-receipt-request-mismatch"
    assert not any(call.startswith("finish:apply:aider") for call in receipts.calls)


def test_ambiguous_worker_reconciles_host_failed_terminal() -> None:
    events: list[str] = []
    receipts = StubReceipts()

    def ambiguous_after_host_failure(
        _grant: LeaseGrant, work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        started, _terminal_receipt = receipts.states[work.operation_key]
        receipts.states[work.operation_key] = (
            started,
            _terminal(started, "failed", "6" * 64),
        )
        raise ExtensionLeaseError(
            "host-work-operation-ambiguous", ambiguous=True
        )

    lifecycle, lock_factory = adapter(
        events, receipts, ambiguous_after_host_failure
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )

    assert caught.value.code == "lifecycle-receipt-terminal-failed"
    assert not any(call.startswith("finish:apply:aider") for call in receipts.calls)


def test_ambiguous_worker_leaves_started_receipt_for_durable_recovery() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    calls = 0

    def ambiguous(
        _grant: LeaseGrant, _work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        nonlocal calls
        calls += 1
        raise ExtensionLeaseError(
            "host-work-operation-ambiguous", ambiguous=True
        )

    lifecycle, lock_factory = adapter(events, receipts, ambiguous)
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(ReceiptedLifecycleAdapterError) as first:
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )
        with pytest.raises(ReceiptedLifecycleAdapterError) as replay:
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )

    assert first.value.code == "lifecycle-receipt-recovery-required"
    assert replay.value.code == "lifecycle-receipt-recovery-required"
    assert first.value.ambiguous is True and replay.value.ambiguous is True
    assert calls == 1
    assert receipts.states["apply:aider"][1] is None
    assert not any(call.startswith("finish:apply:aider") for call in receipts.calls)


def test_begin_ambiguity_converges_only_when_this_call_published_started() -> None:
    events: list[str] = []
    seen: list[LifecycleWorkRequest] = []
    receipts = StubReceipts()
    receipts.begin_failures.append(
        (
            LifecycleReceiptClientError(
                "receipt-operation-ambiguous", ambiguous=True
            ),
            "started",
        )
    )
    lifecycle, lock_factory = adapter(
        events, receipts, _success_worker(seen, events)
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        result = lifecycle.apply_one(
            BINDING, {"serviceId": "aider", "action": "install"}
        )
    assert result["completed"] is True
    assert len(seen) == 1
    assert receipts.calls.count("begin:apply:aider") == 1


def test_repeated_ambiguous_begin_with_absent_snapshots_never_runs_worker() -> None:
    events: list[str] = []
    seen: list[LifecycleWorkRequest] = []
    receipts = StubReceipts()
    for _index in range(2):
        receipts.begin_failures.append(
            (
                LifecycleReceiptClientError(
                    "receipt-operation-ambiguous", ambiguous=True
                ),
                "absent",
            )
        )
    lifecycle, lock_factory = adapter(
        events, receipts, _success_worker(seen, events)
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )
    assert caught.value.code == "lifecycle-receipt-ambiguous"
    assert caught.value.ambiguous is True
    assert seen == []


@pytest.mark.parametrize("finish_state", ["started", "terminal"])
def test_finish_ambiguity_never_reruns_worker(finish_state: str) -> None:
    events: list[str] = []
    seen: list[LifecycleWorkRequest] = []
    receipts = StubReceipts()
    receipts.finish_failures.append(
        (
            LifecycleReceiptClientError(
                "receipt-operation-ambiguous", ambiguous=True
            ),
            finish_state,
        )
    )
    lifecycle, lock_factory = adapter(
        events, receipts, _success_worker(seen, events)
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        result = lifecycle.apply_one(
            BINDING, {"serviceId": "aider", "action": "install"}
        )
    assert result["completed"] is True
    assert len(seen) == 1
    expected_finishes = 2 if finish_state == "started" else 1
    assert receipts.calls.count("finish:apply:aider:completed") == expected_finishes


def test_snapshot_failure_after_ambiguous_begin_preserves_ambiguity() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    receipts.snapshot_failures = [
        None,
        LifecycleReceiptClientError("receipt-unavailable", retryable=True),
    ]
    receipts.begin_failures.append(
        (
            LifecycleReceiptClientError(
                "receipt-operation-ambiguous", ambiguous=True
            ),
            "started",
        )
    )
    lifecycle, lock_factory = adapter(
        events, receipts, lambda *_args: LifecycleWorkResult(EVIDENCE_HASH)
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )
    assert caught.value.code == "lifecycle-receipt-reconciliation-failed"
    assert caught.value.ambiguous is True


def test_invalid_worker_result_publishes_failed_receipt_without_claiming_success() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    lifecycle, lock_factory = adapter(
        events, receipts, lambda *_args: object()  # type: ignore[arg-type]
    )
    with lock_factory.lock_services(BINDING, ["aider"]):
        with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )
    assert caught.value.code == "lifecycle-worker-invalid-result"
    terminal = receipts.states["apply:aider"][1]
    assert terminal is not None and terminal.outcome == "failed"


def test_empty_batch_and_service_operations_are_receiptless_noops() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    lifecycle, _lock_factory = adapter(
        events,
        receipts,
        lambda *_args: (_ for _ in ()).throw(AssertionError("worker called")),
    )
    results = [
        lifecycle.download_and_verify_all(BINDING, []),
        lifecycle.stage_all(BINDING, []),
        lifecycle.backup_all(BINDING, []),
        lifecycle.configure_all(BINDING, []),
        lifecycle.restore_all(BINDING, []),
        lifecycle.release(BINDING, []),
    ]
    assert all(result["completed"] is True for result in results)
    assert receipts.calls == []
    assert events == []


def test_missing_custody_fails_before_receipt_or_worker() -> None:
    events: list[str] = []
    seen: list[LifecycleWorkRequest] = []
    receipts = StubReceipts()
    lifecycle, _lock_factory = adapter(
        events, receipts, _success_worker(seen, events)
    )
    with pytest.raises(ExtensionLeaseError, match="lease-lock-not-active"):
        lifecycle.apply_one(
            BINDING, {"serviceId": "aider", "action": "install"}
        )
    assert receipts.calls == []
    assert seen == []


def test_unexpected_worker_text_never_escapes_error_or_receipt() -> None:
    events: list[str] = []
    receipts = StubReceipts()

    def failing(
        _grant: LeaseGrant, _work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        raise RuntimeError(LEASE_TOKEN)

    lifecycle, lock_factory = adapter(events, receipts, failing)
    with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
        with lock_factory.lock_services(BINDING, ["aider"]):
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )
    rendered = repr(caught.value) + "".join(traceback.format_exception(caught.value))
    rendered += repr(receipts.states)
    assert caught.value.code == "lifecycle-worker-internal"
    assert LEASE_TOKEN not in rendered


def test_work_request_repr_redacts_the_complete_payload() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    rendered: list[str] = []

    def worker(
        _grant: LeaseGrant, work: LifecycleWorkRequest
    ) -> LifecycleWorkResult:
        rendered.append(repr(work))
        return LifecycleWorkResult(EVIDENCE_HASH)

    lifecycle, lock_factory = adapter(events, receipts, worker)
    with lock_factory.lock_services(BINDING, ["aider"]):
        lifecycle.apply_one(
            BINDING,
            {"serviceId": "aider", "action": "install", "opaque": LEASE_TOKEN},
        )
    assert "payload=<redacted>" in rendered[0]
    assert LEASE_TOKEN not in rendered[0]


@pytest.mark.parametrize(
    "phase, expected_code, retryable",
    [
        ("snapshot", "lifecycle-receipt-client-internal", True),
        ("begin", "lifecycle-receipt-ambiguous", False),
        ("finish", "lifecycle-receipt-ambiguous", False),
    ],
)
def test_unexpected_receipt_failures_are_redacted_and_classified(
    phase: str, expected_code: str, retryable: bool
) -> None:
    events: list[str] = []
    receipts = StubReceipts()
    if phase == "snapshot":
        receipts.snapshot_failures.append(RuntimeError(LEASE_TOKEN))
    elif phase == "begin":
        receipts.begin_failures.append((RuntimeError(LEASE_TOKEN), "absent"))
    else:
        receipts.finish_failures.append((RuntimeError(LEASE_TOKEN), "started"))
    lifecycle, lock_factory = adapter(
        events, receipts, lambda *_args: LifecycleWorkResult(EVIDENCE_HASH)
    )
    with pytest.raises(ReceiptedLifecycleAdapterError) as caught:
        with lock_factory.lock_services(BINDING, ["aider"]):
            lifecycle.apply_one(
                BINDING, {"serviceId": "aider", "action": "install"}
            )
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    if phase != "snapshot":
        assert caught.value.ambiguous is True
    assert LEASE_TOKEN not in "".join(traceback.format_exception(caught.value))


def test_adapter_is_the_only_new_dormant_importer_and_production_stays_disabled() -> None:
    source_root = Path(__file__).resolve().parents[1]
    module = source_root / "extension_receipted_lifecycle_adapter.py"
    source = module.read_text(encoding="utf-8")
    production = (source_root / "extension_transaction_production.py").read_text(
        encoding="utf-8"
    )
    importers = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts
        and path != module
        and "extension_receipted_lifecycle_adapter"
        in path.read_text(encoding="utf-8")
    }

    assert importers == {
        "extension_lifecycle_work_client.py",
        "extension_transaction_application_observer.py",
    }
    assert "extension_receipted_lifecycle_adapter" not in production
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


def test_constructor_rejects_invalid_dependencies() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    lock_factory = custody(events)

    def worker(*_args: Any) -> LifecycleWorkResult:
        return LifecycleWorkResult(EVIDENCE_HASH)

    for args, code in (
        ((object(), receipts, worker), "lifecycle-invalid-custody"),
        ((lock_factory, object(), worker), "lifecycle-invalid-receipt-client"),
        ((lock_factory, receipts, None), "lifecycle-invalid-worker"),
    ):
        with pytest.raises(ReceiptedLifecycleAdapterError, match=code):
            ReceiptedLifecycleAdapter(*args)  # type: ignore[arg-type]


def test_invalid_payloads_fail_before_custody_or_receipts() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    lifecycle, _lock_factory = adapter(
        events, receipts, lambda *_args: LifecycleWorkResult(EVIDENCE_HASH)
    )
    invalid = [
        {"serviceId": "Bad", "action": "install"},
        {"serviceId": "aider", "ratio": 1.5},
        {"serviceId": "aider", "opaque": object()},
    ]
    for operation in invalid:
        with pytest.raises(ReceiptedLifecycleAdapterError):
            lifecycle.apply_one(BINDING, operation)
    assert receipts.calls == []
    assert events == []


def test_cross_thread_call_cannot_borrow_owner_custody() -> None:
    events: list[str] = []
    receipts = StubReceipts()
    lifecycle, lock_factory = adapter(
        events, receipts, lambda *_args: LifecycleWorkResult(EVIDENCE_HASH)
    )
    errors: list[ExtensionLeaseError] = []
    with lock_factory.lock_services(BINDING, ["aider"]):
        def invoke() -> None:
            try:
                lifecycle.apply_one(
                    BINDING, {"serviceId": "aider", "action": "install"}
                )
            except ExtensionLeaseError as error:
                errors.append(error)

        thread = threading.Thread(target=invoke)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
    assert [error.code for error in errors] == ["lease-lock-wrong-thread"]
    assert receipts.calls == []
