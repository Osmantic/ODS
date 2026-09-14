"""Adversarial tests for the Phase 5G-C lifecycle-receipt client."""

from __future__ import annotations

import json
import traceback
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

import host_agent_client as agent_client
from extension_lifecycle_receipt_client import (
    MAX_RESPONSE_BYTES,
    ExtensionLifecycleReceiptClient,
    LifecycleReceiptClientError,
    ReceiptResult,
    SnapshotResult,
)
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
REQUEST_HASH = "5" * 64
OTHER_REQUEST_HASH = "6" * 64
EVIDENCE_HASH = "7" * 64
OTHER_EVIDENCE_HASH = "8" * 64
SERVICE_IDS = ["documents", "voice"]
SCHEMA = "ods.extension-lifecycle-receipt-api.v1"
HOST_KEY = "synthetic-receipt-host-key"

BEGIN_PATH = "/v1/extension/lifecycle-receipt/begin"
FINISH_PATH = "/v1/extension/lifecycle-receipt/finish"
SNAPSHOT_PATH = "/v1/extension/lifecycle-receipt/snapshot"

RECEIPT_RESPONSE_KEYS = frozenset({
    "schema", "kind", "transactionId", "planHash", "operationKey",
    "requestHash", "serviceIds", "eventHash", "outcome", "evidenceHash",
    "startedEventHash",
})
SNAPSHOT_RESPONSE_KEYS = frozenset({
    "schema", "transactionId", "planHash", "operationKey", "state",
    "startedReceipt", "terminalReceipt",
})


def begin_request(**changes) -> dict:
    value = {
        "schema": SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "stage",
        "requestHash": REQUEST_HASH,
        "serviceIds": list(SERVICE_IDS),
    }
    value.update(changes)
    return value


def finish_request(**changes) -> dict:
    value = begin_request()
    value.update({"outcome": "completed", "evidenceHash": EVIDENCE_HASH})
    value.update(changes)
    return value


def snapshot_request(**changes) -> dict:
    value = {
        "schema": SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "stage",
    }
    value.update(changes)
    return value


def started_response(**changes) -> dict:
    value = {
        "schema": SCHEMA,
        "kind": "started",
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "stage",
        "requestHash": REQUEST_HASH,
        "serviceIds": list(SERVICE_IDS),
        "eventHash": "a" * 64,
        "outcome": None,
        "evidenceHash": None,
        "startedEventHash": None,
    }
    value.update(changes)
    return value


def terminal_response(**changes) -> dict:
    value = {
        "schema": SCHEMA,
        "kind": "terminal",
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "stage",
        "requestHash": REQUEST_HASH,
        "serviceIds": list(SERVICE_IDS),
        "eventHash": "b" * 64,
        "outcome": "completed",
        "evidenceHash": EVIDENCE_HASH,
        "startedEventHash": "a" * 64,
    }
    value.update(changes)
    return value


def snapshot_response(**changes) -> dict:
    value = {
        "schema": SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "stage",
        "state": "absent",
        "startedReceipt": None,
        "terminalReceipt": None,
    }
    value.update(changes)
    return value


def recording_client(response):
    """Return (client, calls) where the client always answers ``response``."""
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return response() if callable(response) else response

    return ExtensionLifecycleReceiptClient(request), calls


def test_begin_sends_exact_bounded_post_and_returns_started() -> None:
    client, calls = recording_client(started_response())

    result = client.begin(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
    )

    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", BEGIN_PATH)
    assert kwargs["timeout"] == 10.0
    assert kwargs["max_response_bytes"] == MAX_RESPONSE_BYTES
    assert kwargs["payload"] == begin_request()
    assert isinstance(result, ReceiptResult)
    assert result.kind == "started"
    assert result.transaction_id == TRANSACTION_ID
    assert result.plan_hash == PLAN_HASH
    assert result.operation_key == "stage"
    assert result.request_hash == REQUEST_HASH
    assert result.service_ids == tuple(SERVICE_IDS)
    assert result.event_hash == "a" * 64
    assert result.outcome is None
    assert result.evidence_hash is None
    assert result.started_event_hash is None


