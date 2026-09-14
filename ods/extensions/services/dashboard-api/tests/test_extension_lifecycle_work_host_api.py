"""Real HTTP tests for the dormant lease-authorized lifecycle-work route."""

from __future__ import annotations

import ast
import collections
import hashlib
import http.client
import importlib.util
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from extension_document_digest import canonical_document_sha256  # noqa: E402

TRANSACTION_ID = "txn-" + "1" * 24
OTHER_TRANSACTION_ID = "txn-" + "2" * 24
PLAN_HASH = "3" * 64
OTHER_PLAN_HASH = "4" * 64
EVIDENCE_HASH = "5" * 64
TOKEN = "synthetic-lifecycle-work-host-key"

STAGE_SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
)


class CountingLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.acquire_calls = 0

    def acquire(self, blocking=True):
        self.acquire_calls += 1
        return self._lock.acquire(blocking=blocking)

    def release(self):
        return self._lock.release()

    def locked(self):
        return self._lock.locked()


class MemoryReceiptStore:
    def __init__(self, started_type, terminal_type, snapshot_type) -> None:
        self.started_type = started_type
        self.terminal_type = terminal_type
        self.snapshot_type = snapshot_type
        self.states = {}

    def begin(
        self,
        transaction_id,
        plan_hash,
        operation_key,
        request_hash,
        service_ids,
    ):
        prior = self.states.get((transaction_id, operation_key))
        if prior is not None:
            started, terminal = prior
            return terminal or started
        started = self.started_type(
            transaction_id,
            plan_hash,
            operation_key,
            request_hash,
            tuple(service_ids),
            "a" * 64,
        )
        self.states[(transaction_id, operation_key)] = (started, None)
        return started

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
        started, prior = self.states[(transaction_id, operation_key)]
        if prior is not None:
            return prior
        terminal = self.terminal_type(
            transaction_id,
            plan_hash,
            operation_key,
            request_hash,
            tuple(service_ids),
            outcome,
            evidence_hash,
            started.event_hash,
            "b" * 64,
        )
        self.states[(transaction_id, operation_key)] = (started, terminal)
        return terminal

    def snapshot(self, transaction_id, operation_key):
        prior = self.states.get((transaction_id, operation_key))
        if prior is None:
            return self.snapshot_type(
                transaction_id, operation_key, "absent", None, None
            )
        started, terminal = prior
        state = terminal.outcome if terminal is not None else "started"
        return self.snapshot_type(
            transaction_id, operation_key, state, started, terminal
        )


