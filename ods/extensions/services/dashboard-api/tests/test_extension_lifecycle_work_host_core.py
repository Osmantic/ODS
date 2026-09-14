"""Pure contract tests for the dormant host lifecycle-work core."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

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
    seen = []

    result = host_work.dispatch_lifecycle_work(
        command, lambda value: seen.append(value) or EVIDENCE_HASH
    )

    assert seen == [command]
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

    for value in (None, True, "short", "A" * 64):
        with pytest.raises(host_work.LifecycleWorkExecutionError) as invalid:
            host_work.dispatch_lifecycle_work(command, lambda _command, v=value: v)
        assert invalid.value.code == "lifecycle-work-invalid-result"


def test_dispatch_maps_private_exception_without_embedding_it():
    command = host_work.parse_lifecycle_work_request(
        work_request("verify", ["documents"], {"serviceIds": ["documents"]})
    )

    def fail(_command):
        raise RuntimeError("private-dispatch-detail")

    with pytest.raises(host_work.LifecycleWorkExecutionError) as raised:
        host_work.dispatch_lifecycle_work(command, fail)

    assert raised.value.code == "lifecycle-work-operation-failed"
    assert "private-dispatch-detail" not in str(raised.value)


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
