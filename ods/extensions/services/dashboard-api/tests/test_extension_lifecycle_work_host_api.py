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
from extension_library_tree_digest import digest_extension_tree  # noqa: E402

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


def test_application_observation_route_is_leased_and_never_dispatches_apply(
    host_server, host_request,
):
    agent, _listener = host_server
    receipt_root = agent.DATA_DIR / agent._LIFECYCLE_RECEIPT_ROOT_NAME
    receipt_root.mkdir(mode=0o700)
    agent._load_extension_observation_plan = lambda command: command
    calls = []

    def observer_factory(_receipts, active_lease):
        def observe(command):
            assert active_lease() is True
            calls.append(command.operation_key)
            return agent._application_observation_module.ObservationResult(
                service_id="gitea",
                classification="APPLIED",
                identity_sha256="a" * 64,
                record_sha256="b" * 64,
                containers=(),
            )

        return observe

    agent._get_extension_application_observer = observer_factory
    agent._extension_lifecycle_work_dispatcher = lambda _command: pytest.fail(
        "observation selected a mutating dispatcher"
    )
    grant = acquire_lease(agent, host_request, ["gitea"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="apply:gitea",
        service_ids=["gitea"],
        payload={"operation": {"serviceId": "gitea", "action": "install"}},
    )
    for _ in range(2):
        status, result = host_request(
            "/v1/extension/application-observation", request
        )
        assert status == 200
        assert result == {
            "schema": agent._APPLICATION_OBSERVATION_SCHEMA,
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "operationKey": "apply:gitea",
            "requestHash": request["requestHash"],
            "serviceId": "gitea",
            "classification": "APPLIED",
            "identityHash": "a" * 64,
            "recordHash": "b" * 64,
        }
    assert calls == ["apply:gitea", "apply:gitea"]


def test_application_observation_route_rejects_wrong_plan_and_non_apply(
    host_server, host_request,
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request, ["gitea"])
    lease = lease_evidence(agent, grant)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease,
        operation_key="apply:gitea",
        service_ids=["gitea"],
        payload={"operation": {"serviceId": "gitea", "action": "install"}},
    )
    agent._load_extension_observation_plan = lambda _command: (_ for _ in ()).throw(
        agent._extension_lifecycle_work.LifecycleWorkValidationError(
            "lifecycle-work-plan-mismatch"
        )
    )
    status, result = host_request("/v1/extension/application-observation", request)
    assert status == 409
    assert result == {"error": {"code": "application-observation-plan-mismatch"}}
    assert not (agent.DATA_DIR / agent._LIFECYCLE_RECEIPT_ROOT_NAME).exists()

    non_apply = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease,
        operation_key="verify",
        service_ids=["gitea"],
        payload={"serviceIds": ["gitea"]},
    )
    status, result = host_request("/v1/extension/application-observation", non_apply)
    assert status == 422
    assert result == {"error": {"code": "invalid-application-observation-request"}}

    wrong_lease = dict(request, lease=lease_evidence(
        agent, grant, planHash=OTHER_PLAN_HASH
    ))
    status, result = host_request("/v1/extension/application-observation", wrong_lease)
    assert status == 403
    assert result == {"error": {"code": "lease-binding-mismatch"}}


def test_application_observation_route_fails_closed_without_receipt_mutation(
    host_server, host_request,
):
    agent, _listener = host_server
    receipt_root = agent.DATA_DIR / agent._LIFECYCLE_RECEIPT_ROOT_NAME
    receipt_root.mkdir(mode=0o700)
    agent._load_extension_observation_plan = lambda command: command
    receipt_store = agent._lifecycle_receipt_store
    assert receipt_store.states == {}

    def unavailable(_command):
        raise agent._application_observation_module.ApplicationObservationError(
            "application-evidence-current-drift"
        )

    agent._get_extension_application_observer = lambda *_args: unavailable
    grant = acquire_lease(agent, host_request, ["gitea"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="apply:gitea",
        service_ids=["gitea"],
        payload={"operation": {"serviceId": "gitea", "action": "install"}},
    )
    status, result = host_request("/v1/extension/application-observation", request)
    assert status == 503
    assert result == {"error": {"code": "application-observation-unavailable"}}
    assert receipt_store.states == {}


def test_application_observation_route_requires_auth_and_feature_gate(
    host_server, host_request,
):
    agent, _listener = host_server
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        operation_key="apply:gitea",
        service_ids=["gitea"],
        payload={"operation": {"serviceId": "gitea", "action": "install"}},
    )
    status, _body = host_request(
        "/v1/extension/application-observation", request, token="wrong-key"
    )
    assert status == 403
    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    status, body = host_request("/v1/extension/application-observation", request)
    assert status == 404
    assert body == {"error": {"code": "not-found"}}
    assert agent._lifecycle_receipt_store.states == {}


@pytest.mark.skipif(sys.platform != "linux", reason="Linux observer custody")
def test_host_composes_observer_without_library_apply_dispatcher(host_server):
    agent, _listener = host_server
    observer = agent._get_extension_application_observer(
        agent._lifecycle_receipt_store, lambda: True
    )
    assert type(observer) is agent._application_observation_adapter_module.ApplicationObservationAdapter
    assert observer._loader is agent._load_extension_observation_plan
    assert agent._extension_lifecycle_work_dispatcher is None


