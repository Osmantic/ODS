"""Real host-HTTP tests for the Phase 5G-B lifecycle-receipt boundary."""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_lifecycle_receipts as receipts  # noqa: E402


TRANSACTION_ID = "txn-" + "1" * 24
OTHER_TRANSACTION_ID = "txn-" + "2" * 24
PLAN_HASH = "3" * 64
OTHER_PLAN_HASH = "4" * 64
REQUEST_HASH = "5" * 64
EVIDENCE_HASH = "6" * 64
OTHER_EVIDENCE_HASH = "7" * 64
SERVICE_IDS = ["documents", "voice"]
SCHEMA = "ods.extension-lifecycle-receipt-api.v1"
ROOT_NAME = ".assistant-lifecycle-receipts"
TOKEN = "synthetic-receipt-host-key"

RECEIPT_RESPONSE_KEYS = frozenset({
    "schema", "kind", "transactionId", "planHash", "operationKey",
    "requestHash", "serviceIds", "eventHash", "outcome", "evidenceHash",
    "startedEventHash",
})
SNAPSHOT_RESPONSE_KEYS = frozenset({
    "schema", "transactionId", "planHash", "operationKey", "state",
    "startedReceipt", "terminalReceipt",
})


def begin_request(**changes):
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


def finish_request(**changes):
    value = begin_request()
    value.update({"outcome": "completed", "evidenceHash": EVIDENCE_HASH})
    value.update(changes)
    return value


def snapshot_request(**changes):
    value = {
        "schema": SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": "stage",
    }
    value.update(changes)
    return value


@pytest.fixture()
def host_server(tmp_path):
    agent_path = BIN_DIR / "ods-host-agent.py"
    spec = importlib.util.spec_from_file_location(
        "_extension_receipt_host_agent", agent_path
    )
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    spec.loader.exec_module(agent)

    agent.AGENT_API_KEY = TOKEN
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    agent.DATA_DIR = tmp_path
    agent._extension_lifecycle_receipts = receipts
    agent._lifecycle_receipt_store = None

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
        expect_no_store=True,
        transfer_encoding=False,
        duplicate_content_length=False,
    ):
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            payload = raw if raw is not None else json.dumps(body).encode("utf-8")
            if transfer_encoding or duplicate_content_length:
                connection.putrequest("POST", path)
                connection.putheader("Authorization", "Bearer " + token)
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


def assert_receipt_shape(
    receipt: dict,
    kind: str,
    operation_key: str = "stage",
    request_hash: str = REQUEST_HASH,
    outcome: str = "completed",
    evidence_hash: str = EVIDENCE_HASH,
) -> None:
    assert set(receipt) == RECEIPT_RESPONSE_KEYS
    assert receipt["schema"] == SCHEMA
    assert receipt["kind"] == kind
    assert receipt["transactionId"] == TRANSACTION_ID
    assert receipt["planHash"] == PLAN_HASH
    assert receipt["operationKey"] == operation_key
    assert receipt["requestHash"] == request_hash
    assert receipt["serviceIds"] == SERVICE_IDS
    assert isinstance(receipt["eventHash"], str) and len(receipt["eventHash"]) == 64
    if kind == "started":
        assert receipt["outcome"] is None
        assert receipt["evidenceHash"] is None
        assert receipt["startedEventHash"] is None
    else:
        assert receipt["outcome"] == outcome
        assert receipt["evidenceHash"] == evidence_hash
        assert isinstance(receipt["startedEventHash"], str)


def test_receipt_round_trip_is_idempotent_chained_and_no_store(
    host_server, host_request
):
    _agent, _listener = host_server

    status, started = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    assert_receipt_shape(started, "started")

    status, replay = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200 and replay == started

    status, terminal = host_request(
        "/v1/extension/lifecycle-receipt/finish", finish_request()
    )
    assert status == 200
    assert_receipt_shape(terminal, "terminal")
    assert terminal["startedEventHash"] == started["eventHash"]

    status, replay_terminal = host_request(
        "/v1/extension/lifecycle-receipt/finish", finish_request()
    )
    assert status == 200 and replay_terminal == terminal

    # begin after the terminal converges on the terminal: a started receipt
    # is never turned back into an unresolved state.
    status, begin_after_finish = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200 and begin_after_finish == terminal


