"""Pure contract tests for the dormant host lifecycle-work core."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_lifecycle_work as host_work  # noqa: E402
import extension_lifecycle_work_client as work_client  # noqa: E402
from extension_receipted_lifecycle_adapter import (  # noqa: E402
    MAX_WORK_REQUEST_BYTES,
    REQUEST_SCHEMA,
)

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
EVIDENCE_HASH = "3" * 64


class MemoryReceiptStore:
    def __init__(self):
        self.started = None
        self.terminal = None

    def begin(
        self,
        transaction_id,
        plan_hash,
        operation_key,
        request_hash,
        service_ids,
    ):
        self.started = SimpleNamespace(
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            operation_key=operation_key,
            request_hash=request_hash,
            service_ids=tuple(service_ids),
            event_hash="a" * 64,
        )
        return self.started

    def finish(
        self,
        transaction_id,
        plan_hash,
        operation_key,
        request_hash,
        service_ids,
        outcome,
        evidence_hash,
    ):
        assert self.started is not None
        self.terminal = SimpleNamespace(
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            operation_key=operation_key,
            request_hash=request_hash,
            service_ids=tuple(service_ids),
            outcome=outcome,
            evidence_hash=evidence_hash,
            started_event_hash=self.started.event_hash,
            event_hash="b" * 64,
        )
        return self.terminal

    def snapshot(self, transaction_id, operation_key):
        state = (
            self.terminal.outcome
            if self.terminal is not None
            else "started" if self.started is not None else "absent"
        )
        return SimpleNamespace(
            transaction_id=transaction_id,
            operation_key=operation_key,
            state=state,
            started_receipt=self.started,
            terminal_receipt=self.terminal,
        )


def work_request(operation_key, service_ids, payload, **changes):
    unsigned = {
        "schema": host_work.REQUEST_SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": operation_key,
        "serviceIds": list(service_ids),
        "payload": payload,
    }
    value = {
        **unsigned,
        "requestHash": hashlib.sha256(
            json.dumps(
                unsigned,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
    }
    value.update(changes)
    return value


def payload_for(operation_key, service_ids):
    if ":" in operation_key:
        return {
            "operation": {
                "serviceId": service_ids[0],
                "definitionHash": "4" * 64,
            }
        }
    if operation_key in {"download-and-verify", "stage"}:
        return {
            "operations": [
                {"serviceId": service_id, "definitionHash": "4" * 64}
                for service_id in service_ids
            ]
        }
    return {"serviceIds": list(service_ids)}


def test_host_and_dashboard_contract_constants_match():
    assert host_work.REQUEST_SCHEMA == REQUEST_SCHEMA
    assert host_work.REQUEST_SCHEMA == work_client.REQUEST_SCHEMA
    assert host_work.RESULT_SCHEMA == work_client.RESULT_SCHEMA
    assert host_work.MAX_WORK_REQUEST_BYTES == MAX_WORK_REQUEST_BYTES
    assert host_work.MAX_HTTP_REQUEST_BYTES == work_client.MAX_REQUEST_BYTES
    assert host_work.MAX_SERVICE_IDS == work_client.MAX_SERVICE_IDS


@pytest.mark.parametrize(
    "operation_key,service_ids,timeout_seconds",
    [
        ("reserve:documents", ["documents"], 30),
        ("download-and-verify", ["documents", "voice"], 1800),
        ("stage", ["documents", "voice"], 600),
        ("backup", ["documents", "voice"], 600),
        ("configure", ["documents", "voice"], 600),
        ("apply:documents", ["documents"], 900),
        ("verify", ["documents", "voice"], 600),
        ("compensate:documents", ["documents"], 900),
        ("restore", ["documents", "voice"], 600),
        ("release", ["documents", "voice"], 30),
    ],
)
def test_parse_accepts_only_the_closed_operation_grammar(
    operation_key, service_ids, timeout_seconds
):
    request = work_request(
        operation_key,
        service_ids,
        payload_for(operation_key, service_ids),
    )

    command = host_work.parse_lifecycle_work_request(request)

    assert command.transaction_id == TRANSACTION_ID
    assert command.plan_hash == PLAN_HASH
    assert command.operation_key == operation_key
    assert command.request_hash == request["requestHash"]
    assert command.service_ids == tuple(service_ids)
    assert command.payload == request["payload"]
    assert command.timeout_seconds == timeout_seconds


def test_parse_clones_nested_payload_before_dispatch():
    request = work_request(
        "apply:documents",
        ["documents"],
        payload_for("apply:documents", ["documents"]),
    )
    command = host_work.parse_lifecycle_work_request(request)

    request["payload"]["operation"]["serviceId"] = "voice"

    assert command.payload["operation"]["serviceId"] == "documents"


@pytest.mark.parametrize(
    "request_document",
    [
        {},
        work_request(
            "verify", ["documents"], {"serviceIds": ["documents"]}, extra=True
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"]},
            schema="wrong",
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"]},
            transactionId="txn-short",
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"]},
            planHash="not-a-hash",
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"]},
            serviceIds=[],
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"]},
            serviceIds=["documents", "documents"],
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"]},
            serviceIds=["../documents"],
        ),
        work_request(
            "apply:documents",
            ["documents"],
            payload_for("apply:documents", ["documents"]),
            operationKey="apply:voice",
        ),
        work_request(
            "apply:documents",
            ["documents"],
            {"operation": {"serviceId": "voice"}},
        ),
        work_request(
            "stage",
            ["documents", "voice"],
            {"operations": [{"serviceId": "voice"}, {"serviceId": "documents"}]},
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"], "unexpected": True},
        ),
        work_request(
            "verify",
            ["documents"],
            {"serviceIds": ["documents"], "estimate": 1.5},
        ),
        work_request(
            "shell:documents",
            ["documents"],
            {"operation": {"serviceId": "documents"}},
        ),
    ],
)
def test_parse_rejects_invalid_shape_binding_operation_or_payload(
    request_document,
):
    with pytest.raises(
        host_work.LifecycleWorkValidationError,
        match="invalid-lifecycle-work-request",
    ):
        host_work.parse_lifecycle_work_request(request_document)


def test_parse_rejects_hash_mismatch_separately():
    request = work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    request["requestHash"] = "9" * 64

    with pytest.raises(host_work.LifecycleWorkValidationError) as raised:
        host_work.parse_lifecycle_work_request(request)

    assert raised.value.code == "lifecycle-work-request-hash-mismatch"


def test_parse_rejects_more_than_the_closed_service_limit():
    service_ids = [f"service-{index}" for index in range(65)]
    request = work_request("verify", service_ids, {"serviceIds": list(service_ids)})

    with pytest.raises(host_work.LifecycleWorkValidationError) as raised:
        host_work.parse_lifecycle_work_request(request)

    assert raised.value.code == "invalid-lifecycle-work-request"


def test_parse_rejects_excessive_depth_and_canonical_size():
    nested = None
    for _ in range(18):
        nested = [nested]
    deep = work_request(
        "apply:documents",
        ["documents"],
        {"operation": {"serviceId": "documents", "nested": nested}},
    )
    with pytest.raises(host_work.LifecycleWorkValidationError):
        host_work.parse_lifecycle_work_request(deep)

    oversized = work_request(
        "apply:documents",
        ["documents"],
        {
            "operation": {
                "serviceId": "documents",
                "metadata": "x" * host_work.MAX_WORK_REQUEST_BYTES,
            }
        },
    )
    with pytest.raises(host_work.LifecycleWorkValidationError) as raised:
        host_work.parse_lifecycle_work_request(oversized)
    assert raised.value.code == "lifecycle-work-request-size"


def test_dispatch_returns_only_exact_terminal_evidence():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    bound = replace(command, plan_material=SimpleNamespace(bound=True))
    seen = []

    result = host_work.dispatch_lifecycle_work(
        bound, lambda value: seen.append(value) or EVIDENCE_HASH
    )

    assert seen == [bound]
    assert result == {
        "schema": host_work.RESULT_SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "verify",
        "requestHash": command.request_hash,
        "serviceIds": ["documents"],
        "completed": True,
        "outcome": "completed",
        "evidenceHash": EVIDENCE_HASH,
    }


def test_dispatch_fails_closed_without_dispatcher_or_valid_evidence():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    with pytest.raises(host_work.LifecycleWorkUnavailable) as unavailable:
        host_work.dispatch_lifecycle_work(command, None)
    assert unavailable.value.code == "lifecycle-work-dispatcher-unavailable"

    called = []
    with pytest.raises(host_work.LifecycleWorkValidationError) as unbound:
        host_work.dispatch_lifecycle_work(
            command, lambda value: called.append(value) or EVIDENCE_HASH
        )
    assert unbound.value.code == "lifecycle-work-plan-mismatch"
    assert called == []

    bound = replace(command, plan_material=SimpleNamespace(bound=True))
    for value in (None, True, "short", "A" * 64):
        with pytest.raises(host_work.LifecycleWorkExecutionError) as invalid:
            host_work.dispatch_lifecycle_work(bound, lambda _command, v=value: v)
        assert invalid.value.code == "lifecycle-work-invalid-result"


def test_dispatch_maps_private_exception_without_embedding_it():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    bound = replace(command, plan_material=SimpleNamespace(bound=True))

    def fail(_command):
        raise RuntimeError("private-dispatch-detail")

    with pytest.raises(host_work.LifecycleWorkExecutionError) as raised:
        host_work.dispatch_lifecycle_work(bound, fail)

    assert raised.value.code == "lifecycle-work-operation-failed"
    assert "private-dispatch-detail" not in str(raised.value)


def _begun_store(command):
    store = MemoryReceiptStore()
    store.begin(
        command.transaction_id,
        command.plan_hash,
        command.operation_key,
        command.request_hash,
        command.service_ids,
    )
    return store


def _load_plan(command):
    return replace(command, plan_material=SimpleNamespace(bound=True))


def test_receipted_dispatch_terminalizes_before_success_and_replays():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    store = _begun_store(command)
    seen = []
    loaded = []

    def load_plan(value):
        loaded.append(value)
        return _load_plan(value)

    first = host_work.dispatch_receipted_lifecycle_work(
        command, lambda value: seen.append(value) or EVIDENCE_HASH, store, load_plan
    )
    snapshot = store.snapshot(command.transaction_id, command.operation_key)
    second = host_work.dispatch_receipted_lifecycle_work(
        command,
        lambda _value: (_ for _ in ()).throw(AssertionError("replayed work")),
        store,
        load_plan,
    )

    assert first == second
    assert len(seen) == 1
    assert seen[0].plan_material.bound is True
    assert loaded == [command]
    assert snapshot.state == "completed"
    assert snapshot.terminal_receipt is not None
    assert snapshot.terminal_receipt.evidence_hash == EVIDENCE_HASH


def test_receipted_dispatch_requires_plan_binding_before_the_worker():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    store = _begun_store(command)
    worker_calls = []
    loader_calls = []

    with pytest.raises(host_work.LifecycleWorkUnavailable) as unavailable:
        host_work.dispatch_receipted_lifecycle_work(
            command,
            lambda value: worker_calls.append(value) or EVIDENCE_HASH,
            store,
            None,
        )
    assert unavailable.value.code == "lifecycle-work-plan-loader-unavailable"
    assert worker_calls == []
    assert store.snapshot(command.transaction_id, command.operation_key).state == (
        "started"
    )

    def reject_plan(_command):
        loader_calls.append(True)
        raise host_work.LifecycleWorkValidationError("lifecycle-work-plan-mismatch")

    with pytest.raises(host_work.LifecycleWorkValidationError) as rejected:
        host_work.dispatch_receipted_lifecycle_work(
            command,
            lambda value: worker_calls.append(value) or EVIDENCE_HASH,
            store,
            reject_plan,
        )
    assert rejected.value.code == "lifecycle-work-plan-mismatch"
    assert worker_calls == []
    assert loader_calls == [True]
    assert store.snapshot(command.transaction_id, command.operation_key).state == (
        "failed"
    )

    with pytest.raises(host_work.LifecycleWorkExecutionError) as replay:
        host_work.dispatch_receipted_lifecycle_work(
            command,
            lambda value: worker_calls.append(value) or EVIDENCE_HASH,
            store,
            reject_plan,
        )
    assert replay.value.code == "lifecycle-work-terminal-failed"
    assert loader_calls == [True]
    assert worker_calls == []


def test_receipted_dispatch_requires_the_exact_started_binding():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    store = MemoryReceiptStore()
    called = []

    with pytest.raises(host_work.LifecycleWorkValidationError) as absent:
        host_work.dispatch_receipted_lifecycle_work(
            command,
            lambda value: called.append(value) or EVIDENCE_HASH,
            store,
            _load_plan,
        )
    assert absent.value.code == "lifecycle-work-started-receipt-required"

    store.begin(
        command.transaction_id,
        "9" * 64,
        command.operation_key,
        command.request_hash,
        command.service_ids,
    )
    with pytest.raises(host_work.LifecycleWorkValidationError) as mismatch:
        host_work.dispatch_receipted_lifecycle_work(
            command,
            lambda value: called.append(value) or EVIDENCE_HASH,
            store,
            _load_plan,
        )
    assert mismatch.value.code == "lifecycle-work-receipt-mismatch"
    assert called == []


@pytest.mark.parametrize(
    ("failure", "failure_code"),
    [
        (RuntimeError("private"), "lifecycle-work-operation-failed"),
        (None, "lifecycle-work-invalid-result"),
    ],
)
def test_receipted_dispatch_durably_fails_and_never_replays(
    failure, failure_code
):
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    store = _begun_store(command)
    calls = 0

    def dispatch(_command):
        nonlocal calls
        calls += 1
        if failure is not None:
            raise failure
        return "invalid"

    with pytest.raises(host_work.LifecycleWorkExecutionError):
        host_work.dispatch_receipted_lifecycle_work(
            command, dispatch, store, _load_plan
        )
    snapshot = store.snapshot(command.transaction_id, command.operation_key)
    assert snapshot.state == "failed"
    assert snapshot.terminal_receipt is not None
    expected_failure_hash = hashlib.sha256(
        json.dumps(
            {
                "schema": host_work.FAILURE_SCHEMA,
                "transactionId": command.transaction_id,
                "planHash": command.plan_hash,
                "operationKey": command.operation_key,
                "requestHash": command.request_hash,
                "serviceIds": list(command.service_ids),
                "outcome": "failed",
                "code": failure_code,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert snapshot.terminal_receipt.evidence_hash == expected_failure_hash

    with pytest.raises(host_work.LifecycleWorkExecutionError) as replay:
        host_work.dispatch_receipted_lifecycle_work(
            command, dispatch, store, _load_plan
        )
    assert replay.value.code == "lifecycle-work-terminal-failed"
    assert calls == 1


def test_receipted_dispatch_never_reports_success_if_terminal_publish_fails():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )
    store = _begun_store(command)
    calls = []

    def fail_finish(*_args):
        raise OSError("private-store-detail")

    store.finish = fail_finish

    with pytest.raises(host_work.LifecycleWorkExecutionError) as caught:
        host_work.dispatch_receipted_lifecycle_work(
            command,
            lambda value: calls.append(value) or EVIDENCE_HASH,
            store,
            _load_plan,
        )

    assert caught.value.code == "lifecycle-work-receipt-store-unavailable"
    assert "private-store-detail" not in str(caught.value)
    assert calls == [command]
    assert store.snapshot(command.transaction_id, command.operation_key).state == (
        "started"
    )


def test_host_core_is_stdlib_only_and_has_no_mutation_primitives():
    module_path = BIN_DIR / "extension_lifecycle_work.py"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])

    assert imports <= {
        "__future__",
        "collections",
        "dataclasses",
        "hashlib",
        "hmac",
        "json",
        "re",
        "typing",
    }
    assert "subprocess" not in source
    assert "shutil" not in source
    assert "pathlib" not in source
    assert "threading" not in source
    assert "urllib" not in source