def work_request(
    schema,
    lease=None,
    *,
    transaction_id=TRANSACTION_ID,
    plan_hash=PLAN_HASH,
    operation_key="verify",
    service_ids=None,
    payload=None,
    **changes,
):
    service_ids = list(service_ids or ["documents"])
    payload = payload or {"serviceIds": list(service_ids)}
    unsigned = {
        "schema": schema,
        "transactionId": transaction_id,
        "planHash": plan_hash,
        "operationKey": operation_key,
        "serviceIds": service_ids,
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
    if lease is not None:
        value["lease"] = lease
    value.update(changes)
    return value


def acquire_lease(agent, host_request, service_ids=None):
    status, grant = host_request(
        "/v1/extension/lease/acquire",
        {
            "schema": agent._extension_leases.LEASE_SCHEMA,
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "serviceIds": list(service_ids or ["documents"]),
            "ttlSeconds": 60,
        },
    )
    assert status == 200
    return grant


def lease_evidence(agent, grant, **changes):
    value = {
        "schema": agent._extension_leases.LEASE_SCHEMA,
        "leaseId": grant["leaseId"],
        "leaseToken": grant["leaseToken"],
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
    }
    value.update(changes)
    return value


def begin_receipt(agent, host_request, request):
    status, receipt = host_request(
        "/v1/extension/lifecycle-receipt/begin",
        {
            "schema": agent._LIFECYCLE_RECEIPT_SCHEMA,
            "transactionId": request["transactionId"],
            "planHash": request["planHash"],
            "operationKey": request["operationKey"],
            "requestHash": request["requestHash"],
            "serviceIds": request["serviceIds"],
        },
    )
    assert status == 200
    assert receipt["kind"] == "started"
    return receipt


def receipt_snapshot(agent, host_request, request):
    return host_request(
        "/v1/extension/lifecycle-receipt/snapshot",
        {
            "schema": agent._LIFECYCLE_RECEIPT_SCHEMA,
            "transactionId": request["transactionId"],
            "planHash": request["planHash"],
            "operationKey": request["operationKey"],
        },
    )


def bind_fixture_plan(agent, command):
    if command.operation_key != "stage":
        return replace(command, plan_material={"bound": True})

    plan = agent._extension_lifecycle_plan
    operations = tuple(
        plan.PlannedOperation(item["serviceId"], item["action"])
        for item in command.payload["operations"]
    )
    definitions = tuple(
        plan.PlannedDefinition(
            service_id=operation.service_id,
            service_type="docker",
            manifest_schema_version="ods.services.v2",
            version="1.0.0",
            data_schema_version="1",
            definition_sha256=canonical_document_sha256(
                (
                    agent.EXTENSIONS_DIR
                    / operation.service_id
                    / "manifest.yaml"
                ).read_bytes()
            ),
            compose_sha256=None,
            definition_source="library",
            compose_file=None,
            images=(),
            builds=(),
            canonical_document=b"fixture-only\n",
        )
        for operation in operations
    )
    return replace(
        command,
        plan_material=plan.LifecyclePlanMaterial(
            schema=plan.PLAN_MATERIAL_SCHEMA,
            transaction_id=command.transaction_id,
            plan_hash=command.plan_hash,
            state="staged",
            operations=operations,
            definitions=definitions,
        ),
    )


def stage_work_request(agent, lease):
    return work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease,
        operation_key="stage",
        payload={
            "operations": [
                {"serviceId": "documents", "action": "install"},
            ]
        },
    )


@pytest.fixture()
def host_server(tmp_path):
    agent_path = BIN_DIR / "ods-host-agent.py"
    spec = importlib.util.spec_from_file_location(
        "_extension_lifecycle_work_host_agent", agent_path
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

    agent.AGENT_API_KEY = TOKEN
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    agent.DATA_DIR = tmp_path / "data"
    agent.DATA_DIR.mkdir()
    stage_root = agent.DATA_DIR / "assistant-first" / "artifact-stage"
    stage_root.mkdir(mode=0o700, parents=True)
    stage_root.chmod(0o700)
    agent.EXTENSIONS_DIR = builtins
    agent.USER_EXTENSIONS_DIR = users
    agent.ALWAYS_ON_SERVICES = frozenset({"dashboard"})
    agent._service_locks = collections.defaultdict(CountingLock)
    agent._extension_lease_manager = None
    receipt_module = agent.__dict__["_extension_" + "lifecycle_receipts"]
    agent._lifecycle_receipt_store = MemoryReceiptStore(
        receipt_module.StartedReceipt,
        receipt_module.TerminalReceipt,
        receipt_module.LifecycleSnapshot,
    )
    agent._lifecycle_receipt_store_data_dir = agent.DATA_DIR
    agent._extension_lifecycle_work_dispatcher = None
    agent._artifact_stage_runtime = None
    agent._artifact_stage_runtime_binding = None
    agent._extension_lifecycle_plan_loader = lambda command: bind_fixture_plan(
        agent, command
    )

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
        token=TOKEN,
        content_type="application/json",
        transfer_encoding=False,
        duplicate_content_length=False,
    ):
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            payload = raw if raw is not None else json.dumps(body).encode("utf-8")
            if transfer_encoding or duplicate_content_length:
                connection.putrequest("POST", path)
                connection.putheader("Authorization", "Bearer " + token)
                if content_type is not None:
                    connection.putheader("Content-Type", content_type)
                if not transfer_encoding:
                    connection.putheader("Content-Length", str(len(payload)))
                if duplicate_content_length:
                    connection.putheader("Content-Length", str(len(payload)))
                if transfer_encoding:
                    connection.putheader("Transfer-Encoding", "chunked")
                connection.endheaders()
                connection.send(payload)
            else:
                connection.request(
                    "POST",
                    path,
                    body=payload,
                    headers={
                        "Authorization": "Bearer " + token,
                        **(
                            {"Content-Type": content_type}
                            if content_type is not None
                            else {}
                        ),
                    },
                )
            response = connection.getresponse()
            document = json.loads(response.read())
            assert "no-store" in response.getheader("Cache-Control", "")
            return response.status, document
        finally:
            connection.close()

    return call