def bind_fixture_plan(agent, command):
    if command.operation_key == "configure":
        plan = agent._extension_lifecycle_plan
        runtime = agent._configuration_effect_runtime_module
        canonical = (
            json.dumps(
                {"configuration": runtime.CANARY_CONFIGURATION},
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        operation = plan.PlannedOperation(runtime.CANARY_SERVICE_ID, "install")
        definition = plan.PlannedDefinition(
            service_id=runtime.CANARY_SERVICE_ID,
            service_type="docker",
            manifest_schema_version=runtime.CANARY_MANIFEST_SCHEMA,
            version=runtime.CANARY_VERSION,
            data_schema_version=runtime.CANARY_DATA_SCHEMA_VERSION,
            definition_sha256=runtime.CANARY_DEFINITION_SHA256,
            compose_sha256=runtime.CANARY_COMPOSE_SHA256,
            definition_source="builtin",
            compose_file="compose.yaml",
            images=(
                plan.PlannedImage(
                    reference=runtime.CANARY_IMAGE_REFERENCE,
                    digest=runtime.CANARY_IMAGE_DIGEST,
                    download_bytes=runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
                ),
            ),
            builds=(),
            canonical_document=canonical,
            host_ports=(),
            exclusive=(),
        )
        return replace(
            command,
            plan_material=plan.LifecyclePlanMaterial(
                schema=plan.PLAN_MATERIAL_SCHEMA,
                transaction_id=command.transaction_id,
                plan_hash=command.plan_hash,
                state="configuring",
                operations=(operation,),
                definitions=(definition,),
            ),
        )
    if command.operation_key in {"backup", "restore"}:
        plan = agent._extension_lifecycle_plan
        runtime = agent._data_backup_runtime_module
        canonical = (
            json.dumps(
                {"data": [runtime.CANARY_DATA_RECORD]},
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        operation = plan.PlannedOperation(runtime.CANARY_SERVICE_ID, "install")
        definition = plan.PlannedDefinition(
            service_id=runtime.CANARY_SERVICE_ID,
            service_type="docker",
            manifest_schema_version=runtime.CANARY_MANIFEST_SCHEMA,
            version=runtime.CANARY_VERSION,
            data_schema_version=runtime.CANARY_DATA_SCHEMA_VERSION,
            definition_sha256=runtime.CANARY_DEFINITION_SHA256,
            compose_sha256=runtime.CANARY_COMPOSE_SHA256,
            definition_source="builtin",
            compose_file="compose.yaml",
            images=(
                plan.PlannedImage(
                    reference=runtime.CANARY_IMAGE_REFERENCE,
                    digest=runtime.CANARY_IMAGE_DIGEST,
                    download_bytes=runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
                ),
            ),
            builds=(),
            canonical_document=canonical,
            host_ports=(),
            exclusive=(),
        )
        state = (
            "configuring" if command.operation_key == "backup" else "reconciling"
        )
        return replace(
            command,
            plan_material=plan.LifecyclePlanMaterial(
                schema=plan.PLAN_MATERIAL_SCHEMA,
                transaction_id=command.transaction_id,
                plan_hash=command.plan_hash,
                state=state,
                operations=(operation,),
                definitions=(definition,),
            ),
        )
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
                    agent.DATA_DIR
                    / "extensions-library"
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
            source_tree_sha256=digest_extension_tree(
                agent.DATA_DIR / "extensions-library" / operation.service_id
            ),
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


def data_work_request(agent, lease, operation_key):
    runtime = agent._data_backup_runtime_module
    return work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease,
        operation_key=operation_key,
        service_ids=[runtime.CANARY_SERVICE_ID],
        payload={"serviceIds": [runtime.CANARY_SERVICE_ID]},
    )


def configuration_work_request(agent, lease):
    runtime = agent._configuration_effect_runtime_module
    return work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease,
        operation_key="configure",
        service_ids=[runtime.CANARY_SERVICE_ID],
        payload={"serviceIds": [runtime.CANARY_SERVICE_ID]},
    )


def bind_configuration_runtime(agent, *, dispatcher, started_observer):
    agent._configuration_effect_runtime = SimpleNamespace(
        dispatcher=dispatcher,
        started_observer=started_observer,
    )
    agent._configuration_effect_runtime_binding = (
        agent.INSTALL_DIR,
        agent.DATA_DIR,
        agent._extension_lifecycle_plan_loader,
        agent._AssistantFirstSecretStore,
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
    builtins.mkdir(mode=0o700)
    builtins.chmod(0o700)
    users.mkdir(mode=0o700)
    users.chmod(0o700)
    for service_id in (
        "documents", "voice", "dashboard", "searxng",
        "gitea", "miniflux", "ntfy", "ollama",
    ):
        extension = builtins / service_id
        extension.mkdir(mode=0o700)
        extension.chmod(0o700)
        manifest = extension / "manifest.yaml"
        manifest.write_text("service: {}\n", encoding="utf-8")
        manifest.chmod(0o600)

    agent.AGENT_API_KEY = TOKEN
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    agent.INSTALL_DIR = tmp_path / "install"
    agent.INSTALL_DIR.mkdir(mode=0o700)
    agent.INSTALL_DIR.chmod(0o700)
    application_root = agent.INSTALL_DIR / ".ods-assistant-first" / "applications"
    application_root.mkdir(mode=0o700, parents=True)
    application_root.parent.chmod(0o700)
    application_root.chmod(0o700)
    agent.DATA_DIR = agent.INSTALL_DIR / "data"
    agent.DATA_DIR.mkdir(mode=0o700)
    agent.DATA_DIR.chmod(0o700)
    library = agent.DATA_DIR / "extensions-library"
    library.mkdir(mode=0o700)
    library.chmod(0o700)
    for service_id in (
        "documents", "voice", "dashboard", "searxng",
        "gitea", "miniflux", "ntfy", "ollama",
    ):
        extension = library / service_id
        extension.mkdir(mode=0o700)
        extension.chmod(0o700)
        manifest = extension / "manifest.yaml"
        manifest.write_text("service: {}\n", encoding="utf-8")
        manifest.chmod(0o600)
    config_root = agent.INSTALL_DIR / "config"
    config_root.mkdir(mode=0o700)
    config_root.chmod(0o700)
    stage_root = agent.DATA_DIR / "assistant-first" / "artifact-stage"
    stage_root.mkdir(mode=0o700, parents=True)
    stage_root.chmod(0o700)
    reservation_root = agent.DATA_DIR / "assistant-first" / "resource-reservations"
    reservation_root.mkdir(mode=0o700, parents=True)
    reservation_root.chmod(0o700)
    data_backup_root = agent.DATA_DIR / "assistant-first" / "data-backups"
    data_backup_root.mkdir(mode=0o700, parents=True)
    data_backup_root.chmod(0o700)
    (agent.DATA_DIR / "assistant-first").chmod(0o700)
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
    agent._image_artifact_runtime = None
    agent._image_artifact_runtime_plan_loader = None
    agent._data_backup_runtime = None
    agent._data_backup_runtime_binding = None
    agent._configuration_effect_runtime = None
    agent._configuration_effect_runtime_binding = None
    agent._resource_reservation_runtime = None
    agent._resource_reservation_runtime_data_dir = None
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
    assert len(seen) == 2
    assert active_at_response == [False, False]
    assert lock.locked()

    agent._extension_lifecycle_work_dispatcher = lambda _command: "9" * 64
    drift_status, drift = host_request("/v1/extension/lifecycle-work", request)
    assert drift_status == 503
    assert drift == {"error": {"code": "lifecycle-work-operation-failed"}}
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["terminalReceipt"]["evidenceHash"] == EVIDENCE_HASH


def test_approved_library_verify_route_rechecks_current_state_on_replay(
    host_server, host_request
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request, ["gitea"])
    evidence = lease_evidence(agent, grant)
    seen = []
    agent._extension_lifecycle_work_dispatcher = None

    def build(active_lease):
        def dispatch(command):
            assert active_lease() is True
            seen.append(command)
            return EVIDENCE_HASH

        return dispatch

    agent._get_extension_library_verify_runtime = build
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        evidence,
        service_ids=["gitea"],
    )
    begin_receipt(agent, host_request, request)

    first_status, first = host_request("/v1/extension/lifecycle-work", request)
    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert first_status == replay_status == 200
    assert first == replay
    assert len(seen) == 2
    assert all(command.service_ids == ("gitea",) for command in seen)


def test_unapproved_library_verify_route_stays_unavailable(host_server, host_request):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request, ["documents"])
    evidence = lease_evidence(agent, grant)
    agent._extension_lifecycle_work_dispatcher = None
    request = work_request(agent._extension_lifecycle_work.REQUEST_SCHEMA, evidence)
    begin_receipt(agent, host_request, request)

    status, body = host_request("/v1/extension/lifecycle-work", request)
    assert status == 503
    assert body == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}


