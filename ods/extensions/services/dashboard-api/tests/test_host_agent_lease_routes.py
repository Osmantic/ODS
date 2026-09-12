"""Lease-bound HTTP tests for synchronous host extension mutations."""

# Imported pytest fixtures are intentionally discovered by their original
# names and then injected as test parameters.
# ruff: noqa: F401, F811

from __future__ import annotations

import collections
import json
import threading
import traceback

import pytest

from test_extension_operation_lease_host_api import (
    PLAN_HASH,
    TRANSACTION_ID,
    FakeClock,
    acquire_request,
    bound_request,
    host_request,
    host_server,
)


def mutation_lease(agent, grant: dict, **changes) -> dict:
    return bound_request(agent._extension_leases.LEASE_SCHEMA, grant, **changes)


def acquire_lease(agent, request, service_ids=None, **changes) -> dict:
    status, grant = request(
        "/v1/extension/lease/acquire",
        acquire_request(
            agent._extension_leases.LEASE_SCHEMA,
            service_ids,
            **changes,
        ),
    )
    assert status == 200
    return grant


def write_compose(agent, service_id: str, *, active: bool) -> None:
    directory = agent.EXTENSIONS_DIR / service_id
    name = "compose.yaml" if active else "compose.yaml.disabled"
    (directory / name).write_text("services: {}\n", encoding="utf-8")


def test_compose_toggle_without_lease_preserves_legacy_behavior(
    host_server, host_request
):
    agent, _listener = host_server
    write_compose(agent, "documents", active=False)

    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "documents"},
        expect_no_store=False,
    )
    assert status == 200
    assert result == {
        "status": "ok",
        "service_id": "documents",
        "action": "activate",
    }
    assert agent._extension_lease_manager is None
    assert (agent.EXTENSIONS_DIR / "documents" / "compose.yaml").is_file()

    status, result = host_request(
        "/v1/extension/deactivate",
        {"service_id": "documents"},
        expect_no_store=False,
    )
    assert status == 200
    assert result["action"] == "deactivate"
    assert not agent._service_locks["documents"].locked()


def valid_evidence() -> dict:
    return {
        "schema": "ods.extension-operation-lease.v1",
        "leaseId": "lease-" + "a" * 32,
        "leaseToken": "b" * 32,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
    }


@pytest.mark.parametrize(
    "lease",
    [
        None,
        [],
        True,
        {},
        {"schema": "ods.extension-operation-lease.v1"},
        {**valid_evidence(), "note": "not-allowed"},
        {**valid_evidence(), "schema": "ods.extension-operation-lease.v2"},
        {**valid_evidence(), "leaseId": "lease-not-hex"},
        {**valid_evidence(), "leaseToken": "x" * 31},
        {**valid_evidence(), "leaseToken": "x" * 257},
        {**valid_evidence(), "transactionId": "txn-not-hex"},
        {**valid_evidence(), "planHash": "0" * 63},
        {**valid_evidence(), "planHash": None},
    ],
)
def test_malformed_toggle_lease_fails_before_manager_or_lock_creation(
    host_server, host_request, lease
):
    agent, _listener = host_server
    write_compose(agent, "documents", active=False)

    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "documents", "lease": lease},
    )

    assert status == 422
    assert result == {"error": {"code": "invalid-lease-request"}}
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}
    assert (agent.EXTENSIONS_DIR / "documents" / "compose.yaml.disabled").is_file()


def test_disabled_toggle_lease_gate_does_not_construct_manager_or_lock(
    host_server, host_request
):
    agent, _listener = host_server
    write_compose(agent, "documents", active=False)
    agent.ASSISTANT_TRANSACTIONS_ENABLED = False

    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "documents", "lease": valid_evidence()},
    )

    assert status == 404
    assert result == {"error": {"code": "not-found"}}
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}


@pytest.mark.parametrize("token_length", [32, 256])
def test_toggle_lease_parser_accepts_exact_token_boundaries(
    host_server, token_length
):
    agent, _listener = host_server
    evidence = valid_evidence()
    evidence["leaseToken"] = "boundary-secret".ljust(token_length, "x")

    parsed = agent._parse_extension_mutation_lease(None, {"lease": evidence})

    assert parsed is not agent._EXTENSION_MUTATION_LEASE_REJECTED
    assert evidence["leaseToken"] not in repr(parsed)


