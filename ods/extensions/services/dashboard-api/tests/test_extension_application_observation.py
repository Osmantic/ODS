"""Exhaustive tests for extension_application_observation.

Covers:
- Happy APPLIED for started-only and completed receipts
- ABSENT variants (no receipt, started-only, failed terminal)
- Every mismatch/partial/drift case
- Multi-container exactness
- Stopped/unhealthy still APPLIED
- Malformed/oversized/type-confused inputs
- Value leakage
- Tampered frozen dataclasses
- Deterministic output
- Narrow host observation import while the production executor remains None
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_application_identity as app_id  # noqa: E402, RUF100
import extension_application_observation as obs_mod  # noqa: E402, RUF100
import extension_lifecycle_plan as lifecycle_plan  # noqa: E402, RUF100
import extension_lifecycle_receipts as receipts_mod  # noqa: E402, RUF100
import extension_lifecycle_work as lifecycle_work  # noqa: E402, RUF100

# ---------------------------------------------------------------------------
# Constants / fixtures
# ---------------------------------------------------------------------------

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
DEFINITION_SHA = "sha256:" + "3" * 64
COMPOSE_SHA = "sha256:" + "4" * 64
SERVICE_ID = "documents"
VERSION = "1.2.3"
ACTION = "install"
CONFIG_SHA = "sha256:" + "e" * 64
OVERRIDE_SHA = "sha256:" + "f" * 64
CONTAINER_NAMES = ["documents-api", "documents-worker"]


def _definition(service_id: str, compose: str | None = COMPOSE_SHA) -> dict:
    return {
        "id": service_id,
        "serviceType": "docker",
        "manifestSchemaVersion": "ods.services.v2",
        "version": VERSION,
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": "2.0.0", "maximum": "3.0.0"},
        "definitionSha256": DEFINITION_SHA,
        "composeSha256": compose,
        "definitionSource": "library",
        "composeFile": "compose.yaml" if compose else None,
        "dependsOn": [],
        "provides": [],
        "requires": [],
        "conflicts": [],
        "requirements": {},
        "estimates": {},
        "configuration": [],
        "artifacts": {
            "images": [
                {
                    "reference": f"example.invalid/{service_id}:{VERSION}",
                    "digest": "sha256:" + "5" * 64,
                    "downloadBytes": 123,
                }
            ],
            "builds": [],
        },
        "resources": {},
        "lifecycle": {},
        "data": [],
        "trust": {},
        "support": {},
    }


def _transaction(
    state: str, definitions: list[dict], action: str = ACTION
) -> dict:
    return {
        "transactionId": TRANSACTION_ID,
        "state": state,
        "approval": {
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "approvedBy": "owner",
        },
        "envelope": {
            "planHash": PLAN_HASH,
            "plan": {
                "selectedServices": [d["id"] for d in definitions],
                "operations": [
                    {"serviceId": d["id"], "action": action} for d in definitions
                ],
                "definitions": definitions,
            },
        },
    }


def _command(operation_key: str, service_ids: list[str], payload: dict):
    unsigned = {
        "schema": lifecycle_work.REQUEST_SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": operation_key,
        "serviceIds": service_ids,
        "payload": payload,
    }
    request = {
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
    return lifecycle_work.parse_lifecycle_work_request(request)


def _bound_command(
    operation_key: str = f"apply:{SERVICE_ID}",
    service_ids: list[str] | None = None,
    payload: dict | None = None,
    compose: str | None = COMPOSE_SHA,
    action: str = ACTION,
    state: str = "applying",
) -> lifecycle_work.LifecycleWorkCommand:
    if service_ids is None:
        service_ids = [SERVICE_ID]
    if payload is None:
        payload = {"operation": {"serviceId": service_ids[0], "action": action}}
    definitions = [_definition(s, compose) for s in service_ids]
    tx = _transaction(state, definitions, action)
    cmd = _command(operation_key, service_ids, payload)
    return lifecycle_plan.bind_lifecycle_plan(
        cmd, tx, read_only_observation=state == "reconciling"
    )


def _get_identity() -> app_id.ApplicationIdentity:
    cmd = _bound_command()
    return app_id.produce_application_identity(cmd)


def _build_canonical_record(
    identity: app_id.ApplicationIdentity,
    config_sha: str = CONFIG_SHA,
    containers: list[str] | None = None,
    override_sha: str = OVERRIDE_SHA,
) -> dict[str, Any]:
    """Build a valid canonical record for testing."""
    if containers is None:
        containers = sorted(CONTAINER_NAMES)

    record_payload: dict[str, Any] = {
        "schema": obs_mod.RECORD_SCHEMA,
        "service_id": identity.service_id,
        "version": identity.version,
        "action": identity.action,
        "transaction_id": identity.transaction_id,
        "plan_sha256": identity.plan_sha256,
        "request_sha256": identity.request_sha256,
        "definition_sha256": identity.definition_sha256,
        "compose_sha256": identity.compose_sha256,
        "identity_sha256": identity.identity_sha256,
        "config_sha256": config_sha,
        "override_sha256": override_sha,
        "expected_containers": containers,
    }

    canonical = (
        json.dumps(
            record_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    record_sha = hashlib.sha256(canonical).hexdigest()

    record_payload["record_sha256"] = record_sha
    return record_payload


def _recompute_record(record: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(record)
    cloned["expected_containers"] = list(record["expected_containers"])
    payload = {key: value for key, value in cloned.items() if key != "record_sha256"}
    canonical = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    cloned["record_sha256"] = hashlib.sha256(canonical).hexdigest()
    return cloned


def _record_bytes(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _absent_snapshot() -> receipts_mod.LifecycleSnapshot:
    return receipts_mod.LifecycleSnapshot(
        transaction_id=TRANSACTION_ID,
        operation_key=f"apply:{SERVICE_ID}",
        state="absent",
        started_receipt=None,
        terminal_receipt=None,
    )


def _started_receipt(
    identity: app_id.ApplicationIdentity | None = None,
) -> receipts_mod.StartedReceipt:
    if identity is None:
        identity = _get_identity()
    return receipts_mod.StartedReceipt(
        transaction_id=identity.transaction_id,
        plan_hash=identity.plan_sha256,
        operation_key=f"apply:{identity.service_id}",
        request_hash=identity.request_sha256,
        service_ids=(identity.service_id,),
        event_hash="a" * 64,
    )


def _started_snapshot(
    identity: app_id.ApplicationIdentity | None = None,
) -> receipts_mod.LifecycleSnapshot:
    if identity is None:
        identity = _get_identity()
    sr = _started_receipt(identity)
    return receipts_mod.LifecycleSnapshot(
        transaction_id=identity.transaction_id,
        operation_key=f"apply:{identity.service_id}",
        state="started",
        started_receipt=sr,
        terminal_receipt=None,
    )


def _completed_terminal(
    identity: app_id.ApplicationIdentity | None = None,
    evidence_hash: str | None = None,
) -> receipts_mod.TerminalReceipt:
    if identity is None:
        identity = _get_identity()
    if evidence_hash is None:
        evidence_hash = identity.identity_sha256
    return receipts_mod.TerminalReceipt(
        transaction_id=identity.transaction_id,
        plan_hash=identity.plan_sha256,
        operation_key=f"apply:{identity.service_id}",
        request_hash=identity.request_sha256,
        service_ids=(identity.service_id,),
        outcome="completed",
        evidence_hash=evidence_hash,
        started_event_hash="a" * 64,
        event_hash="c" * 64,
    )


def _completed_snapshot(
    identity: app_id.ApplicationIdentity | None = None,
) -> receipts_mod.LifecycleSnapshot:
    if identity is None:
        identity = _get_identity()
    tr = _completed_terminal(identity, identity.identity_sha256)
    return receipts_mod.LifecycleSnapshot(
        transaction_id=identity.transaction_id,
        operation_key=f"apply:{identity.service_id}",
        state="completed",
        started_receipt=_started_receipt(identity),
        terminal_receipt=tr,
    )


def _failed_terminal(
    identity: app_id.ApplicationIdentity | None = None,
) -> receipts_mod.TerminalReceipt:
    if identity is None:
        identity = _get_identity()
    return receipts_mod.TerminalReceipt(
        transaction_id=identity.transaction_id,
        plan_hash=identity.plan_sha256,
        operation_key=f"apply:{identity.service_id}",
        request_hash=identity.request_sha256,
        service_ids=(identity.service_id,),
        outcome="failed",
        evidence_hash="d" * 64,
        started_event_hash="a" * 64,
        event_hash="e" * 64,
    )


def _failed_snapshot(
    identity: app_id.ApplicationIdentity | None = None,
) -> receipts_mod.LifecycleSnapshot:
    if identity is None:
        identity = _get_identity()
    return receipts_mod.LifecycleSnapshot(
        transaction_id=identity.transaction_id,
        operation_key=f"apply:{identity.service_id}",
        state="failed",
        started_receipt=_started_receipt(identity),
        terminal_receipt=_failed_terminal(identity),
    )


def _compensation_snapshot(
    identity: app_id.ApplicationIdentity | None = None,
) -> receipts_mod.LifecycleSnapshot:
    if identity is None:
        identity = _get_identity()
    operation_key = f"compensate:{identity.service_id}"
    command = _command(
        operation_key,
        [identity.service_id],
        {"operation": {"serviceId": identity.service_id, "action": identity.action}},
    )
    started = receipts_mod.StartedReceipt(
        transaction_id=identity.transaction_id,
        plan_hash=identity.plan_sha256,
        operation_key=operation_key,
        request_hash=command.request_hash,
        service_ids=(identity.service_id,),
        event_hash="b" * 64,
    )
    terminal = receipts_mod.TerminalReceipt(
        transaction_id=identity.transaction_id,
        plan_hash=identity.plan_sha256,
        operation_key=operation_key,
        request_hash=command.request_hash,
        service_ids=(identity.service_id,),
        outcome="completed",
        evidence_hash=identity.identity_sha256,
        started_event_hash=started.event_hash,
        event_hash="d" * 64,
    )
    return receipts_mod.LifecycleSnapshot(
        transaction_id=identity.transaction_id,
        operation_key=operation_key,
        state="completed",
        started_receipt=started,
        terminal_receipt=terminal,
    )


_DEFAULT_OVERRIDE = object()


def _build_evidence(
    record: dict[str, Any] | None = None,
    def_digest: str | None = None,
    compose_digest: str | None = None,
    config_digest: str | None = None,
    containers: tuple[obs_mod.ContainerObservation, ...] = (),
    snapshot: receipts_mod.LifecycleSnapshot | None = None,
    topology: str = "docker",
    docker_available: bool = True,
    compensation: receipts_mod.LifecycleSnapshot | None = None,
    override_digest: str | None | object = _DEFAULT_OVERRIDE,
) -> obs_mod.CurrentEvidence:
    if snapshot is None:
        snapshot = _absent_snapshot()
    if override_digest is _DEFAULT_OVERRIDE:
        override_digest = record["override_sha256"] if record is not None else None
    return obs_mod.CurrentEvidence(
        active_record=record,
        active_definition_digest=def_digest,
        active_compose_digest=compose_digest,
        active_config_digest=config_digest,
        container_observations=containers,
        receipt_snapshot=snapshot,
        topology=topology,
        docker_available=docker_available,
        compensation_snapshot=compensation,
        active_override_digest=override_digest,  # type: ignore[arg-type]
    )


def _container_observation(
    name: str,
    state: str = "running",
    health: str = "healthy",
    identity: app_id.ApplicationIdentity | None = None,
) -> obs_mod.ContainerObservation:
    if identity is None:
        identity = _get_identity()
    labels = app_id.identity_labels(identity)
    return obs_mod.ContainerObservation(
        name=name,
        state=state,
        health=health,
        labels=labels,
    )


# ===================================================================
# 1. HAPPY APPLIED - Started-only receipt
# ===================================================================


def test_applied_started_only():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(identity),
    )
    result = obs_mod.observe_application(cmd, evidence)

    assert result.classification == "APPLIED"
    assert result.service_id == SERVICE_ID
    assert result.identity_sha256 == identity.identity_sha256
    assert result.record_sha256 is not None
    assert len(result.containers) == 2


def test_reconciling_read_only_observation_still_detects_applied_mutation():
    identity = _get_identity()
    command = _bound_command(state="reconciling")
    evidence = _build_evidence(
        record=_build_canonical_record(identity),
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=tuple(_container_observation(name) for name in CONTAINER_NAMES),
        snapshot=_started_snapshot(identity),
    )
    result = obs_mod.observe_application(command, evidence)
    assert result.classification == "APPLIED"
    assert result.identity_sha256 == identity.identity_sha256


def test_non_apply_command_is_rejected_before_classification():
    command = replace(_bound_command(), operation_key=f"purge:{SERVICE_ID}")
    evidence = _build_evidence(snapshot=_absent_snapshot())

    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(command, evidence)
    assert exc.value.code == "command-identity-invalid"


def test_applied_started_only_single_container():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity, containers=["single-container"])
    containers = (_container_observation("single-container"),)
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "APPLIED"
    assert len(result.containers) == 1


# ===================================================================
# 2. HAPPY APPLIED - Completed receipt
# ===================================================================


def test_applied_completed_exact_evidence_hash():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_completed_snapshot(identity),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "APPLIED"
    assert result.record_sha256 is not None


def test_applied_no_compose():
    """APPLIED when plan has no Compose digest."""
    cmd = _bound_command(compose=None)
    identity = app_id.produce_application_identity(cmd)
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n, identity=identity) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=None,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(identity),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "APPLIED"


# ===================================================================
# 3. ABSENT variants
# ===================================================================


def test_absent_no_receipt_no_mutation():
    cmd = _bound_command()
    evidence = _build_evidence(snapshot=_absent_snapshot())
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "ABSENT"
    assert result.record_sha256 is None
    assert result.containers == ()


def test_absent_started_only_receipt():
    """Started-only receipt with no mutations is ABSENT (recovery retry)."""
    cmd = _bound_command()
    evidence = _build_evidence(snapshot=_started_snapshot())
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "ABSENT"


def test_absent_failed_terminal_no_mutation():
    """Failed terminal with no mutations is ABSENT."""
    cmd = _bound_command()
    evidence = _build_evidence(snapshot=_failed_snapshot())
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "ABSENT"


# ===================================================================
# 4. Completed terminal with no mutation -> drift error
# ===================================================================


def test_completed_receipt_no_mutation_is_drift():
    identity = _get_identity()
    cmd = _bound_command()
    evidence = _build_evidence(snapshot=_completed_snapshot(identity))
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "completed-receipt-no-mutation-drift"


def test_completed_apply_and_exact_completed_compensation_is_absent():
    identity = _get_identity()
    cmd = _bound_command(state="reconciling")
    evidence = _build_evidence(
        snapshot=_completed_snapshot(identity),
        compensation=_compensation_snapshot(identity),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "ABSENT"
    assert result.identity_sha256 == identity.identity_sha256


@pytest.mark.parametrize("field", ["request_hash", "plan_hash", "evidence_hash"])
def test_compensation_receipt_must_match_exact_plan_and_identity(field):
    identity = _get_identity()
    cmd = _bound_command(state="reconciling")
    snapshot = _compensation_snapshot(identity)
    if field == "evidence_hash":
        snapshot = replace(
            snapshot,
            terminal_receipt=replace(snapshot.terminal_receipt, evidence_hash="0" * 64),
        )
    else:
        snapshot = replace(
            snapshot,
            started_receipt=replace(
                snapshot.started_receipt, **{field: "0" * 64}
            ),
        )
    evidence = _build_evidence(
        snapshot=_completed_snapshot(identity), compensation=snapshot
    )
    with pytest.raises(obs_mod.ApplicationObservationError):
        obs_mod.observe_application(cmd, evidence)


def test_started_compensation_is_not_proof_of_absence():
    identity = _get_identity()
    snapshot = _compensation_snapshot(identity)
    snapshot = replace(snapshot, state="started", terminal_receipt=None)
    evidence = _build_evidence(
        snapshot=_completed_snapshot(identity), compensation=snapshot
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(_bound_command(state="reconciling"), evidence)
    assert exc.value.code == "compensation-receipt-incomplete"


def test_started_compensation_does_not_hide_fully_applied_app():
    identity = _get_identity()
    compensation = _compensation_snapshot(identity)
    compensation = replace(compensation, state="started", terminal_receipt=None)
    evidence = _build_evidence(
        record=_build_canonical_record(identity),
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=tuple(
            _container_observation(name, identity=identity)
            for name in CONTAINER_NAMES
        ),
        snapshot=_completed_snapshot(identity),
        compensation=compensation,
    )
    result = obs_mod.observe_application(
        _bound_command(state="reconciling"), evidence
    )
    assert result.classification == "APPLIED"


def _compensation_progress_evidence(
    *,
    record: bool = True,
    files: tuple[str, ...] = (
        "manifest.yaml",
        "compose.yaml",
        "configuration.json",
        "compose.override.yaml",
    ),
    containers: tuple[str, ...] = tuple(CONTAINER_NAMES),
) -> obs_mod.CurrentEvidence:
    identity = _get_identity()
    completed = _compensation_snapshot(identity)
    started = replace(completed, state="started", terminal_receipt=None)
    observed_containers = tuple(
        replace(
            _container_observation(name, identity=identity),
            labels={
                **app_id.identity_labels(identity),
                "com.docker.compose.project": f"ods-af-{identity.service_id}",
            },
        )
        for name in containers
    )
    return _build_evidence(
        record=_build_canonical_record(identity) if record else None,
        def_digest=DEFINITION_SHA if "manifest.yaml" in files else None,
        compose_digest=COMPOSE_SHA if "compose.yaml" in files else None,
        config_digest=CONFIG_SHA if "configuration.json" in files else None,
        override_digest=OVERRIDE_SHA if "compose.override.yaml" in files else None,
        containers=observed_containers,
        snapshot=_completed_snapshot(identity),
        compensation=started,
    )


@pytest.mark.parametrize(
    ("files", "containers", "record", "state"),
    [
        (("manifest.yaml", "compose.yaml", "configuration.json", "compose.override.yaml"), tuple(CONTAINER_NAMES), True, "CONTAINERS_PRESENT"),
        (("manifest.yaml", "compose.yaml", "configuration.json", "compose.override.yaml"), (CONTAINER_NAMES[0],), True, "CONTAINERS_PRESENT"),
        (("manifest.yaml", "compose.yaml", "configuration.json", "compose.override.yaml"), (), True, "FILES_PRESENT"),
        (("manifest.yaml", "compose.yaml"), (), True, "FILES_PRESENT"),
        ((), (), True, "RECORD_ONLY"),
        ((), (), False, "READY_TO_COMPLETE"),
    ],
)
def test_compensation_progress_monotonic_replay(
    files: tuple[str, ...],
    containers: tuple[str, ...],
    record: bool,
    state: str,
) -> None:
    result = obs_mod.observe_compensation_progress(
        _bound_command(state="reconciling"),
        _compensation_progress_evidence(
            files=files, containers=containers, record=record
        ),
        expected_config_sha256=CONFIG_SHA,
    )
    assert result.state == state
    assert result.remaining_files == files
    assert result.remaining_containers == tuple(sorted(containers))
    assert (result.record_sha256 is not None) == record


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"record": False}, "compensation-progress-record-lost"),
        ({"files": ("manifest.yaml", "compose.yaml")}, "compensation-progress-files-before-containers"),
    ],
)
def test_compensation_progress_rejects_impossible_partial(
    change: dict[str, Any], code: str
) -> None:
    evidence = _compensation_progress_evidence(**change)
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_compensation_progress(
            _bound_command(state="reconciling"),
            evidence,
            expected_config_sha256=CONFIG_SHA,
        )
    assert exc.value.code == code


def test_compensation_progress_rejects_tampered_override() -> None:
    evidence = replace(
        _compensation_progress_evidence(containers=()),
        active_override_digest="sha256:" + "0" * 64,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_compensation_progress(
            _bound_command(state="reconciling"),
            evidence,
            expected_config_sha256=CONFIG_SHA,
        )
    assert exc.value.code == "compensation-progress-file-drift"


@pytest.mark.parametrize("override", [None, "sha256:" + "0" * 64])
def test_applied_observation_requires_record_bound_override(
    override: str | None,
) -> None:
    identity = _get_identity()
    evidence = _build_evidence(
        record=_build_canonical_record(identity),
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        override_digest=override,
        containers=tuple(_container_observation(name) for name in CONTAINER_NAMES),
        snapshot=_completed_snapshot(identity),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(_bound_command(), evidence)
    assert exc.value.code == "override-drift"


def test_active_record_v1_cannot_bypass_override_binding() -> None:
    old = _build_canonical_record(_get_identity())
    old["schema"] = "ods.extension-application-active-record.v1"
    old.pop("override_sha256")
    old = _recompute_record(old)
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.parse_active_record(_record_bytes(old))
    assert exc.value.code == "record-keys-mismatch"


def test_compensation_progress_rejects_foreign_project_container() -> None:
    evidence = _compensation_progress_evidence()
    foreign = replace(
        evidence.container_observations[0],
        labels={
            **evidence.container_observations[0].labels,
            "com.docker.compose.project": "foreign",
        },
    )
    evidence = replace(evidence, container_observations=(foreign, *evidence.container_observations[1:]))
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_compensation_progress(
            _bound_command(state="reconciling"),
            evidence,
            expected_config_sha256=CONFIG_SHA,
        )
    assert exc.value.code == "compensation-progress-container-drift"


def test_compensation_cannot_launder_wrong_completed_apply_evidence():
    identity = _get_identity()
    apply_snapshot = _completed_snapshot(identity)
    apply_snapshot = replace(
        apply_snapshot,
        terminal_receipt=replace(
            apply_snapshot.terminal_receipt, evidence_hash="0" * 64
        ),
    )
    evidence = _build_evidence(
        snapshot=apply_snapshot,
        compensation=_compensation_snapshot(identity),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(_bound_command(state="reconciling"), evidence)
    assert exc.value.code == "compensation-apply-evidence-mismatch"


def test_compensated_app_with_remaining_override_is_not_absent():
    identity = _get_identity()
    evidence = _build_evidence(
        snapshot=_completed_snapshot(identity),
        compensation=_compensation_snapshot(identity),
        override_digest=COMPOSE_SHA,
    )
    with pytest.raises(obs_mod.ApplicationObservationError):
        obs_mod.observe_application(_bound_command(state="reconciling"), evidence)


def test_orphaned_override_alone_blocks_absent():
    evidence = _build_evidence(
        snapshot=_started_snapshot(), override_digest=COMPOSE_SHA
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(_bound_command(), evidence)
    assert exc.value.code == "applied-record-required"


def test_completed_compensation_with_remaining_app_is_drift():
    identity = _get_identity()
    evidence = _build_evidence(
        record=_build_canonical_record(identity),
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=tuple(_container_observation(name, identity=identity) for name in CONTAINER_NAMES),
        snapshot=_completed_snapshot(identity),
        compensation=_compensation_snapshot(identity),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(_bound_command(state="reconciling"), evidence)
    assert exc.value.code == "compensated-application-reappeared"


# ===================================================================
# 5. Container state/health does NOT gate APPLIED
# ===================================================================


def test_stopped_containers_still_applied():
    """Stopped containers are still APPLIED (recovery compensates)."""
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n, state="exited", health="no_healthcheck")
        for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "APPLIED"
    for c in result.containers:
        assert c.state == "exited"
        assert c.health == "no_healthcheck"


def test_unhealthy_containers_still_applied():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n, state="running", health="unhealthy")
        for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "APPLIED"


def test_restarting_containers_still_applied():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n, state="restarting", health="starting")
        for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "APPLIED"


# ===================================================================
# 6. Every mismatch/partial/drift case -> error
# ===================================================================


def test_partial_record_no_definition():
    """Record present but definition digest absent -> error (mixed state)."""
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    evidence = _build_evidence(
        record=record,
        def_digest=None,
        compose_digest=None,
        config_digest=None,
        containers=(),
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError):
        obs_mod.observe_application(cmd, evidence)


def test_partial_record_no_containers():
    """Record and digests present but no container observations -> error."""
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=(),
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "container-count-mismatch"


def test_definition_drift():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest="sha256:" + "f" * 64,  # wrong digest
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "definition-drift"


def test_compose_drift():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest="sha256:" + "f" * 64,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "compose-drift"


def test_config_drift():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest="sha256:" + "f" * 64,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "config-drift"


def test_extra_container():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    ) + (_container_observation("extra-container"),)
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError):
        obs_mod.observe_application(cmd, evidence)


def test_missing_container():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = (_container_observation(CONTAINER_NAMES[0]),)
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "container-count-mismatch"


def test_container_name_mismatch():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = (
        _container_observation(CONTAINER_NAMES[0]),
        _container_observation("wrong-name"),
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "container-name-mismatch"


def test_container_identity_mismatch():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)

    # Build a valid but different identity (version 9.9.9 with matching digest)
    wrong_payload = {
        "action": identity.action,
        "composeSha256": identity.compose_sha256,
        "definitionSha256": identity.definition_sha256,
        "planSha256": identity.plan_sha256,
        "requestSha256": identity.request_sha256,
        "serviceId": identity.service_id,
        "transactionId": identity.transaction_id,
        "version": "9.9.9",
    }
    canonical = (
        json.dumps(
            wrong_payload,
            ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    wrong_identity_sha = hashlib.sha256(canonical).hexdigest()

    wrong_identity = app_id.ApplicationIdentity(
        service_id=identity.service_id,
        version="9.9.9",
        action=identity.action,
        transaction_id=identity.transaction_id,
        plan_sha256=identity.plan_sha256,
        request_sha256=identity.request_sha256,
        definition_sha256=identity.definition_sha256,
        compose_sha256=identity.compose_sha256,
        identity_sha256=wrong_identity_sha,
    )
    wrong_labels = app_id.identity_labels(wrong_identity)
    containers = (
        _container_observation(CONTAINER_NAMES[0]),
        obs_mod.ContainerObservation(
            name=CONTAINER_NAMES[1],
            state="running",
            health="healthy",
            labels=wrong_labels,
        ),
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "container-identity-mismatch"


def test_completed_evidence_hash_mismatch():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    # Terminal with wrong evidence_hash
    wrong_terminal = _completed_terminal(identity, "f" * 64)
    snapshot = receipts_mod.LifecycleSnapshot(
        transaction_id=TRANSACTION_ID,
        operation_key=f"apply:{SERVICE_ID}",
        state="completed",
        started_receipt=_started_receipt(),
        terminal_receipt=wrong_terminal,
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=snapshot,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "completed-evidence-hash-mismatch"


def test_no_receipt_with_effects_is_ambiguous():
    """Effects present (record/digests/containers) but no receipt -> error."""
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_absent_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "applied-receipt-state-invalid"


# ===================================================================
# 7. Docker unavailable -> error
# ===================================================================


def test_docker_unavailable():
    cmd = _bound_command()
    evidence = _build_evidence(docker_available=False)
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "docker-unavailable"


# ===================================================================
# 8. Multi-container exactness
# ===================================================================


def test_multi_container_exact():
    identity = _get_identity()
    cmd = _bound_command()
    many_names = [f"svc-{i}" for i in range(10)]
    record = _build_canonical_record(identity, containers=sorted(many_names))
    containers = tuple(
        _container_observation(n) for n in many_names
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    result = obs_mod.observe_application(cmd, evidence)
    assert result.classification == "APPLIED"
    assert len(result.containers) == 10
    # Verify sorted output
    names = [c.name for c in result.containers]
    assert names == sorted(names)


def test_container_count_exceeds_max():
    identity = _get_identity()
    too_many = sorted([f"svc-{i}" for i in range(obs_mod.MAX_CONTAINERS + 1)])
    record = _build_canonical_record(identity, containers=too_many)
    with pytest.raises(obs_mod.ApplicationObservationError):
        obs_mod._validate_canonical_record(record)


# ===================================================================
# 9. Malformed/oversized/type-confused inputs
# ===================================================================


def test_record_unknown_field():
    identity = _get_identity()
    record = _build_canonical_record(identity)
    record["extra_field"] = "bad"
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-keys-mismatch"


def test_record_missing_field():
    identity = _get_identity()
    record = _build_canonical_record(identity)
    del record["config_sha256"]
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-keys-mismatch"


def test_record_bool_type_confusion():
    identity = _get_identity()
    record = _build_canonical_record(identity)
    record["service_id"] = True  # bool masquerading as string
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-field-invalid-service_id"


def test_record_digest_tamper():
    identity = _get_identity()
    record = _build_canonical_record(identity)
    record["record_sha256"] = "f" * 64  # tampered
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-digest-mismatch"


def test_digest_type_confused_bool():
    cmd = _bound_command()
    evidence = _build_evidence(
        def_digest=True,  # bool, not str
        snapshot=_absent_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "digest-type-confused"


def test_digest_invalid_format():
    cmd = _bound_command()
    evidence = _build_evidence(
        def_digest="not-a-digest",
        snapshot=_absent_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "digest-format-invalid"


def test_container_invalid_state():
    cmd = _bound_command()
    obs = obs_mod.ContainerObservation(
        name="x", state="invalid_state", health="healthy", labels={}
    )
    evidence = _build_evidence(
        containers=(obs,),
        snapshot=_absent_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "container-state-invalid"


def test_container_invalid_health():
    cmd = _bound_command()
    obs = obs_mod.ContainerObservation(
        name="x", state="running", health="invalid_health", labels={}
    )
    evidence = _build_evidence(
        containers=(obs,),
        snapshot=_absent_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "container-health-invalid"


def test_container_labels_not_dict():
    cmd = _bound_command()
    obs = obs_mod.ContainerObservation(
        name="x", state="running", health="healthy", labels="not-dict"
    )
    evidence = _build_evidence(
        containers=(obs,),
        snapshot=_absent_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "container-labels-invalid"


def test_docker_available_not_bool():
    cmd = _bound_command()
    evidence = obs_mod.CurrentEvidence(
        active_record=None,
        active_definition_digest=None,
        active_compose_digest=None,
        active_config_digest=None,
        container_observations=(),
        receipt_snapshot=_absent_snapshot(),
        topology="docker",
        docker_available=1,  # int, not bool
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "docker-available-type-invalid"


def test_evidence_not_current_evidence():
    cmd = _bound_command()
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, "not-evidence")  # type: ignore
    assert exc.value.code == "evidence-type-invalid"


def test_command_not_bound():
    cmd = _command(
        f"apply:{SERVICE_ID}",
        [SERVICE_ID],
        {"operation": {"serviceId": SERVICE_ID, "action": ACTION}},
    )
    evidence = _build_evidence()
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "command-plan-not-bound"


def test_record_not_mapping():
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record("not-a-dict")
    assert exc.value.code == "record-must-be-mapping"


def test_record_expected_containers_unsorted():
    identity = _get_identity()
    record = _build_canonical_record(identity, containers=["b", "a"])
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-field-invalid-expected_containers"


def test_record_expected_containers_duplicate():
    identity = _get_identity()
    record = _build_canonical_record(identity, containers=["a", "a"])
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-field-invalid-expected_containers"


def test_record_expected_containers_unsafe_name():
    identity = _get_identity()
    record = _build_canonical_record(identity, containers=["../escape"])
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-field-invalid-expected_containers"


# ===================================================================
# 10. Value leakage tests
# ===================================================================


def test_result_never_exposes_config_values():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    result = obs_mod.observe_application(cmd, evidence)

    result_str = repr(result)
    assert "secret" not in result_str.lower()
    assert "password" not in result_str.lower()
    assert "api_key" not in result_str.lower()


def test_result_never_exposes_host_paths():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    result = obs_mod.observe_application(cmd, evidence)
    result_str = repr(result)
    assert "/home/" not in result_str
    assert "/etc/" not in result_str


def test_error_never_exposes_values():
    """Error messages must be value-free (only stable codes)."""
    cmd = _bound_command()
    identity = _get_identity()
    record = _build_canonical_record(identity)
    # Supply wrong definition digest to trigger drift error
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest="sha256:" + "f" * 64,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    # Code must be stable and contain no values
    assert re.fullmatch(r"[a-z][a-z0-9_-]*", exc.value.code)
    # Error message must not contain hex digests
    assert "sha256:" not in str(exc.value)


# ===================================================================
# 11. Tampered frozen dataclasses
# ===================================================================


def test_observation_result_frozen():
    result = obs_mod.ObservationResult(
        service_id=SERVICE_ID,
        classification="ABSENT",
        identity_sha256="a" * 64,
        record_sha256=None,
        containers=(),
    )
    with pytest.raises(FrozenInstanceError):
        result.service_id = "x"  # type: ignore


def test_container_state_summary_frozen():
    summary = obs_mod.ContainerStateSummary(
        name="x", state="running", health="healthy", identity_match=True
    )
    with pytest.raises(FrozenInstanceError):
        summary.state = "stopped"  # type: ignore


def test_container_observation_frozen():
    obs = obs_mod.ContainerObservation(
        name="x", state="running", health="healthy", labels={}
    )
    with pytest.raises(FrozenInstanceError):
        obs.name = "y"  # type: ignore


# ===================================================================
# 12. Deterministic output
# ===================================================================


def test_deterministic_output():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    r1 = obs_mod.observe_application(cmd, evidence)
    r2 = obs_mod.observe_application(cmd, evidence)
    assert r1 == r2
    assert r1.classification == r2.classification
    assert r1.identity_sha256 == r2.identity_sha256
    assert r1.record_sha256 == r2.record_sha256
    assert r1.containers == r2.containers


def test_result_as_dict_deterministic():
    """JSON serialization of result is deterministic."""
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    r1 = obs_mod.observe_application(cmd, evidence)
    r2 = obs_mod.observe_application(cmd, evidence)
    s1 = json.dumps(
        {"classification": r1.classification,
         "service_id": r1.service_id,
         "identity_sha256": r1.identity_sha256,
         "record_sha256": r1.record_sha256,
         "containers": [
             {"name": c.name, "state": c.state, "health": c.health,
              "identity_match": c.identity_match}
             for c in r1.containers
         ]},
        sort_keys=True,
    )
    s2 = json.dumps(
        {"classification": r2.classification,
         "service_id": r2.service_id,
         "identity_sha256": r2.identity_sha256,
         "record_sha256": r2.record_sha256,
         "containers": [
             {"name": c.name, "state": c.state, "health": c.health,
              "identity_match": c.identity_match}
             for c in r2.containers
         ]},
        sort_keys=True,
    )
    assert s1 == s2


# ===================================================================
# 13. Source-level tests: narrow host import, executor remains disabled
# ===================================================================


def test_module_is_imported_only_for_host_observation_boundary():
    """The reviewed host route may observe; it must not enable execution."""
    host_agent = BIN_DIR / "ods-host-agent.py"
    if not host_agent.exists():
        pytest.skip("ods-host-agent.py not present")
    source = host_agent.read_text(encoding="utf-8")
    assert "import extension_application_observation as _application_observation_module" in source
    assert "def _handle_application_observation(self)" in source
    production = (
        Path(__file__).resolve().parents[1] / "extension_transaction_production.py"
    ).read_text(encoding="utf-8")
    assert "executor=None" in production


def test_module_not_imported_by_runtime():
    """Prove the module does not import host agent or runtime modules."""
    mod_path = BIN_DIR / "extension_application_observation.py"
    source = mod_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    for imp in imports:
        assert "ods-host-agent" not in imp
        assert "main" not in imp
        assert "runtime" not in imp.lower() or "extension_lifecycle" in imp


def test_module_no_side_effects():
    """Module must not have subprocess, os, network, or mutation imports."""
    mod_path = BIN_DIR / "extension_application_observation.py"
    source = mod_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    forbidden = {
        "subprocess", "socket", "http", "requests", "urllib",
        "threading", "multiprocessing", "asyncio", "httpx",
        "aiohttp", "docker", "paramiko", "ssh",
    }
    for imp in imports:
        top = imp.split(".")[0]
        assert top not in forbidden, f"forbidden import: {imp}"


def test_no_executor_or_dispatcher_references():
    """Module must not reference executor, dispatcher, or run."""
    mod_path = BIN_DIR / "extension_application_observation.py"
    source = mod_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Check for variable names containing executor/dispatcher
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.id, str):
            assert "executor" not in node.id.lower()
            assert "dispatcher" not in node.id.lower()
        if isinstance(node, ast.FunctionDef):
            assert "execute" not in node.name.lower()
            assert "dispatch" not in node.name.lower()


# ===================================================================
# 14. APPLIED receipt required checks
# ===================================================================


def test_applied_requires_started_receipt():
    """APPLIED must have a started receipt."""
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    # No started receipt
    snapshot = receipts_mod.LifecycleSnapshot(
        transaction_id=TRANSACTION_ID,
        operation_key=f"apply:{SERVICE_ID}",
        state="started",
        started_receipt=None,  # missing
        terminal_receipt=None,
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=snapshot,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "receipt-started-binding-invalid"


def test_applied_completed_no_terminal_receipt():
    """Completed state without terminal_receipt -> error."""
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    snapshot = receipts_mod.LifecycleSnapshot(
        transaction_id=TRANSACTION_ID,
        operation_key=f"apply:{SERVICE_ID}",
        state="completed",
        started_receipt=_started_receipt(),
        terminal_receipt=None,  # missing
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=snapshot,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "receipt-terminal-binding-invalid"


# ===================================================================
# 15. Record identity/compose mismatch
# ===================================================================


def test_record_identity_mismatch():
    identity = _get_identity()
    cmd = _bound_command()
    record = _build_canonical_record(identity)
    record["identity_sha256"] = "f" * 64
    # Recompute record_sha256 after tamper
    canonical = (
        json.dumps(
            {k: v for k, v in sorted(record.items()) if k != "record_sha256"},
            ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    record["record_sha256"] = hashlib.sha256(canonical).hexdigest()

    containers = tuple(
        _container_observation(n) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "record-binding-mismatch-identity_sha256"


def test_applied_compose_should_be_absent():
    """Plan has no Compose but evidence has one -> error."""
    cmd = _bound_command(compose=None)
    identity = app_id.produce_application_identity(cmd)
    record = _build_canonical_record(identity)
    containers = tuple(
        _container_observation(n, identity=identity) for n in CONTAINER_NAMES
    )
    evidence = _build_evidence(
        record=record,
        def_digest=DEFINITION_SHA,
        compose_digest=COMPOSE_SHA,  # present but should be absent
        config_digest=CONFIG_SHA,
        containers=containers,
        snapshot=_started_snapshot(identity),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "compose-should-be-absent"


# ===================================================================
# 16. Record schema validation
# ===================================================================


def test_record_schema_invalid():
    identity = _get_identity()
    record = _build_canonical_record(identity)
    record["schema"] = "wrong.schema"
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-schema-invalid"


def test_record_empty_containers_rejected():
    identity = _get_identity()
    record = _build_canonical_record(identity, containers=[])
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod._validate_canonical_record(record)
    assert exc.value.code == "record-field-invalid-expected_containers"


# ===================================================================
# 17. Independent fail-closed counterexamples
# ===================================================================


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("service_id", "other-service"),
        ("version", "9.9.9"),
        ("action", "update"),
        ("transaction_id", "txn-" + "f" * 24),
        ("plan_sha256", "f" * 64),
        ("request_sha256", "f" * 64),
        ("definition_sha256", "sha256:" + "f" * 64),
        ("compose_sha256", "sha256:" + "f" * 64),
        ("identity_sha256", "f" * 64),
    ],
)
def test_every_active_record_identity_field_is_bound(field: str, value: str):
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    record = _build_canonical_record(identity)
    record[field] = value
    record = _recompute_record(record)
    evidence = _build_evidence(
        record=record,
        def_digest=identity.definition_sha256,
        compose_digest=identity.compose_sha256,
        config_digest=CONFIG_SHA,
        containers=tuple(_container_observation(name) for name in CONTAINER_NAMES),
        snapshot=_started_snapshot(identity),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == f"record-binding-mismatch-{field}"


def test_complete_canonical_record_from_another_application_is_rejected():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    record = _build_canonical_record(identity)
    record.update(
        {
            "version": "9.9.9",
            "transaction_id": "txn-" + "f" * 24,
            "plan_sha256": "f" * 64,
            "request_sha256": "e" * 64,
            "identity_sha256": "d" * 64,
        }
    )
    substituted = _recompute_record(record)
    evidence = _build_evidence(
        record=substituted,
        def_digest=identity.definition_sha256,
        compose_digest=identity.compose_sha256,
        config_digest=CONFIG_SHA,
        containers=tuple(_container_observation(name) for name in CONTAINER_NAMES),
        snapshot=_started_snapshot(identity),
    )

    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)

    assert exc.value.code == "record-binding-mismatch-version"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_id", "txn-" + "f" * 24),
        ("plan_hash", "f" * 64),
        ("operation_key", "apply:other-service"),
        ("request_hash", "f" * 64),
        ("service_ids", ("other-service",)),
        ("event_hash", "not-a-hash"),
    ],
)
def test_started_receipt_must_match_exact_command(field: str, value: Any):
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    started = replace(_started_receipt(identity), **{field: value})
    snapshot = receipts_mod.LifecycleSnapshot(
        transaction_id=identity.transaction_id,
        operation_key=cmd.operation_key,
        state="started",
        started_receipt=started,
        terminal_receipt=None,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, _build_evidence(snapshot=snapshot))
    assert exc.value.code == "receipt-started-binding-invalid"


def test_snapshot_path_binding_must_match_exact_command():
    cmd = _bound_command()
    snapshot = replace(_absent_snapshot(), operation_key="apply:other-service")
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, _build_evidence(snapshot=snapshot))
    assert exc.value.code == "receipt-snapshot-binding-invalid"


def test_snapshot_state_and_receipt_shape_must_agree():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    snapshot = receipts_mod.LifecycleSnapshot(
        transaction_id=identity.transaction_id,
        operation_key=cmd.operation_key,
        state="absent",
        started_receipt=_started_receipt(identity),
        terminal_receipt=None,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, _build_evidence(snapshot=snapshot))
    assert exc.value.code == "receipt-snapshot-shape-invalid"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("request_hash", "f" * 64, "receipt-terminal-binding-invalid"),
        ("started_event_hash", "f" * 64, "receipt-terminal-chain-invalid"),
        ("outcome", "failed", "receipt-terminal-chain-invalid"),
        ("evidence_hash", "not-a-hash", "receipt-terminal-chain-invalid"),
    ],
)
def test_terminal_receipt_chain_is_reproved(field: str, value: Any, code: str):
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    terminal = replace(_completed_terminal(identity), **{field: value})
    snapshot = receipts_mod.LifecycleSnapshot(
        transaction_id=identity.transaction_id,
        operation_key=cmd.operation_key,
        state="completed",
        started_receipt=_started_receipt(identity),
        terminal_receipt=terminal,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, _build_evidence(snapshot=snapshot))
    assert exc.value.code == code


def test_failed_terminal_with_current_effects_is_never_absent():
    cmd = _bound_command()
    identity = app_id.produce_application_identity(cmd)
    evidence = _build_evidence(
        record=_build_canonical_record(identity),
        def_digest=identity.definition_sha256,
        compose_digest=identity.compose_sha256,
        config_digest=CONFIG_SHA,
        containers=tuple(_container_observation(name) for name in CONTAINER_NAMES),
        snapshot=_failed_snapshot(identity),
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(cmd, evidence)
    assert exc.value.code == "applied-receipt-state-invalid"


def test_active_record_bytes_are_canonical_and_duplicate_safe():
    identity = _get_identity()
    raw = obs_mod.produce_active_record(
        identity,
        CONFIG_SHA,
        tuple(sorted(CONTAINER_NAMES)),
        override_sha256=OVERRIDE_SHA,
    )
    assert obs_mod.parse_active_record(raw) == _build_canonical_record(identity)
    assert raw == obs_mod.produce_active_record(
        identity,
        CONFIG_SHA,
        tuple(sorted(CONTAINER_NAMES)),
        override_sha256=OVERRIDE_SHA,
    )

    duplicate = raw.replace(
        b'"action":"install"',
        b'"action":"install","action":"install"',
        1,
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.parse_active_record(duplicate)
    assert exc.value.code == "record-duplicate-key"


@pytest.mark.parametrize("action", ["install", "enable", "repair", "update"])
def test_active_record_supports_every_apply_action(action: str):
    command = _bound_command(action=action)
    identity = app_id.produce_application_identity(command)
    raw = obs_mod.produce_active_record(
        identity,
        CONFIG_SHA,
        tuple(sorted(CONTAINER_NAMES)),
        override_sha256=OVERRIDE_SHA,
    )
    assert obs_mod.parse_active_record(raw)["action"] == action


def test_active_record_rejects_noop_action():
    invalid = replace(_get_identity(), action="noop")
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.produce_active_record(
            invalid,
            CONFIG_SHA,
            tuple(sorted(CONTAINER_NAMES)),
            override_sha256=OVERRIDE_SHA,
        )
    assert exc.value.code == "record-identity-invalid"


def test_active_record_parser_rejects_noncanonical_and_nonbytes():
    identity = _get_identity()
    record = _build_canonical_record(identity)
    pretty = (json.dumps(record, indent=2) + "\n").encode("utf-8")
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.parse_active_record(pretty)
    assert exc.value.code == "record-noncanonical"

    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.parse_active_record(bytearray(_record_bytes(record)))  # type: ignore[arg-type]
    assert exc.value.code == "record-bytes-required"


def test_active_record_parser_rejects_oversized_raw_input():
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.parse_active_record(b"x" * (obs_mod.MAX_INPUT_BYTES + 1))
    assert exc.value.code == "record-oversize"


@pytest.mark.parametrize(
    ("state", "health", "code"),
    [
        ([], "healthy", "container-state-invalid"),
        ("running", {}, "container-health-invalid"),
        ("not_found", "healthy", "container-state-invalid"),
    ],
)
def test_container_state_and_health_type_confusion_fails_closed(
    state: Any,
    health: Any,
    code: str,
):
    observation = obs_mod.ContainerObservation(
        name="documents-api",
        state=state,
        health=health,
        labels={},
    )
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(
            _bound_command(),
            _build_evidence(containers=(observation,)),
        )
    assert exc.value.code == code


def test_unsupported_topology_is_not_treated_as_absent():
    with pytest.raises(obs_mod.ApplicationObservationError) as exc:
        obs_mod.observe_application(
            _bound_command(),
            _build_evidence(topology="systemd"),
        )
    assert exc.value.code == "topology-unsupported"


def test_validation_clones_nested_mutable_evidence():
    identity = _get_identity()
    record = _build_canonical_record(identity)
    labels = app_id.identity_labels(identity)
    evidence = _build_evidence(
        record=record,
        containers=(
            obs_mod.ContainerObservation(
                name=CONTAINER_NAMES[0],
                state="running",
                health="healthy",
                labels=labels,
            ),
        ),
    )
    validated = obs_mod._validate_current_evidence(evidence)
    record["expected_containers"].append("later-mutation")
    labels["later"] = "mutation"
    assert "later-mutation" not in validated.active_record["expected_containers"]
    assert "later" not in validated.container_observations[0].labels