@pytest.mark.parametrize(
    "operation_key,payload",
    [
        ("configure", {"serviceIds": ["documents"]}),
        ("verify", {"serviceIds": ["documents"]}),
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
def test_still_dormant_operations_remain_inert_but_authenticate_the_lease(
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


def test_approved_library_apply_selects_lease_bound_receipted_runtime(
    host_server, host_request
):
    agent, _listener = host_server
    service_id = "gitea"
    grant = acquire_lease(agent, host_request, [service_id])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key=f"apply:{service_id}",
        service_ids=[service_id],
        payload={
            "operation": {"serviceId": service_id, "action": "install"}
        },
    )
    begin_receipt(agent, host_request, request)
    calls = []

    def build(receipt_store, active_lease):
        assert receipt_store is agent._lifecycle_receipt_store

        def observe(command):
            calls.append(("observe", command, active_lease()))
            return agent._extension_lifecycle_work.LifecycleWorkStartedObservation(
                state="missing"
            )

        def dispatch(command):
            calls.append(("dispatch", command, active_lease()))
            return EVIDENCE_HASH

        return SimpleNamespace(
            dispatcher=dispatch,
            started_observer=observe,
        )

    agent._get_extension_library_application_runtime = build
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received library apply"))
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert [item[0] for item in calls] == ["observe", "dispatch"]
    assert all(item[2] is True for item in calls)
    assert calls[0][1].plan_material is None
    assert calls[1][1].plan_material == {"bound": True}
    assert grant["leaseToken"] not in repr(calls)


def test_approved_library_apply_never_falls_back_to_generic_dispatcher(
    host_server, host_request
):
    agent, _listener = host_server
    service_id = "gitea"
    grant = acquire_lease(agent, host_request, [service_id])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key=f"apply:{service_id}",
        service_ids=[service_id],
        payload={
            "operation": {"serviceId": service_id, "action": "install"}
        },
    )
    agent._get_extension_library_application_runtime = lambda *_args: None
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received library apply"))
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}


def test_unapproved_library_apply_never_falls_back_to_generic_dispatcher(
    host_server, host_request
):
    agent, _listener = host_server
    service_id = "documents"
    grant = acquire_lease(agent, host_request, [service_id])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key=f"apply:{service_id}",
        service_ids=[service_id],
        payload={
            "operation": {"serviceId": service_id, "action": "install"}
        },
    )
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received unapproved apply"))
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires POSIX custody semantics")
def test_approved_library_runtime_factory_is_effect_free_and_fully_composed(
    host_server
):
    agent, _listener = host_server
    application_root = (
        agent.INSTALL_DIR / ".ods-assistant-first" / "applications"
    )

    runtime = agent._get_extension_library_application_runtime(
        agent._lifecycle_receipt_store,
        lambda: True,
    )

    assert runtime is not None
    assert callable(runtime.dispatcher)
    assert callable(runtime.started_observer)
    assert list(application_root.iterdir()) == []
    assert list(agent.USER_EXTENSIONS_DIR.iterdir()) == []


@pytest.mark.parametrize(
    "operation_key,payload",
    [
        ("release", {"serviceIds": ["documents"]}),
        (
            "reserve:documents",
            {"operation": {"serviceId": "documents", "action": "install"}},
        ),
    ],
)
def test_reservation_operations_authenticate_lease_and_dispatch_to_runtime(
    host_server, host_request, operation_key, payload
):
    """release and reserve:<id> must authenticate the lease and dispatch
    to the reservation runtime (not return dispatcher-unavailable)."""
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key=operation_key,
        payload=payload,
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    # Must be 200 (dispatched via reservation runtime) or 409 (receipt issue),
    # NOT 503 (dispatcher-unavailable) which would mean they're still dormant
    assert status in (200, 409)
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

    def bind(value, transaction, *, require_attested_approval=False):
        calls.append(("bind", value, transaction, require_attested_approval))
        return replace(value, plan_material={"approved": True})

    agent._get_extension_transaction_store = Store
    agent._extension_transactions = SimpleNamespace(TransactionError=RuntimeError)
    agent._extension_lifecycle_plan = SimpleNamespace(bind_lifecycle_plan=bind)

    bound = agent._load_extension_lifecycle_plan(command)

    assert bound.plan_material == {"approved": True}
    assert calls == [
        ("read", TRANSACTION_ID),
        ("bind", command, stored, False),
    ]


def test_host_observation_loader_requires_attested_read_only_apply(host_server):
    agent, _listener = host_server
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        operation_key="apply:gitea",
        service_ids=["gitea"],
        payload={"operation": {"serviceId": "gitea", "action": "install"}},
    )
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(request)
    stored = {"transactionId": TRANSACTION_ID}
    seen = []
    agent._get_extension_transaction_store = lambda: SimpleNamespace(
        read=lambda _transaction_id: stored
    )

    def bind(value, transaction, *, require_attested_approval,
             read_only_observation=False):
        seen.append((value, transaction, require_attested_approval,
                     read_only_observation))
        return replace(value, plan_material={"approved": True})

    agent._extension_lifecycle_plan = SimpleNamespace(bind_lifecycle_plan=bind)
    bound = agent._load_extension_observation_plan(command)
    assert bound.plan_material == {"approved": True}
    assert seen == [(command, stored, True, True)]


@pytest.mark.parametrize(
    "operation_key,service_id,payload,strict",
    [
        (
            "download-and-verify",
            "documents",
            {"operations": [{"serviceId": "documents", "action": "install"}]},
            True,
        ),
        (
            "download-and-verify",
            "searxng",
            {"operations": [{"serviceId": "searxng", "action": "install"}]},
            False,
        ),
        ("verify", "documents", {"serviceIds": ["documents"]}, False),
    ],
)
def test_host_plan_loader_requires_v2_attestation_for_non_canary_images(
    host_server, operation_key, service_id, payload, strict
):
    agent, _listener = host_server
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        operation_key=operation_key,
        service_ids=[service_id],
        payload=payload,
    )
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(request)
    seen = []
    stored = {"transactionId": TRANSACTION_ID}
    agent._get_extension_transaction_store = lambda: SimpleNamespace(
        read=lambda _transaction_id: stored
    )
    agent._extension_lifecycle_plan = SimpleNamespace(
        bind_lifecycle_plan=lambda value, transaction, *,
        require_attested_approval: seen.append(
            (value, transaction, require_attested_approval)
        ) or replace(value, plan_material={"approved": True})
    )

    bound = agent._load_extension_lifecycle_plan(command)

    assert bound.plan_material == {"approved": True}
    assert seen == [(command, stored, strict)]


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


# ── Reservation routing tests ──────────────────────────────────────────────

def test_reserve_selects_reserve_dispatcher_from_cached_runtime(host_server, host_request):
    """reserve:<serviceId> must select reserve_dispatcher from the cached
    resource_reservation_runtime instance, not the generic dispatcher."""
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)

    # Set up the reservation runtime
    res_runtime = agent._get_extension_resource_reservation_runtime()
    assert res_runtime is not None
    seen = []

    def counted_reserve(command):
        seen.append("reserve")
        return EVIDENCE_HASH

    agent._resource_reservation_runtime = type(res_runtime)(
        root=res_runtime.root,
        store=res_runtime.store,
        reserve_dispatcher=counted_reserve,
        release_dispatcher=res_runtime.release_dispatcher,
    )
    agent._extension_lifecycle_work_dispatcher = (
        lambda _c: (_ for _ in ()).throw(AssertionError("generic received reserve"))
    )

    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        evidence,
        operation_key="reserve:documents",
        payload={"operation": {"serviceId": "documents"}},
    )
    begin_receipt(agent, host_request, request)
    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert seen == ["reserve"]


