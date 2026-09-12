from __future__ import annotations

from types import SimpleNamespace

import pytest
import routers.extension_transactions as transaction_api
import security
import session_signer
from extension_transaction_runtime import TransactionRuntime
from fastapi import FastAPI
from fastapi.testclient import TestClient
from plan_provenance import validate_request_intent

API_KEY = "transaction-test-key"
NOW = "2026-09-11T12:00:00Z"
PLAN_HASH = "a" * 64
IDEMPOTENCY_KEY = "b" * 64
TX_ID = "txn-" + "c" * 24


def envelope() -> dict:
    return {
        "schema": "ods.assistant-first.plan-envelope.v1",
        "planId": "plan-" + PLAN_HASH[:24],
        "catalogRevision": "d" * 64,
        "observedStateRevision": "e" * 64,
        "policyRevision": "f" * 64,
        "planHash": PLAN_HASH,
        "plan": {
            "schema": "ods.assistant-first.plan.v1",
            "validUntil": "2026-10-01T00:00:00Z",
            "selectedServices": ["notes"],
            "operations": [{"serviceId": "notes", "action": "install"}],
            "requiredSecretKeys": ["NOTES_API_KEY"],
            "dataEffects": [],
            "rollbackEffects": [],
            "warnings": [],
        },
    }


class FakeStore:
    def __init__(self) -> None:
        self.created = []
        self.approved = []
        self.record = {
            "transactionId": TX_ID,
            "envelope": envelope(),
            "state": "awaiting_approval",
            "sequence": 2,
            "journal": [
                {"sequence": 1, "state": "planned"},
                {"sequence": 2, "state": "awaiting_approval"},
            ],
            "approval": None,
        }

    def create(self, plan, actor, key, timestamp, current_time):
        self.created.append((plan, actor, key, timestamp, current_time))
        duplicate = len(self.created) > 1
        self.record["envelope"] = plan
        return {
            "transactionId": TX_ID,
            "planHash": plan["planHash"],
            "state": "awaiting_approval",
            "sequence": 2,
            "duplicate": duplicate,
        }

    def approve_exact(
        self, transaction_id, plan_hash, approved_by, approved_at, current_time
    ):
        self.approved.append(
            (transaction_id, plan_hash, approved_by, approved_at, current_time)
        )
        self.record["state"] = "approved"
        self.record["sequence"] = 3
        self.record["approval"] = {
            "approvedBy": approved_by,
            "approvedAt": approved_at,
            "actor": "assistant-manager",
            "idempotencyKey": IDEMPOTENCY_KEY,
        }
        return {"transactionId": transaction_id, "state": "approved", "sequence": 3}

    def read(self, transaction_id):
        if transaction_id != TX_ID:
            from extension_transactions import TransactionError

            raise TransactionError("not-found")
        return self.record


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, transaction_id, plan_hash):
        self.calls.append((transaction_id, plan_hash))
        return SimpleNamespace(
            transaction_id=transaction_id,
            final_state="committed",
            sequence=10,
            applied_services=["notes"],
            error=None,
        )


class FakeConfiguration:
    def __init__(self) -> None:
        self.ready_calls = []
        self.submit_calls = []

    def require_ready(self, transaction_id, plan_hash):
        self.ready_calls.append((transaction_id, plan_hash))
        return {"configured": True}

    def view(self, transaction_id):
        return {
            "schema": "ods.assistant-first.transaction-configuration-view.v1",
            "transactionId": transaction_id,
            "planHash": PLAN_HASH,
            "schemaHash": "9" * 64,
            "fields": [],
            "configured": False,
            "values": {},
            "presentConfigKeys": [],
            "presentSecretKeys": [],
            "appliedDefaultKeys": [],
        }

    def submit(self, transaction_id, **submission):
        self.submit_calls.append((transaction_id, submission))
        return {
            **self.view(transaction_id),
            "schemaHash": submission["schema_hash"],
            "configured": True,
            "values": dict(submission["values"]),
            "presentConfigKeys": sorted(submission["values"]),
            "presentSecretKeys": sorted(submission["secret_values"]),
            "duplicate": False,
        }


