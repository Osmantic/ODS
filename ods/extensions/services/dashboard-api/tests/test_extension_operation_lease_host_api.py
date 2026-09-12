"""Real host-HTTP tests for the dormant extension lease boundary."""

from __future__ import annotations

import collections
import http.client
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def acquire_request(schema: str, service_ids=None, **changes):
    value = {
        "schema": schema,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": service_ids or ["documents"],
        "ttlSeconds": 30,
    }
    value.update(changes)
    return value


def bound_request(schema: str, grant: dict, **changes):
    value = {
        "schema": schema,
        "leaseId": grant["leaseId"],
        "leaseToken": grant["leaseToken"],
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
    }
    value.update(changes)
    return value


@pytest.fixture()
def host_server(tmp_path):
    agent_path = BIN_DIR / "ods-host-agent.py"
    spec = importlib.util.spec_from_file_location(
        "_extension_lease_host_agent", agent_path
    )
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    spec.loader.exec_module(agent)

    builtins = tmp_path / "extensions"
    users = tmp_path / "user-extensions"
    users.mkdir()
    for service_id in ("documents", "voice", "dashboard"):
        extension = builtins / service_id
        extension.mkdir(parents=True)
        (extension / "manifest.yaml").write_text("service: {}\n", encoding="utf-8")

    agent.AGENT_API_KEY = "synthetic-lease-host-key"
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    agent.EXTENSIONS_DIR = builtins
    agent.USER_EXTENSIONS_DIR = users
    agent.ALWAYS_ON_SERVICES = frozenset({"dashboard"})
    agent._service_locks = collections.defaultdict(threading.Lock)
    agent._extension_lease_manager = None

    listener = agent.ThreadedHTTPServer(("127.0.0.1", 0), agent.AgentHandler)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        yield agent, listener
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
        sys.modules.pop(spec.name, None)


@pytest.fixture()
def host_request(host_server):
    _agent, listener = host_server

    def call(
        path,
        body=None,
        *,
        raw=None,
        token="synthetic-lease-host-key",
        expect_no_store=True,
    ):
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            payload = raw if raw is not None else json.dumps(body).encode("utf-8")
            connection.request(
                "POST",
                path,
                body=payload,
                headers={"Authorization": "Bearer " + token},
            )
            response = connection.getresponse()
            document = json.loads(response.read())
            if expect_no_store:
                assert "no-store" in response.getheader("Cache-Control", "")
            return response.status, document
        finally:
            connection.close()

    return call


def test_host_lease_round_trip_is_token_bound_and_no_store(host_server, host_request):
    agent, _listener = host_server
    schema = agent._extension_leases.LEASE_SCHEMA
    status, grant = host_request(
        "/v1/extension/lease/acquire", acquire_request(schema)
    )
    assert status == 200
    assert grant["serviceIds"] == ["documents"]
    assert "leaseToken" in grant

    request = bound_request(schema, grant)
    status, present = host_request("/v1/extension/lease/status", request)
    assert status == 200
    assert present["active"] is False
    assert "leaseToken" not in present

    status, renewed = host_request(
        "/v1/extension/lease/renew", {**request, "ttlSeconds": 60}
    )
    assert status == 200 and renewed["ttlSeconds"] == 60
    status, released = host_request("/v1/extension/lease/release", request)
    assert status == 200 and released["released"] is True
    status, result = host_request("/v1/extension/lease/status", request)
    assert status == 410
    assert result == {"error": {"code": "lease-not-active"}}


def test_auth_gate_and_missing_manager_fail_closed_before_lock_creation(
    host_server, host_request
):
    agent, _listener = host_server
    schema = agent._extension_leases.LEASE_SCHEMA
    request = acquire_request(schema)

    assert host_request(
        "/v1/extension/lease/acquire", request, token="wrong"
    )[0] == 403
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}

    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    try:
        status, result = host_request("/v1/extension/lease/acquire", request)
    finally:
        agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    assert status == 404
    assert result == {"error": {"code": "not-found"}}
    assert agent._extension_lease_manager is None
    assert dict(agent._service_locks) == {}

    module = agent._extension_leases
    agent._extension_leases = None
    try:
        status, result = host_request("/v1/extension/lease/acquire", request)
    finally:
        agent._extension_leases = module
    assert status == 503
    assert result == {
        "error": {"code": "extension-lease-manager-unavailable"}
    }
    assert dict(agent._service_locks) == {}


