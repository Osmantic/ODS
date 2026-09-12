"""Lease-bound HTTP tests for synchronous host extension mutations."""

# Imported pytest fixtures are intentionally discovered by their original
# names and then injected as test parameters.
# ruff: noqa: F401, F811

from __future__ import annotations

import collections
import json
import shutil
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


def write_user_config(agent, service_id: str) -> tuple:
    directory = agent.USER_EXTENSIONS_DIR / service_id
    source = directory / "config" / service_id
    source.mkdir(parents=True)
    (directory / "manifest.yaml").write_text("service: {}\n", encoding="utf-8")
    (source / "settings.yaml").write_text("enabled: true\n", encoding="utf-8")
    install_dir = agent.USER_EXTENSIONS_DIR.parent / "install"
    install_dir.mkdir()
    agent.INSTALL_DIR = install_dir
    return source, install_dir / "config" / service_id


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


def test_core_recreate_malformed_lease_fails_before_lock_or_compose(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", frozenset({"open-webui"}))
    compose_calls = []
    monkeypatch.setattr(
        agent,
        "docker_compose_recreate",
        lambda service_ids: (compose_calls.append(service_ids) or (True, "")),
    )

    status, result = host_request(
        "/v1/core/recreate",
        {"service_ids": ["open-webui"], "lease": {"schema": "wrong"}},
    )

    assert status == 422
    assert result == {"error": {"code": "invalid-lease-request"}}
    assert compose_calls == []
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}


def test_core_recreate_without_lease_preserves_legacy_lock_lifetime(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", frozenset({"litellm"}))
    agent._service_locks = collections.defaultdict(CountingLock)
    lock = agent._service_locks["litellm"]
    original_json_response = agent.json_response
    locked_at_response = []

    def observed_recreate(service_ids):
        assert service_ids == ["litellm"]
        assert lock.locked()
        return True, ""

    def observed_json_response(handler, code, body, **kwargs):
        if body.get("action") == "recreate":
            locked_at_response.append(lock.locked())
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent, "docker_compose_recreate", observed_recreate)
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        "/v1/core/recreate",
        {"service_ids": ["litellm"]},
        expect_no_store=False,
    )

    assert status == 200
    assert result == {
        "status": "ok",
        "action": "recreate",
        "service_ids": ["litellm"],
    }
    assert lock.acquire_calls == 1
    assert locked_at_response == [False]
    assert not lock.locked()