def test_toggle_lease_manager_unavailable_is_fixed_and_redacted(
    host_server, host_request, caplog
):
    agent, _listener = host_server
    write_compose(agent, "documents", active=False)
    evidence = valid_evidence()
    module = agent._extension_leases
    caplog.set_level("ERROR", logger="ods-host-agent")
    agent._extension_leases = None
    try:
        status, result = host_request(
            "/v1/extension/activate",
            {"service_id": "documents", "lease": evidence},
        )
    finally:
        agent._extension_leases = module

    rendered = json.dumps(result) + caplog.text
    assert status == 503
    assert result == {
        "error": {"code": "extension-lease-manager-unavailable"}
    }
    assert evidence["leaseToken"] not in rendered
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}


class CountingLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.acquire_calls = 0

    def acquire(self, *args, **kwargs):
        self.acquire_calls += 1
        return self._lock.acquire(*args, **kwargs)

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()


def test_valid_toggle_lease_uses_existing_custody_without_reacquiring(
    host_server, host_request
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    write_compose(agent, "documents", active=False)
    grant = acquire_lease(agent, host_request)
    lock = agent._service_locks["documents"]
    assert lock.acquire_calls == 1

    status, result = host_request(
        "/v1/extension/activate",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )
    assert status == 200
    assert result["action"] == "activate"
    assert lock.acquire_calls == 1

    status, result = host_request(
        "/v1/extension/deactivate",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )
    assert status == 200
    assert result["action"] == "deactivate"
    assert lock.acquire_calls == 1
    assert lock.locked()


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"leaseToken": "wrong-token-value" * 3}, "lease-token-mismatch"),
        (
            {"transactionId": "txn-" + "3" * 24},
            "lease-binding-mismatch",
        ),
        ({"planHash": "4" * 64}, "lease-binding-mismatch"),
    ],
)
def test_toggle_lease_rejects_wrong_token_or_binding_without_leaking_it(
    host_server, host_request, changes, code
):
    agent, _listener = host_server
    write_compose(agent, "documents", active=False)
    grant = acquire_lease(agent, host_request)
    submitted = mutation_lease(agent, grant, **changes)

    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "documents", "lease": submitted},
    )

    rendered = json.dumps(result)
    assert status == 403
    assert result == {"error": {"code": code}}
    assert submitted["leaseToken"] not in rendered
    assert "documents" not in rendered
    assert (agent.EXTENSIONS_DIR / "documents" / "compose.yaml.disabled").is_file()


def test_toggle_lease_scope_and_expiry_fail_closed(host_server, host_request):
    agent, _listener = host_server
    clock = FakeClock()
    agent._extension_lease_manager = agent._extension_leases.ExtensionLeaseManager(
        agent._extension_lease_lock_provider,
        clock=clock,
    )
    write_compose(agent, "voice", active=False)
    grant = acquire_lease(agent, host_request, ["documents"], ttlSeconds=1)

    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "voice", "lease": mutation_lease(agent, grant)},
    )
    assert status == 403
    assert result == {"error": {"code": "lease-service-not-covered"}}
    assert "voice" not in agent._service_locks

    clock.advance(1)
    write_compose(agent, "documents", active=False)
    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "documents", "lease": mutation_lease(agent, grant)},
    )
    assert status == 410
    assert result == {"error": {"code": "lease-not-active"}}


def test_legacy_toggle_contends_with_held_lease(host_server, host_request):
    agent, _listener = host_server
    write_compose(agent, "documents", active=False)
    acquire_lease(agent, host_request)

    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "documents"},
        expect_no_store=False,
    )

    assert status == 409
    assert result == {"error": "Operation already in progress for documents"}


def test_toggle_mutation_error_clears_active_window_and_redacts_evidence(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    write_compose(agent, "documents", active=False)
    grant = acquire_lease(agent, host_request)
    evidence = mutation_lease(agent, grant)
    monkeypatch.setattr(agent.os, "replace", lambda *_args: (_ for _ in ()).throw(
        OSError("synthetic replace failure")
    ))

    status, result = host_request(
        "/v1/extension/activate",
        {"service_id": "documents", "lease": evidence},
        expect_no_store=False,
    )
    assert status == 500
    assert evidence["leaseToken"] not in json.dumps(result)

    status, present = host_request(
        "/v1/extension/lease/status",
        bound_request(agent._extension_leases.LEASE_SCHEMA, grant),
    )
    assert status == 200
    assert present["active"] is False
    assert evidence["leaseToken"] not in repr(
        agent._parse_extension_mutation_lease(None, {"lease": evidence})
    )
    assert evidence["leaseToken"] not in "".join(
        traceback.format_exception(
            agent._ExtensionMutationAdmissionRejected,
            agent._ExtensionMutationAdmissionRejected(),
            None,
        )
    )