def test_host_parser_and_schema_reject_ambiguous_or_oversized_input(
    host_server, host_request
):
    agent, _listener = host_server
    schema = agent._extension_leases.LEASE_SCHEMA
    duplicate = (
        b'{"schema":"'
        + schema.encode()
        + b'","schema":"duplicate","leaseToken":"do-not-echo"}'
    )
    status, result = host_request("/v1/extension/lease/acquire", raw=duplicate)
    assert status == 400
    assert "do-not-echo" not in json.dumps(result)
    for raw in (b"[]", b"null"):
        status, result = host_request("/v1/extension/lease/acquire", raw=raw)
        assert status == 400
        assert result == {"error": {"code": "invalid-lease-request"}}

    status, result = host_request(
        "/v1/extension/lease/acquire",
        raw=json.dumps(acquire_request(schema)).replace("30", "1.5").encode(),
    )
    assert status == 400
    assert "1.5" not in json.dumps(result)
    assert host_request(
        "/v1/extension/lease/acquire",
        raw=b"x" * (agent._ASSISTANT_LEASE_MAX_BODY + 1),
    )[0] == 413
    assert host_request(
        "/v1/extension/lease/acquire?leaseToken=do-not-log", acquire_request(schema)
    )[0] == 404

    status, result = host_request(
        "/v1/extension/lease/acquire",
        acquire_request(schema, unexpected=True),
    )
    assert status == 422
    assert result == {"error": {"code": "invalid-lease-request"}}
    status, result = host_request(
        "/v1/extension/lease/acquire",
        acquire_request(schema, ttlSeconds="60"),
    )
    assert status == 422
    assert result == {"error": {"code": "invalid-lease-ttl"}}
    status, result = host_request(
        "/v1/extension/lease/acquire",
        acquire_request(
            schema,
            [
                f"service-{index}"
                for index in range(agent._extension_leases.MAX_LEASE_SERVICES + 1)
            ],
        ),
    )
    assert status == 422
    assert result == {"error": {"code": "too-many-service-ids"}}
    assert dict(agent._service_locks) == {}


@pytest.mark.parametrize("service_id", ["missing", "dashboard"])
def test_unknown_and_always_on_services_cannot_mint_private_locks(
    host_server, host_request, service_id
):
    agent, _listener = host_server
    schema = agent._extension_leases.LEASE_SCHEMA

    status, result = host_request(
        "/v1/extension/lease/acquire", acquire_request(schema, [service_id])
    )

    assert status == 403
    assert result == {"error": {"code": "lease-service-not-manageable"}}
    assert service_id not in agent._service_locks


def test_lease_contends_on_the_exact_legacy_extension_lock(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    schema = agent._extension_leases.LEASE_SCHEMA
    monkeypatch.setattr(agent, "docker_compose_action", lambda *_args: (True, ""))
    status, grant = host_request(
        "/v1/extension/lease/acquire", acquire_request(schema)
    )
    assert status == 200

    status, _result = host_request(
        "/v1/extension/start",
        {"service_id": "documents"},
        expect_no_store=False,
    )
    assert status == 409

    status, released = host_request(
        "/v1/extension/lease/release", bound_request(schema, grant)
    )
    assert status == 200 and released["released"] is True
    status, result = host_request(
        "/v1/extension/start",
        {"service_id": "documents"},
        expect_no_store=False,
    )
    assert status == 200
    assert result["action"] == "start"


def test_server_maintenance_releases_an_abandoned_idle_lease(
    host_server, host_request, monkeypatch
):
    agent, listener = host_server
    schema = agent._extension_leases.LEASE_SCHEMA
    clock = FakeClock()
    agent._extension_lease_manager = agent._extension_leases.ExtensionLeaseManager(
        agent._extension_lease_lock_provider,
        clock=clock,
    )
    monkeypatch.setattr(agent, "docker_compose_action", lambda *_args: (True, ""))
    status, grant = host_request(
        "/v1/extension/lease/acquire",
        acquire_request(schema, ttlSeconds=1),
    )
    assert status == 200

    clock.advance(1)
    listener.service_actions()

    status, result = host_request(
        "/v1/extension/start",
        {"service_id": "documents"},
        expect_no_store=False,
    )
    assert status == 200
    assert result["action"] == "start"
    status, result = host_request(
        "/v1/extension/lease/status", bound_request(schema, grant)
    )
    assert status == 410
    assert result == {"error": {"code": "lease-not-active"}}