def test_begin_converges_on_a_terminal_response() -> None:
    client, _calls = recording_client(terminal_response())

    result = client.begin(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
    )
    assert result.kind == "terminal"
    assert result.outcome == "completed"
    assert result.evidence_hash == EVIDENCE_HASH
    assert result.started_event_hash == "a" * 64


def test_finish_sends_exact_bounded_post_and_returns_terminal() -> None:
    client, calls = recording_client(terminal_response())

    result = client.finish(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS),
        "completed", EVIDENCE_HASH,
    )

    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", FINISH_PATH)
    assert kwargs["timeout"] == 10.0
    assert kwargs["max_response_bytes"] == MAX_RESPONSE_BYTES
    assert kwargs["payload"] == finish_request()
    assert isinstance(result, ReceiptResult)
    assert result.kind == "terminal"
    assert result.outcome == "completed"
    assert result.started_event_hash == "a" * 64


def test_snapshot_absent_preserves_none_semantics() -> None:
    client, calls = recording_client(snapshot_response())

    result = client.snapshot(TRANSACTION_ID, PLAN_HASH, "stage")

    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", SNAPSHOT_PATH)
    assert kwargs["timeout"] == 5.0
    assert kwargs["max_response_bytes"] == MAX_RESPONSE_BYTES
    assert kwargs["payload"] == snapshot_request()
    assert isinstance(result, SnapshotResult)
    assert result.state == "absent"
    assert result.started_receipt is None
    assert result.terminal_receipt is None
    # Absent snapshot preserves the requested binding verbatim.
    assert result.transaction_id == TRANSACTION_ID
    assert result.plan_hash == PLAN_HASH
    assert result.operation_key == "stage"


def test_snapshot_started_completed_and_failed_states() -> None:
    started_nested = {
        key: value
        for key, value in started_response().items()
        if key in RECEIPT_RESPONSE_KEYS
    }
    terminal_nested = {
        key: value
        for key, value in terminal_response().items()
        if key in RECEIPT_RESPONSE_KEYS
    }

    client, _calls = recording_client(
        snapshot_response(state="started", startedReceipt=started_nested)
    )
    result = client.snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert result.state == "started"
    assert result.started_receipt is not None
    assert result.terminal_receipt is None

    client, _calls = recording_client(
        snapshot_response(
            state="completed",
            startedReceipt=started_nested,
            terminalReceipt=terminal_nested,
        )
    )
    result = client.snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert result.state == "completed"
    assert result.started_receipt is not None
    assert result.terminal_receipt is not None
    assert result.terminal_receipt.started_event_hash == "a" * 64

    failed_nested = dict(terminal_nested)
    failed_nested["outcome"] = "failed"
    failed_nested["eventHash"] = "c" * 64
    failed_nested["evidenceHash"] = OTHER_EVIDENCE_HASH
    client, _calls = recording_client(
        snapshot_response(
            state="failed",
            startedReceipt=started_nested,
            terminalReceipt=failed_nested,
        )
    )
    result = client.snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert result.state == "failed"
    assert result.terminal_receipt.outcome == "failed"


def test_invalid_request_values_are_rejected_before_any_http_call() -> None:
    cases = [
        ("begin", ("bad-id", PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)), {}),
        ("begin", (TRANSACTION_ID, "short", "stage", REQUEST_HASH, tuple(SERVICE_IDS)), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "install", REQUEST_HASH, tuple(SERVICE_IDS)), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", "short", tuple(SERVICE_IDS)), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, ()), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, "documents"), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, {"documents"}), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, iter(SERVICE_IDS)), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, ("Bad ID",)), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, ("a", "a")), {}),
        ("begin", (TRANSACTION_ID, PLAN_HASH, "apply:missing", REQUEST_HASH, tuple(SERVICE_IDS)), {}),
        ("finish", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS), "unknown", EVIDENCE_HASH), {}),
        ("finish", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS), [], EVIDENCE_HASH), {}),
        ("finish", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS), "completed", "short"), {}),
        ("snapshot", ("bad-id", PLAN_HASH, "stage"), {}),
        ("snapshot", (TRANSACTION_ID, "short", "stage"), {}),
        ("snapshot", (TRANSACTION_ID, PLAN_HASH, "install"), {}),
    ]
    for method, args, _kwargs in cases:
        called = False

        def request(*_args, **_kwargs):
            nonlocal called
            called = True
            return started_response()

        client = ExtensionLifecycleReceiptClient(request)
        with pytest.raises(LifecycleReceiptClientError) as caught:
            getattr(client, method)(*args)
        assert caught.value.code == "receipt-invalid-request"
        assert called is False, (method, args)