def create_body(**intent_changes) -> dict:
    intent = {
        "requestedServices": ["notes"],
        "requestedCapabilities": [],
        "providerPreferences": {},
        "validUntil": "2026-10-01T00:00:00Z",
        "missingConfigKeys": [],
        "missingSecretKeys": ["NOTES_API_KEY"],
    }
    intent.update(intent_changes)
    return {"intent": intent, "idempotencyKey": IDEMPOTENCY_KEY}


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("ODS_ASSISTANT_TRANSACTIONS_ENABLED", "true")
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", API_KEY)
    session_signer._set_secret_for_tests("phase3c-session-secret")
    store = FakeStore()
    executor = FakeExecutor()
    configuration = FakeConfiguration()
    calls = []

    def catalog():
        return ([{"id": "server-catalog"}], "1" * 64)

    def observed_state():
        return {"source": "server-state"}

    def policy():
        return {"source": "server-policy"}

    def authorize(intent, *, catalog, observed_state, policy):
        normalized = validate_request_intent(intent)
        calls.append(
            {
                "intent": normalized,
                "catalog": catalog(),
                "observedState": observed_state(),
                "policy": policy(),
            }
        )
        return envelope()

    monkeypatch.setattr(transaction_api, "authorize_plan", authorize)
    app = FastAPI()
    app.include_router(transaction_api.router)
    app.state.extension_transaction_runtime = TransactionRuntime(
        store=store,
        catalog=catalog,
        observed_state=observed_state,
        policy=policy,
        clock=lambda: NOW,
        executor=executor,
        configuration=configuration,
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            store=store,
            executor=executor,
            configuration=configuration,
            authorize_calls=calls,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )


def test_disabled_and_unconfigured_runtime_fail_before_store_creation(
    monkeypatch, tmp_path
):
    app = FastAPI()
    app.include_router(transaction_api.router)
    with TestClient(app) as client:
        monkeypatch.delenv("ODS_ASSISTANT_TRANSACTIONS_ENABLED", raising=False)
        response = client.post("/api/extensions/transactions", json=create_body())
        assert response.status_code == 404
        assert list(tmp_path.iterdir()) == []

        monkeypatch.setenv("ODS_ASSISTANT_TRANSACTIONS_ENABLED", "true")
        response = client.post("/api/extensions/transactions", json=create_body())
        assert response.status_code == 503
        assert list(tmp_path.iterdir()) == []