def test_core_recreate_without_lease_preserves_legacy_busy_response(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", frozenset({"litellm"}))
    lock = threading.Lock()
    lock.acquire()
    agent._service_locks = {"litellm": lock}
    try:
        status, result = host_request(
            "/v1/core/recreate",
            {"service_ids": ["litellm"]},
            expect_no_store=False,
        )
    finally:
        lock.release()

    assert status == 409
    assert result == {"error": "Operation already in progress for litellm"}


def test_core_recreate_timeout_releases_legacy_lock_before_response(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", frozenset({"litellm"}))
    lock = threading.Lock()
    agent._service_locks = {"litellm": lock}
    original_json_response = agent.json_response
    locked_at_response = []

    def observed_json_response(handler, code, body, **kwargs):
        if body.get("error_code") == "compose_recreate_failed":
            locked_at_response.append(lock.locked())
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(
        agent,
        "docker_compose_recreate",
        lambda _service_ids: (False, "Docker compose operation timed out"),
    )
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        "/v1/core/recreate",
        {"service_ids": ["litellm"]},
        expect_no_store=False,
    )

    assert status == 503
    assert result == {
        "error": agent._public_process_failure("compose_recreate_failed"),
        "error_code": "compose_recreate_failed",
    }
    assert locked_at_response == [False]
    assert not lock.locked()


def test_core_recreate_valid_lease_covers_compose_without_reacquiring(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    service_ids = ["hermes", "litellm"]
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", frozenset(service_ids))
    agent._service_locks = collections.defaultdict(CountingLock)
    grant = acquire_lease(agent, host_request, service_ids)
    locks = [agent._service_locks[service_id] for service_id in service_ids]
    original_json_response = agent.json_response
    state_at_response = []

    def observed_recreate(observed_service_ids):
        state = agent._extension_lease_manager.describe(grant["leaseId"])
        assert state["active"] is True
        assert observed_service_ids == service_ids
        assert all(lock.locked() for lock in locks)
        return True, ""

    def observed_json_response(handler, code, body, **kwargs):
        if body.get("action") == "recreate":
            state = agent._extension_lease_manager.describe(grant["leaseId"])
            state_at_response.append(
                (state["active"], [lock.locked() for lock in locks])
            )
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent, "docker_compose_recreate", observed_recreate)
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        "/v1/core/recreate",
        {
            "service_ids": list(reversed(service_ids)),
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )

    assert status == 200
    assert result == {
        "status": "ok",
        "action": "recreate",
        "service_ids": service_ids,
    }
    assert all(lock.acquire_calls == 1 for lock in locks)
    assert state_at_response == [(False, [True, True])]
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False


def test_core_recreate_lease_must_cover_every_requested_service(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    monkeypatch.setattr(
        agent,
        "CORE_SERVICE_IDS",
        frozenset({"hermes", "litellm"}),
    )
    compose_calls = []
    grant = acquire_lease(agent, host_request, ["hermes"])
    monkeypatch.setattr(
        agent,
        "docker_compose_recreate",
        lambda service_ids: (compose_calls.append(service_ids) or (True, "")),
    )

    status, result = host_request(
        "/v1/core/recreate",
        {
            "service_ids": ["litellm"],
            "lease": mutation_lease(agent, grant),
        },
    )

    assert status == 403
    assert result == {"error": {"code": "lease-service-not-covered"}}
    assert compose_calls == []
    assert "litellm" not in agent._service_locks


@pytest.mark.parametrize(
    ("service_id", "always_on"),
    [
        ("not-eligible-core", frozenset()),
        ("open-webui", frozenset({"open-webui"})),
    ],
)
def test_core_recreate_lease_rejects_unmanageable_core_service(
    host_server, host_request, monkeypatch, service_id, always_on
):
    agent, _listener = host_server
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", frozenset({service_id}))
    monkeypatch.setattr(agent, "ALWAYS_ON_SERVICES", always_on)

    status, result = host_request(
        "/v1/extension/lease/acquire",
        acquire_request(agent._extension_leases.LEASE_SCHEMA, [service_id]),
    )

    assert status == 403
    assert result == {"error": {"code": "lease-service-not-manageable"}}
    assert service_id not in agent._service_locks


def test_core_recreate_unhashable_service_id_returns_bounded_400(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    monkeypatch.setattr(agent, "CORE_SERVICE_IDS", frozenset({"litellm"}))
    compose_calls = []
    monkeypatch.setattr(
        agent,
        "docker_compose_recreate",
        lambda service_ids: (compose_calls.append(service_ids) or (True, "")),
    )

    status, result = host_request(
        "/v1/core/recreate",
        {"service_ids": [{"service": "litellm"}]},
        expect_no_store=False,
    )

    assert status == 400
    assert result == {"error": "Invalid service_id: {'service': 'litellm'}"}
    assert compose_calls == []
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}


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


def test_malformed_sync_config_lease_fails_before_manager_lock_or_copy(
    host_server, host_request
):
    agent, _listener = host_server
    _source, target = write_user_config(agent, "documents")

    status, result = host_request(
        "/v1/extension/sync_config",
        {"service_id": "documents", "lease": {"schema": "wrong"}},
    )

    assert status == 422
    assert result == {"error": {"code": "invalid-lease-request"}}
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}
    assert not target.exists()


def test_sync_config_without_lease_preserves_legacy_behavior(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    _source, target = write_user_config(agent, "documents")
    lock = agent._service_locks["documents"]
    original_json_response = agent.json_response
    locked_at_response = []

    def observed_json_response(handler, code, body, **kwargs):
        if code == 200 and body.get("service_id") == "documents":
            locked_at_response.append(lock.locked())
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent, "json_response", observed_json_response)

    status, result = host_request(
        "/v1/extension/sync_config",
        {"service_id": "documents"},
        expect_no_store=False,
    )

    assert status == 200
    assert result == {
        "status": "ok",
        "service_id": "documents",
        "synced": ["documents"],
        "skipped": [],
        "preserve_existing": False,
    }
    assert agent._extension_lease_manager is None
    assert lock.acquire_calls == 1
    assert locked_at_response == [False]
    assert not lock.locked()
    assert (target / "settings.yaml").read_text(encoding="utf-8") == "enabled: true\n"


def test_valid_sync_config_lease_covers_copy_without_reacquiring(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    _source, target = write_user_config(agent, "documents")
    grant = acquire_lease(agent, host_request)
    lock = agent._service_locks["documents"]
    original_copytree = shutil.copytree
    original_json_response = agent.json_response
    state_at_response = []

    def observed_copytree(*args, **kwargs):
        state = agent._extension_lease_manager.describe(grant["leaseId"])
        assert state["active"] is True
        return original_copytree(*args, **kwargs)

    def observed_json_response(handler, code, body, **kwargs):
        if code == 200 and body.get("service_id") == "documents":
            state = agent._extension_lease_manager.describe(grant["leaseId"])
            state_at_response.append((state["active"], lock.locked()))
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent.shutil, "copytree", observed_copytree)
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        "/v1/extension/sync_config",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )

    assert status == 200
    assert result == {
        "status": "ok",
        "service_id": "documents",
        "synced": ["documents"],
        "skipped": [],
        "preserve_existing": False,
    }
    assert (target / "settings.yaml").read_text(encoding="utf-8") == "enabled: true\n"
    assert lock.acquire_calls == 1
    assert state_at_response == [(False, True)]
    assert lock.locked()
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False


