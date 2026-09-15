from __future__ import annotations

import hashlib
import json
import os
import threading

import assistant_first_planner as planner
import extension_configuration as configuration
import extension_transactions as transactions
import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="the durable transaction store is Linux-qualified"
)

ACTOR = "owner-42"
CREATED_AT = "2026-09-11T12:00:00Z"
APPROVED_AT = "2026-09-11T12:01:00Z"
NOW = "2026-09-11T12:02:00Z"
VALID_UNTIL = "2026-10-01T00:00:00Z"
IDEMPOTENCY_KEY = "1" * 64
CATALOG_REVISION = "a" * 64
HOST_STATE = {
    "odsVersion": "2.1.0",
    "platform": "linux",
    "architecture": "amd64",
    "containerRuntime": "docker",
    "gpuBackend": "cpu",
    "driverVersion": None,
    "available": {
        "diskBytes": 1_000_000,
        "ramBytes": 1_000_000,
        "vramBytes": 0,
        "cpuMillicores": 4_000,
        "gpuCount": 0,
    },
    "occupiedPorts": [],
    "reservedResources": [],
    "installedServices": [],
}
POLICY = {
    "allowedTrustTiers": ["bundled"],
    "forbiddenHostPermissions": ["docker-socket", "privileged"],
    "allowExperimental": False,
    "requireApproval": True,
}
STATE_REVISION = hashlib.sha256(planner.canonical_json_bytes(HOST_STATE)).hexdigest()
POLICY_REVISION = hashlib.sha256(planner.canonical_json_bytes(POLICY)).hexdigest()


def catalog_entry(service_id: str = "notes") -> dict:
    return {
        "schema_version": "ods.services.v2",
        "compatibility": {"ods_min": "2.0.0"},
        "service": {
            "id": service_id,
            "version": "1.2.3",
            "data_schema_version": "1",
            "type": "docker",
            "depends_on": [],
            "planning": {
                "provides": ["notes@1"],
                "requires": [],
                "optional": [],
                "conflicts": [],
                "provider_priority": 0,
                "requirements": {
                    "platforms": ["linux"],
                    "architectures": ["amd64"],
                    "container_runtimes": ["docker"],
                    "gpu_backends": ["cpu"],
                    "min_driver_version": None,
                },
                "estimates": {
                    "download_bytes": 100,
                    "disk_bytes": 200,
                    "cpu_millicores": 100,
                    "ram_bytes": 300,
                    "vram_bytes": 0,
                    "gpu_count": 0,
                },
                "resources": {
                    "host_ports": [],
                    "container_ports": [],
                    "networks": [],
                    "volumes": [],
                    "devices": [],
                    "exclusive": [],
                    "linux_capabilities": [],
                    "host_permissions": ["network"],
                },
                "configuration": [],
                "artifacts": {
                    "images": [
                        {
                            "reference": f"example/{service_id}:1.2.3",
                            "digest": "sha256:" + "e" * 64,
                            "download_bytes": 100,
                        }
                    ],
                    "builds": [],
                },
                "lifecycle": {
                    "health_checks": ["http:/health"],
                    "readiness": ["healthy"],
                    "setup_hook": None,
                    "migration_hook": None,
                    "rollback": "definition",
                    "timeout_seconds": 120,
                },
                "data": [],
                "trust": {
                    "tier": "bundled",
                    "publisher": "ODS",
                    "definition_signature": None,
                },
                "support": {"status": "supported", "url": None},
            },
        },
        "_catalog": {
            "definition_sha256": "sha256:" + "d" * 64,
            "compose_sha256": "",
        },
    }


def build_envelope(service_id: str = "notes") -> dict:
    return planner.build_plan(
        [catalog_entry(service_id)],
        requested_action="ensure",
        requested_services=[service_id],
        requested_capabilities=[],
        provider_preferences={},
        missing_config_keys=[],
        missing_secret_keys=[],
        catalog_revision=CATALOG_REVISION,
        observed_state_revision=STATE_REVISION,
        observed_state=HOST_STATE,
        policy_revision=POLICY_REVISION,
        policy=POLICY,
        valid_until=VALID_UNTIL,
    )


def build_configured_envelope() -> dict:
    entry = catalog_entry("provider")
    entry["service"]["planning"]["configuration"] = [
        {
            "key": "ENDPOINT",
            "type": "url",
            "required": True,
            "secret": False,
            "source": "user",
            "restart_behavior": "service",
        },
        {
            "key": "OFFSET",
            "type": "integer",
            "required": True,
            "secret": False,
            "source": "user",
            "restart_behavior": "service",
            "validation": {"minimum": -5, "maximum": 5},
        },
        {
            "key": "TOKEN",
            "type": "string",
            "required": True,
            "secret": True,
            "source": "user",
            "restart_behavior": "service",
        },
    ]
    return planner.build_plan(
        [entry],
        requested_action="ensure",
        requested_services=["provider"],
        requested_capabilities=[],
        provider_preferences={},
        missing_config_keys=["ENDPOINT", "OFFSET"],
        missing_secret_keys=["TOKEN"],
        catalog_revision=CATALOG_REVISION,
        observed_state_revision=STATE_REVISION,
        observed_state=HOST_STATE,
        policy_revision=POLICY_REVISION,
        policy=POLICY,
        valid_until=VALID_UNTIL,
    )


def create(store, envelope=None, key=IDEMPOTENCY_KEY, actor=ACTOR):
    return store.create(envelope or build_envelope(), actor, key, CREATED_AT, NOW)


def approval_for(descriptor, envelope=None, **changes):
    envelope = envelope or build_envelope()
    approval = {
        "actor": ACTOR,
        "approvedAt": APPROVED_AT,
        "approvedBy": "owner-browser-session",
        "catalogRevision": envelope["catalogRevision"],
        "idempotencyKey": IDEMPOTENCY_KEY,
        "observedStateRevision": envelope["observedStateRevision"],
        "planHash": envelope["planHash"],
        "policyRevision": envelope["policyRevision"],
        "transactionId": descriptor["transactionId"],
        "validUntil": VALID_UNTIL,
    }
    approval.update(changes)
    return approval


def advance_to_committed(store, descriptor, envelope=None):
    envelope = envelope or build_envelope()
    transaction_id = descriptor["transactionId"]
    store._approve_record(
        transaction_id, approval_for(descriptor, envelope), NOW
    )
    for state in (
        "reserved",
        "downloading",
        "staged",
        "configuring",
        "applying",
        "verifying",
        "committed",
    ):
        store.transition(transaction_id, state, ACTOR, NOW, NOW)
    return store.read(transaction_id)


def finalization_receipt(transaction, **changes):
    receipt = {
        "schema": transactions.FINALIZATION_SCHEMA,
        "transactionId": transaction["transactionId"],
        "planHash": transaction["envelope"]["planHash"],
        "sequence": transaction["sequence"],
        "lockfileHash": "b" * 64,
        "postCommitObservedStateRevision": "c" * 64,
        "recordedAt": NOW,
    }
    receipt.update(changes)
    return receipt


