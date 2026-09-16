"""Focused contracts for approved library application registration."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_library_application_runtime as runtime  # noqa: E402
from extension_application_identity import ApplicationIdentity  # noqa: E402
from extension_application_observation import ObservationResult  # noqa: E402
from extension_library_compose_apply_effect import (  # noqa: E402
    LibraryComposeApplyEffect,
    LibraryComposeApplyResult,
)
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import (  # noqa: E402
    LifecycleWorkCommand,
    LifecycleWorkStartedObservation,
    LifecycleWorkValidationError,
    dispatch_receipted_lifecycle_work,
)


TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
REQUEST_HASH = "3" * 64
IDENTITY_HASH = "4" * 64


def _definition(service_id: str) -> PlannedDefinition:
    return PlannedDefinition(
        service_id=service_id,
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256="sha256:" + "5" * 64,
        compose_sha256="sha256:" + "6" * 64,
        definition_source="library",
        compose_file="compose.yaml",
        images=(),
        builds=(),
        canonical_document=b"fixture\n",
        source_tree_sha256="sha256:" + "7" * 64,
    )


def _command() -> LifecycleWorkCommand:
    operations = (
        PlannedOperation("gitea", "install"),
        PlannedOperation("ntfy", "noop"),
    )
    definitions = (_definition("gitea"), _definition("ntfy"))
    return LifecycleWorkCommand(
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        operation_key="apply:gitea",
        request_hash=REQUEST_HASH,
        service_ids=("gitea",),
        payload={"operation": {"serviceId": "gitea", "action": "install"}},
        timeout_seconds=900,
        plan_material=LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TRANSACTION_ID,
            plan_hash=PLAN_HASH,
            state="applying",
            operations=operations,
            definitions=definitions,
            attested_approval=True,
        ),
    )


def test_recovery_observation_never_reenables_apply_dispatch() -> None:
    applying = _command()
    recovering = replace(
        applying,
        plan_material=replace(applying.plan_material, state="reconciling"),
    )
    with pytest.raises(LifecycleWorkValidationError) as caught:
        runtime._validate_dispatch_command(recovering)
    assert caught.value.code == "library-application-plan-mismatch"


def _identity() -> ApplicationIdentity:
    return ApplicationIdentity(
        service_id="gitea",
        version="1.0.0",
        action="install",
        transaction_id=TRANSACTION_ID,
        plan_sha256=PLAN_HASH,
        request_sha256=REQUEST_HASH,
        definition_sha256="sha256:" + "5" * 64,
        compose_sha256="sha256:" + "6" * 64,
        identity_sha256=IDENTITY_HASH,
    )


def _result(*, identity_sha256: str = IDENTITY_HASH) -> LibraryComposeApplyResult:
    return LibraryComposeApplyResult(
        service_id="gitea",
        identity_sha256=identity_sha256,
        definition_sha256="sha256:" + "5" * 64,
        compose_sha256="sha256:" + "6" * 64,
        config_sha256="sha256:" + "7" * 64,
        override_sha256="sha256:" + "8" * 64,
        compose_services=("gitea",),
        expected_containers=("ods-af-gitea-gitea-1",),
        materialization_outcome="published",
        active_files_outcome="materialized",
        record_outcome="created",
    )


class Secrets:
    def status(self, _payload):
        raise AssertionError("patched binding owns this call")

    def invoke_with_secrets(self, _payload, _consumer):
        raise AssertionError("patched effect owns this call")


def _dispatcher(monkeypatch, *, result=None):
    calls = []
    identity = _identity()
    staged = object()
    effect_input = object()
    configuration = object()

    def read(transaction_id, plan_hash, service_ids):
        calls.append(("stage", transaction_id, plan_hash, service_ids))
        return staged

    monkeypatch.setattr(runtime, "produce_application_identity", lambda _c: identity)
    monkeypatch.setattr(
        runtime,
        "verify_plan_bound_library_payload",
        lambda command, batch, root: (
            calls.append(("payload", command, batch, root)) or effect_input
        ),
    )
    monkeypatch.setattr(
        runtime,
        "bind_library_configuration",
        lambda command, loader, status: (
            calls.append(("configuration", command, loader, status))
            or configuration
        ),
    )
    monkeypatch.setattr(
        LibraryComposeApplyEffect,
        "apply",
        lambda self, command, supplied_input, supplied_config, supplied_identity, store: (
            calls.append(
                (
                    "effect",
                    command,
                    supplied_input,
                    supplied_config,
                    supplied_identity,
                    store,
                )
            )
            or (result or _result())
        ),
    )
    secrets = Secrets()
    effect = LibraryComposeApplyEffect(
        Path("/fixed/install"),
        Path("/fixed/users"),
        runner=lambda *_args: True,
        capture=lambda *_args: b"",
    )
    dispatcher = runtime.LibraryApplicationDispatcher(
        stage_reader=read,
        library_root=Path("/fixed/library"),
        transaction_loader=lambda _transaction_id: {},
        secret_store=secrets,
        effect=effect,
    )
    return dispatcher, calls, identity, staged, effect_input, configuration, secrets


def test_dispatcher_binds_stage_configuration_effect_and_one_identity_hash(monkeypatch):
    dispatcher, calls, identity, staged, effect_input, configuration, secrets = (
        _dispatcher(monkeypatch)
    )
    command = _command()

    assert dispatcher(command) == IDENTITY_HASH
    assert calls[0] == ("stage", TRANSACTION_ID, PLAN_HASH, ("gitea",))
    assert calls[1] == ("payload", command, staged, Path("/fixed/library"))
    assert calls[2][0:2] == ("configuration", command)
    assert calls[3] == (
        "effect",
        command,
        effect_input,
        configuration,
        identity,
        secrets,
    )


def test_dispatcher_refuses_unapproved_service_before_stage_read(monkeypatch):
    dispatcher, calls, *_rest = _dispatcher(monkeypatch)
    command = _command()
    command = LifecycleWorkCommand(
        **{
            **command.__dict__,
            "operation_key": "apply:documents",
            "service_ids": ("documents",),
            "payload": {
                "operation": {"serviceId": "documents", "action": "install"}
            },
        }
    )
    with pytest.raises(Exception) as error:
        dispatcher(command)
    assert error.value.code == "library-application-command-invalid"
    assert calls == []


def test_dispatcher_marks_unproven_post_effect_result_uncertain(monkeypatch):
    dispatcher, _calls, *_rest = _dispatcher(
        monkeypatch, result=_result(identity_sha256="9" * 64)
    )
    with pytest.raises(runtime.LibraryApplicationUncertain) as error:
        dispatcher(_command())
    assert error.value.code == "library-application-result-mismatch"


def test_unproven_post_effect_result_leaves_started_receipt_for_recovery(
    monkeypatch,
):
    dispatcher, _calls, *_rest = _dispatcher(
        monkeypatch, result=_result(identity_sha256="9" * 64)
    )
    command = _command()

    class Store:
        finish_calls = 0

        def snapshot(self, transaction_id, operation_key):
            return SimpleNamespace(
                transaction_id=transaction_id,
                operation_key=operation_key,
                state="started",
                started_receipt=SimpleNamespace(
                    transaction_id=command.transaction_id,
                    plan_hash=command.plan_hash,
                    operation_key=command.operation_key,
                    request_hash=command.request_hash,
                    service_ids=command.service_ids,
                    event_hash="a" * 64,
                ),
                terminal_receipt=None,
            )

        def finish(self, *_args):
            self.finish_calls += 1
            raise AssertionError("uncertain work must not be terminalized")

    store = Store()
    with pytest.raises(runtime.LibraryApplicationUncertain):
        dispatch_receipted_lifecycle_work(
            command,
            dispatcher,
            store,
            lambda value: value,
            lambda _value: LifecycleWorkStartedObservation(state="missing"),
        )
    assert store.finish_calls == 0


@pytest.mark.parametrize(
    "classification,expected_state,expected_hash",
    [
        ("ABSENT", "missing", None),
        ("APPLIED", "completed", IDENTITY_HASH),
    ],
)
def test_started_observer_maps_current_state_to_receipt_contract(
    classification, expected_state, expected_hash
):
    observed = ObservationResult(
        service_id="gitea",
        classification=classification,
        identity_sha256=IDENTITY_HASH,
        record_sha256=None if classification == "ABSENT" else "a" * 64,
        containers=(),
    )
    adapter = runtime.LibraryApplicationStartedObserver(lambda _command: observed)
    result = adapter(_command())
    assert result.state == expected_state
    assert result.evidence_hash == expected_hash


def test_started_observer_refuses_unknown_or_untyped_classification():
    for observed in (
        object(),
        ObservationResult("gitea", "PARTIAL", IDENTITY_HASH, None, ()),
    ):
        adapter = runtime.LibraryApplicationStartedObserver(
            lambda _command, value=observed: value
        )
        with pytest.raises(runtime.LibraryApplicationRuntimeError) as error:
            adapter(_command())
        assert error.value.code == "library-application-observation-mismatch"


def test_public_service_set_is_exact_and_searxng_remains_separate():
    assert runtime.APPROVED_LIBRARY_SERVICES == frozenset(
        {"gitea", "miniflux", "ntfy", "ollama"}
    )
    assert "searxng" not in runtime.APPROVED_LIBRARY_SERVICES
