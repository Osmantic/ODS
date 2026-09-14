"""Adversarial tests for the dormant synchronous lifecycle-work client."""

from __future__ import annotations

import hashlib
import json
import traceback
from pathlib import Path

import httpx
import pytest

import host_agent_client as agent_client
from extension_lease_client import (
    LEASE_SCHEMA,
    ExtensionLeaseClient,
    LeaseGrant,
)
from extension_lifecycle_work_client import (
    HOST_WORK_PATH,
    MAX_RESPONSE_BYTES,
    RESULT_SCHEMA,
    ExtensionLifecycleWorkClient,
    LifecycleHostWorkError,
)
from extension_receipted_lifecycle_adapter import (
    REQUEST_SCHEMA,
    LifecycleWorkRequest,
    LifecycleWorkResult,
)
from extension_transaction_executor import ExecutionBinding
from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentTimeout,
    AgentUnavailable,
)

TRANSACTION_ID = "txn-" + "1" * 24
OTHER_TRANSACTION_ID = "txn-" + "2" * 24
PLAN_HASH = "3" * 64
OTHER_PLAN_HASH = "4" * 64
EVIDENCE_HASH = "5" * 64
OTHER_EVIDENCE_HASH = "6" * 64
LEASE_ID = "lease-" + "7" * 32
LEASE_TOKEN = "private-lease-token-" + "8" * 48
BINDING = ExecutionBinding(TRANSACTION_ID, PLAN_HASH)
OTHER_BINDING = ExecutionBinding(OTHER_TRANSACTION_ID, OTHER_PLAN_HASH)

DOCUMENT_OPERATION = {
    "serviceId": "documents",
    "action": "install",
    "definitionHash": "9" * 64,
}
VOICE_OPERATION = {
    "serviceId": "voice",
    "action": "install",
    "definitionHash": "a" * 64,
}


def grant(
    *,
    binding: ExecutionBinding = BINDING,
    service_ids: tuple[str, ...] = ("documents", "voice"),
) -> LeaseGrant:
    def request(_method, _path, **_kwargs):
        return {
            "schema": LEASE_SCHEMA,
            "leaseId": LEASE_ID,
            "leaseToken": LEASE_TOKEN,
            "transactionId": binding.transaction_id,
            "planHash": binding.plan_hash,
            "serviceIds": sorted(set(service_ids)),
            "ttlSeconds": 600,
        }

    return ExtensionLeaseClient(request).acquire(binding, service_ids, ttl_seconds=600)


def work_request(
    operation_key: str = "download-and-verify",
    service_ids: tuple[str, ...] = ("documents", "voice"),
    payload: dict | None = None,
    *,
    binding: ExecutionBinding = BINDING,
) -> LifecycleWorkRequest:
    if payload is None:
        payload = {"operations": [dict(DOCUMENT_OPERATION), dict(VOICE_OPERATION)]}
    unsigned = {
        "schema": REQUEST_SCHEMA,
        "transactionId": binding.transaction_id,
        "planHash": binding.plan_hash,
        "operationKey": operation_key,
        "serviceIds": list(service_ids),
        "payload": payload,
    }
    encoded = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return LifecycleWorkRequest(
        binding=binding,
        operation_key=operation_key,
        service_ids=service_ids,
        request_hash=hashlib.sha256(encoded).hexdigest(),
        payload=payload,
    )


def work_response(request: LifecycleWorkRequest | None = None, **changes) -> dict:
    request = request or work_request()
    value = {
        "schema": RESULT_SCHEMA,
        "transactionId": request.binding.transaction_id,
        "planHash": request.binding.plan_hash,
        "operationKey": request.operation_key,
        "requestHash": request.request_hash,
        "serviceIds": list(request.service_ids),
        "completed": True,
        "outcome": "completed",
        "evidenceHash": EVIDENCE_HASH,
    }
    value.update(changes)
    return value


