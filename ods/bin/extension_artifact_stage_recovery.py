"""Recover an exact started-only stage operation from durable bundle evidence.

This dormant observer reads only the bundle name derived from the validated
lifecycle command.  An exact immutable bundle proves stage completion; a
missing bundle permits the existing dispatcher path; every other observation
fails closed without terminalizing the receipt or invoking effects.
"""

from __future__ import annotations

from extension_artifact_stage_adapter import (
    ArtifactStageAdapterError,
    validate_staged_batch,
)
from extension_artifact_stage_store import ArtifactStageError, ArtifactStageStore
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkStartedObservation,
)


class ArtifactStageRecoveryError(LifecycleWorkExecutionError):
    """Stable, value-free failure while observing durable stage evidence."""


def _fail(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise ArtifactStageRecoveryError(code) from None
    raise ArtifactStageRecoveryError(code) from cause


class ArtifactStageRecoveryObserver:
    """Observe only the exact immutable stage bundle for one command."""

    def __init__(self, store: ArtifactStageStore) -> None:
        if type(store) is not ArtifactStageStore:
            _fail("lifecycle-work-artifact-stage-observer-invalid")
        self._store = store

    def __call__(
        self, command: LifecycleWorkCommand
    ) -> LifecycleWorkStartedObservation:
        if (
            type(command) is not LifecycleWorkCommand
            or command.operation_key != "stage"
        ):
            _fail("lifecycle-work-artifact-stage-observer-invalid")
        try:
            batch = self._store.read(
                command.transaction_id,
                command.plan_hash,
                command.service_ids,
            )
        except ArtifactStageError as exc:
            if exc.code == "artifact-stage-missing":
                return LifecycleWorkStartedObservation(state="missing")
            _fail("lifecycle-work-artifact-stage-observation-failed", exc)
        except Exception as exc:
            _fail("lifecycle-work-artifact-stage-observation-failed", exc)

        try:
            validated = validate_staged_batch(batch, command)
        except ArtifactStageAdapterError as exc:
            _fail("lifecycle-work-artifact-stage-observation-failed", exc)
        return LifecycleWorkStartedObservation(
            state="completed",
            evidence_hash=validated.bundle_sha256,
        )


__all__ = ["ArtifactStageRecoveryError", "ArtifactStageRecoveryObserver"]