def test_release_selects_release_dispatcher_from_same_cached_runtime(
    host_server, host_request
):
    """release must select release_dispatcher from the same cached runtime
    instance used by reserve."""
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)

    res_runtime = agent._get_extension_resource_reservation_runtime()
    assert res_runtime is not None
    seen = []

    def counted_release(command):
        seen.append("release")
        return EVIDENCE_HASH

    agent._resource_reservation_runtime = type(res_runtime)(
        root=res_runtime.root,
        store=res_runtime.store,
        reserve_dispatcher=res_runtime.reserve_dispatcher,
        release_dispatcher=counted_release,
    )
    agent._extension_lifecycle_work_dispatcher = (
        lambda _c: (_ for _ in ()).throw(AssertionError("generic received release"))
    )

    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        evidence,
        operation_key="release",
        service_ids=["documents"],
        payload={"serviceIds": ["documents"]},
    )
    begin_receipt(agent, host_request, request)
    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert seen == ["release"]


def test_stage_still_selects_artifact_dispatcher(host_server, host_request):
    """stage must still select the artifact-stage dispatcher/observer,
    not the reservation runtime."""
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)

    res_runtime = agent._get_extension_resource_reservation_runtime()
    if res_runtime is not None:
        # Replace the reservation dispatcher so that if it's mistakenly used,
        # we detect it.
        agent._resource_reservation_runtime = type(res_runtime)(
            root=res_runtime.root,
            store=res_runtime.store,
            reserve_dispatcher=lambda _c: (_ for _ in ()).throw(
                AssertionError("reservation used for stage")
            ),
            release_dispatcher=lambda _c: (_ for _ in ()).throw(
                AssertionError("reservation used for stage")
            ),
        )

    artifact_runtime = agent._get_extension_artifact_stage_runtime()
    seen = []
    original_dispatch = artifact_runtime.dispatcher

    def counted_dispatch(command):
        seen.append("stage")
        return original_dispatch(command)

    agent._artifact_stage_runtime = type(artifact_runtime)(
        root=artifact_runtime.root,
        store=artifact_runtime.store,
        dispatcher=counted_dispatch,
        started_observer=artifact_runtime.started_observer,
    )

    request = stage_work_request(agent, evidence)
    begin_receipt(agent, host_request, request)
    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["operationKey"] == "stage"
    assert seen == ["stage"]


def test_download_selects_image_dispatcher_and_started_observer(
    host_server, host_request
):
    """download-and-verify must use its closed image runtime, not generic work."""

    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)
    seen = []

    def observe(_command):
        seen.append("observe")
        return agent._extension_lifecycle_work.LifecycleWorkStartedObservation(
            state="missing"
        )

    def dispatch(command):
        seen.append(("dispatch", command.plan_material is not None))
        return EVIDENCE_HASH

    agent._image_artifact_runtime = SimpleNamespace(
        dispatcher=dispatch,
        started_observer=observe,
    )
    agent._image_artifact_runtime_plan_loader = (
        agent._extension_lifecycle_plan_loader
    )
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received image work"))
    )

    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        evidence,
        operation_key="download-and-verify",
        service_ids=["documents"],
        payload={
            "operations": [{"serviceId": "documents", "action": "install"}]
        },
    )
    begin_receipt(agent, host_request, request)
    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert seen == ["observe", ("dispatch", True)]


def test_attested_image_observer_timeout_terminalizes_without_docker_effect(
    host_server, host_request
):
    agent, _listener = host_server
    seen = []

    def timeout(_command):
        seen.append("inspect")
        raise agent._extension_lifecycle_work.LifecycleWorkExecutionError(
            "lifecycle-work-image-command-timeout"
        )

    agent._image_artifact_runtime = SimpleNamespace(
        dispatcher=lambda _command: (_ for _ in ()).throw(
            AssertionError("dispatcher reached after observer timeout")
        ),
        started_observer=timeout,
    )
    agent._image_artifact_runtime_plan_loader = (
        agent._extension_lifecycle_plan_loader
    )
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="download-and-verify",
        service_ids=["documents"],
        payload={"operations": [{"serviceId": "documents", "action": "install"}]},
    )
    begin_receipt(agent, host_request, request)

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-operation-failed"}}
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "failed"
    assert seen == ["inspect"]

    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert replay_status == 503
    assert replay == result
    assert seen == ["inspect"]


def test_legacy_approval_cannot_reach_library_image_effect_through_host(
    host_server, host_request
):
    agent, _listener = host_server
    valid_definition = {
        key: [] for key in agent._extension_lifecycle_plan._DEFINITION_KEYS
    }
    valid_definition.update(
        id="documents",
        serviceType="docker",
        manifestSchemaVersion="ods.services.v2",
        version="1.0.0",
        dataSchemaVersion="1",
        definitionSha256="sha256:" + "a" * 64,
        composeSha256="sha256:" + "b" * 64,
        definitionSource="library",
        composeFile="compose.yaml",
        resources={},
        artifacts={
            "images": [{
                "reference": "example.invalid/documents:1.0.0",
                "digest": "sha256:" + "c" * 64,
                "downloadBytes": 123,
            }],
            "builds": [],
        },
    )
    legacy = {
        "transactionId": TRANSACTION_ID,
        "state": "downloading",
        "approval": {
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "approvedBy": "owner",
        },
        "envelope": {
            "planHash": PLAN_HASH,
            "plan": {
                "selectedServices": ["documents"],
                "operations": [{"serviceId": "documents", "action": "install"}],
                "definitions": [valid_definition],
            },
        },
    }
    agent._get_extension_transaction_store = lambda: SimpleNamespace(
        read=lambda _transaction_id: legacy
    )
    agent._extension_lifecycle_plan_loader = agent._load_extension_lifecycle_plan
    calls = []
    image_module = agent._image_artifact_runtime_module
    agent._image_artifact_runtime = image_module.build_image_artifact_runtime(
        plan_loader=agent._extension_lifecycle_plan_loader,
        runner=lambda argv, **_kwargs: calls.append(argv) or (
            (_ for _ in ()).throw(AssertionError("Docker reached with v1 approval"))
        ),
    )
    agent._image_artifact_runtime_plan_loader = (
        agent._extension_lifecycle_plan_loader
    )
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="download-and-verify",
        service_ids=["documents"],
        payload={"operations": [{"serviceId": "documents", "action": "install"}]},
    )
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(
        {key: request[key] for key in agent._extension_lifecycle_work.REQUEST_KEYS}
    )
    assert agent._extension_lifecycle_plan.bind_lifecycle_plan(
        command, legacy
    ).plan_material.attested_approval is False
    begin_receipt(agent, host_request, request)

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 409
    assert result == {"error": {"code": "lifecycle-work-plan-mismatch"}}
    assert calls == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "failed"


