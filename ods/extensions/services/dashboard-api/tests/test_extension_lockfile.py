from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import assistant_first_planner as planner
import extension_lockfile as lockfile


CATALOG_REVISION = "a" * 64
TRANSACTION_ID = "txn-" + "c" * 24
NEXT_TRANSACTION_ID = "txn-" + "d" * 24
SECRET_SENTINEL = "never-store-this-secret"

HOST_STATE = {
    "odsVersion": "2.6.0",
    "platform": "linux",
    "architecture": "amd64",
    "containerRuntime": "docker",
    "gpuBackend": "cpu",
    "driverVersion": None,
    "available": {
        "diskBytes": 1_000_000,
        "ramBytes": 2_000_000,
        "vramBytes": 0,
        "cpuMillicores": 4_000,
        "gpuCount": 0,
    },
    "occupiedPorts": [],
    "reservedResources": [],
    "installedServices": [],
}
PLAN_STATE_REVISION = hashlib.sha256(
    planner.canonical_json_bytes(HOST_STATE)
).hexdigest()
POST_COMMIT_HOST_STATE = {
    **HOST_STATE,
    "installedServices": [
        {
            "id": "app",
            "version": "1.2.3",
            "definitionSha256": "sha256:" + "e" * 64,
            "status": "enabled",
        },
        {
            "id": "db",
            "version": "1.2.3",
            "definitionSha256": "sha256:" + "d" * 64,
            "status": "enabled",
        },
    ],
}


def definition(
    service_id: str,
    *,
    depends_on: list[str] | None = None,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    digest: str = "e",
) -> dict:
    return {
        "id": service_id,
        "serviceType": "docker",
        "manifestSchemaVersion": "ods.services.v2",
        "version": "1.2.3",
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": "2.0.0", "maximum": None},
        "definitionSha256": "sha256:" + digest * 64,
        "composeSha256": "sha256:" + "f" * 64,
        "dependsOn": depends_on or [],
        "provides": provides or [],
        "requires": requires or [],
        "conflicts": [],
        "requirements": {},
        "estimates": {},
        "configuration": [
            {
                "key": "APP_ENDPOINT",
                "type": "url",
                "required": False,
                "secret": False,
                "source": "user",
                "restartBehavior": "service",
            },
            {
                "key": "APP_TOKEN",
                "type": "string",
                "required": False,
                "secret": True,
                "source": "user",
                "restartBehavior": "service",
            },
        ]
        if service_id == "app"
        else [],
        "artifacts": {
            "images": [
                {
                    "reference": f"example/{service_id}:1.2.3",
                    "digest": "sha256:" + digest * 64,
                    "downloadBytes": 100,
                }
            ],
            "builds": [],
        },
        "resources": {},
        "lifecycle": {"rollback": "definition"},
        "data": [],
        "trust": {},
        "support": {},
    }


def committed_transaction(
    *,
    reverse_definitions: bool = False,
    transaction_id: str = TRANSACTION_ID,
    sequence: int = 10,
) -> dict:
    definitions = [
        definition("db", provides=["database@1"], digest="d"),
        definition(
            "app",
            depends_on=["db"],
            requires=["database@1"],
            digest="e",
        ),
    ]
    if reverse_definitions:
        definitions.reverse()
    plan = {
        "schema": "ods.assistant-first.plan.v1",
        "requestedAction": "ensure",
        "catalogRevision": CATALOG_REVISION,
        "observedStateRevision": PLAN_STATE_REVISION,
        "selectedServices": ["db", "app"],
        "operations": [
            {"serviceId": "db", "action": "install"},
            {"serviceId": "app", "action": "install"},
        ],
        "providerBindings": [
            {"capability": "database@1", "serviceId": "db"}
        ],
        "definitions": definitions,
    }
    plan_hash = hashlib.sha256(planner.canonical_json_bytes(plan)).hexdigest()
    envelope = {
        "schema": "ods.assistant-first.plan-envelope.v1",
        "planId": f"plan-{plan_hash[:24]}",
        "catalogRevision": CATALOG_REVISION,
        "observedStateRevision": PLAN_STATE_REVISION,
        "policyRevision": "9" * 64,
        "planHash": plan_hash,
        "plan": plan,
    }
    return {
        "transactionId": transaction_id,
        "state": "committed",
        "sequence": sequence,
        "envelope": envelope,
        "configuration": {
            "schema": "ods.assistant-first.transaction-configuration.v1",
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "schemaHash": "8" * 64,
            "presentConfigKeys": ["APP_ENDPOINT"],
            "presentSecretKeys": ["APP_TOKEN"],
            "secretReference": "secret-v1-" + "7" * 48,
            "values": {"APP_ENDPOINT": "https://example.invalid"},
            "privateTestValue": SECRET_SENTINEL,
        },
    }


