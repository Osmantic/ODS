"""Real HTTP tests for the dormant lease-authorized lifecycle-work route."""

from __future__ import annotations

import ast
import collections
import hashlib
import http.client
import importlib.util
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

TRANSACTION_ID = "txn-" + "1" * 24
OTHER_TRANSACTION_ID = "txn-" + "2" * 24
PLAN_HASH = "3" * 64
OTHER_PLAN_HASH = "4" * 64
EVIDENCE_HASH = "5" * 64
TOKEN = "synthetic-lifecycle-work-host-key"


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


def test_default_dispatcher_is_inert_but_authenticates_the_lease(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    state = agent._extension_lease_manager.describe(grant["leaseId"])
    assert state["active"] is False
    assert grant["leaseToken"] not in json.dumps(result)


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