def test_snapshot_allows_per_service_operation_key_without_service_list() -> None:
    # Snapshot has no service list, so per-service keys are only checked
    # structurally; the host is authoritative for the durable binding.
    client, calls = recording_client(snapshot_response(operationKey="reserve:voice"))
    result = client.snapshot(TRANSACTION_ID, PLAN_HASH, "reserve:voice")
    assert result.operation_key == "reserve:voice"
    assert calls[0][2]["payload"]["operationKey"] == "reserve:voice"


def test_service_ids_follow_the_catalog_and_host_namespace() -> None:
    client, _calls = recording_client(
        started_response(operationKey="apply:a--b", serviceIds=["a--b"])
    )
    result = client.begin(
        TRANSACTION_ID, PLAN_HASH, "apply:a--b", REQUEST_HASH, ("a--b",)
    )
    assert result.service_ids == ("a--b",)

    client, _calls = recording_client(started_response(serviceIds=["a.b"]))
    with pytest.raises(LifecycleReceiptClientError) as caught:
        client.begin(TRANSACTION_ID, PLAN_HASH, "apply:a.b", REQUEST_HASH, ("a.b",))
    assert caught.value.code == "receipt-invalid-request"


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"extra": True}, "receipt-invalid-response"),
        ({"schema": "wrong"}, "receipt-invalid-response"),
        ({"kind": "terminal"}, "receipt-invalid-response"),
        ({"kind": []}, "receipt-invalid-response"),
        ({"transactionId": OTHER_TRANSACTION_ID}, "receipt-binding-mismatch"),
        ({"planHash": OTHER_PLAN_HASH}, "receipt-binding-mismatch"),
        ({"operationKey": "verify"}, "receipt-binding-mismatch"),
        ({"requestHash": OTHER_REQUEST_HASH}, "receipt-binding-mismatch"),
        ({"serviceIds": ["voice", "documents"]}, "receipt-binding-mismatch"),
        ({"serviceIds": ["documents"]}, "receipt-binding-mismatch"),
        ({"eventHash": "short"}, "receipt-invalid-response"),
        ({"eventHash": None}, "receipt-invalid-response"),
        ({"outcome": "unknown"}, "receipt-invalid-response"),
        ({"outcome": []}, "receipt-invalid-response"),
        ({"evidenceHash": "short"}, "receipt-invalid-response"),
        ({"startedEventHash": "short"}, "receipt-invalid-response"),
    ],
)
def test_begin_rejects_non_exact_or_misbound_responses(changes, code) -> None:
    response = started_response(**changes)
    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(
            lambda *_args, **_kwargs: response
        ).begin(
            TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
        )
    assert caught.value.code == code


def test_finish_rejects_non_exact_or_misbound_responses() -> None:
    for changes, code in (
        ({"kind": "started"}, "receipt-invalid-response"),
        ({"outcome": "unknown"}, "receipt-invalid-response"),
        ({"outcome": "failed"}, "receipt-binding-mismatch"),
        ({"startedEventHash": None}, "receipt-invalid-response"),
        ({"evidenceHash": None}, "receipt-invalid-response"),
        ({"evidenceHash": OTHER_EVIDENCE_HASH}, "receipt-binding-mismatch"),
    ):
        with pytest.raises(LifecycleReceiptClientError) as caught:
            ExtensionLifecycleReceiptClient(
                lambda *_args, **_kwargs: terminal_response(**changes)
            ).finish(
                TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS),
                "completed", EVIDENCE_HASH,
            )
        assert caught.value.code == code