def test_snapshot_covers_absent_started_completed_and_failed(
    host_server, host_request
):
    _agent, _listener = host_server

    status, absent = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 200
    assert set(absent) == SNAPSHOT_RESPONSE_KEYS
    assert absent["planHash"] == PLAN_HASH
    assert absent["state"] == "absent"
    assert absent["startedReceipt"] is None
    assert absent["terminalReceipt"] is None

    status, _started = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    status, started_snapshot = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 200
    assert started_snapshot["state"] == "started"
    assert_receipt_shape(started_snapshot["startedReceipt"], "started")
    assert started_snapshot["terminalReceipt"] is None

    status, _terminal = host_request(
        "/v1/extension/lifecycle-receipt/finish", finish_request()
    )
    status, completed = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 200
    assert completed["state"] == "completed"
    assert_receipt_shape(completed["startedReceipt"], "started")
    assert_receipt_shape(completed["terminalReceipt"], "terminal")
    assert (
        completed["terminalReceipt"]["startedEventHash"]
        == completed["startedReceipt"]["eventHash"]
    )

    # An exact terminal repeat stays idempotent; a divergent terminal is a
    # conflict, never a silent rewrite of the completed outcome.
    status, conflict = host_request(
        "/v1/extension/lifecycle-receipt/finish",
        finish_request(outcome="failed", evidenceHash=OTHER_EVIDENCE_HASH),
    )
    assert status == 409
    assert conflict == {"error": {"code": "lifecycle-receipt-conflict"}}


def test_snapshot_failed_state_is_reported_for_a_separate_operation(
    host_server, host_request
):
    _agent, _listener = host_server
    status, _started = host_request(
        "/v1/extension/lifecycle-receipt/begin",
        begin_request(operationKey="verify", requestHash="a" * 64),
    )
    assert status == 200
    status, terminal = host_request(
        "/v1/extension/lifecycle-receipt/finish",
        finish_request(operationKey="verify", requestHash="a" * 64,
                       outcome="failed"),
    )
    assert status == 200
    assert terminal["outcome"] == "failed"
    status, failed_snapshot = host_request(
        "/v1/extension/lifecycle-receipt/snapshot",
        snapshot_request(operationKey="verify"),
    )
    assert status == 200
    assert failed_snapshot["state"] == "failed"
    assert failed_snapshot["terminalReceipt"]["outcome"] == "failed"
    assert_receipt_shape(
        failed_snapshot["startedReceipt"],
        "started",
        operation_key="verify",
        request_hash="a" * 64,
    )
    assert_receipt_shape(
        failed_snapshot["terminalReceipt"],
        "terminal",
        operation_key="verify",
        request_hash="a" * 64,
        outcome="failed",
    )


def test_auth_gate_disabled_gate_and_unavailable_module_fail_closed(
    host_server, host_request
):
    agent, _listener = host_server

    assert host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request(), token="wrong"
    )[0] == 403
    assert agent._lifecycle_receipt_store is None

    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    try:
        status, result = host_request(
            "/v1/extension/lifecycle-receipt/begin", begin_request()
        )
    finally:
        agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    assert status == 404
    assert result == {"error": {"code": "not-found"}}
    assert agent._lifecycle_receipt_store is None

    module = agent._extension_lifecycle_receipts
    agent._extension_lifecycle_receipts = None
    try:
        status, result = host_request(
            "/v1/extension/lifecycle-receipt/begin", begin_request()
        )
    finally:
        agent._extension_lifecycle_receipts = module
    assert status == 503
    assert result == {"error": {"code": "lifecycle-receipt-store-unavailable"}}
    assert agent._lifecycle_receipt_store is None