def build(**overrides) -> dict:
    previous = overrides.get("previous_lockfile")
    values = {
        "transaction": committed_transaction(
            transaction_id=NEXT_TRANSACTION_ID if previous is not None else TRANSACTION_ID
        ),
        "observed_state": POST_COMMIT_HOST_STATE,
        "runtime_mode": "assistant-first",
        "backup_reference": "backup-v1-20260912T160000Z",
        "prior_lockfile_hash": None,
        "previous_lockfile": None,
    }
    values.update(overrides)
    return lockfile.build_lockfile(**values)


def test_builds_complete_canonical_lockfile_without_secret_values() -> None:
    envelope = build()
    document = envelope["lockfile"]

    assert envelope["schema"] == "ods.extensions.lockfile-envelope.v1"
    assert envelope["lockfileHash"] == hashlib.sha256(
        lockfile.canonical_lockfile_bytes(document)
    ).hexdigest()
    assert document["schema"] == "ods.extensions.lockfile.v1"
    assert document["odsVersion"] == "2.6.0"
    assert document["catalogRevision"] == CATALOG_REVISION
    assert document["platform"] == "linux"
    assert document["architecture"] == "amd64"
    assert document["containerRuntime"] == "docker"
    assert document["runtimeMode"] == "assistant-first"
    assert document["postCommitObservedStateRevision"] == hashlib.sha256(
        planner.canonical_json_bytes(POST_COMMIT_HOST_STATE)
    ).hexdigest()
    assert document["lastCommittedTransaction"] == {
        "transactionId": TRANSACTION_ID,
        "planHash": committed_transaction()["envelope"]["planHash"],
        "sequence": 10,
        "plannedObservedStateRevision": PLAN_STATE_REVISION,
    }
    assert document["priorLockfileHash"] is None
    assert document["backupReference"] == "backup-v1-20260912T160000Z"

    assert [item["id"] for item in document["extensions"]] == ["app", "db"]
    app = document["extensions"][0]
    assert app["desiredState"] == "enabled"
    assert app["definitionHashes"] == {
        "manifestSha256": "sha256:" + "e" * 64,
        "composeSha256": "sha256:" + "f" * 64,
    }
    assert app["imageDigests"] == ["sha256:" + "e" * 64]
    assert app["dependencyEdges"] == [
        {"kind": "capability", "capability": "database@1", "target": "db"},
        {"kind": "service", "target": "db"},
    ]
    assert len(app["configuration"]["schemaSha256"]) == 64
    assert app["configuration"] == {
        "schemaSha256": app["configuration"]["schemaSha256"],
        "presentConfigKeys": ["APP_ENDPOINT"],
        "presentSecretKeys": ["APP_TOKEN"],
        "secretReference": "secret-v1-" + "7" * 48,
    }
    assert document["extensions"][1]["configuration"]["secretReference"] is None

    encoded = lockfile.canonical_lockfile_bytes(envelope)
    assert SECRET_SENTINEL.encode() not in encoded
    assert b"https://example.invalid" not in encoded
    assert b'"values"' not in encoded


def test_extension_entries_are_canonical_but_transaction_hash_remains_bound() -> None:
    first = build()
    second = build(transaction=committed_transaction(reverse_definitions=True))
    assert first["lockfile"]["extensions"] == second["lockfile"]["extensions"]
    assert (
        first["lockfile"]["lastCommittedTransaction"]["planHash"]
        != second["lockfile"]["lastCommittedTransaction"]["planHash"]
    )
    assert first["lockfileHash"] != second["lockfileHash"]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda value: value.update(state="verifying"), "transaction-not-committed"),
        (
            lambda value: value["envelope"].update(planHash="0" * 64),
            "plan-hash-mismatch",
        ),
        (
            lambda value: value["configuration"].update(planHash="0" * 64),
            "configuration-plan-mismatch",
        ),
        (
            lambda value: value["configuration"].update(secretReference=None),
            "configuration-secret-reference",
        ),
    ],
)
def test_rejects_uncommitted_or_mismatched_transaction_evidence(mutation, code) -> None:
    transaction = committed_transaction()
    mutation(transaction)
    with pytest.raises(lockfile.ExtensionLockfileError, match=code):
        build(transaction=transaction)