def test_snapshot_rejects_non_exact_or_misbound_responses() -> None:
    for changes, code in (
        ({"extra": True}, "receipt-invalid-response"),
        ({"schema": "wrong"}, "receipt-invalid-response"),
        ({"transactionId": OTHER_TRANSACTION_ID}, "receipt-binding-mismatch"),
        ({"planHash": OTHER_PLAN_HASH}, "receipt-binding-mismatch"),
        ({"operationKey": "verify"}, "receipt-binding-mismatch"),
        ({"state": "unknown"}, "receipt-invalid-response"),
        ({"state": []}, "receipt-invalid-response"),
        ({"state": "started", "startedReceipt": None}, "receipt-invalid-response"),
        (
            {"state": "started", "terminalReceipt": started_response()},
            "receipt-invalid-response",
        ),
        (
            {
                "state": "completed",
                "startedReceipt": None,
                "terminalReceipt": terminal_response(),
            },
            "receipt-invalid-response",
        ),
        (
            {
                "state": "failed",
                "startedReceipt": started_response(),
                "terminalReceipt": None,
            },
            "receipt-invalid-response",
        ),
        # Chain divergence: terminal must chain the started event.
        (
            {
                "state": "completed",
                "startedReceipt": started_response(),
                "terminalReceipt": terminal_response(startedEventHash="e" * 64),
            },
            "receipt-binding-mismatch",
        ),
    ):
        with pytest.raises(LifecycleReceiptClientError) as caught:
            ExtensionLifecycleReceiptClient(
                lambda *_args, **_kwargs: snapshot_response(**changes)
            ).snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
        assert caught.value.code == code


def test_snapshot_nested_receipts_must_match_state_shape() -> None:
    # started state with a terminal nested receipt is invalid.
    bad_started = snapshot_response(
        state="started", startedReceipt=terminal_response()
    )
    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(
            lambda *_args, **_kwargs: bad_started
        ).snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert caught.value.code == "receipt-invalid-response"

    # A started snapshot cannot substitute a terminal receipt for its start.
    bad_kind = snapshot_response(
        state="started",
        startedReceipt={
            key: value
            for key, value in terminal_response().items()
            if key in RECEIPT_RESPONSE_KEYS
        },
    )
    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(
            lambda *_args, **_kwargs: bad_kind
        ).snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert caught.value.code == "receipt-invalid-response"


@pytest.mark.parametrize(
    "field,value",
    [
        ("transactionId", OTHER_TRANSACTION_ID),
        ("planHash", OTHER_PLAN_HASH),
        ("operationKey", "verify"),
        ("requestHash", OTHER_REQUEST_HASH),
        ("serviceIds", ["voice", "documents"]),
    ],
)
def test_snapshot_rejects_nested_binding_divergence(field, value) -> None:
    started = started_response()
    terminal = terminal_response()
    started[field] = value
    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(
            lambda *_args, **_kwargs: snapshot_response(
                state="completed",
                startedReceipt=started,
                terminalReceipt=terminal,
            )
        ).snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert caught.value.code == "receipt-binding-mismatch"


def test_snapshot_terminal_must_match_started_binding_and_state() -> None:
    for changes in (
        {"requestHash": OTHER_REQUEST_HASH},
        {"serviceIds": ["voice", "documents"]},
        {"outcome": "failed"},
    ):
        with pytest.raises(LifecycleReceiptClientError) as caught:
            ExtensionLifecycleReceiptClient(
                lambda *_args, **_kwargs: snapshot_response(
                    state="completed",
                    startedReceipt=started_response(),
                    terminalReceipt=terminal_response(**changes),
                )
            ).snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
        assert caught.value.code == "receipt-binding-mismatch"

def test_non_object_and_invalid_json_responses_fail_closed() -> None:
    class NonObjectResponse:
        pass

    for bad in (
        NonObjectResponse(),
        [],
        "string",
        12345,
        None,
    ):
        with pytest.raises(LifecycleReceiptClientError) as caught:
            ExtensionLifecycleReceiptClient(
                lambda *_args, **_kwargs: bad
            ).begin(
                TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
            )
        assert caught.value.code == "receipt-invalid-response"