def test_strict_parser_rejects_ambiguous_or_oversized_bodies(
    host_server, host_request
):
    agent, _listener = host_server

    duplicate = (
        b'{"schema":"'
        + SCHEMA.encode()
        + b'","schema":"duplicate","transactionId":"txn-'
        + b"1" * 24
        + b'"}'
    )
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/begin", raw=duplicate
    )
    assert status == 400
    assert result == {"error": {"code": "invalid-lifecycle-receipt-request"}}
    assert "duplicate" not in json.dumps(result)

    for raw, expected_status in (
        (b"[]", 400),
        (b"null", 400),
        (b"{}", 422),
        (b"\xff\xfe\x01", 400),
        (json.dumps(begin_request()).encode() + b" trailing", 400),
        (b"x" * (agent._LIFECYCLE_RECEIPT_MAX_BODY + 1), 413),
    ):
        status, result = host_request(
            "/v1/extension/lifecycle-receipt/begin", raw=raw
        )
        assert status == expected_status
        assert "error" in result

    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin",
        raw=json.dumps(begin_request()).encode(),
        transfer_encoding=True,
    )
    assert status == 400
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin",
        raw=json.dumps(begin_request()).encode(),
        duplicate_content_length=True,
    )
    assert status == 400


def test_exact_key_sets_are_enforced(host_server, host_request):
    _agent, _listener = host_server

    for path, request in (
        ("/v1/extension/lifecycle-receipt/begin", begin_request()),
        ("/v1/extension/lifecycle-receipt/finish", finish_request()),
        ("/v1/extension/lifecycle-receipt/snapshot", snapshot_request()),
    ):
        for key in request:
            reduced = {k: v for k, v in request.items() if k != key}
            status, result = host_request(path, reduced)
            assert status == 422, (path, key)
            assert result == {
                "error": {"code": "invalid-lifecycle-receipt-request"}
            }

    for path, request in (
        ("/v1/extension/lifecycle-receipt/begin", begin_request(unexpected="x")),
        ("/v1/extension/lifecycle-receipt/finish", finish_request(unexpected="x")),
        (
            "/v1/extension/lifecycle-receipt/snapshot",
            snapshot_request(unexpected="x"),
        ),
    ):
        status, result = host_request(path, request)
        assert status == 422
        assert result == {"error": {"code": "invalid-lifecycle-receipt-request"}}

    for changes in (
        {"transactionId": 12345},
        {"transactionId": "bad"},
        {"planHash": ["3" * 64]},
        {"planHash": "A" * 64},
        {"operationKey": {"stage": True}},
        {"operationKey": "install"},
        {"requestHash": None},
        {"requestHash": "5" * 63},
        {"serviceIds": "documents"},
        {"serviceIds": []},
        {"serviceIds": ["documents", 42]},
        {"serviceIds": [True]},
        {"serviceIds": ["documents", "documents"]},
    ):
        status, _result = host_request(
            "/v1/extension/lifecycle-receipt/begin", begin_request(**changes)
        )
        assert status == 422, changes

    for changes in (
        {"outcome": "unknown"},
        {"outcome": 1},
        {"evidenceHash": "short"},
    ):
        status, _result = host_request(
            "/v1/extension/lifecycle-receipt/finish", finish_request(**changes)
        )
        assert status == 422, changes
    # Floats are rejected by the strict parser before key validation.
    for changes in (
        {"evidenceHash": 3.14},
        {"planHash": 3.14},
    ):
        status, _result = host_request(
            "/v1/extension/lifecycle-receipt/begin", begin_request(**changes)
        )
        assert status == 400, changes