def test_real_image_runtime_recovers_then_dispatches_once(host_server, host_request):
    agent, _listener = host_server
    image_module = agent._image_artifact_runtime_module
    image_id = "sha256:" + "9" * 64
    calls = []
    responses = [
        SimpleNamespace(returncode=1, stdout="", stderr="missing"),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout=image_id + "\n", stderr=""),
    ]

    def runner(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return responses.pop(0)

    def load(command):
        plan = agent._extension_lifecycle_plan
        operation = plan.PlannedOperation("searxng", "install")
        definition = plan.PlannedDefinition(
            service_id="searxng",
            service_type="docker",
            manifest_schema_version=image_module.CANARY_MANIFEST_SCHEMA,
            version=image_module.CANARY_VERSION,
            data_schema_version=image_module.CANARY_DATA_SCHEMA_VERSION,
            definition_sha256=image_module.CANARY_DEFINITION_SHA256,
            compose_sha256=image_module.CANARY_COMPOSE_SHA256,
            definition_source="builtin",
            compose_file="compose.yaml",
            images=(
                plan.PlannedImage(
                    image_module.CANARY_IMAGE_REFERENCE,
                    image_module.CANARY_IMAGE_DIGEST,
                    image_module.CANARY_IMAGE_DOWNLOAD_BYTES,
                ),
            ),
            builds=(),
            canonical_document=b"fixture-only\n",
            host_ports=(),
            exclusive=(),
        )
        return replace(
            command,
            plan_material=plan.LifecyclePlanMaterial(
                schema=plan.PLAN_MATERIAL_SCHEMA,
                transaction_id=command.transaction_id,
                plan_hash=command.plan_hash,
                state="downloading",
                operations=(operation,),
                definitions=(definition,),
            ),
        )

    agent._extension_lifecycle_plan_loader = load
    agent._image_artifact_runtime = image_module.build_image_artifact_runtime(
        plan_loader=load,
        runner=runner,
    )
    agent._image_artifact_runtime_plan_loader = load
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received image work"))
    )
    grant = acquire_lease(agent, host_request, ["searxng"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="download-and-verify",
        service_ids=["searxng"],
        payload={
            "operations": [{"serviceId": "searxng", "action": "install"}]
        },
    )
    begin_receipt(agent, host_request, request)

    status, result = host_request("/v1/extension/lifecycle-work", request)
    assert status == 200
    assert len(result["evidenceHash"]) == 64
    assert [call[0][2] for call in calls] == ["inspect", "pull", "inspect"]

    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert replay_status == 200
    assert replay == result
    assert len(calls) == 3


def test_unrelated_operations_never_gain_resource_authority(
    host_server, host_request
):
    """Operations outside reserve/release/stage/image work must not
    gain access to the reservation runtime's dispatchers."""
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)

    res_runtime = agent._get_extension_resource_reservation_runtime()
    if res_runtime is not None:
        agent._resource_reservation_runtime = type(res_runtime)(
            root=res_runtime.root,
            store=res_runtime.store,
            reserve_dispatcher=lambda _c: (_ for _ in ()).throw(
                AssertionError("reservation used for unrelated op")
            ),
            release_dispatcher=lambda _c: (_ for _ in ()).throw(
                AssertionError("reservation used for unrelated op")
            ),
        )

        test_cases = [
            ("verify", {"serviceIds": ["documents"]}),
            ("configure", {"serviceIds": ["documents"]}),
        ]
    for op_key, payload in test_cases:
        request = work_request(
            agent._extension_lifecycle_work.REQUEST_SCHEMA,
            evidence,
            operation_key=op_key,
            service_ids=["documents"],
            payload=payload,
        )
        status, result = host_request("/v1/extension/lifecycle-work", request)

        # Must be 503 (dispatcher unavailable) — never 200 via reservation runtime
        assert status == 503
        assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}


def test_runtime_not_constructed_before_admission_checks(host_server, host_request):
    """The reservation runtime must not be constructed before
    authentication/feature/request/lease admission gates pass."""
    agent, _listener = host_server

    # Clear cached runtime so we can observe construction
    agent._resource_reservation_runtime = None
    agent._resource_reservation_runtime_data_dir = None
    construction_count = [0]
    original_module = agent._resource_reservation_runtime_module

    class ConstructionTracker:
        @staticmethod
        def build_resource_reservation_runtime(**kwargs):
            construction_count[0] += 1
            return original_module.build_resource_reservation_runtime(**kwargs)

    agent._resource_reservation_runtime_module = ConstructionTracker

    # Send a request with wrong auth — construction must NOT happen
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
    )
    status, _ = host_request(
        "/v1/extension/lifecycle-work", request, token="wrong"
    )
    assert status == 403
    assert construction_count[0] == 0

    # Disable feature flag — construction must NOT happen
    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    status, _ = host_request("/v1/extension/lifecycle-work", request)
    assert status == 404
    assert construction_count[0] == 0
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True

    # Bad request shape — construction must NOT happen
    status, _ = host_request("/v1/extension/lifecycle-work", raw=b"[]")
    assert status == 400
    assert construction_count[0] == 0

    # Missing lease — construction must NOT happen
    status, _ = host_request("/v1/extension/lifecycle-work", request)
    assert status == 422
    assert construction_count[0] == 0

    agent._resource_reservation_runtime_module = original_module


def test_missing_runtime_module_returns_failure_not_success(
    host_server, host_request
):
    """When the reservation runtime module is unavailable, reserve/release
    requests must return a failure, never success via a generic dispatcher."""
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)

    # Temporarily disable the module
    original = agent._resource_reservation_runtime_module
    agent._resource_reservation_runtime_module = None
    agent._resource_reservation_runtime = None

    generic_calls = []
    agent._extension_lifecycle_work_dispatcher = (
        lambda command: generic_calls.append(command) or EVIDENCE_HASH
    )

    # Reset the receipt store for this test
    agent._lifecycle_receipt_store = MemoryReceiptStore(
        agent.__dict__["_extension_" + "lifecycle_receipts"].StartedReceipt,
        agent.__dict__["_extension_" + "lifecycle_receipts"].TerminalReceipt,
        agent.__dict__["_extension_" + "lifecycle_receipts"].LifecycleSnapshot,
    )

    try:
        request = work_request(
            agent._extension_lifecycle_work.REQUEST_SCHEMA,
            evidence,
            operation_key="reserve:documents",
            payload={"operation": {"serviceId": "documents"}},
        )
        begin_receipt(agent, host_request, request)
        status, result = host_request("/v1/extension/lifecycle-work", request)

        assert status == 503
        assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}

        # Same for release
        request2 = work_request(
            agent._extension_lifecycle_work.REQUEST_SCHEMA,
            evidence,
            operation_key="release",
            service_ids=["documents"],
            payload={"serviceIds": ["documents"]},
        )
        agent._lifecycle_receipt_store = MemoryReceiptStore(
            agent.__dict__["_extension_" + "lifecycle_receipts"].StartedReceipt,
            agent.__dict__["_extension_" + "lifecycle_receipts"].TerminalReceipt,
            agent.__dict__["_extension_" + "lifecycle_receipts"].LifecycleSnapshot,
        )
        begin_receipt(agent, host_request, request2)
        status2, result2 = host_request("/v1/extension/lifecycle-work", request2)

        assert status2 == 503
        assert result2 == {
            "error": {"code": "lifecycle-work-dispatcher-unavailable"}
        }
        assert generic_calls == []
    finally:
        agent._resource_reservation_runtime_module = original


@pytest.mark.parametrize(
    "operation_key,service_ids,payload",
    [
        (
            "reserve:documents",
            ["documents"],
            {"operation": {"serviceId": "documents"}},
        ),
        ("release", ["documents"], {"serviceIds": ["documents"]}),
    ],
)
def test_runtime_constructor_failure_cannot_fall_back_to_generic_dispatcher(
    host_server,
    host_request,
    operation_key,
    service_ids,
    payload,
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    generic_calls = []

    class BrokenRuntimeModule:
        @staticmethod
        def build_resource_reservation_runtime(**_kwargs):
            raise OSError("private-construction-detail")

    agent._resource_reservation_runtime = None
    agent._resource_reservation_runtime_data_dir = None
    agent._resource_reservation_runtime_module = BrokenRuntimeModule
    agent._extension_lifecycle_work_dispatcher = (
        lambda command: generic_calls.append(command) or EVIDENCE_HASH
    )
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key=operation_key,
        service_ids=service_ids,
        payload=payload,
    )
    begin_receipt(agent, host_request, request)

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert generic_calls == []
    assert "private-construction-detail" not in json.dumps(result)