def test_real_planner_envelope_create_read_and_idempotent_replay(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    first = create(store, envelope)
    second = create(store, envelope)

    assert first["state"] == "awaiting_approval"
    assert first["duplicate"] is False
    assert second == {**first, "duplicate": True}
    observed = store.read(first["transactionId"])
    assert observed["envelope"] == envelope
    assert observed["sequence"] == 2
    assert observed["approval"] is None


def test_pre_binding_durable_plan_remains_readable_after_upgrade(tmp_path):
    envelope = build_envelope()
    envelope["plan"].pop("resourcePortBindings")
    old_hash = hashlib.sha256(planner.canonical_json_bytes(envelope["plan"])).hexdigest()
    envelope["planHash"] = old_hash
    envelope["planId"] = f"plan-{old_hash[:24]}"
    store = transactions.TransactionStore(tmp_path / "transactions")
    descriptor = create(store, envelope)
    assert store.read(descriptor["transactionId"])["envelope"] == envelope


def test_root_lock_serializes_threads_without_replacing_active_descriptor(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with store._lock:
            first_entered.set()
            release_first.wait(timeout=2)

    def second() -> None:
        with store._lock:
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    assert first_entered.wait(timeout=2)
    second_thread.start()
    try:
        assert not second_entered.wait(timeout=0.1)
    finally:
        release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered.is_set()


def test_root_lock_rejects_directory_entry_replacement_and_releases_thread_lock(
    tmp_path, monkeypatch
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    decoy = tmp_path / "decoy"
    decoy.write_text("x", encoding="utf-8")
    real_lstat = transactions.os.lstat

    def replaced_lstat(path):
        if str(path) == str(store._lock._lock_path):
            return real_lstat(decoy)
        return real_lstat(path)

    monkeypatch.setattr(transactions.os, "lstat", replaced_lstat)
    with pytest.raises(transactions.TransactionError) as caught:
        with store._lock:
            pass
    assert caught.value.code == "lock-replaced"

    monkeypatch.undo()
    with store._lock:
        pass


def test_root_lock_closes_descriptor_when_unlock_raises(tmp_path, monkeypatch):
    store = transactions.TransactionStore(tmp_path / "transactions")
    real_flock = transactions._fcntl.flock

    def fail_unlock(fd, operation):
        if operation == transactions._fcntl.LOCK_UN:
            raise OSError("injected unlock failure")
        return real_flock(fd, operation)

    store._lock.acquire()
    locked_fd = store._lock._fd
    monkeypatch.setattr(transactions._fcntl, "flock", fail_unlock)
    with pytest.raises(OSError, match="injected unlock failure"):
        store._lock.release()
    assert store._lock._fd is None
    with pytest.raises(OSError):
        os.fstat(locked_fd)

    monkeypatch.undo()
    with store._lock:
        pass


def test_transaction_identity_binds_actor_and_idempotency_key(tmp_path):
    envelope = build_envelope()
    first_store = transactions.TransactionStore(tmp_path / "first")
    second_store = transactions.TransactionStore(tmp_path / "second")
    first = create(first_store, envelope, actor="owner-a")
    second = create(second_store, envelope, actor="owner-b")
    assert first["transactionId"] != second["transactionId"]


def test_operational_assistant_actor_is_allowed_but_cannot_approve(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope, actor="assistant-manager")
    assert store.read(descriptor["transactionId"])["state"] == "awaiting_approval"

    approval = approval_for(
        descriptor,
        envelope,
        actor="assistant-manager",
        approvedBy="assistant-manager",
    )
    with pytest.raises(transactions.ApprovalError) as caught:
        store._approve_record(descriptor["transactionId"], approval, NOW)
    assert caught.value.code == "invalid-approval-data"


def test_idempotency_key_cannot_bind_a_different_plan(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    create(store)
    with pytest.raises(transactions.IdempotencyConflict):
        create(store, build_envelope("calendar"))


@pytest.mark.parametrize("failed_write", [1, 2, 3])
def test_create_replays_exact_durable_write_prefix(tmp_path, monkeypatch, failed_write):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    original = transactions._write_immutable
    calls = 0

    def fail_once(path, data, mode=0o600):
        nonlocal calls
        calls += 1
        if calls == failed_write:
            raise transactions.TransactionError("injected-crash")
        return original(path, data, mode)

    monkeypatch.setattr(transactions, "_write_immutable", fail_once)
    with pytest.raises(transactions.TransactionError, match="injected-crash"):
        create(store, envelope)
    monkeypatch.setattr(transactions, "_write_immutable", original)

    replay = create(store, envelope)
    assert replay["state"] == "awaiting_approval"
    assert replay["duplicate"] is True
    assert store.read(replay["transactionId"])["sequence"] == 2


def test_create_replays_after_only_the_planned_journal_record(tmp_path, monkeypatch):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    original = transactions._journal_append
    calls = 0

    def fail_after_first(path, line, expected_actor=None):
        nonlocal calls
        calls += 1
        result = original(path, line, expected_actor)
        if calls == 1:
            raise transactions.TransactionError("injected-crash")
        return result

    monkeypatch.setattr(transactions, "_journal_append", fail_after_first)
    with pytest.raises(transactions.TransactionError, match="injected-crash"):
        create(store, envelope)
    monkeypatch.setattr(transactions, "_journal_append", original)

    replay = create(store, envelope)
    assert replay["duplicate"] is True
    assert store.read(replay["transactionId"])["sequence"] == 2


def test_create_refuses_non_prefix_partial_transaction(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    txn_id = transactions._derive_transaction_id(
        envelope, ACTOR, IDEMPOTENCY_KEY, CREATED_AT
    )
    tx_dir = store._tx_path(txn_id)
    transactions._safe_mkdir(tx_dir, 0o700)
    transactions._write_immutable(tx_dir / "unexpected", b"x", 0o600)

    with pytest.raises(transactions.IntegrityError, match="incomplete-transaction"):
        create(store, envelope)


def test_approval_is_exact_bound_and_idempotent(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    approval = approval_for(descriptor, envelope)

    accepted = store._approve_record(descriptor["transactionId"], approval, NOW)
    duplicate = store._approve_record(descriptor["transactionId"], approval, NOW)
    assert accepted["state"] == "approved"
    assert duplicate["noop"] is True
    assert store.read(descriptor["transactionId"])["approval"] == approval


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"planHash": "f" * 64}, "approval-binding-mismatch"),
        ({"catalogRevision": "f" * 64}, "approval-binding-mismatch"),
        ({"observedStateRevision": "f" * 64}, "approval-binding-mismatch"),
        ({"policyRevision": "f" * 64}, "approval-binding-mismatch"),
        ({"idempotencyKey": "f" * 64}, "approval-binding-mismatch"),
        ({"actor": "other-owner"}, "actor-mismatch"),
        ({"approvedBy": "assistant"}, "invalid-approval-data"),
        ({"approvedBy": "ai-service"}, "invalid-approval-data"),
        ({"approvedBy": "bot-worker"}, "invalid-approval-data"),
        ({"validUntil": NOW}, "approval-expired"),
    ],
)
def test_approval_rejects_binding_drift_and_assistant_actors(tmp_path, changes, code):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    with pytest.raises(transactions.ApprovalError) as caught:
        store._approve_record(
            descriptor["transactionId"],
            approval_for(descriptor, envelope, **changes),
            NOW,
        )
    assert caught.value.code == code


def test_approval_replay_repairs_crash_between_record_and_journal(
    tmp_path, monkeypatch
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    approval = approval_for(descriptor, envelope)
    original = transactions._journal_append

    def fail_before_journal(*args, **kwargs):
        raise transactions.TransactionError("injected-crash")

    monkeypatch.setattr(transactions, "_journal_append", fail_before_journal)
    with pytest.raises(transactions.TransactionError, match="injected-crash"):
        store._approve_record(descriptor["transactionId"], approval, NOW)
    monkeypatch.setattr(transactions, "_journal_append", original)

    repaired = store._approve_record(descriptor["transactionId"], approval, NOW)
    assert repaired["state"] == "approved"
    assert store.read(descriptor["transactionId"])["sequence"] == 3


def test_reserve_requires_unexpired_exact_approval(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    store._approve_record(descriptor["transactionId"], approval_for(descriptor), NOW)

    with pytest.raises(transactions.TransitionError) as caught:
        store.transition(
            descriptor["transactionId"],
            "reserved",
            ACTOR,
            VALID_UNTIL,
            VALID_UNTIL,
        )
    assert caught.value.code == "expired-at-reserve"


def test_transition_rejects_illegal_terminal_shortcut_even_after_expiry(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    with pytest.raises(transactions.TransitionError) as caught:
        store.transition(
            descriptor["transactionId"],
            "committed",
            ACTOR,
            VALID_UNTIL,
            VALID_UNTIL,
        )
    assert caught.value.code == "illegal-transition"


@pytest.mark.parametrize(
    ("approval", "code"),
    [
        ({"required": 1, "scope": "exact-plan-hash"}, "invalid-approval"),
        ({"required": False, "scope": "exact-plan-hash"}, "invalid-approval"),
        ({"required": True, "scope": "broad"}, "invalid-approval-scope"),
    ],
)
def test_plan_approval_contract_rejects_non_exact_values(tmp_path, approval, code):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    envelope["plan"]["approval"] = approval
    plan_hash = hashlib.sha256(
        transactions.canonical_json_bytes(envelope["plan"])
    ).hexdigest()
    envelope["planHash"] = plan_hash
    envelope["planId"] = f"plan-{plan_hash[:24]}"

    with pytest.raises(transactions.ValidationRejected) as caught:
        create(store, envelope)
    assert caught.value.code == code


def test_transaction_directory_symlink_fails_closed(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    transaction_id = transactions._derive_transaction_id(
        envelope, ACTOR, IDEMPOTENCY_KEY, CREATED_AT
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(
        outside,
        tmp_path / "transactions" / "transactions" / transaction_id,
        target_is_directory=True,
    )

    with pytest.raises(transactions.TransactionError) as caught:
        create(store, envelope)
    assert caught.value.code == "symlink-detected"


def test_read_fails_closed_on_noncanonical_plan_and_list_reports_error(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    descriptor = create(store)
    plan_path = (
        tmp_path
        / "transactions"
        / "transactions"
        / descriptor["transactionId"]
        / "plan.json"
    )
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.chmod(plan_path, 0o600)

    with pytest.raises(transactions.IntegrityError, match="plan-noncanonical"):
        store.read(descriptor["transactionId"])
    listed = store.list_transactions()
    assert listed == [
        {"transactionId": descriptor["transactionId"], "error": "plan-noncanonical"}
    ]


def test_read_rejects_multiple_trailing_journal_linefeeds(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    descriptor = create(store)
    journal_path = (
        tmp_path
        / "transactions"
        / "transactions"
        / descriptor["transactionId"]
        / "journal.jsonl"
    )
    with journal_path.open("ab") as handle:
        handle.write(b"\n")

    with pytest.raises(transactions.IntegrityError, match="journal-blank-record"):
        store.read(descriptor["transactionId"])


def test_failed_transaction_must_enter_reconciliation_before_terminal(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    descriptor = create(store)
    store.transition(descriptor["transactionId"], "failed", ACTOR, NOW, NOW)

    decision = store.inspect_recovery(
        descriptor["transactionId"], {"observedState": "applying"}, NOW
    )
    assert decision["nextState"] == "reconciling"
    with pytest.raises(transactions.TransitionError):
        store.transition(
            descriptor["transactionId"],
            "manual_recovery_required",
            ACTOR,
            NOW,
            NOW,
        )

    store.transition(descriptor["transactionId"], "reconciling", ACTOR, NOW, NOW)
    rolled_back = store.inspect_recovery(
        descriptor["transactionId"], {"completedStep": "rollback-complete"}, NOW
    )
    assert rolled_back["nextState"] == "rolled_back"


def test_transition_persists_exact_bounded_step_metadata(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    store._approve_record(descriptor["transactionId"], approval_for(descriptor), NOW)

    metadata = {
        "step": {
            "step": "reserve",
            "status": "started",
            "serviceId": "notes",
        }
    }
    store.transition(
        descriptor["transactionId"], "reserved", ACTOR, NOW, NOW, metadata
    )

    loaded = store.read(descriptor["transactionId"])
    assert loaded["journal"][-1]["step"] == metadata["step"]


def test_transition_rejects_non_step_or_secret_metadata(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    descriptor = create(store)
    store._approve_record(descriptor["transactionId"], approval_for(descriptor), NOW)

    with pytest.raises(transactions.ValidationRejected, match="invalid-metadata-keys"):
        store.transition(
            descriptor["transactionId"],
            "reserved",
            ACTOR,
            NOW,
            NOW,
            {"other": "value"},
        )

    with pytest.raises(transactions.ValidationRejected, match="step-secret-key"):
        store.transition(
            descriptor["transactionId"],
            "reserved",
            ACTOR,
            NOW,
            NOW,
            {"step": {"step": "reserve", "secretToken": "redacted"}},
        )


def test_canonical_json_rejects_non_json_objects_and_floats():
    with pytest.raises(transactions.ValidationRejected, match="non-json-type"):
        transactions.canonical_json_bytes(("tuple",))
    with pytest.raises(transactions.ValidationRejected, match="float-value"):
        transactions.canonical_json_bytes({"value": 1.5})


def test_untrusted_identifier_types_fail_with_domain_errors(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")

    bad_plan_id = build_envelope()
    bad_plan_id["planId"] = 7
    with pytest.raises(transactions.ValidationRejected) as plan_error:
        create(store, bad_plan_id)
    assert plan_error.value.code == "invalid-planid"

    bad_action = build_envelope()
    bad_action["plan"]["operations"][0]["action"] = []
    plan_hash = hashlib.sha256(
        transactions.canonical_json_bytes(bad_action["plan"])
    ).hexdigest()
    bad_action["planHash"] = plan_hash
    bad_action["planId"] = f"plan-{plan_hash[:24]}"
    with pytest.raises(transactions.ValidationRejected) as action_error:
        create(store, bad_action)
    assert action_error.value.code == "invalid-operation-action"

    with pytest.raises(transactions.ValidationRejected) as read_error:
        store.read(7)
    assert read_error.value.code == "invalid-txn-id"

    descriptor = create(store)
    with pytest.raises(transactions.ValidationRejected) as state_error:
        store.transition(descriptor["transactionId"], [], ACTOR, NOW, NOW)
    assert state_error.value.code == "invalid-target-state"


def test_hardlinked_binding_fails_closed(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    descriptor = create(store)
    binding = (
        tmp_path
        / "transactions"
        / "transactions"
        / descriptor["transactionId"]
        / "binding.json"
    )
    os.link(binding, tmp_path / "binding-copy")
    with pytest.raises(transactions.IntegrityError, match="link-count"):
        store.read(descriptor["transactionId"])


def test_http_retry_reuses_first_created_at(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    first = create(store, envelope)
    later = "2026-09-11T13:00:00Z"

    replay = store.create(envelope, ACTOR, IDEMPOTENCY_KEY, later, later)

    assert replay == {**first, "duplicate": True}
    loaded = store.read(first["transactionId"])
    assert loaded["journal"][0]["timestamp"] == CREATED_AT


def test_http_retry_recovers_prefix_without_idempotency_index(
    tmp_path, monkeypatch
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    original = transactions._journal_append
    calls = 0

    def fail_after_first(path, line, expected_actor=None):
        nonlocal calls
        calls += 1
        result = original(path, line, expected_actor)
        if calls == 1:
            raise transactions.TransactionError("injected-crash")
        return result

    monkeypatch.setattr(transactions, "_journal_append", fail_after_first)
    with pytest.raises(transactions.TransactionError, match="injected-crash"):
        create(store, envelope)
    monkeypatch.setattr(transactions, "_journal_append", original)

    later = "2026-09-11T13:00:00Z"
    replay = store.create(envelope, ACTOR, IDEMPOTENCY_KEY, later, later)
    assert replay["duplicate"] is True
    assert store.read(replay["transactionId"])["sequence"] == 2
    assert store.read(replay["transactionId"])["journal"][0]["timestamp"] == CREATED_AT


@pytest.mark.parametrize(
    ("actor", "envelope"),
    [
        ("other-owner", None),
        (ACTOR, "calendar"),
    ],
)
def test_http_retry_rejects_actor_or_plan_drift(tmp_path, actor, envelope):
    store = transactions.TransactionStore(tmp_path / "transactions")
    create(store)
    candidate = build_envelope(envelope) if envelope else build_envelope()
    later = "2026-09-11T13:00:00Z"

    with pytest.raises(transactions.IdempotencyConflict) as caught:
        store.create(candidate, actor, IDEMPOTENCY_KEY, later, later)
    assert caught.value.code == "idempotency-drift"


def test_exact_owner_approval_uses_only_durable_binding(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope, actor="assistant-manager")
    approved_by = "owner-" + "c" * 16

    result = store.approve_exact(
        descriptor["transactionId"],
        envelope["planHash"],
        approved_by,
        APPROVED_AT,
        NOW,
    )

    assert result == {
        "transactionId": descriptor["transactionId"],
        "state": "approved",
        "sequence": 3,
        "noop": False,
    }
    approval = store.read(descriptor["transactionId"])["approval"]
    assert approval == approval_for(
        descriptor,
        envelope,
        actor="assistant-manager",
        approvedBy=approved_by,
    )


def test_exact_owner_approval_is_idempotent(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    args = (
        descriptor["transactionId"],
        envelope["planHash"],
        "owner-" + "d" * 16,
        APPROVED_AT,
        NOW,
    )
    store.approve_exact(*args)
    replay = store.approve_exact(*args)
    assert replay["noop"] is True
    assert replay["sequence"] == 3


def test_exact_owner_approval_retry_ignores_later_server_timestamp(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    owner = "owner-" + "d" * 16
    store.approve_exact(
        descriptor["transactionId"],
        envelope["planHash"],
        owner,
        APPROVED_AT,
        NOW,
    )

    replay = store.approve_exact(
        descriptor["transactionId"],
        envelope["planHash"],
        owner,
        "2026-09-11T13:00:00Z",
        "2026-09-11T13:00:00Z",
    )

    assert replay["noop"] is True
    loaded = store.read(descriptor["transactionId"])
    assert loaded["approval"]["approvedAt"] == APPROVED_AT
    assert loaded["journal"][-1]["timestamp"] == APPROVED_AT


@pytest.mark.parametrize(
    ("plan_hash", "approved_by", "approved_at", "current_time", "code"),
    [
        ("f" * 64, "owner-" + "c" * 16, APPROVED_AT, NOW, "plan-hash-mismatch"),
        (
            "expected",
            "assistant-manager",
            APPROVED_AT,
            NOW,
            "invalid-owner-approval-identity",
        ),
        ("expected", "owner-" + "c" * 16, VALID_UNTIL, VALID_UNTIL, "approval-expired"),
    ],
)
def test_exact_owner_approval_rejects_invalid_binding(
    tmp_path, plan_hash, approved_by, approved_at, current_time, code
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    selected_hash = envelope["planHash"] if plan_hash == "expected" else plan_hash

    with pytest.raises(transactions.ApprovalError) as caught:
        store.approve_exact(
            descriptor["transactionId"],
            selected_hash,
            approved_by,
            approved_at,
            current_time,
        )
    assert caught.value.code == code


def test_exact_owner_approval_repairs_write_before_journal_crash(
    tmp_path, monkeypatch
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    original = transactions._journal_append

    def fail_journal(*args, **kwargs):
        raise transactions.TransactionError("injected-crash")

    monkeypatch.setattr(transactions, "_journal_append", fail_journal)
    with pytest.raises(transactions.TransactionError, match="injected-crash"):
        store.approve_exact(
            descriptor["transactionId"],
            envelope["planHash"],
            "owner-" + "e" * 16,
            APPROVED_AT,
            NOW,
        )
    monkeypatch.setattr(transactions, "_journal_append", original)

    repaired = store.approve_exact(
        descriptor["transactionId"],
        envelope["planHash"],
        "owner-" + "e" * 16,
        "2026-09-11T13:00:00Z",
        "2026-09-11T13:00:00Z",
    )
    assert repaired["state"] == "approved"
    loaded = store.read(descriptor["transactionId"])
    assert loaded["sequence"] == 3
    assert loaded["approval"]["approvedAt"] == APPROVED_AT
    assert loaded["journal"][-1]["timestamp"] == APPROVED_AT


def test_exact_owner_approval_serializes_concurrent_calls(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    results = []

    def approve():
        results.append(
            store.approve_exact(
                descriptor["transactionId"],
                envelope["planHash"],
                "owner-" + "f" * 16,
                APPROVED_AT,
                NOW,
            )
        )

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(result["noop"] for result in results) == [False, True]


def test_configuration_intent_and_record_are_immutable_value_safe_and_required(
    tmp_path,
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_configured_envelope()
    descriptor = create(store, envelope)
    transaction_id = descriptor["transactionId"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]

    with pytest.raises(transactions.ApprovalError) as caught:
        store.approve_exact(
            transaction_id,
            envelope["planHash"],
            "owner-" + "a" * 16,
            APPROVED_AT,
            NOW,
        )
    assert caught.value.code == "missing-configuration"

    values = {"ENDPOINT": "https://example.invalid/v1", "OFFSET": -1}
    intent = store.begin_configuration_exact(
        transaction_id,
        envelope["planHash"],
        schema_hash,
        "2" * 64,
        values,
        ["TOKEN"],
        [],
        CREATED_AT,
        NOW,
    )
    transaction_dir = store._tx_dir / transaction_id
    intent_path = transaction_dir / "configuration-intent.json"
    intent_bytes = intent_path.read_bytes()
    assert intent["duplicate"] is False
    assert intent["values"]["OFFSET"] == -1
    assert b"secretReference" not in intent_bytes
    assert b"secretValues" not in intent_bytes
    assert "private-do-not-write" not in intent_bytes.decode()
    assert (intent_path.stat().st_mode & 0o777) == 0o600

    reference = "secret-v1-" + "3" * 48
    record = store.finish_configuration_exact(
        transaction_id,
        envelope["planHash"],
        "2" * 64,
        reference,
    )
    record_path = transaction_dir / "configuration.json"
    record_bytes = record_path.read_bytes()
    assert record["duplicate"] is False
    assert record["secretReference"] == reference
    assert b"secretValues" not in record_bytes
    assert (record_path.stat().st_mode & 0o777) == 0o600
    assert store.read(transaction_id)["configuration"]["values"] == values

    approved = store.approve_exact(
        transaction_id,
        envelope["planHash"],
        "owner-" + "a" * 16,
        APPROVED_AT,
        NOW,
    )
    assert approved["state"] == "approved"


def test_configuration_exact_retry_and_conflict_do_not_replace_intent(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_configured_envelope()
    descriptor = create(store, envelope)
    transaction_id = descriptor["transactionId"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    arguments = (
        transaction_id,
        envelope["planHash"],
        schema_hash,
        "4" * 64,
        {"ENDPOINT": "https://example.invalid/v1", "OFFSET": -1},
        ["TOKEN"],
        [],
        CREATED_AT,
        NOW,
    )
    first = store.begin_configuration_exact(*arguments)
    replay = store.begin_configuration_exact(*arguments)
    assert first["duplicate"] is False
    assert replay["duplicate"] is True

    changed = list(arguments)
    changed[4] = {"ENDPOINT": "https://other.invalid/v1", "OFFSET": -1}
    with pytest.raises(transactions.IdempotencyConflict) as caught:
        store.begin_configuration_exact(*changed)
    assert caught.value.code == "configuration-conflict"
    assert store.read(transaction_id)["configuration"] is None


def test_configuration_intent_serializes_competing_store_instances(tmp_path):
    root = tmp_path / "transactions"
    first_store = transactions.TransactionStore(root)
    second_store = transactions.TransactionStore(root)
    envelope = build_configured_envelope()
    descriptor = create(first_store, envelope)
    transaction_id = descriptor["transactionId"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    results = []
    errors = []

    def reserve(store, suffix: str) -> None:
        try:
            results.append(
                store.begin_configuration_exact(
                    transaction_id,
                    envelope["planHash"],
                    schema_hash,
                    suffix * 64,
                    {
                        "ENDPOINT": f"https://{suffix}.example.invalid/v1",
                        "OFFSET": -1,
                    },
                    ["TOKEN"],
                    [],
                    CREATED_AT,
                    NOW,
                )
            )
        except transactions.TransactionError as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=reserve, args=(first_store, "5")),
        threading.Thread(target=reserve, args=(second_store, "6")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0].code == "configuration-conflict"


def test_configuration_record_must_match_its_durable_intent(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_configured_envelope()
    descriptor = create(store, envelope)
    transaction_id = descriptor["transactionId"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    store.begin_configuration_exact(
        transaction_id,
        envelope["planHash"],
        schema_hash,
        "7" * 64,
        {"ENDPOINT": "https://example.invalid/v1", "OFFSET": -1},
        ["TOKEN"],
        [],
        CREATED_AT,
        NOW,
    )
    store.finish_configuration_exact(
        transaction_id,
        envelope["planHash"],
        "7" * 64,
        "secret-v1-" + "8" * 48,
    )
    record_path = store._tx_dir / transaction_id / "configuration.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["values"]["OFFSET"] = -2
    record_path.write_bytes(transactions.canonical_json_bytes(record))

    with pytest.raises(transactions.IntegrityError) as caught:
        store.read(transaction_id)
    assert caught.value.code == "configuration-intent-mismatch"


@pytest.mark.parametrize(
    ("values", "secret_keys", "default_keys", "code"),
    [
        ([], ["TOKEN"], [], "invalid-configuration-values"),
        ({"OFFSET": -1}, "TOKEN", [], "invalid-configuration-keys"),
        ({"OFFSET": -1}, ["TOKEN", "TOKEN"], [], "invalid-configuration-keys"),
    ],
)
def test_configuration_reservation_rejects_malformed_direct_inputs(
    tmp_path, values, secret_keys, default_keys, code
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_configured_envelope()
    descriptor = create(store, envelope)
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    with pytest.raises(transactions.ValidationRejected) as caught:
        store.begin_configuration_exact(
            descriptor["transactionId"],
            envelope["planHash"],
            schema_hash,
            "9" * 64,
            values,
            secret_keys,
            default_keys,
            CREATED_AT,
            NOW,
        )
    assert caught.value.code == code


def test_finalization_receipt_requires_a_committed_transaction(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    loaded = store.read(descriptor["transactionId"])

    with pytest.raises(transactions.TransitionError) as caught:
        store.record_finalization(
            descriptor["transactionId"], finalization_receipt(loaded)
        )
    assert caught.value.code == "transaction-not-committed"


def test_finalization_receipt_is_bound_immutable_and_idempotent(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    committed = advance_to_committed(store, descriptor, envelope)
    receipt = finalization_receipt(committed)

    first = store.record_finalization(descriptor["transactionId"], receipt)
    second = store.record_finalization(
        descriptor["transactionId"],
        finalization_receipt(committed, recordedAt="2026-09-11T12:03:00Z"),
    )

    assert first == {**receipt, "duplicate": False}
    assert second == {**receipt, "duplicate": True}
    assert store.read(descriptor["transactionId"])["finalization"] == receipt
    path = store._tx_dir / descriptor["transactionId"] / "finalization.json"
    assert path.read_bytes() == transactions.canonical_json_bytes(receipt)

    conflicting = finalization_receipt(committed, lockfileHash="d" * 64)
    with pytest.raises(transactions.IdempotencyConflict) as caught:
        store.record_finalization(descriptor["transactionId"], conflicting)
    assert caught.value.code == "finalization-conflict"


def test_finalization_receipt_serializes_competing_store_instances(tmp_path):
    root = tmp_path / "transactions"
    first_store = transactions.TransactionStore(root)
    second_store = transactions.TransactionStore(root)
    envelope = build_envelope()
    descriptor = create(first_store, envelope)
    committed = advance_to_committed(first_store, descriptor, envelope)
    receipt = finalization_receipt(committed)
    results = []
    errors = []

    def record(store):
        try:
            results.append(
                store.record_finalization(descriptor["transactionId"], receipt)
            )
        except transactions.TransactionError as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=record, args=(first_store,)),
        threading.Thread(target=record, args=(second_store,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert first_store.read(descriptor["transactionId"])["finalization"] == receipt


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"transactionId": "txn-" + "0" * 24}, "finalization-binding-mismatch"),
        ({"planHash": "0" * 64}, "finalization-binding-mismatch"),
        ({"sequence": 0}, "finalization-binding-mismatch"),
        ({"lockfileHash": "secret-value"}, "invalid-finalization-digest"),
        ({"recordedAt": "2020-01-01T00:00:00Z"}, "finalization-time-order"),
    ],
)
def test_finalization_receipt_rejects_unbound_or_invalid_fields(
    tmp_path, changes, code
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    committed = advance_to_committed(store, descriptor, envelope)

    with pytest.raises(transactions.TransactionError) as caught:
        store.record_finalization(
            descriptor["transactionId"],
            finalization_receipt(committed, **changes),
        )
    assert caught.value.code == code


def test_finalization_receipt_tampering_fails_closed(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    committed = advance_to_committed(store, descriptor, envelope)
    receipt = finalization_receipt(committed)
    store.record_finalization(descriptor["transactionId"], receipt)

    path = store._tx_dir / descriptor["transactionId"] / "finalization.json"
    tampered = dict(receipt)
    tampered["secretValue"] = "must-not-be-accepted"
    path.write_bytes(transactions.canonical_json_bytes(tampered))

    with pytest.raises(transactions.IntegrityError) as caught:
        store.read(descriptor["transactionId"])
    assert caught.value.code == "finalization-keys"


def _write_private(path, value):
    """Write a canonical JSON file with store-qualified 0600 permissions."""
    path.write_bytes(transactions.canonical_json_bytes(value))
    os.chmod(path, 0o600)
    os.chmod(path.parent, 0o700)


def _approval_record_v1() -> dict:
    return {
        "actor": "owner-42",
        "approvedAt": APPROVED_AT,
        "approvedBy": "owner-42",
        "catalogRevision": CATALOG_REVISION,
        "idempotencyKey": IDEMPOTENCY_KEY,
        "observedStateRevision": STATE_REVISION,
        "planHash": "e" * 64,
        "policyRevision": POLICY_REVISION,
        "transactionId": "txn-" + "1" * 24,
        "validUntil": VALID_UNTIL,
    }


def test_decode_canonical_object_accepts_exact_v1_or_v2_key_sets(tmp_path):
    path = tmp_path / "approval.json"
    v1 = _approval_record_v1()
    v2 = {
        **v1,
        "configurationHash": "b" * 64,
        "configurationSchemaHash": "c" * 64,
        "privateConfigurationDigest": "d" * 64,
    }

    _write_private(path, v1)
    decoded = transactions._decode_canonical_object(
        path, (transactions.APPROVAL_KEYS, transactions.APPROVAL_V2_KEYS),
        "approval",
    )
    assert decoded == v1

    _write_private(path, v2)
    decoded = transactions._decode_canonical_object(
        path, (transactions.APPROVAL_KEYS, transactions.APPROVAL_V2_KEYS),
        "approval",
    )
    assert decoded == v2


def test_decode_canonical_object_tuple_rejects_extra_missing_and_mixed(tmp_path):
    path = tmp_path / "approval.json"
    base = _approval_record_v1()
    full_v2 = {
        **base,
        "configurationHash": "b" * 64,
        "configurationSchemaHash": "c" * 64,
        "privateConfigurationDigest": "d" * 64,
    }
    # Extra v2 field over v1: matches neither set.
    extra = {**base, "configurationHash": "b" * 64}
    # Missing v2 field over v2: matches neither set.
    missing = {k: v for k, v in full_v2.items() if k != "privateConfigurationDigest"}
    # All ten v1 fields plus a dropped v1 field: matches neither set.
    mixed = {**full_v2}
    del mixed["actor"]

    for candidate in (extra, missing, mixed):
        _write_private(path, candidate)
        with pytest.raises(transactions.IntegrityError) as caught:
            transactions._decode_canonical_object(
                path,
                (transactions.APPROVAL_KEYS, transactions.APPROVAL_V2_KEYS),
                "approval",
            )
        assert caught.value.code == "approval-keys"

    # Union keys are never accepted implicitly: passing the bare union set
    # must also reject a strict v1 record.
    _write_private(path, base)
    with pytest.raises(transactions.IntegrityError) as caught:
        transactions._decode_canonical_object(
            path, transactions.APPROVAL_V2_KEYS, "approval"
        )
    assert caught.value.code == "approval-keys"

    # An empty alternation matches no key set: fail closed, never permissive.
    with pytest.raises(transactions.IntegrityError) as caught:
        transactions._decode_canonical_object(path, (), "approval")
    assert caught.value.code == "approval-keys"


def _replace_test_approval_with_v2(store, transaction_id, envelope):
    """Fabricate a v2 approval only in a temporary, already-approved test store."""
    loaded = store.read(transaction_id)
    record = loaded["configuration"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    safe_hash = transactions.configuration_attestation_hash(
        transaction_id,
        envelope["planHash"],
        schema_hash,
        record is not None,
        record["values"] if record else {},
        record["presentConfigKeys"] if record else [],
        record["presentSecretKeys"] if record else [],
        record["appliedDefaultKeys"] if record else [],
    )
    approval = {
        **loaded["approval"],
        "configurationHash": safe_hash,
        "configurationSchemaHash": schema_hash,
        "privateConfigurationDigest": transactions.configuration_record_digest(
            record, schema_hash
        ),
    }
    path = store._tx_dir / transaction_id / "approval.json"
    _write_private(path, approval)
    return approval


def test_attested_v2_approval_reads_without_exposing_secret_reference(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    transaction_id = descriptor["transactionId"]
    store.approve_exact(
        transaction_id, envelope["planHash"], "owner-" + "a" * 16,
        APPROVED_AT, NOW,
    )
    legacy = store.read(transaction_id)["approval"]
    assert set(legacy) == transactions.APPROVAL_KEYS

    v2 = _replace_test_approval_with_v2(store, transaction_id, envelope)
    assert store.read(transaction_id)["approval"] == v2
    raw = (store._tx_dir / transaction_id / "approval.json").read_bytes()
    assert b"secretReference" not in raw
    assert b"secretValues" not in raw


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("configurationSchemaHash", "approval-v2-schema-mismatch"),
        ("configurationHash", "approval-v2-configuration-mismatch"),
        ("privateConfigurationDigest", "approval-v2-custody-mismatch"),
    ],
)
def test_attested_v2_approval_hash_tampering_fails_closed(tmp_path, field, code):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    transaction_id = descriptor["transactionId"]
    store.approve_exact(
        transaction_id, envelope["planHash"], "owner-" + "a" * 16,
        APPROVED_AT, NOW,
    )
    v2 = _replace_test_approval_with_v2(store, transaction_id, envelope)
    bad = {**v2, field: "0" * 64}
    _write_private(store._tx_dir / transaction_id / "approval.json", bad)
    with pytest.raises(transactions.IntegrityError) as caught:
        store.read(transaction_id)
    assert caught.value.code == code


def test_attested_v2_approval_rejects_malformed_digest(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope)
    transaction_id = descriptor["transactionId"]
    store.approve_exact(
        transaction_id, envelope["planHash"], "owner-" + "a" * 16,
        APPROVED_AT, NOW,
    )
    v2 = _replace_test_approval_with_v2(store, transaction_id, envelope)
    _write_private(
        store._tx_dir / transaction_id / "approval.json",
        {**v2, "privateConfigurationDigest": "not-a-hash"},
    )
    with pytest.raises(transactions.IntegrityError) as caught:
        store.read(transaction_id)
    assert caught.value.code == "approval-v2-digest-invalid"


def test_attested_v2_approval_detects_swapped_opaque_secret_reference(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_configured_envelope()
    descriptor = create(store, envelope)
    transaction_id = descriptor["transactionId"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    store.begin_configuration_exact(
        transaction_id, envelope["planHash"], schema_hash, "2" * 64,
        {"ENDPOINT": "https://example.invalid/v1", "OFFSET": -1},
        ["TOKEN"], [], CREATED_AT, NOW,
    )
    store.finish_configuration_exact(
        transaction_id, envelope["planHash"], "2" * 64,
        "secret-v1-" + "3" * 48,
    )
    store.approve_exact(
        transaction_id, envelope["planHash"], "owner-" + "a" * 16,
        APPROVED_AT, NOW,
    )
    _replace_test_approval_with_v2(store, transaction_id, envelope)
    assert store.read(transaction_id)["state"] == "approved"

    path = store._tx_dir / transaction_id / "configuration.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["secretReference"] = "secret-v1-" + "4" * 48
    _write_private(path, record)
    with pytest.raises(transactions.IntegrityError) as caught:
        store.read(transaction_id)
    assert caught.value.code == "approval-v2-custody-mismatch"


def _reviewed_configuration_hash(store, transaction_id, envelope):
    record = store.read(transaction_id)["configuration"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    safe_hash = transactions.configuration_attestation_hash(
        transaction_id,
        envelope["planHash"],
        schema_hash,
        record is not None,
        record["values"] if record else {},
        record["presentConfigKeys"] if record else [],
        record["presentSecretKeys"] if record else [],
        record["appliedDefaultKeys"] if record else [],
    )
    return schema_hash, safe_hash


def test_configured_exact_owner_approval_writes_durable_v2_record(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    descriptor = create(store, envelope, actor="assistant-manager")
    transaction_id = descriptor["transactionId"]
    schema_hash, safe_hash = _reviewed_configuration_hash(
        store, transaction_id, envelope
    )
    owner = "owner-" + "a" * 16

    result = store.approve_configured_exact(
        transaction_id, envelope["planHash"], safe_hash, schema_hash,
        owner, APPROVED_AT, NOW,
    )
    assert result["state"] == "approved"
    assert result["sequence"] == 3
    assert result["noop"] is False
    loaded = store.read(transaction_id)
    approval = loaded["approval"]
    assert set(approval) == transactions.APPROVAL_V2_KEYS
    assert approval["actor"] == "assistant-manager"
    assert approval["approvedBy"] == owner
    assert approval["configurationHash"] == safe_hash
    assert approval["configurationSchemaHash"] == schema_hash
    assert approval["privateConfigurationDigest"] == (
        transactions.configuration_record_digest(None, schema_hash)
    )
    path = store._tx_dir / transaction_id / "approval.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert b"secretReference" not in path.read_bytes()

    replay = store.approve_configured_exact(
        transaction_id, envelope["planHash"], safe_hash, schema_hash,
        owner, "2026-09-11T13:00:00Z", "2026-09-11T13:00:00Z",
    )
    assert replay["noop"] is True
    assert store.read(transaction_id)["approval"] == approval


@pytest.mark.parametrize(
    ("which", "replacement"),
    [
        ("plan", "0" * 64),
        ("configuration", "0" * 64),
        ("schema", "0" * 64),
        ("owner", "assistant-manager"),
    ],
)
def test_configured_exact_owner_approval_rejects_drift_before_write(
    tmp_path, which, replacement
):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    transaction_id = create(store, envelope)["transactionId"]
    schema_hash, safe_hash = _reviewed_configuration_hash(
        store, transaction_id, envelope
    )
    supplied = {
        "plan": envelope["planHash"],
        "configuration": safe_hash,
        "schema": schema_hash,
        "owner": "owner-" + "a" * 16,
    }
    supplied[which] = replacement

    with pytest.raises(transactions.ApprovalError):
        store.approve_configured_exact(
            transaction_id, supplied["plan"], supplied["configuration"],
            supplied["schema"], supplied["owner"], APPROVED_AT, NOW,
        )
    assert store.read(transaction_id)["state"] == "awaiting_approval"
    assert not (store._tx_dir / transaction_id / "approval.json").exists()


def test_configured_exact_owner_approval_rejects_expiry_before_write(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    transaction_id = create(store, envelope)["transactionId"]
    schema_hash, safe_hash = _reviewed_configuration_hash(
        store, transaction_id, envelope
    )

    with pytest.raises(transactions.ApprovalError) as caught:
        store.approve_configured_exact(
            transaction_id, envelope["planHash"], safe_hash, schema_hash,
            "owner-" + "a" * 16, VALID_UNTIL, VALID_UNTIL,
        )
    assert caught.value.code == "approval-expired"
    assert not (store._tx_dir / transaction_id / "approval.json").exists()


def test_configured_exact_owner_approval_never_upgrades_legacy_v1(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    transaction_id = create(store, envelope)["transactionId"]
    schema_hash, safe_hash = _reviewed_configuration_hash(
        store, transaction_id, envelope
    )
    owner = "owner-" + "a" * 16
    store.approve_exact(transaction_id, envelope["planHash"], owner, APPROVED_AT, NOW)

    with pytest.raises(transactions.ApprovalError):
        store.approve_configured_exact(
            transaction_id, envelope["planHash"], safe_hash, schema_hash,
            owner, APPROVED_AT, NOW,
        )
    assert set(store.read(transaction_id)["approval"]) == transactions.APPROVAL_KEYS


def test_configured_exact_owner_approval_requires_saved_configuration(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_configured_envelope()
    transaction_id = create(store, envelope)["transactionId"]
    schema_hash, safe_hash = _reviewed_configuration_hash(
        store, transaction_id, envelope
    )

    with pytest.raises(transactions.ApprovalError) as caught:
        store.approve_configured_exact(
            transaction_id, envelope["planHash"], safe_hash, schema_hash,
            "owner-" + "a" * 16, APPROVED_AT, NOW,
        )
    assert caught.value.code == "missing-configuration"
    assert not (store._tx_dir / transaction_id / "approval.json").exists()


def test_configured_exact_owner_approval_replays_unjournaled_v2(tmp_path, monkeypatch):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    transaction_id = create(store, envelope)["transactionId"]
    schema_hash, safe_hash = _reviewed_configuration_hash(
        store, transaction_id, envelope
    )
    owner = "owner-" + "a" * 16
    original_append = transactions._journal_append

    def fail_append(*args, **kwargs):
        raise transactions.TransactionError("injected-crash")

    monkeypatch.setattr(transactions, "_journal_append", fail_append)
    with pytest.raises(transactions.TransactionError, match="injected-crash"):
        store.approve_configured_exact(
            transaction_id, envelope["planHash"], safe_hash, schema_hash,
            owner, APPROVED_AT, NOW,
        )
    monkeypatch.setattr(transactions, "_journal_append", original_append)

    path = store._tx_dir / transaction_id / "approval.json"
    assert path.exists()
    with pytest.raises(transactions.IntegrityError, match="unexpected-approval"):
        store.read(transaction_id)
    with pytest.raises(transactions.ApprovalError) as caught:
        store.approve_configured_exact(
            transaction_id, envelope["planHash"], safe_hash, schema_hash,
            "owner-" + "b" * 16,
            "2026-09-11T13:00:00Z", "2026-09-11T13:00:00Z",
        )
    assert caught.value.code == "approval-conflict"
    assert path.exists()
    recovered = store.approve_configured_exact(
        transaction_id, envelope["planHash"], safe_hash, schema_hash,
        owner, "2026-09-11T13:00:00Z", "2026-09-11T13:00:00Z",
    )
    assert recovered["state"] == "approved"
    assert store.read(transaction_id)["approval"]["approvedAt"] == APPROVED_AT


def test_configured_exact_owner_approval_binds_opaque_custody(tmp_path):
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_configured_envelope()
    transaction_id = create(store, envelope)["transactionId"]
    schema_hash = configuration.configuration_schema(
        envelope, expected_plan_hash=envelope["planHash"]
    )["schemaHash"]
    store.begin_configuration_exact(
        transaction_id, envelope["planHash"], schema_hash, "2" * 64,
        {"ENDPOINT": "https://example.invalid/v1", "OFFSET": -1},
        ["TOKEN"], [], CREATED_AT, NOW,
    )
    store.finish_configuration_exact(
        transaction_id, envelope["planHash"], "2" * 64,
        "secret-v1-" + "3" * 48,
    )
    schema_hash, safe_hash = _reviewed_configuration_hash(
        store, transaction_id, envelope
    )
    owner = "owner-" + "a" * 16
    store.approve_configured_exact(
        transaction_id, envelope["planHash"], safe_hash, schema_hash,
        owner, APPROVED_AT, NOW,
    )
    record = store.read(transaction_id)["configuration"]
    approval = store.read(transaction_id)["approval"]
    assert approval["privateConfigurationDigest"] == (
        transactions.configuration_record_digest(record, schema_hash)
    )
    raw = (store._tx_dir / transaction_id / "approval.json").read_bytes()
    assert b"secret-v1-" not in raw
    assert b"https://example.invalid/v1" not in raw

    path = store._tx_dir / transaction_id / "configuration.json"
    altered = json.loads(path.read_text(encoding="utf-8"))
    altered["secretReference"] = "secret-v1-" + "4" * 48
    _write_private(path, altered)
    with pytest.raises(transactions.IntegrityError) as caught:
        store.read(transaction_id)
    assert caught.value.code == "approval-v2-custody-mismatch"


def test_owner_browser_route_uses_real_attested_store_and_redacts_status(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.extension_transactions as transaction_api
    import security
    import session_signer
    from extension_transaction_configuration import TransactionConfigurationManager
    from extension_transaction_runtime import TransactionRuntime

    monkeypatch.setenv("ODS_ASSISTANT_TRANSACTIONS_ENABLED", "true")
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "transaction-test-key")
    session_signer._set_secret_for_tests("real-v2-approval-route-test-secret")
    store = transactions.TransactionStore(tmp_path / "transactions")
    envelope = build_envelope()
    transaction_id = create(store, envelope)["transactionId"]
    manager = TransactionConfigurationManager(store, None, lambda: NOW)
    reviewed = manager.require_ready(transaction_id, envelope["planHash"])
    cookie = session_signer.issue_scoped("owner", ttl_seconds=60)
    owner_identity = session_signer.owner_approval_identity(cookie)
    app = FastAPI()
    app.include_router(transaction_api.router)
    app.state.extension_transaction_runtime = TransactionRuntime(
        store=store,
        catalog=lambda: ([], CATALOG_REVISION),
        observed_state=lambda: HOST_STATE,
        policy=lambda: POLICY,
        clock=lambda: NOW,
        configuration=manager,
    )

    with TestClient(app) as client:
        client.cookies.set("ods-session", cookie)
        approved = client.post(
            f"/api/extensions/transactions/{transaction_id}/approval",
            json={
                "planHash": envelope["planHash"],
                "configurationHash": reviewed["configurationHash"],
            },
        )
        assert approved.status_code == 200
        assert approved.json()["state"] == "approved"
        assert store.read(transaction_id)["approval"]["approvedBy"] == owner_identity
        assert set(store.read(transaction_id)["approval"]) == (
            transactions.APPROVAL_V2_KEYS
        )

        status = client.get(
            f"/api/extensions/transactions/{transaction_id}",
            headers={"Authorization": "Bearer transaction-test-key"},
        )
        assert status.status_code == 200
        assert status.json()["approval"] == {
            "approved": True,
            "approvedAt": NOW,
            "approvedBy": owner_identity,
        }
        assert "privateConfigurationDigest" not in status.text
        assert "configurationHash" not in status.text
