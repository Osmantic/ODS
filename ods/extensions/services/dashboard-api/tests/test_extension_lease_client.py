from __future__ import annotations

import json
import traceback
from dataclasses import asdict
from pathlib import Path

import pytest

from extension_lease_client import (
    MAX_RESPONSE_BYTES,
    ExtensionLeaseClient,
    ExtensionLeaseError,
    LeaseGrant,
)
from extension_transaction_executor import ExecutionBinding
from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentTimeout,
    AgentUnavailable,
)

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
LEASE_ID = "lease-" + "3" * 32
LEASE_TOKEN = "private-lease-token-" + "4" * 32
BINDING = ExecutionBinding(TRANSACTION_ID, PLAN_HASH)
SERVICE_IDS = ("aider", "ollama")


def acquire_response(**changes) -> dict:
    response = {
        "schema": "ods.extension-operation-lease.v1",
        "leaseId": LEASE_ID,
        "leaseToken": LEASE_TOKEN,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": list(SERVICE_IDS),
        "ttlSeconds": 600,
    }
    response.update(changes)
    return response


def public_response(*, ttl: bool = False, **changes) -> dict:
    response = {
        "schema": "ods.extension-operation-lease.v1",
        "leaseId": LEASE_ID,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": list(SERVICE_IDS),
        "active": False,
    }
    if ttl:
        response["ttlSeconds"] = 600
    response.update(changes)
    return response


def grant() -> LeaseGrant:
    return ExtensionLeaseClient(lambda *_args, **_kwargs: acquire_response()).acquire(
        BINDING, reversed(SERVICE_IDS)
    )


def test_acquire_uses_exact_bounded_post_and_returns_redacted_grant() -> None:
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return acquire_response()

    result = ExtensionLeaseClient(request).acquire(
        BINDING, ["ollama", "aider", "ollama"]
    )

    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/v1/extension/lease/acquire")
    assert kwargs["timeout"] == 10.0
    assert kwargs["max_response_bytes"] == MAX_RESPONSE_BYTES
    assert kwargs["payload"] == {
        "schema": "ods.extension-operation-lease.v1",
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": list(SERVICE_IDS),
        "ttlSeconds": 600,
    }
    assert result.binding is BINDING
    assert result.service_ids == SERVICE_IDS
    assert LEASE_TOKEN not in str(result)
    assert LEASE_TOKEN not in repr(result)
    assert LEASE_TOKEN not in repr(asdict(result))


def test_renew_status_and_release_keep_token_in_body_only() -> None:
    current = grant()
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        operation = path.rsplit("/", 1)[-1]
        if operation == "renew":
            return public_response(ttl=True)
        if operation == "status":
            return public_response(active=True)
        return {
            "schema": "ods.extension-operation-lease.v1",
            "leaseId": LEASE_ID,
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "released": True,
        }

    client = ExtensionLeaseClient(request)
    renewed = client.renew(current)
    status = client.status(renewed)
    released = client.release(renewed)

    assert renewed.binding is BINDING
    assert status.active is True
    assert released.released is True
    assert [call[1].rsplit("/", 1)[-1] for call in calls] == [
        "renew",
        "status",
        "release",
    ]
    for _method, path, kwargs in calls:
        assert LEASE_TOKEN not in path
        assert kwargs["payload"]["leaseToken"] == LEASE_TOKEN
        assert kwargs["max_response_bytes"] == MAX_RESPONSE_BYTES


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"extra": True}, "lease-invalid-response"),
        ({"schema": "wrong"}, "lease-invalid-response"),
        ({"transactionId": "txn-" + "9" * 24}, "lease-binding-mismatch"),
        ({"planHash": "9" * 64}, "lease-binding-mismatch"),
        ({"serviceIds": ["aider"]}, "lease-binding-mismatch"),
        ({"leaseId": "bad"}, "lease-invalid-response"),
        ({"leaseToken": "short"}, "lease-invalid-response"),
        ({"ttlSeconds": True}, "lease-invalid-response"),
        ({"ttlSeconds": 601}, "lease-binding-mismatch"),
    ],
)
def test_acquire_rejects_non_exact_or_misbound_response(changes, code) -> None:
    response = acquire_response(**changes)
    with pytest.raises(ExtensionLeaseError) as caught:
        ExtensionLeaseClient(lambda *_args, **_kwargs: response).acquire(
            BINDING, SERVICE_IDS
        )
    assert caught.value.code == code