def recording_client(response):
    calls = []

    def requester(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return response() if callable(response) else response

    return ExtensionLifecycleWorkClient(requester), calls


def test_client_sends_exact_leased_request_and_returns_only_evidence() -> None:
    request = work_request()
    client, calls = recording_client(work_response(request))

    result = client(grant(), request)

    assert result == LifecycleWorkResult(evidence_hash=EVIDENCE_HASH)
    assert len(calls) == 1
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", HOST_WORK_PATH)
    assert kwargs["timeout"] == 1800.0
    assert kwargs["max_response_bytes"] == MAX_RESPONSE_BYTES
    assert kwargs["payload"] == {
        "schema": REQUEST_SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "download-and-verify",
        "serviceIds": ["documents", "voice"],
        "payload": {"operations": [dict(DOCUMENT_OPERATION), dict(VOICE_OPERATION)]},
        "requestHash": request.request_hash,
        "lease": {
            "schema": LEASE_SCHEMA,
            "leaseId": LEASE_ID,
            "leaseToken": LEASE_TOKEN,
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
        },
    }
    assert LEASE_TOKEN not in repr(result)


@pytest.mark.parametrize(
    "operation_key,service_ids,payload,timeout",
    [
        (
            "reserve:documents",
            ("documents",),
            {"operation": dict(DOCUMENT_OPERATION)},
            30.0,
        ),
        (
            "apply:documents",
            ("documents",),
            {"operation": dict(DOCUMENT_OPERATION)},
            900.0,
        ),
        (
            "compensate:documents",
            ("documents",),
            {"operation": dict(DOCUMENT_OPERATION)},
            900.0,
        ),
        (
            "stage",
            ("documents", "voice"),
            {
                "operations": [
                    dict(DOCUMENT_OPERATION),
                    dict(VOICE_OPERATION),
                ]
            },
            600.0,
        ),
        (
            "backup",
            ("documents", "voice"),
            {"serviceIds": ["documents", "voice"]},
            600.0,
        ),
        (
            "configure",
            ("documents", "voice"),
            {"serviceIds": ["documents", "voice"]},
            600.0,
        ),
        (
            "verify",
            ("documents", "voice"),
            {"serviceIds": ["documents", "voice"]},
            600.0,
        ),
        (
            "restore",
            ("documents", "voice"),
            {"serviceIds": ["documents", "voice"]},
            600.0,
        ),
        (
            "release",
            ("documents", "voice"),
            {"serviceIds": ["documents", "voice"]},
            30.0,
        ),
    ],
)
def test_closed_operation_set_has_fixed_timeouts(
    operation_key, service_ids, payload, timeout
) -> None:
    request = work_request(operation_key, service_ids, payload)
    client, calls = recording_client(work_response(request))

    assert client.run(grant(), request).evidence_hash == EVIDENCE_HASH
    assert calls[0][2]["timeout"] == timeout


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        work_request("unknown", ("documents",), {"serviceIds": ["documents"]}),
        work_request(
            "apply:voice",
            ("documents",),
            {"operation": dict(DOCUMENT_OPERATION)},
        ),
        work_request(
            "apply:documents",
            ("documents", "voice"),
            {"operation": dict(DOCUMENT_OPERATION)},
        ),
        work_request(
            "apply:documents",
            ("documents",),
            {"serviceIds": ["documents"]},
        ),
        work_request(
            "apply:documents",
            ("documents",),
            {"operation": dict(VOICE_OPERATION)},
        ),
        work_request(
            "stage",
            ("documents", "voice"),
            {"operations": [dict(VOICE_OPERATION), dict(DOCUMENT_OPERATION)]},
        ),
        work_request(
            "backup",
            ("documents", "voice"),
            {"serviceIds": ["voice", "documents"]},
        ),
        work_request("backup", ("documents",), {"serviceIds": ["documents"], "x": 1}),
        work_request("backup", ("Bad ID",), {"serviceIds": ["Bad ID"]}),
        work_request(
            "backup",
            ("documents", "documents"),
            {"serviceIds": ["documents", "documents"]},
        ),
        work_request("backup", (), {"serviceIds": []}),
        work_request("backup", ("documents",), {"serviceIds": ["documents"], "x": 1.5}),
    ],
)
def test_invalid_requests_fail_before_lease_or_http(candidate) -> None:
    called = False

    def requester(*_args, **_kwargs):
        nonlocal called
        called = True
        return work_response()

    with pytest.raises(LifecycleHostWorkError) as caught:
        ExtensionLifecycleWorkClient(requester).run(grant(), candidate)
    assert caught.value.code == "host-work-invalid-request"
    assert caught.value.ambiguous is False
    assert called is False