def test_rejects_unapproved_runtime_and_backup_path() -> None:
    with pytest.raises(lockfile.ExtensionLockfileError, match="invalid-runtime-mode"):
        build(runtime_mode="experimental")
    with pytest.raises(lockfile.ExtensionLockfileError, match="invalid-backup-reference"):
        build(backup_reference="../../private")


def test_rejects_post_commit_observation_that_does_not_match_selected_state() -> None:
    mismatched = copy.deepcopy(POST_COMMIT_HOST_STATE)
    mismatched["installedServices"][0]["version"] = "9.9.9"

    with pytest.raises(
        lockfile.ExtensionLockfileError, match="post-commit-observation-mismatch"
    ):
        build(observed_state=mismatched)


def test_store_commits_atomically_and_enforces_hash_chain(tmp_path: Path) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    assert store.read() is None

    first = build()
    assert store.commit(first["lockfile"]) == first
    assert store.read() == first
    assert store.commit(copy.deepcopy(first["lockfile"])) == first

    second = build(previous_lockfile=first)
    assert store.commit(second["lockfile"]) == second
    assert store.read() == second

    stale = build(previous_lockfile=first)
    stale["lockfile"]["backupReference"] = "backup-v1-stale"
    with pytest.raises(lockfile.ExtensionLockfileError, match="prior-lockfile-mismatch"):
        store.commit(stale["lockfile"])
    assert store.read() == second

    raw = store.path.read_bytes()
    assert raw == lockfile.canonical_lockfile_bytes(second)
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600


def test_store_rejects_conflicting_replay_of_recorded_transaction(tmp_path: Path) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    first = store.commit(build()["lockfile"])
    replay = build(
        previous_lockfile=first,
        transaction=committed_transaction(transaction_id=TRANSACTION_ID),
    )

    with pytest.raises(lockfile.ExtensionLockfileError, match="transaction-already-recorded"):
        store.commit(replay["lockfile"])


def test_transaction_sequence_is_scoped_to_each_transaction(tmp_path: Path) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    first = store.commit(build()["lockfile"])
    second = build(
        previous_lockfile=first,
        transaction=committed_transaction(
            transaction_id=NEXT_TRANSACTION_ID,
            sequence=5,
        ),
    )

    assert store.commit(second["lockfile"])["lockfile"]["lastCommittedTransaction"] == {
        "transactionId": NEXT_TRANSACTION_ID,
        "planHash": second["lockfile"]["lastCommittedTransaction"]["planHash"],
        "sequence": 5,
        "plannedObservedStateRevision": PLAN_STATE_REVISION,
    }


def test_store_does_not_replace_prior_file_when_atomic_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    first = store.commit(build()["lockfile"])
    before = store.path.read_bytes()
    second = build(previous_lockfile=first)
    real_replace = os.replace

    def fail_replace(source, destination):
        if Path(destination) == store.path:
            raise OSError("injected swap failure")
        return real_replace(source, destination)

    monkeypatch.setattr(lockfile.os, "replace", fail_replace)
    with pytest.raises(lockfile.ExtensionLockfileError, match="lockfile-write-failed"):
        store.commit(second["lockfile"])

    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob(".extensions.lock.*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX-specific")
def test_store_reports_uncertain_durability_after_directory_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    first = store.commit(build()["lockfile"])
    second = build(previous_lockfile=first)
    real_fsync = os.fsync

    def fail_directory_fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected directory fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(lockfile.os, "fsync", fail_directory_fsync)
    with pytest.raises(
        lockfile.ExtensionLockfileError, match="lockfile-durability-uncertain"
    ):
        store.commit(second["lockfile"])

    monkeypatch.setattr(lockfile.os, "fsync", real_fsync)
    assert store.read() == second
    assert store.confirm_durable(second["lockfile"]) == second


def test_store_refuses_to_confirm_a_nonactive_lockfile(tmp_path: Path) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    first = store.commit(build()["lockfile"])
    second = build(previous_lockfile=first)

    with pytest.raises(
        lockfile.ExtensionLockfileError, match="lockfile-durability-mismatch"
    ):
        store.confirm_durable(second["lockfile"])


def test_store_rejects_corrupt_or_noncanonical_existing_file(tmp_path: Path) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    store.commit(build()["lockfile"])
    document = json.loads(store.path.read_text(encoding="utf-8"))
    store.path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    if os.name == "posix":
        os.chmod(store.path, 0o600)

    with pytest.raises(lockfile.ExtensionLockfileError, match="lockfile-noncanonical"):
        store.read()
    with pytest.raises(lockfile.ExtensionLockfileError, match="lockfile-noncanonical"):
        store.commit(build()["lockfile"])