@pytest.mark.parametrize(
    "status_code, host_code, expected, retryable, ambiguous",
    [
        (401, None, "receipt-host-auth", False, False),
        (403, None, "receipt-host-auth", False, False),
        (404, None, "receipt-boundary-disabled", False, False),
        (409, "lifecycle-receipt-conflict", "receipt-conflict", False, False),
        (409, "lifecycle-receipt-integrity", "receipt-integrity", False, False),
        (409, None, "receipt-conflict", False, False),
        (413, "lifecycle-receipt-request-size", "receipt-invalid-request", False, False),
        (422, "invalid-lifecycle-receipt", "receipt-invalid-request", False, False),
        (
            422,
            "invalid-lifecycle-receipt-request",
            "receipt-invalid-request",
            False,
            False,
        ),
        (503, "lifecycle-receipt-store-unavailable", "receipt-operation-ambiguous", False, True),
        (503, "lifecycle-receipt-durability-unavailable", "receipt-operation-ambiguous", False, True),
        (500, None, "receipt-operation-ambiguous", False, True),
        (502, None, "receipt-operation-ambiguous", False, True),
    ],
)
def test_http_failures_have_stable_classification(
    status_code, host_code, expected, retryable, ambiguous
) -> None:
    detail = json.dumps({"code": host_code}) if host_code else "private-host-detail"

    def request(*_args, **_kwargs):
        raise AgentHTTPError(status_code, detail, HOST_KEY)

    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(request).begin(
            TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
        )
    assert caught.value.code == expected
    assert caught.value.retryable is retryable
    assert caught.value.ambiguous is ambiguous
    assert HOST_KEY not in str(caught.value)
    assert HOST_KEY not in repr(caught.value)
    assert HOST_KEY not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "error",
    [
        AgentTimeout(HOST_KEY),
        AgentUnavailable(HOST_KEY),
        AgentProtocolError(HOST_KEY),
    ],
)
def test_mutation_transport_failures_are_ambiguous_and_never_retryable(error) -> None:
    def request(*_args, **_kwargs):
        raise error

    for method, args in (
        ("begin", (TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS))),
        (
            "finish",
            (
                TRANSACTION_ID,
                PLAN_HASH,
                "stage",
                REQUEST_HASH,
                tuple(SERVICE_IDS),
                "completed",
                EVIDENCE_HASH,
            ),
        ),
    ):
        with pytest.raises(LifecycleReceiptClientError) as caught:
            getattr(
                ExtensionLifecycleReceiptClient(request), method)(*args)
        assert caught.value.code == "receipt-operation-ambiguous"
        assert caught.value.ambiguous is True
        assert caught.value.retryable is False
        assert HOST_KEY not in str(caught.value)


@pytest.mark.parametrize("error", [AgentTimeout(HOST_KEY), AgentUnavailable(HOST_KEY)])
def test_snapshot_unavailability_is_retryable_and_not_ambiguous(error) -> None:
    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
        ).snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert caught.value.code == "receipt-unavailable"
    assert caught.value.retryable is True
    assert caught.value.ambiguous is False


def test_snapshot_protocol_failure_is_invalid_and_not_ambiguous() -> None:
    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AgentProtocolError(HOST_KEY))
        ).snapshot(TRANSACTION_ID, PLAN_HASH, "stage")
    assert caught.value.code == "receipt-invalid-response"
    assert caught.value.retryable is False
    assert caught.value.ambiguous is False


@pytest.mark.parametrize(
    "status_code,host_code",
    [
        (500, None),
        (503, "lifecycle-receipt-store-unavailable"),
        (503, "lifecycle-receipt-durability-unavailable"),
    ],
)
def test_snapshot_server_unavailability_is_retryable(status_code, host_code) -> None:
    detail = json.dumps({"code": host_code}) if host_code else "private-host-detail"

    def request(*_args, **_kwargs):
        raise AgentHTTPError(status_code, detail, HOST_KEY)

    with pytest.raises(LifecycleReceiptClientError) as caught:
        ExtensionLifecycleReceiptClient(request).snapshot(
            TRANSACTION_ID, PLAN_HASH, "stage"
        )
    assert caught.value.code == "receipt-unavailable"
    assert caught.value.retryable is True
    assert caught.value.ambiguous is False


