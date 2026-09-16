"""Fail-closed transaction-wide assembly of host application observations."""

from __future__ import annotations

import hashlib
import json

import pytest

from extension_lifecycle_work_client import ApplicationObservationResult
from extension_transaction_application_observer import (
    TransactionApplicationObservationError,
    TransactionApplicationObserver,
)
from extension_transaction_executor import ExecutionBinding

BINDING = ExecutionBinding("txn-" + "1" * 24, "2" * 64)
OPERATIONS = [
    {"serviceId": "gitea", "action": "install"},
    {"serviceId": "ntfy", "action": "install"},
    {"serviceId": "miniflux", "action": "install"},
]


def transaction(*, state="applying", operations=None, plan_hash=None):
    return {
        "transactionId": BINDING.transaction_id,
        "state": state,
        "envelope": {
            "planHash": plan_hash or BINDING.plan_hash,
            "plan": {"operations": operations or list(OPERATIONS)},
        },
    }


def result(service_id, classification):
    return ApplicationObservationResult(
        service_id=service_id,
        classification=classification,
        identity_hash="a" * 64,
        record_hash="b" * 64 if classification == "APPLIED" else None,
    )


def observer_for(classifications, *, stored=None):
    calls = []

    def grant(binding, service_ids):
        assert binding == BINDING
        calls.append(("grant", service_ids))
        return object()

    def observe(_grant, request):
        service_id = request.service_ids[0]
        calls.append(("observe", service_id))
        unsigned = {
            "schema": "ods.extension-lifecycle-work-request.v1",
            "transactionId": BINDING.transaction_id,
            "planHash": BINDING.plan_hash,
            "operationKey": f"apply:{service_id}",
            "serviceIds": [service_id],
            "payload": {
                "operation": next(
                    item for item in OPERATIONS if item["serviceId"] == service_id
                )
            },
        }
        expected = hashlib.sha256(
            json.dumps(
                unsigned,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        assert request.request_hash == expected
        return result(service_id, classifications[service_id])

    observer = TransactionApplicationObserver(
        lambda _transaction_id: stored or transaction(), grant, observe
    )
    return observer, calls


def test_exact_prefix_and_reusable_transaction_lease() -> None:
    observer, calls = observer_for(
        {
            "gitea": "APPLIED",
            "ntfy": "ABSENT",
            "miniflux": "ABSENT",
        }
    )
    assert observer.observe(BINDING) == {
        "transactionId": BINDING.transaction_id,
        "planHash": BINDING.plan_hash,
        "appliedServices": ["gitea"],
    }
    assert calls == [
        ("grant", ("gitea",)),
        ("observe", "gitea"),
        ("grant", ("ntfy",)),
        ("observe", "ntfy"),
        ("grant", ("miniflux",)),
        ("observe", "miniflux"),
    ]


@pytest.mark.parametrize("state", ["applying", "verifying", "reconciling"])
def test_observable_transaction_states(state) -> None:
    observer, _calls = observer_for(
        {item["serviceId"]: "APPLIED" for item in OPERATIONS},
        stored=transaction(state=state),
    )
    assert observer.observe(BINDING)["appliedServices"] == ["gitea", "ntfy", "miniflux"]


def test_non_prefix_or_unknown_never_emits_applied_prefix() -> None:
    observer, _calls = observer_for(
        {
            "gitea": "APPLIED",
            "ntfy": "ABSENT",
            "miniflux": "APPLIED",
        }
    )
    with pytest.raises(TransactionApplicationObservationError) as caught:
        observer.observe(BINDING)
    assert str(caught.value) == "transaction-observation-non-prefix"

    observer, _calls = observer_for(
        {
            "gitea": "APPLIED",
            "ntfy": "UNKNOWN",
            "miniflux": "ABSENT",
        }
    )
    with pytest.raises(TransactionApplicationObservationError):
        observer.observe(BINDING)


def test_wrong_plan_and_terminal_state_fail_before_host_call() -> None:
    for stored in (
        transaction(plan_hash="3" * 64),
        transaction(state="committed"),
        transaction(operations=[OPERATIONS[0], OPERATIONS[0]]),
    ):
        observer, calls = observer_for({}, stored=stored)
        with pytest.raises(TransactionApplicationObservationError):
            observer.observe(BINDING)
        assert calls == []


def test_host_failure_remains_unknown_not_absent() -> None:
    def fail(_grant, _request):
        raise TimeoutError("private host detail")

    observer = TransactionApplicationObserver(
        lambda _transaction_id: transaction(),
        lambda _binding, _services: object(),
        fail,
    )
    with pytest.raises(TransactionApplicationObservationError) as caught:
        observer.observe(BINDING)
    assert str(caught.value) == "transaction-observation-unavailable"


def test_noop_plan_has_no_applied_effect_and_does_not_call_host() -> None:
    observer, calls = observer_for(
        {}, stored=transaction(operations=[{"serviceId": "gitea", "action": "noop"}])
    )
    assert observer.observe(BINDING)["appliedServices"] == []
    assert calls == []
