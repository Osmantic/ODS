from __future__ import annotations

import copy
import hashlib
import json

import pytest
from assistant_first_secret_client import SecretCustodyError
from extension_configuration import configuration_schema
from extension_transaction_configuration import TransactionConfigurationManager
from extension_transactions import IntegrityError, TransitionError, ValidationRejected

TRANSACTION_ID = "txn-" + "1" * 24
IDEMPOTENCY_KEY = "2" * 64
REFERENCE = "secret-v1-" + "3" * 48
SENTINEL = "private-do-not-echo"
NOW = "2026-09-12T07:00:00Z"
_DEFAULT_CUSTODY = object()


def contract(key: str, *, secret: bool = False) -> dict:
    return {
        "key": key,
        "type": "string",
        "required": True,
        "secret": secret,
        "source": "user",
        "restartBehavior": "service",
    }


def envelope() -> dict:
    plan = {
        "schema": "ods.assistant-first.plan.v1",
        "selectedServices": ["provider"],
        "definitions": [
            {
                "id": "provider",
                "configuration": [
                    contract("ENDPOINT"),
                    contract("TOKEN", secret=True),
                ],
            }
        ],
        "requiredConfigKeys": ["ENDPOINT"],
        "requiredSecretKeys": ["TOKEN"],
    }
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n"
    return {"plan": plan, "planHash": hashlib.sha256(canonical.encode()).hexdigest()}


def optional_envelope() -> dict:
    plan = {
        "schema": "ods.assistant-first.plan.v1",
        "selectedServices": ["notes"],
        "definitions": [
            {
                "id": "notes",
                "configuration": [
                    {
                        **contract("LABEL"),
                        "required": False,
                    }
                ],
            }
        ],
        "requiredConfigKeys": [],
        "requiredSecretKeys": [],
    }
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n"
    return {"plan": plan, "planHash": hashlib.sha256(canonical.encode()).hexdigest()}


class FakeStore:
    def __init__(self) -> None:
        self.loaded = {
            "transactionId": TRANSACTION_ID,
            "state": "awaiting_approval",
            "envelope": envelope(),
            "configuration": None,
        }
        self.intent = None
        self.events = []

    def read(self, transaction_id):
        assert transaction_id == TRANSACTION_ID
        return copy.deepcopy(self.loaded)

    def begin_configuration_exact(
        self,
        transaction_id,
        plan_hash,
        schema_hash,
        idempotency_key,
        values,
        present_secret_keys,
        applied_default_keys,
        configured_at,
        _current_time,
    ):
        self.events.append("intent")
        candidate = {
            "schema": "ods.assistant-first.transaction-configuration-intent.v1",
            "transactionId": transaction_id,
            "actor": "assistant-manager",
            "planHash": plan_hash,
            "schemaHash": schema_hash,
            "idempotencyKey": idempotency_key,
            "configuredAt": configured_at,
            "values": copy.deepcopy(values),
            "presentConfigKeys": sorted(values),
            "presentSecretKeys": list(present_secret_keys),
            "appliedDefaultKeys": list(applied_default_keys),
        }
        duplicate = self.intent is not None
        if duplicate:
            candidate["configuredAt"] = self.intent["configuredAt"]
            assert candidate == self.intent
        else:
            self.intent = candidate
        return {**copy.deepcopy(self.intent), "duplicate": duplicate}

    def finish_configuration_exact(
        self, transaction_id, plan_hash, idempotency_key, reference
    ):
        self.events.append("finish")
        assert self.intent is not None
        assert (transaction_id, plan_hash, idempotency_key) == (
            TRANSACTION_ID,
            self.intent["planHash"],
            self.intent["idempotencyKey"],
        )
        candidate = {
            **copy.deepcopy(self.intent),
            "schema": "ods.assistant-first.transaction-configuration.v1",
            "secretReference": reference,
        }
        duplicate = self.loaded["configuration"] is not None
        if duplicate:
            assert candidate == self.loaded["configuration"]
        else:
            self.loaded["configuration"] = candidate
        return {**copy.deepcopy(candidate), "duplicate": duplicate}


class FakeCustodian:
    def __init__(self) -> None:
        self.events = []
        self.fail = False
        self.store = None

    def stage(self, **submission):
        assert self.store is not None
        assert self.store.intent is not None
        if not self.events:
            assert self.store.loaded["configuration"] is None
        self.events.append(("stage", copy.deepcopy(submission)))
        if self.fail:
            raise SecretCustodyError("secret-custody-unavailable")
        return {
            "reference": REFERENCE,
            "duplicate": len(self.events) > 1,
            "presentSecretKeys": sorted(submission["secret_values"]),
        }

    def status(self, **binding):
        self.events.append(("status", copy.deepcopy(binding)))
        return {"presentSecretKeys": ["TOKEN"]}


def manager(store=None, custody=_DEFAULT_CUSTODY):
    store = store or FakeStore()
    if custody is _DEFAULT_CUSTODY:
        custody = FakeCustodian()
    if isinstance(custody, FakeCustodian):
        custody.store = store
    return TransactionConfigurationManager(store, custody, lambda: NOW), store, custody