def test_create_requires_api_key_and_uses_only_server_providers(api):
    assert api.client.post(
        "/api/extensions/transactions", json=create_body()
    ).status_code == 401

    response = api.client.post(
        "/api/extensions/transactions", json=create_body(), headers=api.headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "awaiting_approval"
    assert body["envelope"] == envelope()
    assert body.get("finalState") is None
    assert api.store.created == [
        (envelope(), "assistant-manager", IDEMPOTENCY_KEY, NOW, NOW)
    ]
    assert api.authorize_calls == [
        {
            "intent": {
                "requested_services": ("notes",),
                "requested_capabilities": (),
                "provider_preferences": {},
                "valid_until": "2026-10-01T00:00:00Z",
                "missing_config_keys": (),
                "missing_secret_keys": ("NOTES_API_KEY",),
            },
            "catalog": ([{"id": "server-catalog"}], "1" * 64),
            "observedState": {"source": "server-state"},
            "policy": {"source": "server-policy"},
        }
    ]


def test_capabilities_advertise_only_the_injected_runtime(api):
    response = api.client.get(
        "/api/extensions/transactions/capabilities", headers=api.headers
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema": "ods.assistant-first.transaction-capabilities.v1",
        "planning": True,
        "configuration": True,
        "execution": True,
    }

    runtime = api.client.app.state.extension_transaction_runtime
    api.client.app.state.extension_transaction_runtime = TransactionRuntime(
        store=runtime.store,
        catalog=runtime.catalog,
        observed_state=runtime.observed_state,
        policy=runtime.policy,
        clock=runtime.clock,
        executor=None,
        configuration=None,
    )
    unavailable = api.client.get(
        "/api/extensions/transactions/capabilities", headers=api.headers
    )
    assert unavailable.status_code == 200
    assert unavailable.json()["configuration"] is False
    assert unavailable.json()["execution"] is False


def test_create_retry_reports_duplicate_without_completion(api):
    first = api.client.post(
        "/api/extensions/transactions", json=create_body(), headers=api.headers
    )
    second = api.client.post(
        "/api/extensions/transactions", json=create_body(), headers=api.headers
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["state"] == "awaiting_approval"


@pytest.mark.parametrize(
    "body",
    [
        {**create_body(), "operations": []},
        create_body(catalogRevision="1" * 64),
        create_body(observedStateRevision="2" * 64),
        create_body(policyRevision="3" * 64),
        create_body(actor="assistant-manager"),
        create_body(approval={"required": False}),
        create_body(secretValue="do-not-accept"),
        create_body(shell="docker compose up"),
        create_body(purge=True),
    ],
)
def test_create_rejects_caller_controlled_authority(body, api):
    response = api.client.post(
        "/api/extensions/transactions", json=body, headers=api.headers
    )
    assert response.status_code == 422
    assert api.store.created == []


def test_create_rejects_duplicate_keys_floats_and_oversize(api):
    duplicate = (
        '{"intent":{"requestedServices":[],"requestedServices":[]},'
        f'"idempotencyKey":"{IDEMPOTENCY_KEY}"}}'
    )
    response = api.client.post(
        "/api/extensions/transactions",
        content=duplicate,
        headers={**api.headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 422

    body = create_body()
    body["intent"]["providerPreferences"] = {"capability@1": 1.5}
    response = api.client.post(
        "/api/extensions/transactions", json=body, headers=api.headers
    )
    assert response.status_code == 422

    response = api.client.post(
        "/api/extensions/transactions",
        content=b"{" + b" " * transaction_api.MAX_REQUEST_BYTES + b"}",
        headers={**api.headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


@pytest.mark.parametrize("scope", ["guest", "admin"])
def test_only_owner_cookie_can_approve(scope, api):
    cookie = session_signer.issue_scoped(scope, ttl_seconds=60)
    api.client.cookies.set("ods-session", cookie)
    response = api.client.post(
        f"/api/extensions/transactions/{TX_ID}/approval",
        json={"planHash": PLAN_HASH},
    )
    assert response.status_code == 403
    assert api.store.approved == []


def test_legacy_cookie_and_api_key_cannot_approve(api):
    legacy = session_signer.issue(ttl_seconds=60)
    api.client.cookies.set("ods-session", legacy)
    response = api.client.post(
        f"/api/extensions/transactions/{TX_ID}/approval",
        json={"planHash": PLAN_HASH},
    )
    assert response.status_code == 403

    api.client.cookies.delete("ods-session")
    response = api.client.post(
        f"/api/extensions/transactions/{TX_ID}/approval",
        json={"planHash": PLAN_HASH},
        headers=api.headers,
    )
    assert response.status_code == 403
    assert api.store.approved == []


def test_owner_approval_passes_only_exact_hash_and_hashed_identity(api):
    cookie = session_signer.issue_scoped("owner", ttl_seconds=60)
    approved_by = session_signer.owner_approval_identity(cookie)
    api.client.cookies.set("ods-session", cookie)
    response = api.client.post(
        f"/api/extensions/transactions/{TX_ID}/approval",
        json={"planHash": PLAN_HASH},
    )
    assert response.status_code == 200
    assert api.store.approved == [(TX_ID, PLAN_HASH, approved_by, NOW, NOW)]
    assert api.configuration.ready_calls == [(TX_ID, PLAN_HASH)]
    assert "approval" not in response.request.content.decode("utf-8")


def test_approval_rejects_extra_fields(api):
    cookie = session_signer.issue_scoped("owner", ttl_seconds=60)
    api.client.cookies.set("ods-session", cookie)
    response = api.client.post(
        f"/api/extensions/transactions/{TX_ID}/approval",
        json={"planHash": PLAN_HASH, "approvedBy": "assistant-manager"},
    )
    assert response.status_code == 422
    assert api.store.approved == []


def test_status_is_no_store_and_redacts_approval_binding(api):
    api.store.record["approval"] = {
        "approvedBy": "owner-" + "1" * 16,
        "approvedAt": NOW,
        "actor": "assistant-manager",
        "idempotencyKey": IDEMPOTENCY_KEY,
        "validUntil": "2026-10-01T00:00:00Z",
    }
    response = api.client.get(
        f"/api/extensions/transactions/{TX_ID}", headers=api.headers
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    approval = response.json()["approval"]
    assert approval == {
        "approved": True,
        "approvedAt": NOW,
        "approvedBy": "owner-" + "1" * 16,
    }
    assert IDEMPOTENCY_KEY not in response.text
    assert response.json()["plan"]["requiredSecretKeys"] == ["NOTES_API_KEY"]


def test_configuration_routes_are_authenticated_bounded_and_value_safe(api):
    path = f"/api/extensions/transactions/{TX_ID}/configuration"
    assert api.client.get(path).status_code == 401
    viewed = api.client.get(path, headers=api.headers)
    assert viewed.status_code == 200
    assert viewed.headers["cache-control"] == "no-store"

    secret = "do-not-echo-configuration-secret"
    body = {
        "planHash": PLAN_HASH,
        "schemaHash": "9" * 64,
        "idempotencyKey": "8" * 64,
        "values": {"NOTES_PATH": "/notes"},
        "secretValues": {"NOTES_API_KEY": secret},
    }
    assert api.client.post(path, json=body).status_code == 401
    response = api.client.post(path, json=body, headers=api.headers)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert secret not in response.text
    assert response.json()["presentSecretKeys"] == ["NOTES_API_KEY"]
    assert api.configuration.submit_calls == [
        (
            TX_ID,
            {
                "plan_hash": PLAN_HASH,
                "schema_hash": "9" * 64,
                "idempotency_key": "8" * 64,
                "values": {"NOTES_PATH": "/notes"},
                "secret_values": {"NOTES_API_KEY": secret},
            },
        )
    ]


def test_configuration_submission_rejects_extra_authority(api):
    response = api.client.post(
        f"/api/extensions/transactions/{TX_ID}/configuration",
        json={
            "planHash": PLAN_HASH,
            "schemaHash": "9" * 64,
            "idempotencyKey": "8" * 64,
            "values": {},
            "secretValues": {},
            "envelope": envelope(),
        },
        headers=api.headers,
    )
    assert response.status_code == 422
    assert api.configuration.submit_calls == []


def test_configuration_parser_and_internal_failures_never_echo_secret(api):
    path = f"/api/extensions/transactions/{TX_ID}/configuration"
    secret = "do-not-echo-parser-secret"
    schema_hash = "9" * 64
    idempotency_key = "8" * 64
    duplicate = (
        f'{{"planHash":"{PLAN_HASH}","schemaHash":"{schema_hash}",'
        f'"idempotencyKey":"{idempotency_key}","values":{{}},'
        f'"secretValues":{{"NOTES_API_KEY":"{secret}",'
        f'"NOTES_API_KEY":"{secret}"}}}}'
    )
    rejected = api.client.post(
        path,
        content=duplicate,
        headers={**api.headers, "Content-Type": "application/json"},
    )
    assert rejected.status_code == 422
    assert secret not in rejected.text

    def fail_with_secret(*_args, **_kwargs):
        raise RuntimeError(secret)

    api.configuration.submit = fail_with_secret
    failed = api.client.post(
        path,
        json={
            "planHash": PLAN_HASH,
            "schemaHash": "9" * 64,
            "idempotencyKey": "8" * 64,
            "values": {},
            "secretValues": {"NOTES_API_KEY": secret},
        },
        headers=api.headers,
    )
    assert failed.status_code == 503
    assert failed.json() == {"error": {"code": "configuration-unavailable"}}
    assert secret not in failed.text


def test_execute_requires_api_key_and_passes_only_id_and_hash(api):
    path = f"/api/extensions/transactions/{TX_ID}/execute"
    assert api.client.post(path, json={"planHash": PLAN_HASH}).status_code == 401
    response = api.client.post(
        path, json={"planHash": PLAN_HASH}, headers=api.headers
    )
    assert response.status_code == 200
    assert api.executor.calls == [(TX_ID, PLAN_HASH)]
    assert api.configuration.ready_calls == [(TX_ID, PLAN_HASH)]
    assert response.json()["finalState"] == "committed"

    rejected = api.client.post(
        path,
        json={"planHash": PLAN_HASH, "operations": []},
        headers=api.headers,
    )
    assert rejected.status_code == 422
    assert api.executor.calls == [(TX_ID, PLAN_HASH)]


def test_execute_is_unavailable_without_injected_executor(api):
    runtime = api.client.app.state.extension_transaction_runtime
    api.client.app.state.extension_transaction_runtime = TransactionRuntime(
        store=runtime.store,
        catalog=runtime.catalog,
        observed_state=runtime.observed_state,
        policy=runtime.policy,
        clock=runtime.clock,
        executor=None,
        configuration=runtime.configuration,
    )
    response = api.client.post(
        f"/api/extensions/transactions/{TX_ID}/execute",
        json={"planHash": PLAN_HASH},
        headers=api.headers,
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


def test_main_registers_transaction_routes():
    from main import app

    paths = {route.path for route in app.routes}
    assert "/api/extensions/transactions" in paths
    assert "/api/extensions/transactions/{transaction_id}/approval" in paths
    assert "/api/extensions/transactions/{transaction_id}/configuration" in paths
    assert "/api/extensions/transactions/{transaction_id}" in paths
    assert "/api/extensions/transactions/{transaction_id}/execute" in paths


def test_main_csrf_blocks_cross_origin_owner_approval(test_client, monkeypatch):
    monkeypatch.setenv("ODS_ASSISTANT_TRANSACTIONS_ENABLED", "true")
    session_signer._set_secret_for_tests("phase3c-session-secret")
    owner_cookie = session_signer.issue_scoped("owner", ttl_seconds=60)
    test_client.cookies.set("ods-session", owner_cookie)

    response = test_client.post(
        f"/api/extensions/transactions/{TX_ID}/approval",
        json={"planHash": PLAN_HASH},
        headers={
            "Origin": "https://evil.invalid",
            "Sec-Fetch-Site": "cross-site",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Cross-origin state-changing request rejected."
    }