def test_plan_hash_is_validated_before_store_construction(host_server, host_request):
    agent, _listener = host_server
    requests = (
        ("/v1/extension/lifecycle-receipt/begin", begin_request),
        ("/v1/extension/lifecycle-receipt/finish", finish_request),
        ("/v1/extension/lifecycle-receipt/snapshot", snapshot_request),
    )
    invalid_hashes = (
        None,
        True,
        1,
        [],
        {},
        "",
        "3" * 63,
        "3" * 65,
        "g" * 64,
        "A" * 64,
        PLAN_HASH + "\n",
    )

    for path, request_factory in requests:
        for plan_hash in invalid_hashes:
            status, result = host_request(
                path, request_factory(planHash=plan_hash)
            )
            assert status == 422, (path, plan_hash)
            assert result == {
                "error": {"code": "invalid-lifecycle-receipt-request"}
            }
            assert agent._lifecycle_receipt_store is None


def test_lease_keys_and_caller_paths_are_never_accepted(host_server, host_request):
    agent, _listener = host_server

    # A lease acquire body is not a receipt request.
    lease_body = {
        "schema": "ods.extension-lease-api.v1",
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "serviceIds": SERVICE_IDS,
        "ttlSeconds": 30,
    }
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", lease_body
    )
    assert status == 422

    for extra in ({"ttlSeconds": 30}, {"leaseId": "x"}, {"leaseToken": "y"}):
        status, _result = host_request(
            "/v1/extension/lifecycle-receipt/begin", begin_request(**extra)
        )
        assert status == 422

    for path, request in (
        ("/v1/extension/lifecycle-receipt/begin", begin_request()),
        ("/v1/extension/lifecycle-receipt/finish", finish_request()),
        ("/v1/extension/lifecycle-receipt/snapshot", snapshot_request()),
    ):
        status, result = host_request(
            path + "?transactionId=do-not-log",
            request,
        )
        assert status == 404
        assert result == {"error": {"code": "not-found"}}

    # The store root is fixed directly under DATA_DIR; no caller path exists.
    host_request("/v1/extension/lifecycle-receipt/begin", begin_request())
    root = Path(agent._lifecycle_receipt_store.root)
    assert root.parent == agent.DATA_DIR
    assert root.name == ROOT_NAME


def test_receipt_store_root_is_fixed_under_data_dir(host_server, host_request):
    agent, _listener = host_server
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    root = Path(agent._lifecycle_receipt_store.root)
    assert root.parent == agent.DATA_DIR
    assert root.name == ROOT_NAME
    assert root.is_dir()
    if os.name == "posix":
        assert root.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_invalid_existing_root_maps_to_integrity(host_server, host_request):
    agent, _listener = host_server
    root = agent.DATA_DIR / ROOT_NAME
    root.mkdir(mode=0o700)
    root.chmod(0o755)

    status, result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-integrity"}}
    assert agent._lifecycle_receipt_store is None


@pytest.mark.parametrize(
    "store_error",
    [OSError("private-os-detail"), RuntimeError("private-runtime-detail")],
)
def test_store_initialization_failures_are_public_safe(
    host_server, host_request, monkeypatch, store_error
):
    agent, _listener = host_server

    def fail_store(_root):
        raise store_error

    monkeypatch.setattr(
        agent._extension_lifecycle_receipts,
        "LifecycleReceiptStore",
        fail_store,
    )
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 503
    assert result == {"error": {"code": "lifecycle-receipt-store-unavailable"}}
    assert "private" not in json.dumps(result)
    assert agent._lifecycle_receipt_store is None


def test_changing_data_dir_rebuilds_the_store_for_tests(
    host_server, host_request, tmp_path
):
    agent, _listener = host_server
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    first_root = Path(agent._lifecycle_receipt_store.root)
    assert first_root.parent == tmp_path

    other = tmp_path / "other-data"
    other.mkdir()
    agent.DATA_DIR = other
    try:
        status, _result = host_request(
            "/v1/extension/lifecycle-receipt/begin", begin_request()
        )
        assert status == 200
        assert Path(agent._lifecycle_receipt_store.root).parent == other
    finally:
        agent.DATA_DIR = tmp_path
        agent._lifecycle_receipt_store = None