def test_default_transport_rejects_http_202_as_ambiguous(monkeypatch) -> None:
    client = httpx.Client(
        base_url="http://agent",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(202, json=started_response())
        ),
    )
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(LifecycleReceiptClientError) as caught:
            ExtensionLifecycleReceiptClient().begin(
                TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
            )
        assert caught.value.code == "receipt-operation-ambiguous"
        assert caught.value.ambiguous is True
        assert caught.value.retryable is False
    finally:
        client.close()


def test_default_transport_rejects_invalid_json_and_oversize(monkeypatch) -> None:
    # Invalid JSON
    client = httpx.Client(
        base_url="http://agent",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"not json",
            )
        ),
    )
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(LifecycleReceiptClientError) as caught:
            ExtensionLifecycleReceiptClient().begin(
                TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
            )
        assert caught.value.code == "receipt-operation-ambiguous"
    finally:
        client.close()

    # Oversize body
    big = json.dumps(started_response()).encode() + b"x" * (MAX_RESPONSE_BYTES + 1)
    client = httpx.Client(
        base_url="http://agent",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=big,
            )
        ),
    )
    monkeypatch.setattr(agent_client, "_sync_client", client)
    try:
        with pytest.raises(LifecycleReceiptClientError) as caught:
            ExtensionLifecycleReceiptClient().begin(
                TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
            )
        assert caught.value.code == "receipt-operation-ambiguous"
    finally:
        client.close()


def test_error_messages_do_not_leak_payloads_paths_or_exception_text() -> None:
    secret = "synthetic-host-secret-value"
    marker = "private-os-detail-9812"

    def request(*_args, **_kwargs):
        raise AgentHTTPError(409, json.dumps({"code": "lifecycle-receipt-conflict"}), secret)

    try:
        ExtensionLifecycleReceiptClient(request).begin(
            TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
        )
    except LifecycleReceiptClientError as caught:
        text = str(caught) + repr(caught)
        assert secret not in text
        assert "lifecycle-receipt-conflict" not in text
        assert marker not in text

    def request2(*_args, **_kwargs):
        try:
            raise RuntimeError(f"{marker} /etc/ods/paths")
        except RuntimeError as inner:
            raise AgentUnavailable("private message") from inner

    try:
        ExtensionLifecycleReceiptClient(request2).snapshot(
            TRANSACTION_ID, PLAN_HASH, "stage"
        )
    except LifecycleReceiptClientError as caught:
        text = str(caught) + repr(caught)
        assert marker not in text
        assert "/etc/ods" not in text
        assert "private message" not in text
        assert "RuntimeError" not in text


def test_result_records_do_not_expose_secrets() -> None:
    client, _calls = recording_client(started_response())
    result = client.begin(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, tuple(SERVICE_IDS)
    )
    rendered = str(result) + repr(result) + json.dumps(asdict(result))
    assert HOST_KEY not in rendered
    assert isinstance(result, ReceiptResult)


def test_client_module_has_no_production_wiring_or_filesystem_writes() -> None:
    source_root = Path(__file__).resolve().parents[1]
    module = source_root / "extension_lifecycle_receipt_client.py"
    text = module.read_text(encoding="utf-8")
    for forbidden in (
        "import logging",
        "logging.",
        "logger =",
        "print(",
        "open(",
        "os.",
        "pathlib",
        "Path(",
        "AGENT_URL",
        "ODS_AGENT_KEY",
        "threading",
        "asyncio",
        "subprocess",
    ):
        assert forbidden not in text, forbidden

    importers = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts
        and path != module
        and "extension_lifecycle_receipt_client" in path.read_text(encoding="utf-8")
    }
    assert importers == set()


def test_executor_and_production_modules_do_not_import_the_client() -> None:
    source_root = Path(__file__).resolve().parents[1]
    for module_name in (
        "extension_transaction_executor.py",
        "extension_transaction_production.py",
        "extension_transaction_runtime.py",
        "extension_transactions.py",
        "extension_transaction_finalizer.py",
        "extension_lease_renewer.py",
    ):
        source = (source_root / module_name).read_text(
            encoding="utf-8", errors="ignore"
        )
        assert "extension_lifecycle_receipt_client" not in source, module_name
    production = (source_root / "extension_transaction_production.py").read_text(
        encoding="utf-8"
    )
    assert "executor=None" in production