def test_store_maps_json_parser_recursion_to_stable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    assert store.read() is None
    store.path.write_text("{}\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(store.path, 0o600)

    def fail_parser(*args, **kwargs):
        del args, kwargs
        raise RecursionError("injected parser recursion")

    monkeypatch.setattr(lockfile.json, "loads", fail_parser)

    with pytest.raises(lockfile.ExtensionLockfileError, match="lockfile-parse-error"):
        store.read()


def test_validation_rejects_extra_fields_and_secret_values() -> None:
    envelope = build()
    envelope["lockfile"]["extensions"][0]["configuration"][
        "secretValue"
    ] = SECRET_SENTINEL
    with pytest.raises(lockfile.ExtensionLockfileError, match="configuration-fields"):
        lockfile.validate_lockfile_envelope(envelope)


@pytest.mark.parametrize(
    ("path", "invalid_value", "code"),
    [
        (
            ("extensions", 0, "configuration", "presentConfigKeys"),
            [{}],
            "configuration-present-keys",
        ),
        (
            ("extensions", 0, "imageDigests"),
            [None],
            "invalid-image-digests",
        ),
        (
            ("extensions", 0, "dependencyEdges"),
            [None],
            "invalid-dependency-edge",
        ),
    ],
)
def test_validation_rejects_malformed_nested_members_with_stable_errors(
    path, invalid_value, code
) -> None:
    envelope = build()
    target = envelope["lockfile"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid_value

    with pytest.raises(lockfile.ExtensionLockfileError, match=code):
        lockfile.validate_lockfile_envelope(envelope)


def test_build_requires_previous_lockfile_when_linking_a_prior_hash() -> None:
    with pytest.raises(lockfile.ExtensionLockfileError, match="previous-lockfile-required"):
        build(prior_lockfile_hash="1" * 64)


def test_previous_lockfile_entries_are_preserved_and_linked() -> None:
    first = build()
    prior = copy.deepcopy(first)
    prior["lockfile"]["extensions"].append(
        {
            **copy.deepcopy(prior["lockfile"]["extensions"][1]),
            "id": "voice",
            "desiredState": "disabled",
        }
    )
    prior["lockfile"]["extensions"].sort(key=lambda item: item["id"])
    prior = lockfile.lockfile_envelope(prior["lockfile"])

    second = build(previous_lockfile=prior)
    by_id = {item["id"]: item for item in second["lockfile"]["extensions"]}
    assert by_id["voice"]["desiredState"] == "disabled"
    assert second["lockfile"]["priorLockfileHash"] == prior["lockfileHash"]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is privilege-gated")
def test_store_rejects_symlink_leaf(tmp_path: Path) -> None:
    root = tmp_path / "assistant-first"
    root.mkdir(mode=0o700)
    target = tmp_path / "outside.json"
    target.write_text("{}\n", encoding="utf-8")
    (root / "extensions.lock.json").symlink_to(target)
    store = lockfile.ExtensionLockfileStore(root)

    with pytest.raises(lockfile.ExtensionLockfileError, match="lockfile-symlink"):
        store.read()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is privilege-gated")
def test_store_rejects_symlink_root_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    store = lockfile.ExtensionLockfileStore(linked / "assistant-first")

    with pytest.raises(lockfile.ExtensionLockfileError, match="lockfile-root-symlink"):
        store.read()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are required")
def test_store_rejects_lock_file_with_unsafe_mode(tmp_path: Path) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    assert store.read() is None
    os.chmod(store._lock_path, 0o666)

    with pytest.raises(lockfile.ExtensionLockfileError, match="lockfile-lock-mode"):
        store.read()


def test_concurrent_compare_and_swap_allows_one_writer(tmp_path: Path) -> None:
    store = lockfile.ExtensionLockfileStore(tmp_path / "assistant-first")
    first = store.commit(build()["lockfile"])
    candidates = [
        build(previous_lockfile=first, backup_reference=f"backup-v1-race-{index}")
        for index in range(2)
    ]

    def commit(candidate):
        try:
            store.commit(candidate["lockfile"])
            return "committed"
        except lockfile.ExtensionLockfileError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(commit, candidates))

    assert sorted(outcomes) == ["committed", "prior-lockfile-mismatch"]