def test_importing_runtime_performs_no_reservation_write(host_server):
    """Building the reservation runtime must not write any reservation/release
    data to disk."""
    agent, _listener = host_server
    res_runtime = agent._get_extension_resource_reservation_runtime()
    assert res_runtime is not None
    root = res_runtime.root

    assert list(root.iterdir()) == []


def test_reserve_and_release_production_reachability_is_paired(
    host_server, host_request
):
    """Both reserve and release must be equally reachable (or equally
    unreachable) through the reservation runtime, replacing the prior
    dormant-unreachability assertion."""
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request)
    evidence = lease_evidence(agent, grant)

    res_runtime = agent._get_extension_resource_reservation_runtime()
    reserve_reachable = res_runtime is not None and res_runtime.reserve_dispatcher is not None
    release_reachable = res_runtime is not None and res_runtime.release_dispatcher is not None

    if reserve_reachable or release_reachable:
        # If either is reachable, both must be (paired)
        assert reserve_reachable == release_reachable, (
            f"reserve reachable={reserve_reachable}, release reachable={release_reachable}"
        )

        # Both must NOT be dormant (503 dispatcher-unavailable) when runtime is available.
        # They may return 200 (success), 409 (plan/receipt), or similar — but never 503
        # which would indicate the dispatcher is still unwired.
        for op_key, svc_ids, payload in [
            ("reserve:documents", ["documents"], {"operation": {"serviceId": "documents"}}),
            ("release", ["documents"], {"serviceIds": ["documents"]}),
        ]:
            request = work_request(
                agent._extension_lifecycle_work.REQUEST_SCHEMA,
                evidence,
                operation_key=op_key,
                service_ids=svc_ids,
                payload=payload,
            )
            begin_receipt(agent, host_request, request)
            status, result = host_request("/v1/extension/lifecycle-work", request)

            # Must not be 503 dispatcher-unavailable — that means the runtime
            # is not properly wired for this operation
            assert status != 503, (
                f"{op_key} returned 503 (dormant) — expected reachable via runtime: {result}"
            )
    else:
        # Both unreachable — still paired (both dormant)
        assert reserve_reachable == release_reachable


# Update the production_unreachability test to reflect the candidate
# (skip the old check that agent source does NOT contain reservation imports;
# the candidate intentionally imports them now)
def test_host_agent_source_wires_reservation_runtime_module():
    """The host agent source must now import the reservation runtime
    for lazy composition, replacing the prior unreachability assertion."""
    agent_source = (BIN_DIR / "ods-host-agent.py").read_text(encoding="utf-8")
    assert "extension_resource_reservation_runtime" in agent_source
    assert "_resource_reservation_runtime" in agent_source
    assert "_get_extension_resource_reservation_runtime" in agent_source