def test_success_is_exact_synchronous_bound_and_token_free(host_server, host_request):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)
    lock = agent._service_locks["documents"]
    seen = []
    active_at_response = []
    original_json_response = agent.json_response

    def dispatch(command):
        lease = agent._extension_lease_manager.describe(grant["leaseId"])
        assert lease["active"] is True
        assert lock.locked()
        seen.append(command)
        return EVIDENCE_HASH

    def observed_json_response(handler, status_code, body, **kwargs):
        if body.get("schema") == agent._extension_lifecycle_work.RESULT_SCHEMA:
            state = agent._extension_lease_manager.describe(grant["leaseId"])
            active_at_response.append(state["active"])
        return original_json_response(handler, status_code, body, **kwargs)

    agent._extension_lifecycle_work_dispatcher = dispatch
    agent.json_response = observed_json_response
    request = work_request(agent._extension_lifecycle_work.REQUEST_SCHEMA, evidence)
    begin_receipt(agent, host_request, request)

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result == {
        "schema": agent._extension_lifecycle_work.RESULT_SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "verify",
        "requestHash": request["requestHash"],
        "serviceIds": ["documents"],
        "completed": True,
        "outcome": "completed",
        "evidenceHash": EVIDENCE_HASH,
    }
    assert len(seen) == 1
    assert seen[0].payload == {"serviceIds": ["documents"]}
    assert seen[0].plan_material == {"bound": True}
    assert not hasattr(seen[0], "lease")
    assert not hasattr(seen[0], "lease_token")
    assert grant["leaseToken"] not in repr(seen[0])
    assert active_at_response == [False]
    state = agent._extension_lease_manager.describe(grant["leaseId"])
    assert state["active"] is False
    assert lock.acquire_calls == 1
    assert lock.locked()
    assert grant["leaseToken"] not in json.dumps(result)
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "completed"
    assert snapshot["terminalReceipt"]["evidenceHash"] == EVIDENCE_HASH

    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert replay_status == 200
    assert replay == result
    assert len(seen) == 1