def test_invalid_binding_and_request_hash_fail_before_http() -> None:
    invalid_binding = ExecutionBinding("bad-id", PLAN_HASH)
    bad_binding_request = LifecycleWorkRequest(
        binding=invalid_binding,
        operation_key="backup",
        service_ids=("documents",),
        request_hash="b" * 64,
        payload={"serviceIds": ["documents"]},
    )
    valid = work_request("backup", ("documents",), {"serviceIds": ["documents"]})
    bad_hash_request = LifecycleWorkRequest(
        binding=valid.binding,
        operation_key=valid.operation_key,
        service_ids=valid.service_ids,
        request_hash="b" * 64,
        payload=valid.payload,
    )
    wrong_binding_type = LifecycleWorkRequest(
        binding=object(),
        operation_key=valid.operation_key,
        service_ids=valid.service_ids,
        request_hash=valid.request_hash,
        payload=valid.payload,
    )
    unhashable_operation = LifecycleWorkRequest(
        binding=valid.binding,
        operation_key=[],
        service_ids=valid.service_ids,
        request_hash=valid.request_hash,
        payload=valid.payload,
    )

    for request, code in (
        (bad_binding_request, "host-work-invalid-request"),
        (bad_hash_request, "host-work-request-hash-mismatch"),
        (wrong_binding_type, "host-work-invalid-request"),
        (unhashable_operation, "host-work-invalid-request"),
    ):
        called = False

        def requester(*_args, **_kwargs):
            nonlocal called
            called = True
            return work_response()

        with pytest.raises(LifecycleHostWorkError) as caught:
            ExtensionLifecycleWorkClient(requester).run(grant(), request)
        assert caught.value.code == code
        assert called is False


def test_oversized_request_fails_before_http() -> None:
    operation = {**DOCUMENT_OPERATION, "padding": "x" * (40 * 1024)}
    request = work_request(
        "apply:documents",
        ("documents",),
        {"operation": operation},
    )
    called = False

    def requester(*_args, **_kwargs):
        nonlocal called
        called = True
        return work_response()

    with pytest.raises(LifecycleHostWorkError) as caught:
        ExtensionLifecycleWorkClient(requester).run(grant(), request)
    assert caught.value.code == "host-work-request-size"
    assert called is False


def test_lease_must_match_binding_and_cover_every_service() -> None:
    request = work_request(
        "backup", ("documents", "voice"), {"serviceIds": ["documents", "voice"]}
    )
    for bad_grant in (
        grant(binding=OTHER_BINDING),
        grant(service_ids=("documents",)),
        object(),
    ):
        client, calls = recording_client(work_response(request))
        with pytest.raises(LifecycleHostWorkError) as caught:
            client.run(bad_grant, request)
        assert caught.value.code == "host-work-invalid-lease"
        assert caught.value.ambiguous is False
        assert calls == []
        assert LEASE_TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"extra": True}, "host-work-invalid-response"),
        ({"schema": "wrong"}, "host-work-invalid-response"),
        ({"transactionId": OTHER_TRANSACTION_ID}, "host-work-binding-mismatch"),
        ({"planHash": OTHER_PLAN_HASH}, "host-work-binding-mismatch"),
        ({"operationKey": "stage"}, "host-work-binding-mismatch"),
        ({"requestHash": "b" * 64}, "host-work-binding-mismatch"),
        ({"serviceIds": ["voice", "documents"]}, "host-work-binding-mismatch"),
        ({"completed": False}, "host-work-invalid-response"),
        ({"completed": 1}, "host-work-invalid-response"),
        ({"outcome": "failed"}, "host-work-invalid-response"),
        ({"evidenceHash": OTHER_EVIDENCE_HASH[:-1]}, "host-work-invalid-response"),
        ({"evidenceHash": None}, "host-work-invalid-response"),
    ],
)
def test_non_exact_or_misbound_success_is_ambiguous(changes, code) -> None:
    request = work_request()
    client, _calls = recording_client(work_response(request, **changes))
    with pytest.raises(LifecycleHostWorkError) as caught:
        client.run(grant(), request)
    assert caught.value.code == code
    assert caught.value.ambiguous is True
    assert caught.value.retryable is False


@pytest.mark.parametrize("response", [None, [], "ok", 1])
def test_non_object_success_is_ambiguous(response) -> None:
    client, _calls = recording_client(response)
    with pytest.raises(LifecycleHostWorkError) as caught:
        client.run(grant(), work_request())
    assert caught.value.code == "host-work-invalid-response"
    assert caught.value.ambiguous is True