def test_host_agent_source_wires_closed_image_runtime_module():
    agent_source = (BIN_DIR / "ods-host-agent.py").read_text(encoding="utf-8")
    assert "extension_image_artifact_runtime" in agent_source
    assert "_image_artifact_runtime" in agent_source
    assert "_get_extension_image_artifact_runtime" in agent_source


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_backup_and_restore_use_one_closed_runtime_and_replay_once(
    host_server, host_request
):
    agent, _listener = host_server
    runtime_module = agent._data_backup_runtime_module
    source = agent.INSTALL_DIR / "config" / "searxng"
    source.mkdir(mode=0o700)
    settings = source / "settings.yml"
    settings.write_bytes(b"original")
    settings.chmod(0o600)
    grant = acquire_lease(agent, host_request, [runtime_module.CANARY_SERVICE_ID])
    evidence = lease_evidence(agent, grant)
    runtime = agent._get_extension_data_backup_runtime()
    assert runtime is not None
    calls = []

    def backup_dispatch(command):
        calls.append("backup")
        return runtime.backup_dispatcher(command)

    def restore_dispatch(command):
        calls.append("restore")
        return runtime.restore_dispatcher(command)

    agent._data_backup_runtime = replace(
        runtime,
        backup_dispatcher=backup_dispatch,
        restore_dispatcher=restore_dispatch,
    )
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received data work"))
    )

    backup_request = data_work_request(agent, evidence, "backup")
    begin_receipt(agent, host_request, backup_request)
    status, result = host_request("/v1/extension/lifecycle-work", backup_request)
    assert status == 200
    assert result["operationKey"] == "backup"
    assert result["completed"] is True
    assert calls == ["backup"]

    replay_status, replay = host_request(
        "/v1/extension/lifecycle-work", backup_request
    )
    assert replay_status == 200
    assert replay == result
    assert calls == ["backup"]

    settings.write_bytes(b"changed")
    settings.chmod(0o600)
    restore_request = data_work_request(agent, evidence, "restore")
    begin_receipt(agent, host_request, restore_request)
    status, restored = host_request(
        "/v1/extension/lifecycle-work", restore_request
    )
    assert status == 200
    assert restored["operationKey"] == "restore"
    assert restored["completed"] is True
    assert calls == ["backup", "restore"]
    assert settings.read_bytes() == b"original"


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_backup_started_receipt_recovers_snapshot_without_redispatch(
    host_server, host_request
):
    agent, _listener = host_server
    runtime_module = agent._data_backup_runtime_module
    source = agent.INSTALL_DIR / "config" / "searxng"
    source.mkdir(mode=0o700)
    settings = source / "settings.yml"
    settings.write_bytes(b"original")
    settings.chmod(0o600)
    grant = acquire_lease(agent, host_request, [runtime_module.CANARY_SERVICE_ID])
    request = data_work_request(agent, lease_evidence(agent, grant), "backup")
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request(
        {key: request[key] for key in agent._extension_lifecycle_work.REQUEST_KEYS}
    )
    bound = bind_fixture_plan(agent, command)
    runtime = agent._get_extension_data_backup_runtime()
    assert runtime is not None
    begin_receipt(agent, host_request, request)
    expected = runtime.backup_dispatcher(bound)
    calls = []
    agent._data_backup_runtime = replace(
        runtime,
        backup_dispatcher=lambda value: calls.append(value) or EVIDENCE_HASH,
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == expected
    assert calls == []
    snapshot_status, snapshot = receipt_snapshot(agent, host_request, request)
    assert snapshot_status == 200
    assert snapshot["state"] == "completed"


@pytest.mark.parametrize("operation_key", ["backup", "restore"])
def test_missing_data_runtime_never_falls_back_to_generic_dispatcher(
    host_server, host_request, operation_key
):
    agent, _listener = host_server
    runtime_module = agent._data_backup_runtime_module
    grant = acquire_lease(agent, host_request, [runtime_module.CANARY_SERVICE_ID])
    request = data_work_request(
        agent, lease_evidence(agent, grant), operation_key
    )
    begin_receipt(agent, host_request, request)
    calls = []
    agent._data_backup_runtime_module = None
    agent._data_backup_runtime = None
    agent._data_backup_runtime_binding = None
    agent._extension_lifecycle_work_dispatcher = (
        lambda value: calls.append(value) or EVIDENCE_HASH
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert calls == []


def test_restore_route_rechecks_the_real_host_lease_before_runtime_selection(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    service_id = agent._data_backup_runtime_module.CANARY_SERVICE_ID
    grant = acquire_lease(agent, host_request, [service_id])
    request = data_work_request(agent, lease_evidence(agent, grant), "restore")
    manager = agent._extension_lease_manager
    original_status = manager.status
    observed = []

    def witnessed(*args):
        record = original_status(*args)
        observed.append((record["active"], record["serviceIds"]))
        return record

    monkeypatch.setattr(manager, "status", witnessed)
    monkeypatch.setattr(agent, "_get_extension_data_backup_runtime", lambda: None)
    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert observed == [(True, [service_id])]
    assert manager.describe(grant["leaseId"])["active"] is False
    assert grant["leaseToken"] not in json.dumps(result)


def test_restore_route_rejects_lost_status_before_runtime_or_receipt_creation(
    host_server, host_request, monkeypatch
):
    agent, _listener = host_server
    service_id = agent._data_backup_runtime_module.CANARY_SERVICE_ID
    grant = acquire_lease(agent, host_request, [service_id])
    request = data_work_request(agent, lease_evidence(agent, grant), "restore")
    manager = agent._extension_lease_manager
    initial_store = agent._lifecycle_receipt_store
    monkeypatch.setattr(
        manager, "status",
        lambda *_args: (_ for _ in ()).throw(
            agent._extension_leases.LeaseExpired("lease-not-active")
        ),
    )
    monkeypatch.setattr(
        agent, "_get_extension_data_backup_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("restore runtime selected")),
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)
    assert status == 410
    assert result == {"error": {"code": "lease-not-active"}}
    assert agent._lifecycle_receipt_store is initial_store
    assert manager.describe(grant["leaseId"])["active"] is False
    assert grant["leaseToken"] not in json.dumps(result)


@pytest.mark.parametrize("operation_key", ["backup", "restore"])
def test_data_runtime_constructor_failure_cannot_fall_back(
    host_server, host_request, operation_key
):
    agent, _listener = host_server
    original_module = agent._data_backup_runtime_module
    grant = acquire_lease(agent, host_request, [original_module.CANARY_SERVICE_ID])
    request = data_work_request(
        agent, lease_evidence(agent, grant), operation_key
    )
    begin_receipt(agent, host_request, request)
    calls = []

    class BrokenRuntimeModule:
        @staticmethod
        def build_data_backup_runtime(**_kwargs):
            raise OSError("private-construction-detail")

    agent._data_backup_runtime_module = BrokenRuntimeModule
    agent._data_backup_runtime = None
    agent._data_backup_runtime_binding = None
    agent._extension_lifecycle_work_dispatcher = (
        lambda value: calls.append(value) or EVIDENCE_HASH
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert calls == []
    assert "private-construction-detail" not in json.dumps(result)


def test_data_runtime_not_constructed_before_admission_gates(
    host_server, host_request
):
    agent, _listener = host_server
    original_module = agent._data_backup_runtime_module
    constructed = []

    class ConstructionTracker:
        CANARY_SERVICE_ID = original_module.CANARY_SERVICE_ID

        @staticmethod
        def build_data_backup_runtime(**_kwargs):
            constructed.append(True)
            raise AssertionError("must not construct before admission")

    agent._data_backup_runtime_module = ConstructionTracker
    agent._data_backup_runtime = None
    agent._data_backup_runtime_binding = None
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        operation_key="backup",
        service_ids=[original_module.CANARY_SERVICE_ID],
        payload={"serviceIds": [original_module.CANARY_SERVICE_ID]},
    )

    assert host_request(
        "/v1/extension/lifecycle-work", request, token="wrong"
    )[0] == 403
    assert constructed == []
    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    assert host_request("/v1/extension/lifecycle-work", request)[0] == 404
    assert constructed == []
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    assert host_request("/v1/extension/lifecycle-work", request)[0] == 422
    assert constructed == []


def test_unrelated_operations_never_gain_data_runtime_authority(
    host_server, host_request
):
    agent, _listener = host_server
    calls = []
    agent._data_backup_runtime = SimpleNamespace(
        backup_dispatcher=lambda value: calls.append(("backup", value)),
        backup_started_observer=lambda value: calls.append(("observe-backup", value)),
        restore_dispatcher=lambda value: calls.append(("restore", value)),
        restore_started_observer=lambda value: calls.append(("observe-restore", value)),
    )
    agent._data_backup_runtime_binding = (
        agent.INSTALL_DIR,
        agent.DATA_DIR,
        agent._extension_lifecycle_plan_loader,
    )
    grant = acquire_lease(agent, host_request)
    for operation_key in ("verify",):
        request = work_request(
            agent._extension_lifecycle_work.REQUEST_SCHEMA,
            lease_evidence(agent, grant),
            operation_key=operation_key,
        )
        status, result = host_request("/v1/extension/lifecycle-work", request)
        assert status == 503
        assert result == {
            "error": {"code": "lifecycle-work-dispatcher-unavailable"}
        }
    assert calls == []


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_backup_and_restore_production_reachability_is_paired(host_server):
    agent, _listener = host_server
    runtime = agent._get_extension_data_backup_runtime()
    assert runtime is not None
    assert callable(runtime.backup_dispatcher)
    assert callable(runtime.backup_started_observer)
    assert callable(runtime.restore_dispatcher)
    assert callable(runtime.restore_started_observer)
    assert list(runtime.root.iterdir()) == []


def test_host_agent_source_wires_closed_data_runtime_module():
    agent_source = (BIN_DIR / "ods-host-agent.py").read_text(encoding="utf-8")
    assert "extension_data_backup_runtime" in agent_source
    assert "_data_backup_runtime" in agent_source
    assert "_get_extension_data_backup_runtime" in agent_source


def test_configure_uses_closed_runtime_and_replays_once(host_server, host_request):
    agent, _listener = host_server
    runtime_module = agent._configuration_effect_runtime_module
    grant = acquire_lease(agent, host_request, [runtime_module.CANARY_SERVICE_ID])
    request = configuration_work_request(agent, lease_evidence(agent, grant))
    calls = []
    bind_configuration_runtime(
        agent,
        dispatcher=lambda value: calls.append(value) or EVIDENCE_HASH,
        started_observer=lambda _value: agent._extension_lifecycle_work.LifecycleWorkStartedObservation(
            state="missing"
        ),
    )
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received configure work"))
    )
    begin_receipt(agent, host_request, request)

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["operationKey"] == "configure"
    assert result["completed"] is True
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert len(calls) == 1
    assert calls[0].operation_key == "configure"
    replay_status, replay = host_request(
        "/v1/extension/lifecycle-work", request
    )
    assert replay_status == 200
    assert replay == result
    assert len(calls) == 1


def test_approved_library_configure_selects_pure_receipted_runtime(
    host_server, host_request
):
    agent, _listener = host_server
    service_ids = ["gitea", "ntfy"]
    grant = acquire_lease(agent, host_request, service_ids)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="configure",
        service_ids=service_ids,
        payload={"serviceIds": service_ids},
    )
    begin_receipt(agent, host_request, request)
    calls = []

    def build():
        def dispatch(command):
            calls.append(command)
            return EVIDENCE_HASH

        return SimpleNamespace(dispatcher=dispatch)

    agent._get_extension_library_configuration_runtime = build
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic received library configure"))
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert len(calls) == 1
    assert calls[0].service_ids == tuple(service_ids)
    assert calls[0].plan_material.state == "configuring"
    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert replay_status == 200
    assert replay == result
    assert len(calls) == 1


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires POSIX owner custody")
def test_library_configuration_runtime_factory_has_no_application_effect(host_server):
    agent, _listener = host_server
    applications = agent.INSTALL_DIR / ".ods-assistant-first" / "applications"
    before = tuple(sorted(applications.iterdir())) if applications.exists() else None

    runtime = agent._get_extension_library_configuration_runtime()

    assert runtime is not None
    assert callable(runtime.dispatcher)
    after = tuple(sorted(applications.iterdir())) if applications.exists() else None
    assert after == before


@pytest.mark.parametrize("service_ids", [["searxng", "gitea"], ["documents"]])
def test_non_library_configure_does_not_fall_through_to_library_runtime(
    host_server, host_request, service_ids
):
    agent, _listener = host_server
    grant = acquire_lease(agent, host_request, service_ids)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="configure",
        service_ids=service_ids,
        payload={"serviceIds": service_ids},
    )
    agent._get_extension_library_configuration_runtime = lambda: (
        (_ for _ in ()).throw(AssertionError("library runtime selected"))
    )
    agent._extension_lifecycle_work_dispatcher = lambda _command: (
        (_ for _ in ()).throw(AssertionError("generic runtime selected"))
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}