@pytest.mark.parametrize(
    "operation_key,payload",
    [
        (
            "download-and-verify",
            {"operations": [{"serviceId": "documents", "action": "install"}]},
        ),
        ("backup", {"serviceIds": ["documents"]}),
        ("configure", {"serviceIds": ["documents"]}),
        ("verify", {"serviceIds": ["documents"]}),
        ("restore", {"serviceIds": ["documents"]}),
        ("release", {"serviceIds": ["documents"]}),
        (
            "reserve:documents",
            {"operation": {"serviceId": "documents", "action": "install"}},
        ),
        (
            "apply:documents",
            {"operation": {"serviceId": "documents", "action": "install"}},
        ),
        (
            "compensate:documents",
            {"operation": {"serviceId": "documents", "action": "install"}},
        ),
    ],
)
def test_non_stage_operations_remain_inert_but_authenticate_the_lease(
    host_server, host_request, operation_key, payload
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key=operation_key,
        payload=payload,
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    state = agent._extension_lease_manager.describe(grant["leaseId"])
    assert state["active"] is False
    assert grant["leaseToken"] not in json.dumps(result)


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_stage_uses_fixed_runtime_terminalizes_and_replays_once(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = stage_work_request(agent, lease_evidence(agent, grant))
    begin_receipt(agent, host_request, request)

    runtime = agent._get_extension_artifact_stage_runtime()
    original_dispatch = runtime.dispatcher
    calls = []

    def counted_dispatch(command):
        calls.append(command)
        return original_dispatch(command)

    agent._artifact_stage_runtime = replace(
        runtime,
        dispatcher=counted_dispatch,
    )
    agent._extension_lifecycle_work_dispatcher = lambda _command: (_ for _ in ()).throw(
        AssertionError("generic dispatcher received stage work")
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["completed"] is True
    assert result["outcome"] == "completed"
    assert result["operationKey"] == "stage"
    assert len(calls) == 1
    stage_root = agent.DATA_DIR / "assistant-first" / "artifact-stage"
    bundles = list(stage_root.iterdir())
    assert len(bundles) == 1
    assert hashlib.sha256(bundles[0].read_bytes()).hexdigest() == result[
        "evidenceHash"
    ]

    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "completed"
    assert snapshot["terminalReceipt"]["evidenceHash"] == result["evidenceHash"]

    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert replay_status == 200
    assert replay == result
    assert len(calls) == 1
    assert list(stage_root.iterdir()) == bundles


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_stage_recovers_exact_started_bundle_without_dispatch(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = stage_work_request(agent, lease_evidence(agent, grant))
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(
        {
            key: request[key]
            for key in agent._extension_lifecycle_work.REQUEST_KEYS
        }
    )
    bound = bind_fixture_plan(agent, command)
    runtime = agent._get_extension_artifact_stage_runtime()
    begin_receipt(agent, host_request, request)
    expected_hash = runtime.dispatcher(bound)
    calls = []
    agent._artifact_stage_runtime = replace(
        runtime,
        dispatcher=lambda command: calls.append(command) or EVIDENCE_HASH,
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == expected_hash
    assert calls == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "completed"


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_stage_corrupt_started_bundle_fails_closed_without_dispatch(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = stage_work_request(agent, lease_evidence(agent, grant))
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(
        {
            key: request[key]
            for key in agent._extension_lifecycle_work.REQUEST_KEYS
        }
    )
    runtime = agent._get_extension_artifact_stage_runtime()
    begin_receipt(agent, host_request, request)
    runtime.dispatcher(bind_fixture_plan(agent, command))
    bundle = next(runtime.root.iterdir())
    bundle.chmod(0o600)
    bundle.write_bytes(b"corrupt\n")
    bundle.chmod(0o400)
    calls = []
    agent._artifact_stage_runtime = replace(
        runtime,
        dispatcher=lambda command: calls.append(command) or EVIDENCE_HASH,
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-operation-failed"}}
    assert calls == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "started"


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_stage_unsafe_root_preserves_started_receipt_and_ignores_generic_dispatch(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = stage_work_request(agent, lease_evidence(agent, grant))
    begin_receipt(agent, host_request, request)
    calls = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        calls.append(command) or EVIDENCE_HASH
    )
    (agent.DATA_DIR / "assistant-first" / "artifact-stage").chmod(0o755)

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-operation-failed"}}
    assert calls == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "started"


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_stage_plan_mismatch_terminalizes_failure_without_writing(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = stage_work_request(agent, lease_evidence(agent, grant))
    begin_receipt(agent, host_request, request)
    agent._extension_lifecycle_plan_loader = lambda command: replace(
        command,
        plan_material={"wrong": True},
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 409
    assert result == {"error": {"code": "lifecycle-work-plan-mismatch"}}
    stage_root = agent.DATA_DIR / "assistant-first" / "artifact-stage"
    assert list(stage_root.iterdir()) == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "failed"


def test_missing_plan_loader_fails_before_worker_and_preserves_started_receipt(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
    )
    begin_receipt(agent, host_request, request)
    calls = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        calls.append(command) or EVIDENCE_HASH
    )
    agent._extension_lifecycle_plan_loader = None

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {
        "error": {"code": "lifecycle-work-plan-loader-unavailable"}
    }
    assert calls == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "started"


def test_plan_mismatch_terminalizes_failure_and_never_runs_or_reloads(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
    )
    begin_receipt(agent, host_request, request)
    worker_calls = []
    loader_calls = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        worker_calls.append(command) or EVIDENCE_HASH
    )

    def reject(_command):
        loader_calls.append(True)
        raise agent._extension_lifecycle_work.LifecycleWorkValidationError(
            "lifecycle-work-plan-mismatch"
        )

    agent._extension_lifecycle_plan_loader = reject

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 409
    assert result == {"error": {"code": "lifecycle-work-plan-mismatch"}}
    assert loader_calls == [True]
    assert worker_calls == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "failed"

    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert replay_status == 503
    assert replay == {"error": {"code": "lifecycle-work-operation-failed"}}
    assert loader_calls == [True]
    assert worker_calls == []


def test_host_plan_loader_reads_exact_transaction_and_passes_only_store_result(
    host_server
):
    agent, _listener = host_server
    request = work_request(agent._extension_lifecycle_work.REQUEST_SCHEMA)
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(request)
    stored = {"transactionId": TRANSACTION_ID, "marker": object()}
    calls = []

    class Store:
        def read(self, transaction_id):
            calls.append(("read", transaction_id))
            return stored

    def bind(value, transaction):
        calls.append(("bind", value, transaction))
        return replace(value, plan_material={"approved": True})

    agent._get_extension_transaction_store = Store
    agent._extension_transactions = SimpleNamespace(TransactionError=RuntimeError)
    agent._extension_lifecycle_plan = SimpleNamespace(bind_lifecycle_plan=bind)

    bound = agent._load_extension_lifecycle_plan(command)

    assert bound.plan_material == {"approved": True}
    assert calls == [
        ("read", TRANSACTION_ID),
        ("bind", command, stored),
    ]


def test_host_plan_loader_maps_store_integrity_failure_without_private_detail(
    host_server
):
    agent, _listener = host_server
    request = work_request(agent._extension_lifecycle_work.REQUEST_SCHEMA)
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(request)

    class StoreError(Exception):
        pass

    class Store:
        def read(self, _transaction_id):
            raise StoreError("private-store-path")

    agent._get_extension_transaction_store = Store
    agent._extension_transactions = SimpleNamespace(TransactionError=StoreError)
    agent._extension_lifecycle_plan = SimpleNamespace(
        bind_lifecycle_plan=lambda *_args: (_ for _ in ()).throw(
            AssertionError("binder called after store failure")
        )
    )

    with pytest.raises(
        agent._extension_lifecycle_work.LifecycleWorkValidationError
    ) as raised:
        agent._load_extension_lifecycle_plan(command)

    assert raised.value.code == "lifecycle-work-plan-mismatch"
    assert "private-store-path" not in str(raised.value)


def test_host_transaction_store_uses_fixed_shared_data_root_and_caches(host_server):
    agent, _listener = host_server
    roots = []

    class Store:
        def __init__(self, root):
            self.root = root
            roots.append(root)

    agent._extension_transactions = SimpleNamespace(TransactionStore=Store)
    agent._extension_transaction_store = None
    agent._extension_transaction_store_data_dir = None

    first = agent._get_extension_transaction_store()
    second = agent._get_extension_transaction_store()

    assert first is second
    assert roots == [agent.DATA_DIR / "assistant-first" / "transaction-store"]


def test_auth_gate_and_missing_core_fail_closed_before_dispatch(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
    )
    called = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        called.append(command) or EVIDENCE_HASH
    )

    assert (
        host_request("/v1/extension/lifecycle-work", raw=b"", token="wrong")[0] == 403
    )
    assert called == []

    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    try:
        status, result = host_request("/v1/extension/lifecycle-work", request)
    finally:
        agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    assert status == 404 and result == {"error": {"code": "not-found"}}
    assert called == []

    module = agent._extension_lifecycle_work
    agent._extension_lifecycle_work = None
    try:
        status, result = host_request("/v1/extension/lifecycle-work", request)
    finally:
        agent._extension_lifecycle_work = module
    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-boundary-unavailable"}}
    assert called == []


@pytest.mark.parametrize(
    "raw,expected_status,expected_code",
    [
        (b"[]", 400, "invalid-lifecycle-work-request"),
        (b"null", 400, "invalid-lifecycle-work-request"),
        (b"{} trailing", 400, "invalid-lifecycle-work-request"),
        (b'{"estimate":1.5}', 400, "invalid-lifecycle-work-request"),
        (b"\xff", 400, "invalid-lifecycle-work-request"),
    ],
)
def test_strict_parser_rejects_ambiguous_documents(
    host_server, host_request, raw, expected_status, expected_code
):
    _agent, _listener = host_server
    status, result = host_request("/v1/extension/lifecycle-work", raw=raw)
    assert status == expected_status
    assert result == {"error": {"code": expected_code}}


def test_strict_parser_rejects_duplicate_framing_size_and_query(
    host_server, host_request
):
    agent, _listener = host_server
    secret = "do-not-echo-lease-token"
    duplicate = (
        b'{"schema":"'
        + agent._extension_lifecycle_work.REQUEST_SCHEMA.encode()
        + b'","schema":"duplicate","leaseToken":"'
        + secret.encode()
        + b'"}'
    )
    status, result = host_request("/v1/extension/lifecycle-work", raw=duplicate)
    assert status == 400 and secret not in json.dumps(result)

    for option in ("transfer_encoding", "duplicate_content_length"):
        status, result = host_request(
            "/v1/extension/lifecycle-work",
            raw=b"{}",
            **{option: True},
        )
        assert status == 400
        assert result == {"error": {"code": "invalid-lifecycle-work-request-framing"}}

    status, result = host_request(
        "/v1/extension/lifecycle-work",
        raw=b"x" * (agent._LIFECYCLE_WORK_MAX_BODY + 1),
    )
    assert status == 413
    assert result == {"error": {"code": "lifecycle-work-request-size"}}

    status, result = host_request(
        "/v1/extension/lifecycle-work?leaseToken=do-not-log", raw=b"{}"
    )
    assert status == 404
    assert result == {"error": {"code": "not-found"}}


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/xml"])
def test_content_type_must_identify_json(host_server, host_request, content_type):
    _agent, _listener = host_server
    status, result = host_request(
        "/v1/extension/lifecycle-work",
        raw=b"{}",
        content_type=content_type,
    )
    assert status == 415
    assert result == {"error": {"code": "invalid-lifecycle-work-content-type"}}


def test_shape_hash_and_lease_validation_precede_dispatch(host_server, host_request):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)
    called = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        called.append(command) or EVIDENCE_HASH
    )

    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA, evidence, extra=True
    )
    assert host_request("/v1/extension/lifecycle-work", request) == (
        422,
        {"error": {"code": "invalid-lifecycle-work-request"}},
    )

    request = work_request(agent._extension_lifecycle_work.REQUEST_SCHEMA)
    assert host_request("/v1/extension/lifecycle-work", request) == (
        422,
        {"error": {"code": "invalid-lifecycle-work-request"}},
    )

    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        {"schema": "wrong"},
    )
    assert host_request("/v1/extension/lifecycle-work", request) == (
        422,
        {"error": {"code": "invalid-lease-request"}},
    )

    request = work_request(agent._extension_lifecycle_work.REQUEST_SCHEMA, evidence)
    request["requestHash"] = "9" * 64
    assert host_request("/v1/extension/lifecycle-work", request) == (
        422,
        {"error": {"code": "lifecycle-work-request-hash-mismatch"}},
    )
    assert called == []


@pytest.mark.parametrize(
    "work_changes,lease_changes",
    [
        ({"transaction_id": OTHER_TRANSACTION_ID}, {}),
        ({"plan_hash": OTHER_PLAN_HASH}, {}),
        ({}, {"transactionId": OTHER_TRANSACTION_ID}),
        ({}, {"planHash": OTHER_PLAN_HASH}),
    ],
)
def test_request_and_lease_binding_must_match_before_dispatch(
    host_server, host_request, work_changes, lease_changes
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant, **lease_changes)
    called = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        called.append(command) or EVIDENCE_HASH
    )
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        evidence,
        **work_changes,
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 403
    assert result == {"error": {"code": "lease-binding-mismatch"}}
    assert called == []


@pytest.mark.parametrize(
    "transaction_id,plan_hash",
    [
        (OTHER_TRANSACTION_ID, PLAN_HASH),
        (TRANSACTION_ID, OTHER_PLAN_HASH),
    ],
)
def test_lease_manager_rejects_jointly_misbound_request_and_credential(
    host_server, host_request, transaction_id, plan_hash
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(
        agent,
        grant,
        transactionId=transaction_id,
        planHash=plan_hash,
    )
    called = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        called.append(command) or EVIDENCE_HASH
    )
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        evidence,
        transaction_id=transaction_id,
        plan_hash=plan_hash,
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 403
    assert result == {"error": {"code": "lease-binding-mismatch"}}
    assert called == []


def test_lease_service_coverage_precedes_dispatch(host_server, host_request):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request, ["documents"])
    called = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        called.append(command) or EVIDENCE_HASH
    )
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        service_ids=["voice"],
        payload={"serviceIds": ["voice"]},
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 403
    assert result == {"error": {"code": "lease-service-not-covered"}}
    assert called == []
    assert "voice" not in agent._service_locks