@pytest.mark.parametrize(
    "status_code,host_code,expected,retryable,ambiguous",
    [
        (401, None, "host-work-host-auth", False, False),
        (403, "lease-token-mismatch", "lease-token-mismatch", False, False),
        (404, None, "host-work-boundary-disabled", False, False),
        (409, "service-lock-busy", "service-lock-busy", True, False),
        (409, "private-conflict", "host-work-conflict", False, False),
        (410, "lease-not-active", "lease-not-active", False, False),
        (415, "private-validation", "host-work-invalid-request", False, False),
        (422, "private-validation", "host-work-invalid-request", False, False),
        (500, "private-failure", "host-work-operation-ambiguous", False, True),
        (503, None, "host-work-operation-ambiguous", False, True),
    ],
)
def test_http_failures_have_stable_public_classification(
    status_code, host_code, expected, retryable, ambiguous
) -> None:
    detail = json.dumps({"code": host_code}) if host_code else LEASE_TOKEN

    def requester(*_args, **_kwargs):
        raise AgentHTTPError(status_code, detail, LEASE_TOKEN)

    with pytest.raises(LifecycleHostWorkError) as caught:
        ExtensionLifecycleWorkClient(requester).run(grant(), work_request())
    assert caught.value.code == expected
    assert caught.value.retryable is retryable
    assert caught.value.ambiguous is ambiguous
    rendered = "".join(traceback.format_exception(caught.value))
    assert LEASE_TOKEN not in str(caught.value)
    assert LEASE_TOKEN not in repr(caught.value)
    assert LEASE_TOKEN not in rendered


@pytest.mark.parametrize(
    "error",
    [
        AgentTimeout(LEASE_TOKEN),
        AgentUnavailable(LEASE_TOKEN),
        AgentProtocolError(LEASE_TOKEN),
        RuntimeError(LEASE_TOKEN),
    ],
)
def test_transport_and_unexpected_failures_are_ambiguous_and_redacted(
    error,
) -> None:
    def requester(*_args, **_kwargs):
        raise error

    with pytest.raises(LifecycleHostWorkError) as caught:
        ExtensionLifecycleWorkClient(requester).run(grant(), work_request())
    assert caught.value.code == "host-work-operation-ambiguous"
    assert caught.value.retryable is False
    assert caught.value.ambiguous is True
    assert LEASE_TOKEN not in "".join(traceback.format_exception(caught.value))


def test_default_transport_rejects_http_202_as_ambiguous(monkeypatch) -> None:
    request = work_request()
    client = httpx.Client(
        base_url="http://agent",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(202, json=work_response(request))
        ),
    )
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(LifecycleHostWorkError) as caught:
            ExtensionLifecycleWorkClient().run(grant(), request)
        assert caught.value.code == "host-work-operation-ambiguous"
        assert caught.value.ambiguous is True
        assert caught.value.retryable is False
    finally:
        client.close()


@pytest.mark.parametrize(
    "content,content_type",
    [
        (b'{"schema":"x","schema":"y"}', "application/json"),
        (b'{"schema":"x"} trailing', "application/json"),
        (b"\xff", "application/json"),
        (b"{}", "text/plain"),
    ],
)
def test_default_transport_rejects_ambiguous_wire_payloads(
    monkeypatch, content, content_type
) -> None:
    client = httpx.Client(
        base_url="http://agent",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=content,
                headers={"content-type": content_type},
            )
        ),
    )
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(LifecycleHostWorkError) as caught:
            ExtensionLifecycleWorkClient().run(grant(), work_request())
        assert caught.value.code == "host-work-operation-ambiguous"
        assert caught.value.ambiguous is True
    finally:
        client.close()


def test_request_and_grant_representations_redact_sensitive_values() -> None:
    request = work_request(
        "configure",
        ("documents",),
        {"serviceIds": ["documents"], "secretReference": LEASE_TOKEN},
    )
    held_grant = grant(service_ids=("documents",))
    for rendered in (repr(request), str(request), repr(held_grant), str(held_grant)):
        assert LEASE_TOKEN not in rendered
    assert "payload=<redacted>" in repr(request)
    assert "lease_token=<redacted>" in repr(held_grant)


def test_client_has_no_logging_persistence_retry_or_production_importer() -> None:
    source_root = Path(__file__).resolve().parents[1]
    module = source_root / "extension_lifecycle_work_client.py"
    text = module.read_text(encoding="utf-8")
    for forbidden in (
        "import logging",
        "logging.",
        "logger =",
        "print(",
        "open(",
        "sleep(",
        "AGENT_URL",
    ):
        assert forbidden not in text

    importers = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts
        and path != module
        and "extension_lifecycle_work_client" in path.read_text(encoding="utf-8")
    }
    assert importers == set()

    production = (source_root / "extension_transaction_production.py").read_text(
        encoding="utf-8"
    )
    assert "executor=None" in production