def test_sync_config_noop_response_is_written_after_lease_window_closes(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    directory = agent.USER_EXTENSIONS_DIR / "documents"
    directory.mkdir()
    (directory / "manifest.yaml").write_text("service: {}\n", encoding="utf-8")
    grant = acquire_lease(agent, host_request)
    original_json_response = agent.json_response
    active_at_response = []

    def observed_json_response(handler, code, body, **kwargs):
        if code == 200 and body.get("service_id") == "documents":
            state = agent._extension_lease_manager.describe(grant["leaseId"])
            active_at_response.append(state["active"])
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        "/v1/extension/sync_config",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )

    assert status == 200
    assert result == {"status": "ok", "service_id": "documents", "synced": []}
    assert active_at_response == [False]


def test_sync_config_error_response_is_written_after_lease_window_closes(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    write_user_config(agent, "documents")
    grant = acquire_lease(agent, host_request)
    lock = agent._service_locks["documents"]
    original_json_response = agent.json_response
    state_at_response = []

    def fail_copytree(*_args, **_kwargs):
        raise OSError("synthetic copy failure")

    def observed_json_response(handler, code, body, **kwargs):
        if code == 500:
            state = agent._extension_lease_manager.describe(grant["leaseId"])
            state_at_response.append((state["active"], lock.locked()))
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent.shutil, "copytree", fail_copytree)
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        "/v1/extension/sync_config",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )

    assert status == 500
    assert result == {
        "error": "Failed to copy documents/config/documents: synthetic copy failure"
    }
    assert state_at_response == [(False, True)]
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False


def test_sync_config_lease_scope_failure_cannot_prepare_target(
    host_server, host_request
):
    agent, _listener = host_server
    _source, target = write_user_config(agent, "voice")
    grant = acquire_lease(agent, host_request, ["documents"])

    status, result = host_request(
        "/v1/extension/sync_config",
        {
            "service_id": "voice",
            "lease": mutation_lease(agent, grant),
        },
    )

    assert status == 403
    assert result == {"error": {"code": "lease-service-not-covered"}}
    assert not target.exists()
    assert "voice" not in agent._service_locks