def test_divergent_begin_and_service_reordering_conflict(host_server, host_request):
    _agent, _listener = host_server
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/begin",
        begin_request(planHash=OTHER_PLAN_HASH),
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-conflict"}}
    for service_ids in (
        ["voice", "documents"],
        ["documents"],
        ["documents", "voice", "web-search"],
    ):
        status, result = host_request(
            "/v1/extension/lifecycle-receipt/begin",
            begin_request(serviceIds=service_ids),
        )
        assert status == 409
        assert result == {"error": {"code": "lifecycle-receipt-conflict"}}


def test_snapshot_is_bound_to_the_exact_plan_hash(host_server, host_request):
    _agent, _listener = host_server
    status, absent = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 200
    assert absent["planHash"] == PLAN_HASH

    status, _started = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/snapshot",
        snapshot_request(planHash=OTHER_PLAN_HASH),
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-conflict"}}


def test_divergent_finish_conflicts(host_server, host_request):
    _agent, _listener = host_server
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/finish", finish_request()
    )
    assert status == 200
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/finish",
        finish_request(evidenceHash=OTHER_EVIDENCE_HASH),
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-conflict"}}


def test_concurrent_http_replays_converge_on_one_receipt(host_server, host_request):
    _agent, _listener = host_server

    with ThreadPoolExecutor(max_workers=8) as pool:
        begun = list(
            pool.map(
                lambda _index: host_request(
                    "/v1/extension/lifecycle-receipt/begin", begin_request()
                ),
                range(8),
            )
        )
    assert {status for status, _body in begun} == {200}
    assert len({json.dumps(body, sort_keys=True) for _status, body in begun}) == 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        finished = list(
            pool.map(
                lambda _index: host_request(
                    "/v1/extension/lifecycle-receipt/finish", finish_request()
                ),
                range(8),
            )
        )
    assert {status for status, _body in finished} == {200}
    assert len({json.dumps(body, sort_keys=True) for _status, body in finished}) == 1
    terminal = finished[0][1]
    assert terminal["startedEventHash"] == begun[0][1]["eventHash"]


def test_service_order_is_preserved(host_server, host_request):
    _agent, _listener = host_server
    status, started = host_request(
        "/v1/extension/lifecycle-receipt/begin",
        begin_request(serviceIds=["voice", "documents"]),
    )
    assert status == 200
    assert started["serviceIds"] == ["voice", "documents"]
    status, snapshot = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 200
    assert snapshot["startedReceipt"]["serviceIds"] == ["voice", "documents"]


def test_hash_chain_is_enforced(host_server, host_request, tmp_path):
    _agent, _listener = host_server
    status, started = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    status, terminal = host_request(
        "/v1/extension/lifecycle-receipt/finish", finish_request()
    )
    assert status == 200
    assert terminal["startedEventHash"] == started["eventHash"]

    started_raw = next(tmp_path.rglob("*.started.json")).read_bytes()
    terminal_raw = next(tmp_path.rglob("*.terminal.json")).read_bytes()
    started_payload = json.loads(started_raw)
    terminal_payload = json.loads(terminal_raw)
    assert started_payload["eventHash"] == started["eventHash"]
    assert terminal_payload["startedEventHash"] == started["eventHash"]


def test_orphan_terminal_is_integrity_failure(host_server, host_request):
    agent, _listener = host_server

    # Finish without a started receipt is invalid, never trusted.
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/finish", finish_request()
    )
    assert status == 422
    assert result == {"error": {"code": "invalid-lifecycle-receipt"}}

    host_request("/v1/extension/lifecycle-receipt/begin", begin_request())
    status, _terminal = host_request(
        "/v1/extension/lifecycle-receipt/finish", finish_request()
    )
    assert status == 200

    root = Path(agent._lifecycle_receipt_store.root)
    started_path = next(root.rglob("*.started.json"))
    if os.name == "posix":
        started_path.chmod(0o644)
    started_path.unlink()

    status, result = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-integrity"}}
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-integrity"}}


