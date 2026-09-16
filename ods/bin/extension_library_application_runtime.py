"""Compose the exact receipted runtime for approved library applications.

This is the narrow production bridge between an already plan-bound lifecycle
command and the reviewed library Compose effect.  It reads only the immutable
artifact stage and fixed owner-private stores supplied by the host.  Runtime
construction performs no Compose, container, network, or application effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from extension_application_identity import (
    ApplicationIdentity,
    produce_application_identity,
)
from extension_application_observation import ObservationResult
from extension_application_observation_adapter import ApplicationObservationAdapter
from extension_application_record_store import ApplicationRecordStore
from extension_artifact_stage_store import ArtifactStageError, ArtifactStageStore
from extension_library_compose_apply_effect import (
    LibraryComposeApplyEffect,
    LibraryComposeApplyResult,
    capture_compose,
    run_compose,
)
from extension_library_configuration_binding import bind_library_configuration
from extension_library_effect_input import verify_plan_bound_library_payload
from extension_lifecycle_plan import LifecyclePlanMaterial, PlannedDefinition, PlannedOperation
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkStartedObservation,
    LifecycleWorkUncertainEffect,
    LifecycleWorkValidationError,
)


APPROVED_LIBRARY_SERVICES = frozenset({"gitea", "miniflux", "ntfy", "ollama"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ACTIONS = frozenset({"install", "enable", "repair", "update", "noop"})
_APPLICATION_ROOT_PARTS = (".ods-assistant-first", "applications")

_StageReader = Callable[[str, str, tuple[str, ...]], Any]
_TransactionLoader = Callable[[str], dict[str, Any]]
_PlanLoader = Callable[[LifecycleWorkCommand], LifecycleWorkCommand]


class LibraryApplicationRuntimeError(LifecycleWorkExecutionError):
    """Stable, value-free production-composition failure."""


class LibraryApplicationUncertain(LifecycleWorkUncertainEffect):
    """The effect returned but its durable result could not be proven."""


def _fail(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise LibraryApplicationRuntimeError(code) from None
    raise LibraryApplicationRuntimeError(code) from cause


def _validate_dispatch_command(
    command: Any,
) -> tuple[ApplicationIdentity, tuple[str, ...]]:
    if type(command) is not LifecycleWorkCommand or len(command.service_ids) != 1:
        raise LifecycleWorkValidationError(
            "library-application-command-invalid"
        ) from None
    service_id = command.service_ids[0]
    if (
        service_id not in APPROVED_LIBRARY_SERVICES
        or command.operation_key != f"apply:{service_id}"
        or command.payload
        != {"operation": {"serviceId": service_id, "action": "install"}}
    ):
        raise LifecycleWorkValidationError(
            "library-application-command-invalid"
        ) from None
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != "applying"
        or material.attested_approval is not True
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or len(material.operations) != len(material.definitions)
    ):
        raise LifecycleWorkValidationError("library-application-plan-mismatch") from None

    mutable: list[str] = []
    for operation, definition in zip(
        material.operations, material.definitions, strict=True
    ):
        if (
            type(operation) is not PlannedOperation
            or type(definition) is not PlannedDefinition
            or definition.service_id != operation.service_id
            or operation.action not in _ACTIONS
        ):
            raise LifecycleWorkValidationError(
                "library-application-plan-mismatch"
            ) from None
        if operation.action != "noop":
            mutable.append(operation.service_id)
    if (
        service_id not in mutable
        or len(mutable) != len(set(mutable))
        or not mutable
    ):
        raise LifecycleWorkValidationError("library-application-plan-mismatch") from None
    try:
        identity = produce_application_identity(command)
    except Exception as exc:
        raise LifecycleWorkValidationError(
            "library-application-plan-mismatch"
        ) from exc
    return identity, tuple(mutable)


class LibraryApplicationDispatcher:
    """Prepare exact inputs and invoke one approved Compose application."""

    def __init__(
        self,
        *,
        stage_reader: _StageReader,
        library_root: Path,
        transaction_loader: _TransactionLoader,
        secret_store: Any,
        effect: LibraryComposeApplyEffect,
    ) -> None:
        if (
            not callable(stage_reader)
            or not callable(transaction_loader)
            or not callable(getattr(secret_store, "status", None))
            or not callable(getattr(secret_store, "invoke_with_secrets", None))
            or type(effect) is not LibraryComposeApplyEffect
        ):
            _fail("library-application-runtime-invalid")
        self._stage_reader = stage_reader
        self._library_root = library_root
        self._transaction_loader = transaction_loader
        self._secret_store = secret_store
        self._effect = effect

    def __call__(self, command: LifecycleWorkCommand) -> str:
        identity, mutable = _validate_dispatch_command(command)
        try:
            staged = self._stage_reader(
                command.transaction_id, command.plan_hash, mutable
            )
        except ArtifactStageError as exc:
            _fail("library-application-stage-unavailable", exc)
        except Exception as exc:  # noqa: BLE001 - stage details remain private
            _fail("library-application-stage-unavailable", exc)
        effect_input = verify_plan_bound_library_payload(
            command, staged, self._library_root
        )
        configuration = bind_library_configuration(
            command,
            self._transaction_loader,
            self._secret_store.status,
        )
        result = self._effect.apply(
            command,
            effect_input,
            configuration,
            identity,
            self._secret_store,
        )
        if (
            type(result) is not LibraryComposeApplyResult
            or result.service_id != identity.service_id
            or result.identity_sha256 != identity.identity_sha256
            or _HASH_RE.fullmatch(result.identity_sha256) is None
        ):
            raise LibraryApplicationUncertain(
                "library-application-result-mismatch"
            ) from None
        # Current-state recovery proves this same identity hash.  Using one
        # evidence value lets a started-only receipt converge without rerun.
        return identity.identity_sha256


class LibraryApplicationStartedObserver:
    """Map exact current-state evidence to the lifecycle replay contract."""

    def __init__(self, observer: Callable[[LifecycleWorkCommand], Any]) -> None:
        if not callable(observer):
            _fail("library-application-runtime-invalid")
        self._observer = observer

    def __call__(
        self, command: LifecycleWorkCommand
    ) -> LifecycleWorkStartedObservation:
        result = self._observer(command)
        if type(result) is not ObservationResult:
            _fail("library-application-observation-mismatch")
        if result.classification == "ABSENT":
            return LifecycleWorkStartedObservation(state="missing")
        if (
            result.classification == "APPLIED"
            and isinstance(result.identity_sha256, str)
            and _HASH_RE.fullmatch(result.identity_sha256) is not None
        ):
            return LifecycleWorkStartedObservation(
                state="completed", evidence_hash=result.identity_sha256
            )
        _fail("library-application-observation-mismatch")


@dataclass(frozen=True)
class LibraryApplicationRuntime:
    dispatcher: LibraryApplicationDispatcher
    started_observer: LibraryApplicationStartedObserver


def build_library_application_runtime(
    *,
    install_dir: Path,
    user_extensions_root: Path,
    library_root: Path,
    stage_store: ArtifactStageStore,
    receipt_store: Any,
    plan_loader: _PlanLoader,
    transaction_loader: _TransactionLoader,
    secret_store: Any,
    active_lease: Callable[[], bool],
    runner: Any = run_compose,
    capture: Any = capture_compose,
) -> LibraryApplicationRuntime:
    """Compose the exact runtime without performing an application effect."""

    if (
        type(stage_store) is not ArtifactStageStore
        or not callable(getattr(stage_store, "read", None))
        or not callable(getattr(receipt_store, "snapshot", None))
        or not callable(plan_loader)
        or not callable(transaction_loader)
        or not callable(active_lease)
        or not callable(runner)
        or not callable(capture)
    ):
        _fail("library-application-runtime-invalid")
    effect = LibraryComposeApplyEffect(
        install_dir, user_extensions_root, runner=runner, capture=capture
    )
    records = ApplicationRecordStore(
        install_dir.joinpath(*_APPLICATION_ROOT_PARTS)
    )
    observation = ApplicationObservationAdapter(
        install_dir,
        records,
        receipt_store,
        plan_loader,
        active_lease,
    )
    return LibraryApplicationRuntime(
        dispatcher=LibraryApplicationDispatcher(
            stage_reader=stage_store.read,
            library_root=library_root,
            transaction_loader=transaction_loader,
            secret_store=secret_store,
            effect=effect,
        ),
        started_observer=LibraryApplicationStartedObserver(observation),
    )


__all__ = [
    "APPROVED_LIBRARY_SERVICES",
    "LibraryApplicationDispatcher",
    "LibraryApplicationRuntime",
    "LibraryApplicationRuntimeError",
    "LibraryApplicationStartedObserver",
    "LibraryApplicationUncertain",
    "build_library_application_runtime",
]