def test_concurrent_work_under_one_lease_is_rejected_as_busy(host_server, host_request):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
    )
    begin_receipt(agent, host_request, request)
    entered = threading.Event()
    release = threading.Event()

    def dispatch(_command):
        entered.set()
        assert release.wait(5)
        return EVIDENCE_HASH

    agent._extension_lifecycle_work_dispatcher = dispatch
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(host_request, "/v1/extension/lifecycle-work", request)
        assert entered.wait(5)
        status, result = host_request("/v1/extension/lifecycle-work", request)
        assert status == 409
        assert result == {"error": {"code": "lease-mutation-active"}}
        release.set()
        assert first.result(timeout=5)[0] == 200

    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False


@pytest.mark.parametrize("failure", ["exception", "invalid-result"])
def test_dispatch_failure_is_generic_and_releases_active_window(
    host_server, host_request, caplog, failure
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)
    private_detail = "private-dispatch-detail"

    def dispatch(_command):
        if failure == "exception":
            raise RuntimeError(private_detail)
        return private_detail

    agent._extension_lifecycle_work_dispatcher = dispatch
    request = work_request(agent._extension_lifecycle_work.REQUEST_SCHEMA, evidence)
    begin_receipt(agent, host_request, request)

    with caplog.at_level("ERROR", logger="ods-host-agent"):
        status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-operation-failed"}}
    assert private_detail not in json.dumps(result)
    assert private_detail not in caplog.text
    assert grant["leaseToken"] not in json.dumps(result)
    assert grant["leaseToken"] not in caplog.text
    assert agent._extension_lease_manager.describe(grant["leaseId"])["active"] is False
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "failed"
    assert snapshot["terminalReceipt"]["outcome"] == "failed"
    assert private_detail not in json.dumps(snapshot)


def test_dispatch_requires_preexisting_exact_started_receipt(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
    )
    called = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        called.append(command) or EVIDENCE_HASH
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 409
    assert result == {
        "error": {"code": "lifecycle-work-started-receipt-required"}
    }
    assert called == []


def test_dispatch_rejects_misbound_started_receipt_at_http_boundary(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
    )
    agent._lifecycle_receipt_store.begin(
        request["transactionId"],
        "9" * 64,
        request["operationKey"],
        request["requestHash"],
        request["serviceIds"],
    )
    called = []
    agent._extension_lifecycle_work_dispatcher = lambda command: (
        called.append(command) or EVIDENCE_HASH
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 409
    assert result == {"error": {"code": "lifecycle-work-receipt-mismatch"}}
    assert called == []


def test_host_agent_source_leaves_production_dispatcher_unwired():
    tree = ast.parse((BIN_DIR / "ods-host-agent.py").read_text(encoding="utf-8"))
    assignments = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(
            isinstance(target, ast.Name)
            and target.id == "_extension_lifecycle_work_dispatcher"
            for target in targets
        ):
            assignments.append(node.value)

    assert len(assignments) == 1
    assert isinstance(assignments[0], ast.Constant)
    assert assignments[0].value is None