def test_sync_config_authenticates_lease_before_noop(host_server, host_request):
    agent, _listener = host_server
    directory = agent.USER_EXTENSIONS_DIR / "documents"
    directory.mkdir()
    (directory / "manifest.yaml").write_text("service: {}\n", encoding="utf-8")
    grant = acquire_lease(agent, host_request)
    submitted = mutation_lease(
        agent,
        grant,
        leaseToken="wrong-token-value" * 3,
    )

    status, result = host_request(
        "/v1/extension/sync_config",
        {"service_id": "documents", "lease": submitted},
    )

    assert status == 403
    assert result == {"error": {"code": "lease-token-mismatch"}}
    assert not (directory / "config").exists()
    assert submitted["leaseToken"] not in json.dumps(result)


@pytest.mark.parametrize("action", ["start", "stop"])
def test_malformed_start_stop_lease_fails_before_lock_or_compose(
    host_server, host_request, monkeypatch, action
):
    agent, _listener = host_server
    compose_calls = []
    monkeypatch.setattr(
        agent,
        "docker_compose_action",
        lambda *args: (compose_calls.append(args) or (True, "")),
    )

    status, result = host_request(
        f"/v1/extension/{action}",
        {"service_id": "documents", "lease": {"schema": "wrong"}},
    )

    assert status == 422
    assert result == {"error": {"code": "invalid-lease-request"}}
    assert compose_calls == []
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}


@pytest.mark.parametrize("action", ["start", "stop"])
def test_start_stop_without_lease_releases_lock_before_response(
    host_server, host_request, monkeypatch, action
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    lock = agent._service_locks["documents"]
    original_json_response = agent.json_response
    locked_at_response = []

    def observed_json_response(handler, code, body, **kwargs):
        if body.get("service_id") == "documents":
            locked_at_response.append(lock.locked())
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent, "_read_progress_status", lambda _sid: "complete")
    monkeypatch.setattr(agent, "docker_compose_action", lambda *_args: (True, ""))
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        f"/v1/extension/{action}",
        {"service_id": "documents"},
        expect_no_store=False,
    )

    assert status == 200
    assert result == {
        "status": "ok",
        "service_id": "documents",
        "action": action,
    }
    assert lock.acquire_calls == 1
    assert locked_at_response == [False]
    assert not lock.locked()


@pytest.mark.parametrize("action", ["start", "stop"])
def test_valid_start_stop_lease_covers_compose_without_reacquiring(
    host_server, host_request, monkeypatch, action
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    grant = acquire_lease(agent, host_request)
    lock = agent._service_locks["documents"]
    original_json_response = agent.json_response
    state_at_response = []

    def observed_compose(service_id, observed_action):
        state = agent._extension_lease_manager.describe(grant["leaseId"])
        assert state["active"] is True
        assert service_id == "documents"
        assert observed_action == action
        return True, ""

    def observed_json_response(handler, code, body, **kwargs):
        if body.get("service_id") == "documents":
            state = agent._extension_lease_manager.describe(grant["leaseId"])
            state_at_response.append((state["active"], lock.locked()))
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent, "docker_compose_action", observed_compose)
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        f"/v1/extension/{action}",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )

    assert status == 200
    assert result == {
        "status": "ok",
        "service_id": "documents",
        "action": action,
    }
    assert lock.acquire_calls == 1
    assert state_at_response == [(False, True)]
    assert lock.locked()
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False