def test_configure_started_receipt_recovers_without_redispatch(
    host_server, host_request
):
    agent, _listener = host_server
    runtime_module = agent._configuration_effect_runtime_module
    grant = acquire_lease(agent, host_request, [runtime_module.CANARY_SERVICE_ID])
    request = configuration_work_request(agent, lease_evidence(agent, grant))
    calls = []
    bind_configuration_runtime(
        agent,
        dispatcher=lambda value: calls.append(value) or EVIDENCE_HASH,
        started_observer=lambda _value: agent._extension_lifecycle_work.LifecycleWorkStartedObservation(
            state="completed", evidence_hash=EVIDENCE_HASH
        ),
    )
    begin_receipt(agent, host_request, request)

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 200
    assert result["evidenceHash"] == EVIDENCE_HASH
    assert calls == []
    snapshot_status, snapshot = receipt_snapshot(
        agent, host_request, request
    )
    assert snapshot_status == 200
    assert snapshot["state"] == "completed"


def test_missing_configuration_runtime_never_falls_back_to_generic_dispatcher(
    host_server, host_request
):
    agent, _listener = host_server
    runtime_module = agent._configuration_effect_runtime_module
    grant = acquire_lease(agent, host_request, [runtime_module.CANARY_SERVICE_ID])
    request = configuration_work_request(agent, lease_evidence(agent, grant))
    begin_receipt(agent, host_request, request)
    calls = []
    agent._configuration_effect_runtime_module = None
    agent._configuration_effect_runtime = None
    agent._configuration_effect_runtime_binding = None
    agent._extension_lifecycle_work_dispatcher = (
        lambda value: calls.append(value) or EVIDENCE_HASH
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert calls == []


def test_configuration_runtime_constructor_failure_cannot_fall_back(
    host_server, host_request
):
    agent, _listener = host_server
    original_module = agent._configuration_effect_runtime_module
    grant = acquire_lease(agent, host_request, [original_module.CANARY_SERVICE_ID])
    request = configuration_work_request(agent, lease_evidence(agent, grant))
    begin_receipt(agent, host_request, request)
    calls = []

    class BrokenRuntimeModule:
        CANARY_SERVICE_ID = original_module.CANARY_SERVICE_ID

        @staticmethod
        def build_configuration_effect_runtime(**_kwargs):
            raise OSError("private-configuration-construction-detail")

    agent._configuration_effect_runtime_module = BrokenRuntimeModule
    agent._configuration_effect_runtime = None
    agent._configuration_effect_runtime_binding = None
    agent._extension_lifecycle_work_dispatcher = (
        lambda value: calls.append(value) or EVIDENCE_HASH
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert calls == []
    assert "private-configuration-construction-detail" not in json.dumps(result)


def test_configuration_runtime_not_constructed_before_admission_gates(
    host_server, host_request
):
    agent, _listener = host_server
    original_module = agent._configuration_effect_runtime_module
    constructed = []

    class ConstructionTracker:
        CANARY_SERVICE_ID = original_module.CANARY_SERVICE_ID

        @staticmethod
        def build_configuration_effect_runtime(**_kwargs):
            constructed.append(True)
            raise AssertionError("must not construct before admission")

    agent._configuration_effect_runtime_module = ConstructionTracker
    agent._configuration_effect_runtime = None
    agent._configuration_effect_runtime_binding = None
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        operation_key="configure",
        service_ids=[original_module.CANARY_SERVICE_ID],
        payload={"serviceIds": [original_module.CANARY_SERVICE_ID]},
    )

    assert host_request(
        "/v1/extension/lifecycle-work", request, token="wrong"
    )[0] == 403
    assert constructed == []
    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    assert host_request("/v1/extension/lifecycle-work", request)[0] == 404
    assert constructed == []
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    assert host_request("/v1/extension/lifecycle-work", request)[0] == 422
    assert constructed == []


def test_unrelated_operations_never_gain_configuration_runtime_authority(
    host_server, host_request
):
    agent, _listener = host_server
    calls = []
    bind_configuration_runtime(
        agent,
        dispatcher=lambda value: calls.append(("configure", value)),
        started_observer=lambda value: calls.append(("observe", value)),
    )
    grant = acquire_lease(agent, host_request)
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant),
        operation_key="verify",
    )

    status, result = host_request("/v1/extension/lifecycle-work", request)

    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert calls == []


@pytest.mark.skipif(not STAGE_SUPPORTED, reason="requires Linux dir_fd semantics")
def test_configuration_production_reachability_is_paired_and_inert(host_server):
    agent, _listener = host_server
    calls = []

    class InertSecretStore:
        def __init__(self, data_dir):
            calls.append(("secret-store", data_dir))

        def status(self, _payload):
            raise AssertionError("construction must not query secret custody")

    agent._AssistantFirstSecretStore = InertSecretStore
    agent._get_extension_transaction_store = lambda: SimpleNamespace(
        read=lambda _transaction_id: (_ for _ in ()).throw(
            AssertionError("construction must not load a transaction")
        )
    )
    runtime = agent._get_extension_configuration_effect_runtime()
    assert runtime is not None
    assert callable(runtime.dispatcher)
    assert callable(runtime.started_observer)
    assert calls == [("secret-store", agent.DATA_DIR)]
    assert not (agent.INSTALL_DIR / "config" / "searxng").exists()


def test_host_agent_source_wires_closed_configuration_runtime_module():
    agent_source = (BIN_DIR / "ods-host-agent.py").read_text(encoding="utf-8")
    assert "extension_configuration_effect_runtime" in agent_source
    assert "_configuration_effect_runtime" in agent_source
    assert "_get_extension_configuration_effect_runtime" in agent_source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