@pytest.mark.parametrize(
    "value",
    [
        ExecutionBinding("not-canonical", PLAN_HASH),
        object(),
    ],
)
def test_invalid_binding_is_rejected_before_request(value) -> None:
    called = False

    def request(*_args, **_kwargs):
        nonlocal called
        called = True
        return acquire_response()

    with pytest.raises(ExtensionLeaseError, match="lease-invalid-binding"):
        ExtensionLeaseClient(request).acquire(value, SERVICE_IDS)
    assert called is False


@pytest.mark.parametrize(
    "service_ids, ttl",
    [
        ([], 600),
        ("aider", 600),
        (["Bad ID"], 600),
        (["a" * 129], 600),
        ([f"service-{index}" for index in range(129)], 600),
        (SERVICE_IDS, True),
        (SERVICE_IDS, 0),
        (SERVICE_IDS, 3601),
    ],
)
def test_invalid_acquire_values_are_rejected_before_request(service_ids, ttl) -> None:
    called = False

    def request(*_args, **_kwargs):
        nonlocal called
        called = True
        return acquire_response()

    with pytest.raises(ExtensionLeaseError):
        ExtensionLeaseClient(request).acquire(BINDING, service_ids, ttl_seconds=ttl)
    assert called is False


@pytest.mark.parametrize(
    "status_code, host_code, expected, retryable, ambiguous",
    [
        (401, None, "lease-host-auth", False, False),
        (403, "lease-token-mismatch", "lease-token-mismatch", False, False),
        (404, None, "lease-boundary-disabled", False, False),
        (409, "service-lock-busy", "service-lock-busy", True, False),
        (410, "lease-not-active", "lease-not-active", False, False),
        (422, "invalid-lease-ttl", "invalid-lease-ttl", False, False),
        (
            503,
            "extension-lease-manager-unavailable",
            "lease-unavailable",
            True,
            False,
        ),
        (500, None, "lease-operation-ambiguous", False, True),
    ],
)
def test_http_failures_have_stable_classification(
    status_code, host_code, expected, retryable, ambiguous
) -> None:
    detail = json.dumps({"code": host_code}) if host_code else "private-host-detail"

    def request(*_args, **_kwargs):
        raise AgentHTTPError(status_code, detail, LEASE_TOKEN)

    with pytest.raises(ExtensionLeaseError) as caught:
        ExtensionLeaseClient(request).acquire(BINDING, SERVICE_IDS)
    assert caught.value.code == expected
    assert caught.value.retryable is retryable
    assert caught.value.ambiguous is ambiguous
    assert LEASE_TOKEN not in str(caught.value)
    assert LEASE_TOKEN not in repr(caught.value)
    assert LEASE_TOKEN not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "error",
    [
        AgentTimeout(LEASE_TOKEN),
        AgentUnavailable(LEASE_TOKEN),
        AgentProtocolError(LEASE_TOKEN),
    ],
)
def test_mutation_transport_failures_are_ambiguous_and_never_retryable(error) -> None:
    def request(*_args, **_kwargs):
        raise error

    with pytest.raises(ExtensionLeaseError) as caught:
        ExtensionLeaseClient(request).acquire(BINDING, SERVICE_IDS)
    assert caught.value.code == "lease-operation-ambiguous"
    assert caught.value.ambiguous is True
    assert caught.value.retryable is False
    assert LEASE_TOKEN not in str(caught.value)


def test_status_transport_failure_is_retryable() -> None:
    def request(*_args, **_kwargs):
        raise AgentTimeout(LEASE_TOKEN)

    with pytest.raises(ExtensionLeaseError) as caught:
        ExtensionLeaseClient(request).status(grant())
    assert caught.value.code == "lease-unavailable"
    assert caught.value.retryable is True
    assert caught.value.ambiguous is False


def test_client_has_no_logging_persistence_or_production_wiring() -> None:
    source_root = Path(__file__).resolve().parents[1]
    module = source_root / "extension_lease_client.py"
    text = module.read_text(encoding="utf-8")
    for forbidden in (
        "import logging",
        "logging.",
        "logger =",
        "print(",
        "open(",
        "AGENT_URL",
    ):
        assert forbidden not in text

    importers = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts
        and path != module
        and "extension_lease_client" in path.read_text(encoding="utf-8")
    }
    assert importers == set()