def test_retry_start_transfers_active_lease_window_to_worker(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    grant = acquire_lease(agent, host_request)
    lock = agent._service_locks["documents"]
    worker_started = threading.Event()
    allow_worker_exit = threading.Event()
    spawned = []
    original_thread = threading.Thread

    def observed_work(service_id):
        assert service_id == "documents"
        state = agent._extension_lease_manager.describe(grant["leaseId"])
        assert state["active"] is True
        worker_started.set()
        assert allow_worker_exit.wait(5)

    def recording_thread(*args, **kwargs):
        thread = original_thread(*args, **kwargs)
        target = kwargs.get("target")
        if getattr(target, "__name__", "") == "_thread_target":
            spawned.append(thread)
        return thread

    monkeypatch.setattr(agent, "_read_progress_status", lambda _sid: "error")
    monkeypatch.setattr(agent, "_enable_retry_work", observed_work)
    monkeypatch.setattr(agent.threading, "Thread", recording_thread)

    status, result = host_request(
        "/v1/extension/start",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )

    assert status == 202
    assert result == {
        "status": "retrying",
        "service_id": "documents",
        "action": "start",
    }
    assert worker_started.wait(5)
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is True
    assert len(spawned) == 1
    status, busy = host_request(
        "/v1/extension/lease/release",
        bound_request(agent._extension_leases.LEASE_SCHEMA, grant),
    )
    assert status == 409
    assert busy == {"error": {"code": "lease-mutation-active"}}
    allow_worker_exit.set()
    spawned[0].join(timeout=5)
    assert not spawned[0].is_alive()
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False
    assert lock.acquire_calls == 1
    assert lock.locked()
    status, released = host_request(
        "/v1/extension/lease/release",
        bound_request(agent._extension_leases.LEASE_SCHEMA, grant),
    )
    assert status == 200
    assert released["released"] is True
    assert not lock.locked()


def test_retry_thread_start_failure_returns_admission_to_handler(
    host_server, host_request, monkeypatch, caplog
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    grant = acquire_lease(agent, host_request)
    lock = agent._service_locks["documents"]
    original_thread = threading.Thread
    progress = []
    progress_written = threading.Event()

    class FailingRetryThread:
        def start(self):
            raise RuntimeError("synthetic retry thread failure")

    def selective_thread(*args, **kwargs):
        target = kwargs.get("target")
        if getattr(target, "__name__", "") == "_thread_target":
            return FailingRetryThread()
        return original_thread(*args, **kwargs)

    def record_progress(*args, **kwargs):
        progress.append((args, kwargs))
        progress_written.set()

    monkeypatch.setattr(agent, "_read_progress_status", lambda _sid: "error")
    monkeypatch.setattr(
        agent,
        "_write_progress",
        record_progress,
    )
    monkeypatch.setattr(agent.threading, "Thread", selective_thread)

    with caplog.at_level("ERROR", logger="ods-host-agent"):
        status, result = host_request(
            "/v1/extension/start",
            {
                "service_id": "documents",
                "lease": mutation_lease(agent, grant),
            },
            expect_no_store=False,
        )

    assert status == 202
    assert result["status"] == "retrying"
    assert progress_written.wait(5)
    assert progress[-1][0][:3] == ("documents", "error", "Retry failed")
    assert progress[-1][1]["error_code"] == "extension_retry_failed"
    assert "synthetic retry thread failure" not in caplog.text
    for _ in range(100):
        if not agent._extension_lease_manager.describe(grant["leaseId"])["active"]:
            break
        threading.Event().wait(0.01)
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False
    assert lock.acquire_calls == 1
    assert lock.locked()


def test_start_compose_exception_clears_active_lease_window(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    agent._service_locks = collections.defaultdict(CountingLock)
    grant = acquire_lease(agent, host_request)
    lock = agent._service_locks["documents"]
    original_json_response = agent.json_response
    state_at_response = []

    def fail_compose(*_args):
        raise RuntimeError("synthetic compose failure")

    def observed_json_response(handler, code, body, **kwargs):
        if code == 500:
            state = agent._extension_lease_manager.describe(grant["leaseId"])
            state_at_response.append((state["active"], lock.locked()))
        return original_json_response(handler, code, body, **kwargs)

    monkeypatch.setattr(agent, "docker_compose_action", fail_compose)
    monkeypatch.setattr(agent, "json_response", observed_json_response)
    status, result = host_request(
        "/v1/extension/start",
        {
            "service_id": "documents",
            "lease": mutation_lease(agent, grant),
        },
        expect_no_store=False,
    )

    assert status == 500
    assert result == {
        "error": agent._public_process_failure("compose_action_failed"),
        "error_code": "compose_action_failed",
    }
    assert state_at_response == [(False, True)]
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False
    assert lock.acquire_calls == 1
    assert lock.locked()
