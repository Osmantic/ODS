from __future__ import annotations

import copy
import hashlib
import json

import pytest
from assistant_first_secret_client import SecretCustodyError
from extension_configuration import configuration_schema
from extension_transaction_configuration import TransactionConfigurationManager
from extension_transactions import (
    configuration_attestation_hash,
    configuration_record_digest,
    IntegrityError,
    TransitionError,
    ValidationRejected,
)

TRANSACTION_ID = "txn-" + "1" * 24
IDEMPOTENCY_KEY = "2" * 64
REFERENCE = "secret-v1-" + "3" * 48
SENTINEL = "private-do-not-echo"
NOW = "2026-09-12T07:00:00Z"
_DEFAULT_CUSTODY = object()


def contract(key: str, *, secret: bool = False, source: str = "user") -> dict:
    result = {
        "key": key,
        "type": "string",
        "required": True,
        "secret": secret,
        "source": source,
        "restartBehavior": "service",
    }
    if source == "generated":
        result["validation"] = {"minLength": 32, "maxLength": 128}
    return result


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


def generated_envelope() -> dict:
    plan = {
        "schema": "ods.assistant-first.plan.v1",
        "selectedServices": ["searxng"],
        "definitions": [
            {
                "id": "searxng",
                "configuration": [
                    contract("SEARXNG_SECRET", secret=True, source="generated")
                ],
            }
        ],
        "requiredConfigKeys": [],
        "requiredSecretKeys": ["SEARXNG_SECRET"],
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
            "presentSecretKeys": sorted(
                set(submission["secret_values"])
                | set(submission["generated_secret_keys"])
            ),
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


def test_required_generated_secret_is_created_only_inside_host_custody() -> None:
    service, store, custody = manager()
    store.loaded["envelope"] = generated_envelope()
    stored = store.loaded["envelope"]
    request = {
        "plan_hash": stored["planHash"],
        "schema_hash": configuration_schema(
            stored, expected_plan_hash=stored["planHash"]
        )["schemaHash"],
        "idempotency_key": IDEMPOTENCY_KEY,
        "values": {},
        "secret_values": {},
    }

    result = service.submit(TRANSACTION_ID, **request)

    staged = custody.events[0][1]
    assert staged["secret_values"] == {}
    assert staged["generated_secret_keys"] == ["SEARXNG_SECRET"]
    assert store.intent["presentSecretKeys"] == ["SEARXNG_SECRET"]
    assert result["presentSecretKeys"] == ["SEARXNG_SECRET"]
    assert "secretReference" not in result


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

def _compute_expected_hash(
    transaction_id, plan_hash, schema_hash, configured,
    values, present_config_keys, present_secret_keys, applied_default_keys,
):
    """Reproduce the preimage hash for assertion."""
    from extension_transactions import canonical_json_bytes
    preimage = {
        "schema": "ods.assistant-first.configuration-attestation.v1",
        "transactionId": transaction_id,
        "planHash": plan_hash,
        "schemaHash": schema_hash,
        "configured": configured,
        "values": values,
        "presentConfigKeys": present_config_keys,
        "presentSecretKeys": present_secret_keys,
        "appliedDefaultKeys": applied_default_keys,
    }
    return hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()


def test_empty_view_has_stable_configuration_hash() -> None:
    service, _store, _custody = manager()
    result = service.view(TRANSACTION_ID)
    assert "configurationHash" in result
    assert len(result["configurationHash"]) == 64
    schema = configuration_schema(
        _store.loaded["envelope"],
        expected_plan_hash=_store.loaded["envelope"]["planHash"],
    )
    expected = _compute_expected_hash(
        TRANSACTION_ID,
        _store.loaded["envelope"]["planHash"],
        schema["schemaHash"],
        False, {}, [], [], [],
    )
    assert result["configurationHash"] == expected
    # Stable across calls
    result2 = service.view(TRANSACTION_ID)
    assert result2["configurationHash"] == result["configurationHash"]


def test_changed_nonsecret_value_changes_hash() -> None:
    """Two identical configurations with different non-secret values produce distinct hashes."""
    env = envelope()
    schema_hash = configuration_schema(
        env, expected_plan_hash=env["planHash"]
    )["schemaHash"]

    # Instance A: ENDPOINT = https://example.invalid/v1
    svc_a, store_a, _ = manager()
    store_a.loaded["envelope"] = copy.deepcopy(env)
    svc_a.submit(
        TRANSACTION_ID,
        plan_hash=env["planHash"],
        schema_hash=schema_hash,
        idempotency_key=IDEMPOTENCY_KEY,
        values={"ENDPOINT": "https://example.invalid/v1"},
        secret_values={"TOKEN": SENTINEL},
    )
    result_a = svc_a.view(TRANSACTION_ID)

    # Instance B: ENDPOINT = https://example.invalid/v2
    svc_b, store_b, _ = manager()
    store_b.loaded["envelope"] = copy.deepcopy(env)
    svc_b.submit(
        TRANSACTION_ID,
        plan_hash=env["planHash"],
        schema_hash=schema_hash,
        idempotency_key=IDEMPOTENCY_KEY,
        values={"ENDPOINT": "https://example.invalid/v2"},
        secret_values={"TOKEN": SENTINEL},
    )
    result_b = svc_b.view(TRANSACTION_ID)

    assert result_a["configurationHash"] != result_b["configurationHash"]
    expected_a = _compute_expected_hash(
        TRANSACTION_ID, env["planHash"], schema_hash, True,
        {"ENDPOINT": "https://example.invalid/v1"},
        ["ENDPOINT"], ["TOKEN"], [],
    )
    expected_b = _compute_expected_hash(
        TRANSACTION_ID, env["planHash"], schema_hash, True,
        {"ENDPOINT": "https://example.invalid/v2"},
        ["ENDPOINT"], ["TOKEN"], [],
    )
    assert result_a["configurationHash"] == expected_a
    assert result_b["configurationHash"] == expected_b


def test_changed_secret_key_presence_changes_hash() -> None:
    """Helper-level test: presentSecretKeys alone changes the hash.

    This is NOT stored-schema validation; it calls the _configuration_hash
    helper directly with identical inputs except presentSecretKeys to prove
    that secret-key presence is included in the canonical preimage.
    """
    env = envelope()
    schema_hash = configuration_schema(
        env, expected_plan_hash=env["planHash"]
    )["schemaHash"]

    h_none = TransactionConfigurationManager._configuration_hash(
        TRANSACTION_ID, env["planHash"], schema_hash, False,
        {}, [], [], [],
    )
    h_token = TransactionConfigurationManager._configuration_hash(
        TRANSACTION_ID, env["planHash"], schema_hash, False,
        {}, [], ["TOKEN"], [],
    )

    assert h_none != h_token
    expected_none = _compute_expected_hash(
        TRANSACTION_ID, env["planHash"], schema_hash, False,
        {}, [], [], [],
    )
    expected_token = _compute_expected_hash(
        TRANSACTION_ID, env["planHash"], schema_hash, False,
        {}, [], ["TOKEN"], [],
    )
    assert h_none == expected_none
    assert h_token == expected_token


def test_no_secret_material_in_projection_or_preimage() -> None:
    service, store, _custody = manager()
    result = service.submit(TRANSACTION_ID, **submission(store))
    result_json = json.dumps(result)
    assert SENTINEL not in result_json
    assert "secretReference" not in result
    assert "secretValues" not in result
    # Verify preimage used for hash has no secret fields
    assert result["configurationHash"] == _compute_expected_hash(
        TRANSACTION_ID,
        store.loaded["envelope"]["planHash"],
        store.loaded["configuration"]["schemaHash"],
        True,
        {"ENDPOINT": "https://example.invalid/v1"},
        ["ENDPOINT"],
        ["TOKEN"],
        [],
    )


def test_submit_view_require_ready_equal_hash() -> None:
    service, store, _custody = manager()
    submitted = service.submit(TRANSACTION_ID, **submission(store))
    viewed = service.view(TRANSACTION_ID)
    required = service.require_ready(
        TRANSACTION_ID, store.loaded["envelope"]["planHash"]
    )
    assert submitted["configurationHash"] == viewed["configurationHash"]
    assert viewed["configurationHash"] == required["configurationHash"]
    # All three agree with the independently computed value
    expected = _compute_expected_hash(
        TRANSACTION_ID,
        store.loaded["envelope"]["planHash"],
        store.loaded["configuration"]["schemaHash"],
        True,
        {"ENDPOINT": "https://example.invalid/v1"},
        ["ENDPOINT"],
        ["TOKEN"],
        [],
    )
    assert submitted["configurationHash"] == expected


def test_manager_delegates_to_public_configuration_attestation_hash() -> None:
    """The manager's projections must equal the public module-level helper.

    Preserves the exact hash contract: the static method delegates to the
    public helper, so old expected hashes remain valid bit-for-bit.
    """
    service, store, _custody = manager()
    result = service.view(TRANSACTION_ID)
    schema = configuration_schema(
        store.loaded["envelope"],
        expected_plan_hash=store.loaded["envelope"]["planHash"],
    )
    assert (
        result["configurationHash"]
        == configuration_attestation_hash(
            TRANSACTION_ID,
            store.loaded["envelope"]["planHash"],
            schema["schemaHash"],
            False,
            {},
            [],
            [],
            [],
        )
        == _compute_expected_hash(
            TRANSACTION_ID,
            store.loaded["envelope"]["planHash"],
            schema["schemaHash"],
            False, {}, [], [], [],
        )
    )

    submitted = service.submit(TRANSACTION_ID, **submission(store))
    assert (
        submitted["configurationHash"]
        == configuration_attestation_hash(
            TRANSACTION_ID,
            store.loaded["envelope"]["planHash"],
            store.loaded["configuration"]["schemaHash"],
            True,
            {"ENDPOINT": "https://example.invalid/v1"},
            ["ENDPOINT"],
            ["TOKEN"],
            [],
        )
        == _compute_expected_hash(
            TRANSACTION_ID,
            store.loaded["envelope"]["planHash"],
            store.loaded["configuration"]["schemaHash"],
            True,
            {"ENDPOINT": "https://example.invalid/v1"},
            ["ENDPOINT"],
            ["TOKEN"],
            [],
        )
    )


def test_static_configuration_hash_matches_public_helper_exactly() -> None:
    """Direct equality across every projection dimension."""
    cases = [
        (TRANSACTION_ID, "a" * 64, "b" * 64, False, {}, [], [], []),
        (
            TRANSACTION_ID,
            "a" * 64,
            "b" * 64,
            True,
            {"ENDPOINT": "https://example.invalid/v1"},
            ["ENDPOINT"],
            ["TOKEN"],
            ["ENDPOINT"],
        ),
        (
            "txn-" + "9" * 24,
            "c" * 64,
            "d" * 64,
            True,
            {"LABEL": "notes", "PORT": 8080},
            ["LABEL", "PORT"],
            [],
            [],
        ),
    ]
    for case in cases:
        assert TransactionConfigurationManager._configuration_hash(
            *case
        ) == configuration_attestation_hash(*case)


def test_private_record_digest_tracks_reference_but_safe_hash_does_not() -> None:
    """Opaque secretReference changes the private digest, never the safe hash."""
    env = envelope()
    stored = env
    schema_hash = configuration_schema(
        stored, expected_plan_hash=stored["planHash"]
    )["schemaHash"]

    def record(reference):
        return {
            "schema": "ods.assistant-first.transaction-configuration.v1",
            "transactionId": TRANSACTION_ID,
            "actor": "assistant-manager",
            "planHash": env["planHash"],
            "schemaHash": schema_hash,
            "idempotencyKey": IDEMPOTENCY_KEY,
            "configuredAt": NOW,
            "values": {"ENDPOINT": "https://example.invalid/v1"},
            "presentConfigKeys": ["ENDPOINT"],
            "presentSecretKeys": ["TOKEN"],
            "appliedDefaultKeys": [],
            "secretReference": reference,
        }

    digest_a = configuration_record_digest(record(REFERENCE), schema_hash)
    digest_b = configuration_record_digest(
        record("secret-v1-" + "f" * 48), schema_hash
    )
    digest_none = configuration_record_digest(record(None), schema_hash)
    assert digest_a != digest_b
    assert digest_a != digest_none
    assert len(digest_a) == 64

    # The owner-visible attestation hash excludes secretReference entirely:
    # projecting each record's nonsecret fields yields ONE stable hash even
    # though the two records' private digests differ.
    def safe_hash_from(record):
        return configuration_attestation_hash(
            record["transactionId"],
            record["planHash"],
            record["schemaHash"],
            True,
            dict(record["values"]),
            list(record["presentConfigKeys"]),
            list(record["presentSecretKeys"]),
            list(record["appliedDefaultKeys"]),
        )

    assert safe_hash_from(record(REFERENCE)) == safe_hash_from(
        record("secret-v1-" + "f" * 48)
    )
    assert safe_hash_from(record(REFERENCE)) == _compute_expected_hash(
        TRANSACTION_ID,
        env["planHash"],
        schema_hash,
        True,
        {"ENDPOINT": "https://example.invalid/v1"},
        ["ENDPOINT"],
        ["TOKEN"],
        [],
    )
    assert safe_hash_from(record(REFERENCE)) != digest_a

    # Digest is domain-separated: bound to schemaHash and record identity,
    # and distinct from any value derived from the nonsecret projection.
    assert configuration_record_digest(
        record(REFERENCE), "0" * 64
    ) != digest_a
    assert configuration_record_digest(record(REFERENCE), schema_hash) == digest_a