def test_corrupt_receipt_is_integrity_failure(host_server, host_request):
    agent, _listener = host_server
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    root = Path(agent._lifecycle_receipt_store.root)
    started_path = next(root.rglob("*.started.json"))
    if os.name == "posix":
        started_path.chmod(0o644)
    started_path.write_bytes(b"{}\n")
    if os.name == "posix":
        started_path.chmod(0o444)

    status, result = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-integrity"}}
    status, result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 409
    assert result == {"error": {"code": "lifecycle-receipt-integrity"}}


def test_unavailable_module_fails_closed_before_store_creation(
    host_server, host_request
):
    agent, _listener = host_server
    module = agent._extension_lifecycle_receipts
    agent._extension_lifecycle_receipts = None
    try:
        for path in (
            "/v1/extension/lifecycle-receipt/begin",
            "/v1/extension/lifecycle-receipt/finish",
            "/v1/extension/lifecycle-receipt/snapshot",
        ):
            status, result = host_request(path, begin_request())
            assert status == 503
            assert result == {
                "error": {"code": "lifecycle-receipt-store-unavailable"}
            }
    finally:
        agent._extension_lifecycle_receipts = module
    assert agent._lifecycle_receipt_store is None
    assert not (agent.DATA_DIR / ROOT_NAME).exists()


def test_no_caller_path_is_honoured(host_server, host_request, tmp_path):
    agent, _listener = host_server
    decoy = tmp_path / "decoy-receipts"
    decoy.mkdir()
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    assert Path(agent._lifecycle_receipt_store.root) == tmp_path / ROOT_NAME
    assert not (decoy / ROOT_NAME).exists()
    assert not any(decoy.iterdir())


def test_legacy_lease_routes_keep_their_own_contract(host_server, host_request):
    _agent, _listener = host_server
    status, result = host_request(
        "/v1/extension/lease/acquire",
        {
            "schema": "ods.extension-lease-api.v1",
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "serviceIds": SERVICE_IDS,
        },
    )
    assert status in (403, 422, 503)
    assert "lifecycle-receipt" not in json.dumps(result)


def test_no_production_import_or_executor_activation():
    dashboard_api = Path(__file__).resolve().parents[1]
    for module_name in (
        "extension_transaction_executor.py",
        "extension_transaction_production.py",
        "extension_transaction_runtime.py",
        "extension_transactions.py",
    ):
        source = (dashboard_api / module_name).read_text(
            encoding="utf-8", errors="ignore"
        )
        assert "extension_lifecycle_receipts" not in source, module_name

    agent_source = (BIN_DIR / "ods-host-agent.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert (
        "import extension_lifecycle_receipts as _extension_lifecycle_receipts"
        in agent_source
    )
    # The store is created lazily inside _get_lifecycle_receipt_store(),
    # never at import time and never anywhere else.
    call_sites = [
        line.strip()
        for line in agent_source.splitlines()
        if "LifecycleReceiptStore(" in line
    ]
    assert call_sites == [
        "_extension_lifecycle_receipts.LifecycleReceiptStore(root)"
    ]
    assert "_lifecycle_receipt_store = None" in agent_source


def test_no_background_thread_or_cleanup_actions(host_server, host_request):
    agent, listener = host_server
    status, _result = host_request(
        "/v1/extension/lifecycle-receipt/begin", begin_request()
    )
    assert status == 200
    listener.service_actions()
    root = Path(agent._lifecycle_receipt_store.root)
    assert len(list(root.iterdir())) == 1
    status, snapshot = host_request(
        "/v1/extension/lifecycle-receipt/snapshot", snapshot_request()
    )
    assert status == 200
    assert snapshot["state"] == "started"