def submission(store) -> dict:
    stored = store.loaded["envelope"]
    return {
        "plan_hash": stored["planHash"],
        "schema_hash": configuration_schema(
            stored, expected_plan_hash=stored["planHash"]
        )["schemaHash"],
        "idempotency_key": IDEMPOTENCY_KEY,
        "values": {"ENDPOINT": "https://example.invalid/v1"},
        "secret_values": {"TOKEN": SENTINEL},
    }


def test_view_derives_schema_from_stored_plan_without_secret_reference() -> None:
    service, _store, _custody = manager()
    result = service.view(TRANSACTION_ID)
    assert result["configured"] is False
    assert [field["key"] for field in result["fields"]] == ["ENDPOINT", "TOKEN"]
    assert "secretReference" not in result


def test_submit_reserves_before_custody_and_projects_no_secret_or_reference() -> None:
    service, store, custody = manager()
    result = service.submit(TRANSACTION_ID, **submission(store))
    assert store.events == ["intent", "finish"]
    assert custody.events[0][0] == "stage"
    assert custody.events[0][1]["secret_values"] == {"TOKEN": SENTINEL}
    assert store.intent["presentSecretKeys"] == ["TOKEN"]
    assert SENTINEL not in json.dumps(store.intent)
    assert SENTINEL not in json.dumps(result)
    assert REFERENCE not in json.dumps(result)
    assert result["configured"] is True
    assert result["duplicate"] is False


def test_exact_retry_is_idempotent_across_intent_custody_and_final_record() -> None:
    service, store, _custody = manager()
    first = service.submit(TRANSACTION_ID, **submission(store))
    second = service.submit(TRANSACTION_ID, **submission(store))
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert store.events == ["intent", "finish", "intent", "finish"]


def test_custody_unavailable_is_rejected_before_writing_intent() -> None:
    service, store, _custody = manager(custody=None)
    with pytest.raises(IntegrityError) as caught:
        service.submit(TRANSACTION_ID, **submission(store))
    assert caught.value.code == "secret-custody-unavailable"
    assert store.intent is None


def test_custody_failure_leaves_value_safe_retriable_intent() -> None:
    custody = FakeCustodian()
    custody.fail = True
    service, store, _custody = manager(custody=custody)
    with pytest.raises(IntegrityError) as caught:
        service.submit(TRANSACTION_ID, **submission(store))
    assert caught.value.code == "secret-custody-unavailable"
    assert store.intent is not None
    assert store.loaded["configuration"] is None
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in json.dumps(store.intent)


def test_wrong_binding_or_locked_state_fails_before_custody() -> None:
    service, store, custody = manager()
    invalid = submission(store)
    invalid["plan_hash"] = "0" * 64
    with pytest.raises(ValidationRejected) as caught:
        service.submit(TRANSACTION_ID, **invalid)
    assert caught.value.code == "plan-hash-mismatch"
    store.loaded["state"] = "approved"
    with pytest.raises(TransitionError) as caught:
        service.submit(TRANSACTION_ID, **submission(store))
    assert caught.value.code == "configuration-locked"
    assert custody.events == []


def test_require_ready_revalidates_record_and_host_presence() -> None:
    service, store, custody = manager()
    service.submit(TRANSACTION_ID, **submission(store))
    result = service.require_ready(TRANSACTION_ID, store.loaded["envelope"]["planHash"])
    assert result["configured"] is True
    assert custody.events[-1][0] == "status"
    assert custody.events[-1][1]["reference"] == REFERENCE

    original_status = custody.status
    custody.status = lambda **_binding: {"presentSecretKeys": []}
    with pytest.raises(IntegrityError) as caught:
        service.require_ready(TRANSACTION_ID, store.loaded["envelope"]["planHash"])
    assert caught.value.code == "secret-custody-presence-mismatch"
    custody.status = original_status


def test_require_ready_rejects_missing_or_stale_configuration() -> None:
    service, store, _custody = manager()
    with pytest.raises(TransitionError) as caught:
        service.require_ready(TRANSACTION_ID, store.loaded["envelope"]["planHash"])
    assert caught.value.code == "missing-configuration"

    service.submit(TRANSACTION_ID, **submission(store))
    store.loaded["configuration"]["schemaHash"] = "0" * 64
    with pytest.raises(IntegrityError) as caught:
        service.require_ready(TRANSACTION_ID, store.loaded["envelope"]["planHash"])
    assert caught.value.code == "configuration-record-invalid"


def test_require_ready_allows_unset_optional_configuration() -> None:
    service, store, custody = manager()
    store.loaded["envelope"] = optional_envelope()
    result = service.require_ready(
        TRANSACTION_ID, store.loaded["envelope"]["planHash"]
    )
    assert result["configured"] is False
    assert [field["key"] for field in result["fields"]] == ["LABEL"]
    assert custody.events == []
